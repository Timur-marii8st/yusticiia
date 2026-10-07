"""Разборы: история, отчёт, выгрузка заключения, правка обстоятельств судьёй."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...fact_values import FactValidationError
from ...pipeline import (
    UNSET_VALUE,
    AnalysisNotFound,
    AnalysisPipeline,
    DocumentNotFound,
    FactNotFound,
)
from ..errors import human_message
from ..ownership import require_owner
from ..request_context import actor
from ..schemas import AnalysisReportResponse, DeletedResponse, HistoryResponse

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class FactUpdatePayload(BaseModel):
    status: str | None = None
    value: Any = None
    reason: str | None = None


class FactAddPayload(BaseModel):
    type: str
    value: Any
    quote: str = Field(min_length=1)


def build_router(pipeline: AnalysisPipeline) -> APIRouter:
    router = APIRouter(prefix="/analyses", tags=["разборы"])

    @router.get("")
    def list_analyses(
        request: Request, limit: int = 50, scope: str = "own"
    ) -> HistoryResponse:
        """История дел: свои — всем, все — только администратору.

        Разделение по актору, а не по «кто первый открыл»: судья работает со
        своими материалами, и чужое дело в его списке — это утечка, а не
        удобство. Администратор запрашивает полный список явно (``scope=all``),
        чтобы расширение видимости было намеренным.
        """
        requester = actor(request)
        owner: str | None = requester
        effective_scope = "own"
        if scope == "all":
            from ...auth.service import get_auth_service
            from ...domain.users import UserRole

            user = get_auth_service().get_user(requester)
            if user is None or user.role is not UserRole.ADMIN:
                raise HTTPException(
                    status_code=403,
                    detail="полный список истории доступен только администратору",
                )
            owner = None
            effective_scope = "all"

        items = []
        for report, document in pipeline.history(owner=owner, limit=limit):
            checks: dict[str, int] = {}
            for evaluation in report.evaluations:
                key = getattr(evaluation.status, "value", str(evaluation.status))
                checks[key] = checks.get(key, 0) + 1
            qualifications = report.case_facts.offense.qualifications
            items.append(
                {
                    "analysis_id": report.analysis_id,
                    "document_id": report.document_id,
                    "filename": document.filename if document else "",
                    "created_at": report.created_at,
                    "applicable_at": report.applicable_at,
                    "qualification": qualifications[0].ref if qualifications else "",
                    "facts_total": len(report.facts),
                    "checks": checks,
                }
            )
        return {"scope": effective_scope, "total": len(items), "items": items}

    def _own_analysis(analysis_id: str, request: Request):  # noqa: ANN202
        report = pipeline.get_analysis(analysis_id)
        if report is None:
            raise HTTPException(status_code=404, detail="отчёт не найден")
        require_owner(report.owner, request, "отчёт не найден")
        return report

    @router.get("/{analysis_id}")
    def get_analysis(analysis_id: str, request: Request) -> AnalysisReportResponse:
        return _own_analysis(analysis_id, request)

    @router.delete("/{analysis_id}")
    def delete_analysis(analysis_id: str, request: Request) -> DeletedResponse:
        _own_analysis(analysis_id, request)
        try:
            pipeline.delete_analysis(analysis_id, actor=actor(request))
        except AnalysisNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return {"deleted": analysis_id}

    @router.get(
        "/{analysis_id}/document",
        response_class=Response,
        responses={
            200: {
                "description": "Заключение в формате .docx",
                "content": {
                    DOCX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}
                },
            }
        },
    )
    def download_analysis_document(
        analysis_id: str, request: Request, mask: bool = False
    ) -> Response:
        """Заключение в .docx — файл, который можно подшить в дело.

        Печать из браузера годится посмотреть, но не переслать и не приложить
        к материалам.

        ``mask=true`` скрывает персональные данные в выгружаемом файле (Д-06).
        Хранимый документ при этом не меняется: обезличивается то, что
        покидает систему.
        """
        from ...export import build_docx

        report = _own_analysis(analysis_id, request)
        try:
            document = pipeline.get_document(report.document_id)
        except DocumentNotFound:
            document = None
        payload = build_docx(report, document, mask_personal_data=mask)
        readable_name = f"Заключение_{analysis_id[:8]}.docx"
        ascii_fallback = f"zaklyuchenie_{analysis_id[:8]}.docx"
        return Response(
            content=payload,
            media_type=DOCX_MEDIA_TYPE,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_fallback}"; '
                    f"filename*=UTF-8''{quote(readable_name)}"
                )
            },
        )

    @router.post("/{analysis_id}/facts")
    def add_fact(
        analysis_id: str, payload: FactAddPayload, request: Request
    ) -> AnalysisReportResponse:
        """Судья добавляет обстоятельство «с нуля»: тип, значение и цитата.

        Цитата обязана дословно находиться в тексте документа.
        """
        _own_analysis(analysis_id, request)
        try:
            report = pipeline.add_fact(
                analysis_id,
                fact_type=payload.type,
                value=payload.value,
                quote=payload.quote,
                actor=actor(request),
            )
        except (AnalysisNotFound, DocumentNotFound) as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        except FactValidationError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return report

    @router.patch("/{analysis_id}/facts/{fact_id}")
    def update_fact(
        analysis_id: str,
        fact_id: str,
        payload: FactUpdatePayload,
        request: Request,
    ) -> AnalysisReportResponse:
        """Правка факта человеком: подтверждение, исключение или коррекция.

        Отчёт пересчитывается детерминированным движком.
        """
        _own_analysis(analysis_id, request)
        value = payload.value if "value" in payload.model_fields_set else UNSET_VALUE
        try:
            report = pipeline.update_fact(
                analysis_id,
                fact_id,
                status=payload.status,
                value=value,
                reason=payload.reason,
                actor=actor(request),
            )
        except (AnalysisNotFound, FactNotFound) as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return report

    return router
