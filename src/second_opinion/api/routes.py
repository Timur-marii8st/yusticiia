from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import Response as FileLikeResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..audit.trail import ANONYMOUS_ACTOR, SHARED_TOKEN_ACTOR
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
    HistoryResponse,
    MetricsResponse,
    NormCoverageItem,
    NormCoverageResponse,
    NormDetailResponse,
    NormListResponse,
    SearchResponse,
)

#: Тип содержимого .docx — заключение для подшивки в дело (ФТ-34).
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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


def _actor(request: Request) -> str:
    """Кто выполняет запрос. Опознание делает middleware выше."""
    return getattr(request.state, "actor", ANONYMOUS_ACTOR)


#: Размер порции при чтении загружаемого файла.
UPLOAD_CHUNK_BYTES = 64 * 1024


async def _read_within_limit(file: UploadFile, limit: int) -> bytes:
    """Прочитать файл, оборвав чтение при превышении лимита.

    ``await file.read()`` без аргумента затягивает в память весь запрос
    целиком, и лимит проверялся уже после этого: запрос на гигабайты
    буферизовался, чтобы затем быть отклонённым.
    """
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=400,
                detail=f"файл больше лимита {limit} байт",
            )
        chunks.append(chunk)
    return b"".join(chunks)


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

    # Опознание вызывающего и авторизация (ADR-008).
    #
    # Опознание идёт всегда, даже когда SO_AUTH_TOKEN пуст и доступ открыт:
    # аудит должен записывать, кто выполнил действие, а в открытом режиме
    # вход по личной учётной записи по-прежнему возможен.
    #
    # Принуждение остаётся условным: без SO_AUTH_TOKEN /api/* открыт
    # (loopback-MVP). При заданном секрете принимается общий Bearer ИЛИ
    # валидный JWT; /auth/login и /auth/refresh открыты всегда, иначе для
    # получения токена требовался бы токен.
    @app.middleware("http")
    async def _identify_and_authorize(request, call_next):  # noqa: ANN202
        request.state.actor = ANONYMOUS_ACTOR
        header = request.headers.get("authorization", "")
        authorized = not auth_token

        if auth_token and header == f"Bearer {auth_token}":
            request.state.actor = SHARED_TOKEN_ACTOR
            authorized = True
        elif header.startswith("Bearer "):
            from ..auth.service import get_auth_service

            auth_service = get_auth_service()
            payload = auth_service.decode_token(header.split(" ", 1)[1])
            if payload and payload.type == "access":
                user = auth_service.get_user(payload.sub)
                if user and user.is_active:
                    request.state.actor = user.user_id
                    authorized = True

        if not authorized and request.url.path.startswith("/api"):
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
    def create_document(
        payload: TextDocumentPayload, request: Request
    ) -> DocumentCreated:
        try:
            document = pipeline.ingest(
                payload.filename, payload.text.encode("utf-8"), actor=_actor(request)
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
    async def upload_document(
        file: Annotated[UploadFile, File(...)], request: Request
    ) -> DocumentCreated:
        content = await _read_within_limit(file, pipeline.max_upload_bytes)
        try:
            document = pipeline.ingest(
                file.filename or "upload.txt", content, actor=_actor(request)
            )
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
    def delete_document(document_id: str, request: Request) -> DeletedResponse:
        """Удалить документ и все отчёты по нему (право на забвение)."""
        try:
            pipeline.delete_document(document_id, actor=_actor(request))
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": document_id}

    @app.get(
        "/api/analyses/{analysis_id}/document",
        # Ответ — файл, а не JSON. Без явного описания схема объявляла бы
        # его «любым объектом», и по спецификации нельзя было бы понять,
        # что эндпойнт отдаёт документ.
        response_class=FileLikeResponse,
        responses={
            200: {
                "description": "Заключение в формате .docx",
                "content": {DOCX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    def download_analysis_document(analysis_id: str):  # noqa: ANN202
        """Заключение в .docx — файл, который можно подшить в дело.

        Печать из браузера годится посмотреть, но не переслать и не
        приложить к материалам.
        """
        from ..export import build_docx

        report = pipeline.get_analysis(analysis_id)
        if report is None:
            raise HTTPException(status_code=404, detail="отчёт не найден")
        try:
            document = pipeline.get_document(report.document_id)
        except DocumentNotFound:
            # Документ удалён по праву на забвение, отчёт остался:
            # заключение собирается без реквизитов файла, а не падает.
            document = None
        payload = build_docx(report, document)
        return FileLikeResponse(
            content=payload,
            media_type=DOCX_MEDIA_TYPE,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="second-opinion-{analysis_id[:8]}.docx"'
                )
            },
        )

    @app.delete("/api/analyses/{analysis_id}")
    def delete_analysis(analysis_id: str, request: Request) -> DeletedResponse:
        try:
            pipeline.delete_analysis(analysis_id, actor=_actor(request))
        except AnalysisNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"deleted": analysis_id}

    @app.post("/api/documents/{document_id}/analyze")
    def analyze(
        document_id: str,
        request: Request,
        payload: AnalyzePayload | None = None,
    ) -> AnalysisReportResponse:
        try:
            report = pipeline.analyze(
                document_id,
                applicable_at=payload.applicable_at if payload else None,
                actor=_actor(request),
            )
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return report

    @app.get("/api/analyses")
    def list_analyses(
        request: Request, limit: int = 50, scope: str = "own"
    ) -> HistoryResponse:
        """История дел: свои — всем, все — только администратору.

        Разделение по актору, а не по «кто первый открыл»: судья работает
        со своими материалами, и чужое дело в его списке — это утечка, а
        не удобство. Администратор запрашивает полный список явно
        (``scope=all``), чтобы расширение видимости было намеренным.
        """
        actor = _actor(request)
        owner: str | None = actor
        effective_scope = "own"
        if scope == "all":
            from ..auth.service import get_auth_service
            from ..domain.users import UserRole

            user = get_auth_service().get_user(actor)
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

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> AnalysisReportResponse:
        report = pipeline.get_analysis(analysis_id)
        if report is None:
            raise HTTPException(status_code=404, detail="отчёт не найден")
        return report

    @app.post("/api/analyses/{analysis_id}/facts")
    def add_fact(
        analysis_id: str, payload: FactAddPayload, request: Request
    ) -> AnalysisReportResponse:
        """Судья добавляет обстоятельство «с нуля»: тип + значение + цитата
        из документа. Цитата обязана дословно находиться в тексте."""

        try:
            report = pipeline.add_fact(
                analysis_id,
                fact_type=payload.type,
                value=payload.value,
                quote=payload.quote,
                actor=_actor(request),
            )
        except (AnalysisNotFound, DocumentNotFound) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FactValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report

    @app.patch("/api/analyses/{analysis_id}/facts/{fact_id}")
    def update_fact(
        analysis_id: str,
        fact_id: str,
        payload: FactUpdatePayload,
        request: Request,
    ) -> AnalysisReportResponse:
        """Правка факта человеком: подтверждение, исключение или коррекция
        значения. Отчёт пересчитывается детерминированным движком."""
        value = payload.value if "value" in payload.model_fields_set else UNSET_VALUE
        try:
            report = pipeline.update_fact(
                analysis_id,
                fact_id,
                status=payload.status,
                value=value,
                actor=_actor(request),
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

    @app.get("/api/norms/coverage")
    def norms_coverage() -> NormCoverageResponse:
        """Состояние корпуса: что сверено, что требует юридической сверки.

        Объявлен до ``/api/norms/{norm_id}``: иначе «coverage» приедет
        сюда как идентификатор нормы и вернётся 404.

        Свежесть относительно внешнего источника здесь не проверяется —
        это сетевой запрос к медленному серверу, которому не место в
        обработке HTTP-запроса. Она остаётся за `norms-check-updates`.
        """
        from ..legal_sources.coverage import analyze_store, needs_legal_review

        store = pipeline.norm_store
        coverages = analyze_store(store)
        # Наряд считается той же функцией, что и в CLI: своя формула
        # здесь однажды разойдётся с `norms-coverage`, и два числа
        # будут называться одинаково.
        flagged = {c.norm_id for c in needs_legal_review(coverages)}

        items: list[NormCoverageItem] = []
        for coverage in coverages:
            editions = sorted(
                {
                    v.source_edition
                    for v in store.versions(coverage.norm_id)
                    if v.source_edition
                }
            )
            items.append(
                NormCoverageItem(
                    norm_id=coverage.norm_id,
                    ref=coverage.ref,
                    version_count=coverage.version_count,
                    covered_from=coverage.covered_from,
                    covered_to=coverage.covered_to,
                    rule_relevant=coverage.rule_relevant,
                    history_verified=coverage.history_verified,
                    history_unrecorded=coverage.history_unrecorded(),
                    needs_review=coverage.norm_id in flagged,
                    gaps=[list(gap) for gap in coverage.gaps],
                    source_editions=editions,
                )
            )

        config = load_config()
        sealed: bool | None = None
        if config.norms_backend == "file":
            from ..legal_sources.sealing import check as check_seal

            try:
                sealed = check_seal(config.fixtures_dir / "norms").ok
            except (OSError, ValueError):
                # Манифест нечитаем — это «неизвестно», а не «цел».
                sealed = None

        return {
            "total": len(items),
            "needs_review": len(flagged),
            "corpus_sealed": sealed,
            "norms": items,
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
