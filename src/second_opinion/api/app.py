"""Сборка приложения.

Две точки входа, разные по уровню:

* :func:`make_app` — процессная. Читает конфигурацию, поднимает носители и
  сервис учётных записей, отдаёт готовое приложение; её зовёт uvicorn.
* :func:`create_app` — связывает маршруты вокруг уже собранного конвейера;
  её зовут тесты, подставляя свой конвейер.

Сами обработчики живут в :mod:`.routers`, разложенные по ресурсам API.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..audit.trail import ANONYMOUS_ACTOR, SHARED_TOKEN_ACTOR
from ..auth.rate_limit import LoginRateLimiter
from ..config import AppConfig, load_config
from ..pipeline import AnalysisPipeline
from .auth_mode import AUTH_MODE_OPEN, AUTH_MODE_SHARED, resolve_auth_mode
from .auth_routes import router as auth_router
from .errors import install_error_handlers
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

#: Канонический префикс API.
API_PREFIX = "/api/v1"

#: Прежний префикс без версии. Остаётся рабочим псевдонимом: у несовместимых
#: изменений должно быть куда переезжать, иначе их либо не делают, либо ломают
#: работающих клиентов.
LEGACY_PREFIX = "/api"


def make_app(config: AppConfig | None = None) -> FastAPI:
    """Приложение по конфигурации окружения — точка входа uvicorn."""
    from ..auth.service import get_auth_service
    from .deps import build_pipeline

    config = config or load_config()
    pipeline = build_pipeline(config)
    # Поднимаем auth-сервис заранее: неверный SO_JWT_SECRET должен ронять
    # запуск, а не первый пришедший логин.
    get_auth_service()
    return create_app(pipeline, auth_token=config.auth_token)


def create_app(pipeline: AnalysisPipeline, auth_token: str = "") -> FastAPI:
    app = FastAPI(
        title="Второе мнение",
        version=__version__,
        description=(
            "Верифицируемая система юридического аудита проекта судебного акта. "
            "Не заменяет судью и не назначает наказание."
        ),
    )
    config = load_config()
    auth_mode = resolve_auth_mode(config.auth_mode, auth_token)

    app.state.pipeline = pipeline
    app.state.auth_mode = auth_mode
    # Лимитер привязан к приложению, а не к модулю: иначе экземпляры делили бы
    # счётчики между собой.
    app.state.login_rate_limiter = LoginRateLimiter(
        config.login_rate_limit, config.login_rate_window_seconds
    )

    _install_identification(app, auth_mode=auth_mode, auth_token=auth_token)
    install_error_handlers(app)

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
        # Тот же набор под прежним путём — и скрыт из схемы: спецификация
        # описывает один канонический путь, а не два одинаковых.
        app.include_router(router, prefix=LEGACY_PREFIX, include_in_schema=False)

    _mount_interface(app)
    return app


def _install_identification(app: FastAPI, *, auth_mode: str, auth_token: str) -> None:
    """Опознание вызывающего и принуждение к авторизации (ADR-008, ФТ-40).

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

        # В режиме accounts общий секрет не принимается вовсе: в судебной
        # системе действие должно быть привязано к человеку, а под общим
        # секретом все записи в журнале обезличены.
        if (
            auth_mode == AUTH_MODE_SHARED
            and auth_token
            and header == f"Bearer {auth_token}"
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
            return JSONResponse(
                status_code=401,
                content={"detail": "требуется авторизация"},
                headers={REQUEST_ID_HEADER: request.state.request_id},
            )
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response


def _mount_interface(app: FastAPI) -> None:
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
