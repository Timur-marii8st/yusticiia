"""Сборка приложения.

Две точки входа, разные по уровню:

* :func:`make_app` — процессная. Читает конфигурацию, поднимает носители и
  сервис учётных записей, отдаёт готовое приложение; её зовёт uvicorn.
* :func:`create_app` — связывает маршруты вокруг уже собранного конвейера;
  её зовут тесты, подставляя свой конвейер.

Сами обработчики живут в :mod:`.routers`, разложенные по ресурсам API.
"""

from __future__ import annotations

import hmac
import threading
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..audit.trail import ANONYMOUS_ACTOR, SHARED_TOKEN_ACTOR
from ..auth.rate_limit import LoginRateLimiter
from ..config import AppConfig, load_config
from ..metrics import get_metrics, http_status_metric
from ..pipeline import AnalysisPipeline
from .auth_mode import AUTH_MODE_OPEN, AUTH_MODE_SHARED, resolve_auth_mode
from .auth_routes import router as auth_router
from .errors import install_error_handlers
from .limits import BodyLimitMiddleware
from .request_context import REQUEST_ID_HEADER, request_id
from .routers import (
    build_analyses_router,
    build_cases_router,
    build_documents_router,
    build_norms_router,
    build_search_router,
    build_service_router,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

API_PREFIX = "/api/v1"

BODY_LIMIT_FACTOR = 4

DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)

LEGACY_PREFIX = "/api"


def make_app(config: AppConfig | None = None) -> FastAPI:
    """Приложение по конфигурации окружения — точка входа uvicorn."""
    from ..auth.service import get_auth_service
    from .deps import build_pipeline

    config = config or load_config()
    pipeline = build_pipeline(config)
    get_auth_service()
    return create_app(pipeline, auth_token=config.auth_token)


def _docs_enabled(setting: str, auth_mode: str) -> bool:
    """Документация API без авторизации только в открытом режиме или по явной просьбе."""
    if setting in ("1", "true", "yes"):
        return True
    if setting in ("0", "false", "no"):
        return False
    return auth_mode == AUTH_MODE_OPEN


def create_app(pipeline: AnalysisPipeline, auth_token: str = "") -> FastAPI:
    config = load_config()
    auth_mode = resolve_auth_mode(config.auth_mode, auth_token)
    docs = _docs_enabled(config.api_docs, auth_mode)
    app = FastAPI(
        title="Второе мнение",
        version=__version__,
        description=(
            "Верифицируемая система юридического аудита проекта судебного акта. "
            "Не заменяет судью и не назначает наказание."
        ),
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )

    app.state.pipeline = pipeline
    app.state.auth_mode = auth_mode
    app.state.login_rate_limiter = LoginRateLimiter(
        config.login_rate_limit, config.login_rate_window_seconds
    )
    app.state.analyze_rate_limiter = LoginRateLimiter(
        config.analyze_rate_limit, config.analyze_rate_window_seconds
    )
    app.state.analyze_slots = (
        threading.BoundedSemaphore(config.analyze_max_concurrency)
        if config.analyze_max_concurrency > 0
        else None
    )
    app.state.analyze_queue_seconds = config.analyze_queue_seconds

    _install_identification(app, auth_mode=auth_mode, auth_token=auth_token)
    install_error_handlers(app)
    app.add_middleware(BodyLimitMiddleware, max_bytes=config.max_upload_bytes * BODY_LIMIT_FACTOR)

    from .deps import build_legal_rag

    rag = build_legal_rag(pipeline.norm_store)

    app.include_router(auth_router)
    app.include_router(build_service_router(auth_mode))

    api_routers = [
        build_documents_router(pipeline),
        build_analyses_router(pipeline),
        build_norms_router(pipeline),
        build_cases_router(pipeline),
        build_search_router(rag),
    ]
    for router in api_routers:
        app.include_router(router, prefix=API_PREFIX)
        app.include_router(router, prefix=LEGACY_PREFIX, include_in_schema=False)

    _mount_interface(app)
    _strip_docstrings_from_openapi(app)
    return app


def _strip_docstrings_from_openapi(app: FastAPI) -> None:
    """``/docs``/``/openapi.json`` не должны отдавать текст докстрингов.

    FastAPI по умолчанию копирует docstring обработчика и Pydantic-модели
    в поле ``description`` схемы — оно и есть содержимое ``/docs``,
    доступное неаутентифицированным GET-запросом (``_install_identification``
    накладывает проверку только на пути, начинающиеся с ``/api``). Схема
    остаётся: ``/openapi.json`` по-прежнему годится для генерации клиента
    (пути, параметры, модели, ``summary``) — убирается только свободный
    текст, а не сам эндпойнт.
    """
    from fastapi.openapi.utils import get_openapi

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        _strip_descriptions(schema)
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _strip_descriptions(node: object) -> None:
    if isinstance(node, dict):
        node.pop("description", None)
        for value in node.values():
            _strip_descriptions(value)
    elif isinstance(node, list):
        for item in node:
            _strip_descriptions(item)


def _count_status(status_code: int) -> None:
    name = http_status_metric(status_code)
    metrics = get_metrics()
    if name is not None and metrics.enabled:
        metrics.counter(name).inc()


def _add_security_headers(request: Request, response) -> None:  # noqa: ANN001
    """Заголовки безопасности; HSTS и TLS остаются на обратном прокси."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    path = request.url.path
    if path.startswith(("/api", "/auth")):
        response.headers["Cache-Control"] = "no-store"
    if not path.startswith(DOCS_PATHS):
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY


def _install_identification(app: FastAPI, *, auth_mode: str, auth_token: str) -> None:
    """Опознание вызывающего и принуждение к авторизации (ФТ-40).

    Опознание идёт всегда, даже в открытом режиме: аудит должен записывать,
    кто выполнил действие, а вход по личной учётной записи возможен и там, где
    доступ не ограничен.

    Принуждение зависит от режима: ``open`` — ``/api/*`` открыт; ``shared`` —
    общий секрет или личный токен; ``accounts`` — только личный токен.
    ``/auth/login`` и ``/auth/refresh`` открыты всегда, иначе для получения
    токена требовался бы токен.
    """

    @app.middleware("http")
    async def _identify_and_authorize(request: Request, call_next):  # noqa: ANN001, ANN202
        request.state.actor = ANONYMOUS_ACTOR
        request.state.request_id = request_id(request)
        header = request.headers.get("authorization", "")
        authorized = auth_mode == AUTH_MODE_OPEN

        if (
            auth_mode == AUTH_MODE_SHARED
            and auth_token
            and hmac.compare_digest(header.encode(), f"Bearer {auth_token}".encode())
        ):
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
            response = JSONResponse(
                status_code=401,
                content={"detail": "требуется авторизация"},
            )
        else:
            response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        _add_security_headers(request, response)
        _count_status(response.status_code)
        return response


def _mount_interface(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
