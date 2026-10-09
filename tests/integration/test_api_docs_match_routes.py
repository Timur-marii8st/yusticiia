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

from second_opinion.api.app import create_app

API_DOC = Path(__file__).resolve().parents[2] / "docs" / "API.md"

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


_ANY_PATH = re.compile(r"/api/[^\s`\"'()\[\]{},;]*(?:\{[^}]+\}[^\s`\"'()\[\]{},;]*)*")


def _served_paths(pipeline) -> set[str]:
    return {path for _method, path in _served_endpoints(pipeline)}


def test_paths_in_other_docs_are_real_routes(pipeline) -> None:
    """Справочные документы не должны звать читателя по несуществующему пути.

    Регрессия: после перевода API на `/api/v1` в README, ARCHITECTURE,
    DEPLOYMENT, LEGAL_RAG и PRIVACY остались прежние пути. Они отвечали —
    через псевдоним, — но читатель списывал оттуда путь, которого в
    спецификации уже нет.

    Сверяется путь, а не пара «метод + путь»: в тексте метод стоит рядом
    не всегда, зато сам путь встречается и в команде `curl`, и в скобках
    посреди фразы — там прежние пути и остались.
    """
    root = API_DOC.parents[1]
    served = _served_paths(pipeline)

    stale: list[str] = []
    for doc in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        text = doc.read_text(encoding="utf-8")
        for match in _ANY_PATH.finditer(text):
            path = match.group(0).split("?", 1)[0].rstrip(".,:;")
            if path.endswith("*") or path.rstrip("/") in {"/api", "/api/v1"}:
                continue
            if _normalize(path) not in served:
                stale.append(f"{doc.relative_to(root)}: {path}")

    assert not stale, "в документации пути, которых сервер не отдаёт: " + "; ".join(
        sorted(set(stale))
    )
