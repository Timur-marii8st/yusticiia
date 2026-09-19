"""Совместимость: прежний адрес сборки приложения.

Приложение собирается в :mod:`second_opinion.api.app`, обработчики живут в
:mod:`second_opinion.api.routers`. Модуль оставлен, потому что на
``second_opinion.api.routes`` ссылаются точка входа CLI и тесты.
"""

from __future__ import annotations

from .app import STATIC_DIR, create_app
from .auth_mode import (
    AUTH_MODE_ACCOUNTS,
    AUTH_MODE_OPEN,
    AUTH_MODE_SHARED,
    AUTH_MODES,
    resolve_auth_mode,
)
from .errors import human_message
from .request_context import REQUEST_ID_HEADER, actor, request_id
from .routers.analyses import DOCX_MEDIA_TYPE
from .uploads import UPLOAD_CHUNK_BYTES, read_within_limit

__all__ = [
    "AUTH_MODES",
    "AUTH_MODE_ACCOUNTS",
    "AUTH_MODE_OPEN",
    "AUTH_MODE_SHARED",
    "DOCX_MEDIA_TYPE",
    "REQUEST_ID_HEADER",
    "STATIC_DIR",
    "UPLOAD_CHUNK_BYTES",
    "actor",
    "create_app",
    "human_message",
    "read_within_limit",
    "request_id",
    "resolve_auth_mode",
]
