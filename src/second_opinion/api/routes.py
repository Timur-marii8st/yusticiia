from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import Response as FileLikeResponse
from fastapi.routing import APIRoute
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

#: Режимы авторизации /api/* (ФТ-40). Описание — в docs/SECURITY.md.
AUTH_MODE_OPEN = "open"
AUTH_MODE_SHARED = "shared"
AUTH_MODE_ACCOUNTS = "accounts"
AUTH_MODES = (AUTH_MODE_OPEN, AUTH_MODE_SHARED, AUTH_MODE_ACCOUNTS)


def resolve_auth_mode(configured: str, auth_token: str) -> str:
    """Определить режим авторизации.

    Пустое значение сохраняет прежнее поведение развёртываний, где о
    режиме никто не объявлял: есть общий секрет — принимаются оба способа,
    нет — доступ открыт. Молчаливое ужесточение сломало бы работающие
    установки, молчаливое ослабление — открыло бы их.
    """
    mode = (configured or "").strip().lower()
    if mode in AUTH_MODES:
        return mode
    if mode:
        raise ValueError(
            f"SO_AUTH_MODE: ожидается одно из {', '.join(AUTH_MODES)} (получено: {mode!r})"
        )
    return AUTH_MODE_SHARED if auth_token else AUTH_MODE_OPEN


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


#: Заголовок сквозного идентификатора запроса.
#:
#: Конвейер уже пишет request_id в структурные логи и в аудит-журнал, но
#: снаружи его было не узнать: судья или оператор, наткнувшись на ошибку,
#: не мог назвать, какую именно запись искать. Заголовок связывает то,
#: что видел человек, с тем, что записано в журнале.
REQUEST_ID_HEADER = "X-Request-Id"


def _request_id(request: Request) -> str:
    """Идентификатор запроса: принятый от клиента или свой."""
    incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
    # Чужое значение принимается только в безопасном виде: оно попадает в
    # журнал и в заголовок ответа, а значит может стать вектором подмены
    # строк лога.
    if incoming and len(incoming) <= 64 and incoming.isascii() and incoming.isprintable():
        return incoming
    return uuid.uuid4().hex


