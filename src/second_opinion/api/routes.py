from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..domain.cases import ComparableCase
from ..ingestion.parser import ParseError
from ..legal_sources.store import NoApplicableVersionError, NormNotFoundError
from ..pipeline import (
    UNSET_VALUE,
    AnalysisNotFound,
    AnalysisPipeline,
    DocumentNotFound,
    FactNotFound,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class TextDocumentPayload(BaseModel):
    filename: str
    text: str = Field(min_length=1)


class AnalyzePayload(BaseModel):
    applicable_at: str | None = None


class FactUpdatePayload(BaseModel):
    status: str | None = None
    value: Any = None


def create_app(pipeline: AnalysisPipeline) -> FastAPI:
    app = FastAPI(
        title="Второе мнение",
        version=__version__,
        description=(
            "Верифицируемая система юридического аудита проекта судебного акта. "
            "Не заменяет судью и не назначает наказание."
        ),
    )
    app.state.pipeline = pipeline

    # -- служебные ----------------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    # -- документы ----------------------------------------------------------

    @app.post("/api/documents")
    def create_document(payload: TextDocumentPayload) -> dict:
        try:
            document = pipeline.ingest(
                payload.filename, payload.text.encode("utf-8")
            )
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "content_type": document.content_type,
            "chars": len(document.text),
        }

    @app.post("/api/documents/upload")
    async def upload_document(file: Annotated[UploadFile, File(...)]) -> dict:
        content = await file.read()
        try:
            document = pipeline.ingest(file.filename or "upload.txt", content)
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "content_type": document.content_type,
            "chars": len(document.text),
        }

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str) -> dict:
        try:
            document = pipeline.get_document(document_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "text": document.text,
            "sha256": document.sha256,
        }

    @app.post("/api/documents/{document_id}/analyze")
    def analyze(document_id: str, payload: AnalyzePayload | None = None) -> dict:
        try:
            report = pipeline.analyze(
                document_id,
                applicable_at=payload.applicable_at if payload else None,
            )
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return report.model_dump(mode="json")

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> dict:
        report = pipeline.get_analysis(analysis_id)
        if report is None:
            raise HTTPException(status_code=404, detail="отчёт не найден")
        return report.model_dump(mode="json")

    @app.patch("/api/analyses/{analysis_id}/facts/{fact_id}")
    def update_fact(
        analysis_id: str, fact_id: str, payload: FactUpdatePayload
    ) -> dict:
        """Правка факта человеком: подтверждение, исключение или коррекция
        значения. Отчёт пересчитывается детерминированным движком."""
        value = payload.value if "value" in payload.model_fields_set else UNSET_VALUE
        try:
            report = pipeline.update_fact(
                analysis_id, fact_id, status=payload.status, value=value
            )
        except (AnalysisNotFound, FactNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report.model_dump(mode="json")

    # -- нормы --------------------------------------------------------------

    @app.get("/api/norms")
    def list_norms() -> dict:
        store = pipeline.norm_store
        return {
            "norms": [
                {"norm_id": n.norm_id, "ref": n.ref, "title": n.title}
                for n in store.list_norms()
            ]
        }

    @app.get("/api/norms/{norm_id}")
    def get_norm(norm_id: str, applicable_at: str | None = Query(default=None)) -> dict:
        store = pipeline.norm_store
        try:
            meta = store.get_norm_meta(norm_id)
        except NormNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if applicable_at:
            try:
                when = date.fromisoformat(applicable_at)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="applicable_at: ожидается ISO-дата"
                ) from exc
            try:
                version = store.get_norm(norm_id, when)
            except NoApplicableVersionError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"norm": meta.model_dump(), "version": version.model_dump(mode="json")}
        return {
            "norm": meta.model_dump(),
            "versions": [v.model_dump(mode="json") for v in store.versions(norm_id)],
        }

    # -- практика -------------------------------------------------------------

    @app.get("/api/cases")
    def list_cases(
        article: int | None = Query(default=None),
        part: int | None = Query(default=None),
        limit: int = Query(default=50, le=200),
    ) -> dict:
        cases = pipeline.retriever.cases
        selected: list[ComparableCase] = []
        for case in cases:
            if article is not None and case.article != article:
                continue
            if part is not None and case.part != part:
                continue
            selected.append(case)
        return {
            "total": len(selected),
            "cases": [c.model_dump(mode="json") for c in selected[:limit]],
        }

    # -- статический UI -------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
