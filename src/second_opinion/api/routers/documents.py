"""Документы: приём текста и файла, чтение, удаление, запуск разбора."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ...ingestion.parser import ParseError
from ...pipeline import AnalysisPipeline, DocumentNotFound
from ..errors import human_message
from ..ownership import require_owner
from ..request_context import actor
from ..schemas import (
    AnalysisReportResponse,
    DeletedResponse,
    DocumentContent,
    DocumentCreated,
)
from ..uploads import read_within_limit


class TextDocumentPayload(BaseModel):
    filename: str
    text: str = Field(min_length=1)


class AnalyzePayload(BaseModel):
    applicable_at: str | None = None


@contextmanager
def analysis_slot(request: Request) -> Iterator[None]:
    """Ограничить частоту и число одновременных анализов.

    Анализ может обращаться к модели и занимать видеокарту на десятки секунд:
    без ограничений один клиент способен занять её целиком. Частота считается
    на актора, число одновременных запусков общее; ожидание свободного места
    ограничено, затем ``503``.
    """
    limiter = getattr(request.app.state, "analyze_rate_limiter", None)
    if limiter is not None:
        retry_after = limiter.retry_after(actor(request))
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="Слишком много запусков анализа — повторите позже",
                headers={"Retry-After": str(retry_after)},
            )
    slots = getattr(request.app.state, "analyze_slots", None)
    if slots is None:
        yield
        return
    wait = float(getattr(request.app.state, "analyze_queue_seconds", 30.0))
    if not slots.acquire(timeout=wait):
        raise HTTPException(
            status_code=503,
            detail="Сервер занят другими анализами — повторите позже",
            headers={"Retry-After": str(max(1, int(wait)))},
        )
    try:
        yield
    finally:
        slots.release()


def build_router(pipeline: AnalysisPipeline) -> APIRouter:
    router = APIRouter(prefix="/documents", tags=["документы"])

    def _created(document) -> DocumentCreated:  # noqa: ANN001
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "content_type": document.content_type,
            "chars": len(document.text),
        }

    @router.post("")
    def create_document(
        payload: TextDocumentPayload, request: Request
    ) -> DocumentCreated:
        try:
            document = pipeline.ingest(
                payload.filename,
                payload.text.encode("utf-8"),
                actor=actor(request),
                request_id=request.state.request_id,
            )
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return _created(document)

    @router.post("/upload")
    async def upload_document(
        file: Annotated[UploadFile, File(...)], request: Request
    ) -> DocumentCreated:
        content = await read_within_limit(file, pipeline.max_upload_bytes)
        try:
            document = pipeline.ingest(
                file.filename or "upload.txt",
                content,
                actor=actor(request),
                request_id=request.state.request_id,
            )
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return _created(document)

    def _own_document(document_id: str, request: Request):  # noqa: ANN202
        try:
            document = pipeline.get_document(document_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        require_owner(document.owner, request, f"документ не найден: {document_id}")
        return document

    @router.get("/{document_id}")
    def get_document(document_id: str, request: Request) -> DocumentContent:
        document = _own_document(document_id, request)
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "text": document.text,
            "sha256": document.sha256,
        }

    @router.delete("/{document_id}")
    def delete_document(document_id: str, request: Request) -> DeletedResponse:
        """Удалить документ и все отчёты по нему (право на забвение)."""
        _own_document(document_id, request)
        try:
            pipeline.delete_document(document_id, actor=actor(request))
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return {"deleted": document_id}

    @router.post("/{document_id}/analyze")
    def analyze(
        document_id: str,
        request: Request,
        payload: AnalyzePayload | None = None,
    ) -> AnalysisReportResponse:
        _own_document(document_id, request)
        with analysis_slot(request):
            try:
                report = pipeline.analyze(
                    document_id,
                    applicable_at=payload.applicable_at if payload else None,
                    actor=actor(request),
                    request_id=request.state.request_id,
                )
            except DocumentNotFound as exc:
                raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return report

    return router
