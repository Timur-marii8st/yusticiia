"""Единый формат ошибок и окно выдачи в списках (техдолг).

Ошибка приходила то строкой в `detail`, то списком объектов там же —
форму приходилось угадывать по коду ответа. А у списков не было окна:
клиент, привыкший к полной выдаче, ломается ровно тогда, когда корпус
наконец вырастет.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.api.errors import human_message
from second_opinion.api.request_context import REQUEST_ID_HEADER


def test_key_error_message_has_no_repr_quotes() -> None:
    """`str()` у KeyError возвращает repr — наружу уходили кавычки."""
    assert human_message(KeyError("норма не найдена: uk-rf:art-1")) == (
        "норма не найдена: uk-rf:art-1"
    )
    assert human_message(ValueError("обычное сообщение")) == "обычное сообщение"


def test_not_found_has_the_unified_shape(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.get("/api/v1/norms/нет-такой")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "'" not in body["error"]["message"], "сообщение снова в кавычках repr"
    assert body["error"]["request_id"]
    assert body["detail"] == body["error"]["message"]


def test_validation_error_has_the_same_shape(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.post("/api/v1/documents", json={"filename": "a.txt"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["detail"], str)
    assert body["error"]["fields"][0]["location"] == "body.text"


def test_error_response_carries_the_request_id(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.get("/api/v1/norms/нет", headers={REQUEST_ID_HEADER: "trace-err"})
    assert response.json()["error"]["request_id"] == "trace-err"
    assert response.headers[REQUEST_ID_HEADER] == "trace-err"


def test_norms_list_is_paginated(pipeline) -> None:
    client = TestClient(create_app(pipeline))

    full = client.get("/api/v1/norms?limit=500").json()
    assert full["page"]["total"] == len(full["norms"])
    assert full["page"]["has_more"] is False
    assert len(client.get("/api/v1/norms").json()["norms"]) == min(100, full["page"]["total"])

    window = client.get("/api/v1/norms?limit=5&offset=0").json()
    assert len(window["norms"]) == 5
    assert window["page"]["total"] == full["page"]["total"]
    assert window["page"]["has_more"] is True

    tail = client.get(f"/api/v1/norms?limit=5&offset={full['page']['total'] - 2}").json()
    assert len(tail["norms"]) == 2
    assert tail["page"]["has_more"] is False


def test_cases_list_is_paginated(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    first = client.get("/api/v1/cases?limit=3&offset=0").json()
    second = client.get("/api/v1/cases?limit=3&offset=3").json()

    assert len(first["cases"]) == 3
    assert first["page"]["has_more"] is True
    assert {c["case_id"] for c in first["cases"]} & {
        c["case_id"] for c in second["cases"]
    } == set()


def test_bad_window_is_refused(pipeline) -> None:
    """Отрицательное смещение — ошибка запроса, а не тихая нормализация."""
    client = TestClient(create_app(pipeline))
    assert client.get("/api/v1/norms?offset=-1").status_code == 422
    assert client.get("/api/v1/norms?limit=0").status_code == 422


def test_error_keeps_protocol_headers(pipeline, monkeypatch) -> None:
    """Единый формат тела не повод терять заголовки протокола.

    В 429 едет `Retry-After`, в 401 — `WWW-Authenticate`. Первая версия
    обработчика собирала новый ответ и теряла их; поймал существующий тест
    ограничителя входов.
    """
    from second_opinion.config import load_config

    monkeypatch.setenv("SO_LOGIN_RATE_LIMIT", "2")
    monkeypatch.setenv("SO_LOGIN_RATE_WINDOW_SECONDS", "60")
    load_config.cache_clear()
    try:
        client = TestClient(create_app(pipeline, auth_token="secret"))
        body = {"email": "admin@example.com", "password": "неверный"}
        for _ in range(2):
            client.post("/auth/login", json=body)

        blocked = client.post("/auth/login", json=body)
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        assert blocked.json()["error"]["code"] == "too_many_requests"
    finally:
        load_config.cache_clear()


def test_canonical_path_is_versioned(pipeline) -> None:
    """Канонический путь — `/api/v1`; в спецификации только он."""
    app = create_app(pipeline)
    schema = app.openapi()
    api_paths = [p for p in schema["paths"] if p.startswith("/api")]

    assert api_paths, "в схеме нет ни одного пути API"
    assert all(p.startswith("/api/v1/") for p in api_paths), (
        f"в схеме остались пути без версии: {[p for p in api_paths if not p.startswith('/api/v1/')]}"
    )


def test_paths_without_version_keep_working(pipeline) -> None:
    """Прежние клиенты не ломаются: путь без версии остаётся псевдонимом.

    Интерфейс переведён на `/api/v1`, но скрипты и внешние вызовы могли
    остаться на старом пути — одномоментная замена обошлась бы дороже,
    чем стоит чистота.
    """
    client = TestClient(create_app(pipeline))

    versioned = client.get("/api/v1/norms?limit=2")
    legacy = client.get("/api/norms?limit=2")

    assert versioned.status_code == legacy.status_code == 200
    assert versioned.json() == legacy.json()

    body = {"filename": "legacy.txt", "text": "Приговор."}
    by_alias = client.post("/api/documents", json=body)
    canonical = client.post("/api/v1/documents", json=body)

    assert by_alias.status_code == canonical.status_code == 200, by_alias.text


def test_interface_calls_the_versioned_path(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text

    assert "/api/v1/" in js
    assert '"/api/analyses' not in js and "`/api/analyses" not in js


def test_process_entry_point_builds_an_app() -> None:
    """`second-opinion serve` поднимает приложение через `make_app`.

    Регрессия: модуль сборки был записан поверх существующего `api/app.py`,
    и `make_app` — та самая точка входа, которую зовёт uvicorn, — исчезла.
    Прогон остался зелёным: тесты собирают приложение через
    `create_app(pipeline)` и до `make_app` не доходят.
    """
    import importlib

    from second_opinion.api.app import make_app

    serve_source = importlib.import_module("second_opinion.cli.serve")
    target = "second_opinion.api.app:make_app"
    assert target in serve_source.serve.__code__.co_consts, (
        f"CLI зовёт не {target}: сверьте строку в cli/serve.py"
    )

    client = TestClient(make_app())
    assert client.get("/health").status_code == 200

    assert client.get("/api/v1/norms?limit=1").status_code != 404
