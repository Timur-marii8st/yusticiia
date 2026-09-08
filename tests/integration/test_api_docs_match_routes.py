"""docs/API.md должен описывать те пути, которые сервер действительно отдаёт.

Регрессия: справочник описывал auth-эндпоинты как `/api/auth/*`, тогда
как роутер смонтирован по `/auth/*`. Расхождение жило долго, потому что
проверялось только глазами: клиент по документации получал 404, а из
описания «два слоя авторизации» следовало, что общий Bearer покрывает и
управление пользователями, — чего в коде нет.
"""

from __future__ import annotations

import re
from pathlib import Path

from second_opinion.api.routes import create_app

API_DOC = Path(__file__).resolve().parents[2] / "docs" / "API.md"

#: `GET /api/documents/{id}` в тексте против `{document_id}` в коде —
#: имена параметров пути документации знать не обязательно.
_PARAM = re.compile(r"\{[^}]+\}")
_ENDPOINT = re.compile(r"`(GET|POST|PATCH|DELETE|PUT) (/[^`\s?]*)")


def _normalize(path: str) -> str:
    return _PARAM.sub("{}", path.rstrip("/")) or "/"


def _documented_endpoints() -> set[tuple[str, str]]:
    text = API_DOC.read_text(encoding="utf-8")
    return {(m.group(1), _normalize(m.group(2))) for m in _ENDPOINT.finditer(text)}


def _served_endpoints(pipeline) -> set[tuple[str, str]]:
    schema = create_app(pipeline, auth_token="test-secret").openapi()
    return {
        (method.upper(), _normalize(path))
        for path, operations in schema["paths"].items()
        for method in operations
    }


def test_documented_endpoints_exist(pipeline) -> None:
    missing = sorted(_documented_endpoints() - _served_endpoints(pipeline))
    assert not missing, f"описаны в docs/API.md, но сервер их не отдаёт: {missing}"


def test_served_endpoints_are_documented(pipeline) -> None:
    """Необъявленных эндпоинтов быть не должно: API — часть контракта."""
    undocumented = sorted(_served_endpoints(pipeline) - _documented_endpoints())
    assert not undocumented, f"сервер отдаёт, но docs/API.md молчит: {undocumented}"
