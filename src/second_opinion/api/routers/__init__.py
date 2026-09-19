"""Обработчики HTTP, разложенные по ресурсам API.

Каждый модуль отдаёт готовый ``APIRouter``: маршруты собираются в
:func:`second_opinion.api.app.create_app`. Деление по ресурсу, а не по
техническому слою, — путь в адресной строке и имя файла совпадают.
"""

from __future__ import annotations

from .analyses import build_router as build_analyses_router
from .cases import build_router as build_cases_router
from .documents import build_router as build_documents_router
from .norms import build_router as build_norms_router
from .search import build_router as build_search_router
from .service import build_router as build_service_router

__all__ = [
    "build_analyses_router",
    "build_cases_router",
    "build_documents_router",
    "build_norms_router",
    "build_search_router",
    "build_service_router",
]