def human_message(exc: Exception) -> str:
    """Сообщение об ошибке без служебного обрамления.

    ``KeyError`` — основа половины наших исключений («норма не найдена»,
    «документ не найден»), а ``str()`` у него возвращает **repr** аргумента:
    наружу уходило `"'норма не найдена: uk-rf:art-1'"` вместе с кавычками.
    Для судьи это выглядит как сбой, а не как объяснение.
    """
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


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
    auth_mode = resolve_auth_mode(_config.auth_mode, auth_token)
    app.state.auth_mode = auth_mode
    app.state.login_rate_limiter = LoginRateLimiter(
        _config.login_rate_limit, _config.login_rate_window_seconds
    )

    # Подключаем роутер аутентификации

    app.include_router(auth_router)

    # Создаём экземпляр LegalRag для поиска по источникам
    from ..api.deps import build_legal_rag

    rag = build_legal_rag(pipeline.norm_store)

    # Опознание вызывающего и авторизация (ADR-008, режимы — ФТ-40).
    #
    # Опознание идёт всегда, даже в открытом режиме: аудит должен
    # записывать, кто выполнил действие, а вход по личной учётной записи
    # возможен и там, где доступ не ограничен.
    #
    # Принуждение зависит от режима:
    #   open     — /api/* открыт (демонстрация на loopback);
    #   shared   — общий секрет ИЛИ личный токен;
    #   accounts — только личный токен, общий секрет не принимается.
    # /auth/login и /auth/refresh открыты всегда, иначе для получения
    # токена требовался бы токен.
    @app.middleware("http")
    async def _identify_and_authorize(request, call_next):  # noqa: ANN202
        request.state.actor = ANONYMOUS_ACTOR
        request.state.request_id = _request_id(request)
        header = request.headers.get("authorization", "")
        # В режиме accounts общий секрет не принимается вовсе: в судебной
        # системе действие должно быть привязано к человеку, а под общим
        # секретом все записи в журнале обезличены.
        authorized = auth_mode == AUTH_MODE_OPEN

        if auth_mode == AUTH_MODE_SHARED and auth_token and header == f"Bearer {auth_token}":
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
                headers={REQUEST_ID_HEADER: request.state.request_id},
            )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    # Единый формат ошибок (техдолг из OPEN_QUESTIONS).
    #
    # Было: 404 отдавал строку в `detail`, а 422 — список объектов там же.
    # Клиенту приходилось угадывать форму по коду ответа. Теперь рядом с
    # `detail` (его читает текущий интерфейс, поэтому поле сохранено)
    # едет `error` с кодом, сообщением, полями и идентификатором запроса —
    # тем самым, который стоит в заголовке ответа и в аудит-журнале.
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse as _JSONResponse

    _ERROR_CODES = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "too_many_requests",
    }

    def _error_body(
        status_code: int, message: str, request, fields: list[dict] | None = None
    ) -> dict:
        body: dict[str, object] = {
            "detail": message,
            "error": {
                "code": _ERROR_CODES.get(status_code, "error"),
                "message": message,
                "request_id": getattr(request.state, "request_id", ""),
            },
        }
        if fields:
            body["error"]["fields"] = fields  # type: ignore[index]
        return body

    @app.exception_handler(HTTPException)
    async def _http_error(request, exc: HTTPException):  # noqa: ANN202
        message = exc.detail if isinstance(exc.detail, str) else human_message(exc)
        # Заголовки исключения обязаны дожить до ответа: в них едет
        # Retry-After у 429 и WWW-Authenticate у 401. Единый формат тела
        # не повод терять часть протокола.
        headers = dict(exc.headers or {})
        headers[REQUEST_ID_HEADER] = getattr(request.state, "request_id", "")
        return _JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.status_code, message, request),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc: RequestValidationError):  # noqa: ANN202
        fields = [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", ""),
            }
            for error in exc.errors()
        ]
        first = fields[0] if fields else {"location": "", "message": "неверный запрос"}
        message = f"{first['location']}: {first['message']}" if first["location"] else first["message"]
        return _JSONResponse(
            status_code=422,
            content=_error_body(422, message, request, fields),
            headers={REQUEST_ID_HEADER: getattr(request.state, "request_id", "")},
        )

    # -- служебные ----------------------------------------------------------

    @app.get("/health")
    def health() -> HealthResponse:
        # Режим авторизации — факт развёртывания, а не секрет: интерфейсу
        # он нужен, чтобы не предлагать судье вход по общему секрету там,
        # где секрет не принимается.
        return {"status": "ok", "version": __version__, "auth_mode": auth_mode}

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

    @app.post("/api/v1/documents")
    def create_document(
        payload: TextDocumentPayload, request: Request
    ) -> DocumentCreated:
        try:
            document = pipeline.ingest(
                payload.filename,
                payload.text.encode("utf-8"),
                actor=_actor(request),
                request_id=request.state.request_id,
            )
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "content_type": document.content_type,
            "chars": len(document.text),
        }

    @app.post("/api/v1/documents/upload")
    async def upload_document(
        file: Annotated[UploadFile, File(...)], request: Request
    ) -> DocumentCreated:
        content = await _read_within_limit(file, pipeline.max_upload_bytes)
        try:
            document = pipeline.ingest(
                file.filename or "upload.txt",
                content,
                actor=_actor(request),
                request_id=request.state.request_id,
            )
        except ParseError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "content_type": document.content_type,
            "chars": len(document.text),
        }

    @app.get("/api/v1/documents/{document_id}")
    def get_document(document_id: str) -> DocumentContent:
        try:
            document = pipeline.get_document(document_id)
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return {
            "document_id": document.document_id,
            "filename": document.filename,
            "text": document.text,
            "sha256": document.sha256,
        }

    @app.delete("/api/v1/documents/{document_id}")
    def delete_document(document_id: str, request: Request) -> DeletedResponse:
        """Удалить документ и все отчёты по нему (право на забвение)."""
        try:
            pipeline.delete_document(document_id, actor=_actor(request))
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return {"deleted": document_id}

    @app.get(
        "/api/v1/analyses/{analysis_id}/document",
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
    def download_analysis_document(analysis_id: str, mask: bool = False):  # noqa: ANN202
        """Заключение в .docx — файл, который можно подшить в дело.

        Печать из браузера годится посмотреть, но не переслать и не
        приложить к материалам.

        ``mask=true`` скрывает персональные данные в выгружаемом файле
        (Д-06). Хранимый документ при этом не меняется: обезличивается
        то, что покидает систему.
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
        payload = build_docx(report, document, mask_personal_data=mask)
        return FileLikeResponse(
            content=payload,
            media_type=DOCX_MEDIA_TYPE,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="second-opinion-{analysis_id[:8]}.docx"'
                )
            },
        )

    @app.delete("/api/v1/analyses/{analysis_id}")
    def delete_analysis(analysis_id: str, request: Request) -> DeletedResponse:
        try:
            pipeline.delete_analysis(analysis_id, actor=_actor(request))
        except AnalysisNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return {"deleted": analysis_id}

    @app.post("/api/v1/documents/{document_id}/analyze")
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
                request_id=request.state.request_id,
            )
        except DocumentNotFound as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        return report

    @app.get("/api/v1/analyses")
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

    @app.get("/api/v1/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> AnalysisReportResponse:
        report = pipeline.get_analysis(analysis_id)
        if report is None:
            raise HTTPException(status_code=404, detail="отчёт не найден")
        return report

    @app.post("/api/v1/analyses/{analysis_id}/facts")
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
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        except FactValidationError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return report

    @app.patch("/api/v1/analyses/{analysis_id}/facts/{fact_id}")
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
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc
        return report

    # -- нормы --------------------------------------------------------------

    @app.get("/api/v1/norms")
    def list_norms(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> NormListResponse:
        """Список норм корпуса окном.

        Значение по умолчанию покрывает нынешние 27 норм целиком, поэтому
        существующие клиенты ничего не заметят; окно появляется заранее,
        чтобы рост корпуса не стал ломающим изменением.
        """
        store = pipeline.norm_store
        norms = store.list_norms()
        window = norms[offset : offset + limit]
        return {
            "norms": [
                {"norm_id": n.norm_id, "ref": n.ref, "title": n.title} for n in window
            ],
            "page": {
                "total": len(norms),
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(window) < len(norms),
            },
        }

    @app.get("/api/v1/norms/coverage")
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

    @app.get("/api/v1/norms/{norm_id}")
    def get_norm(
        norm_id: str, applicable_at: str | None = Query(default=None)
    ) -> NormDetailResponse:
        store = pipeline.norm_store
        try:
            meta = store.get_norm_meta(norm_id)
        except NormNotFoundError as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
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
                raise HTTPException(status_code=404, detail=human_message(exc)) from exc
            return {"norm": meta, "version": version}
        return {"norm": meta, "versions": store.versions(norm_id)}

    # -- практика -------------------------------------------------------------

    @app.get("/api/v1/cases")
    def list_cases(
        article: int | None = Query(default=None),
        part: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> CaseListResponse:
        cases = pipeline.retriever.cases
        selected: list[ComparableCase] = []
        for case in cases:
            if article is not None and case.article != article:
                continue
            if part is not None and case.part != part:
                continue
            selected.append(case)
        window = selected[offset : offset + limit]
        return {
            "total": len(selected),
            "cases": window,
            "page": {
                "total": len(selected),
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(window) < len(selected),
            },
        }

    # -- поиск по базе источников ---------------------------------------------

    @app.get("/api/v1/search")
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

    # -- совместимость с путями без версии -------------------------------------
    #
    # Канонический путь теперь `/api/v1/...`: у будущих несовместимых
    # изменений должно быть куда переезжать, иначе их либо не делают, либо
    # ломают работающих клиентов. Прежние `/api/...` продолжают работать —
    # их зовут интерфейс, скрипты и тесты, и одномоментная замена пути ради
    # чистоты обошлась бы дороже, чем она стоит.
    #
    # Дубликаты скрыты из схемы: спецификация описывает один канонический
    # путь, а не два одинаковых.
    for route in list(app.router.routes):
        # APIRoute, а не BaseRoute: у смонтированной статики нет ни метода,
        # ни модели ответа, и копировать там нечего.
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/"):
            continue
        path = route.path
        app.router.add_api_route(
            "/api/" + path[len("/api/v1/") :],
            route.endpoint,
            methods=sorted(route.methods or {"GET"}),
            response_model=route.response_model,
            response_class=route.response_class,
            responses=route.responses,
            # Код ответа копируется наравне с остальным: сегодня все
            # обработчики отвечают 200, но появись завтра 201 — псевдоним
            # обязан отвечать так же, а не откатываться к умолчанию.
            status_code=route.status_code,
            name=f"{route.name}__legacy",
            include_in_schema=False,
        )

    # -- статический UI -------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
