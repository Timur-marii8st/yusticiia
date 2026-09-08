from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..auth.rate_limit import LoginRateLimiter
from ..config import load_config
from ..domain.cases import ComparableCase
from ..ingestion.parser import ParseError
from ..legal_sources.store import NoApplicableVersionError, NormNotFoundError
from ..pipeline import (
    UNSET_VALUE,
    AnalysisNotFound,
    AnalysisPipeline,
    DocumentNotFound,
    FactNotFound,
    FactValidationError,
)
from .auth_routes import router as auth_router
from .schemas import (
    AnalysisReportResponse,
    CaseListResponse,
    DeletedResponse,
    DocumentContent,
    DocumentCreated,
    HealthResponse,
    MetricsResponse,
    NormDetailResponse,
    NormListResponse,
    SearchResponse,
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


class FactAddPayload(BaseModel):
    type: str
    value: Any
    quote: str = Field(min_length=1)


def create_app(pipeline: AnalysisPipeline, auth_token: str = "") -> FastAPI:
    app = FastAPI(
        title="Второе мнение",
        version=__version__,
        description=(
            "Верифицируемая система юридического аудита проекта судебного акта. "
            "Не заменяет судью и не назначает наказание."
        ),
    )
    app.state.pipeline = pipeline
    # Лимитер привязан к приложению, а не к модулю: иначе экземпляры
    # делили бы счётчики между собой.
    _config = load_config()
    app.state.login_rate_limiter = LoginRateLimiter(
        _config.login_rate_limit, _config.login_rate_window_seconds
    )

    # Подключаем роутер аутентификации

    app.include_router(auth_router)

    # Создаём экземпляр LegalRag для поиска по источникам
    from ..api.deps import build_legal_rag

    rag = build_legal_rag(pipeline.norm_store)

    # Двухслойная авторизация (ADR-008): если задан SO_AUTH_TOKEN, /api/*
    # принимает общий секрет ИЛИ валидный JWT access-токен. /auth/login и
    # /auth/refresh остаются открытыми (иначе вход невозможен).
    if auth_token:

        @app.middleware("http")
        async def _require_token(request, call_next):  # noqa: ANN202
            path = request.url.path
            if path.startswith("/api"):
                header = request.headers.get("authorization", "")
                if header == f"Bearer {auth_token}":
                    return await call_next(request)
                # Второй слой: JWT access-токен персональной учётки.
                if header.startswith("Bearer "):
                    from ..auth.service import get_auth_service

                    auth_service = get_auth_service()
                    payload = auth_service.decode_token(header.split(" ", 1)[1])
                    if payload and payload.type == "access":
                        user = auth_service.get_user(payload.sub)
                        if user and user.is_active:
                            return await call_next(request)
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=401,
                    content={"detail": "требуется авторизация"},
                )
            return await call_next(request)

    # -- служебные ----------------------------------------------------------

    @app.get("/health")
    def health() -> HealthResponse:
        return {"status": "ok", "version": __version__}

    # exclude_none сохраняет документированную форму `{"enabled": false}`:
    # у вложенных моделей метрик необязательных полей нет, поэтому побочных
    # эффектов не возникает.
    @app.get("/metrics", response_model_exclude_none=True)
    def metrics() -> MetricsResponse:
        """Снимок внутренних метрик конвейера.

        Включается через ``SO_METRICS_ENABLED=1``. Если выключено,
        возвращается ``{"enabled": false}`` без накопления значений.
        """
        from ..metrics import get_metrics

        registry = get_metrics()
        if not registry.enabled:
            return {"enabled": False}
        return {"enabled": True, "metrics": registry.snapshot()}

    # -- документы ----------------------------------------------------------

    @app.post("/api/documents")
    def create_document(payload: TextDocumentPayload) -> DocumentCreated:
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
    async def upload_document(file: Annotated[UploadFile, File(...)]) -> DocumentCreated:
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
    def get_document(document_id: str) -> DocumentContent:
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

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id: str) -> DeletedResponse:
        """Удалить документ и все отчёты по нему (право на забвение)."""
        try:
            pipeline.delete_document(document_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": document_id}

    @app.delete("/api/analyses/{analysis_id}")
    def delete_analysis(analysis_id: str) -> DeletedResponse:
        try:
            pipeline.delete_analysis(analysis_id)
        except AnalysisNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": analysis_id}

    @app.post("/api/documents/{document_id}/analyze")
    def analyze(
        document_id: str, payload: AnalyzePayload | None = None
    ) -> AnalysisReportResponse:
        try:
            report = pipeline.analyze(
                document_id,
                applicable_at=payload.applicable_at if payload else None,
            )
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return report

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> AnalysisReportResponse:
        report = pipeline.get_analysis(analysis_id)
        if report is None:
            raise HTTPException(status_code=404, detail="отчёт не найден")
        return report

    @app.post("/api/analyses/{analysis_id}/facts")
    def add_fact(analysis_id: str, payload: FactAddPayload) -> AnalysisReportResponse:
        """Судья добавляет обстоятельство «с нуля»: тип + значение + цитата
        из документа. Цитата обязана дословно находиться в тексте."""

        try:
            report = pipeline.add_fact(
                analysis_id,
                fact_type=payload.type,
                value=payload.value,
                quote=payload.quote,
            )
        except (AnalysisNotFound, DocumentNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report

    @app.patch("/api/analyses/{analysis_id}/facts/{fact_id}")
    def update_fact(
        analysis_id: str, fact_id: str, payload: FactUpdatePayload
    ) -> AnalysisReportResponse:
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
        return report

    # -- нормы --------------------------------------------------------------

    @app.get("/api/norms")
    def list_norms() -> NormListResponse:
        store = pipeline.norm_store
        return {
            "norms": [
                {"norm_id": n.norm_id, "ref": n.ref, "title": n.title}
                for n in store.list_norms()
            ]
        }

    @app.get("/api/norms/{norm_id}")
    def get_norm(
        norm_id: str, applicable_at: str | None = Query(default=None)
    ) -> NormDetailResponse:
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
            return {"norm": meta, "version": version}
        return {"norm": meta, "versions": store.versions(norm_id)}

    # -- практика -------------------------------------------------------------

    @app.get("/api/cases")
    def list_cases(
        article: int | None = Query(default=None),
        part: int | None = Query(default=None),
        limit: int = Query(default=50, le=200),
    ) -> CaseListResponse:
        cases = pipeline.retriever.cases
        selected: list[ComparableCase] = []
        for case in cases:
            if article is not None and case.article != article:
                continue
            if part is not None and case.part != part:
                continue
            selected.append(case)
        return {"total": len(selected), "cases": selected[:limit]}

    # -- поиск по базе источников ---------------------------------------------

    @app.get("/api/search")
    def search_sources(
        q: str = Query(min_length=1),
        limit: int = Query(default=10, ge=1, le=50),
        code: str | None = None,
        article: int | None = None,
        applicable_at: str | None = None,
    ) -> SearchResponse:
        """Лексический поиск по базе норм. Только реальные фрагменты
        источников; пустой результат честнее выдуманного."""
        when: date | None = None
        if applicable_at:
            try:
                when = date.fromisoformat(applicable_at)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="applicable_at: ожидается ISO-дата"
                ) from exc
        hits = rag.search(
            q, limit=limit, code=code, article=article, applicable_at=when
        )
        return {"query": q, "results": hits}

    # -- статический UI -------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
