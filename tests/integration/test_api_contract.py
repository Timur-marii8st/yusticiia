"""Единый формат ошибок и окно выдачи в списках (техдолг).

Ошибка приходила то строкой в `detail`, то списком объектов там же —
форму приходилось угадывать по коду ответа. А у списков не было окна:
клиент, привыкший к полной выдаче, ломается ровно тогда, когда корпус
наконец вырастет.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.routes import REQUEST_ID_HEADER, create_app, human_message


def test_key_error_message_has_no_repr_quotes() -> None:
    """`str()` у KeyError возвращает repr — наружу уходили кавычки."""
    assert human_message(KeyError("норма не найдена: uk-rf:art-1")) == (
        "норма не найдена: uk-rf:art-1"
    )
    assert human_message(ValueError("обычное сообщение")) == "обычное сообщение"


def test_not_found_has_the_unified_shape(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.get("/api/norms/нет-такой")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "'" not in body["error"]["message"], "сообщение снова в кавычках repr"
    assert body["error"]["request_id"]
    # `detail` сохранён: его читает текущий интерфейс.
    assert body["detail"] == body["error"]["message"]


def test_validation_error_has_the_same_shape(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.post("/api/documents", json={"filename": "a.txt"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    # Раньше здесь лежал список объектов, а не строка: клиент не мог
    # показать сообщение, не разобрав структуру.
    assert isinstance(body["detail"], str)
    assert body["error"]["fields"][0]["location"] == "body.text"


def test_error_response_carries_the_request_id(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.get("/api/norms/нет", headers={REQUEST_ID_HEADER: "trace-err"})
    assert response.json()["error"]["request_id"] == "trace-err"
    assert response.headers[REQUEST_ID_HEADER] == "trace-err"


def test_norms_list_is_paginated(pipeline) -> None:
    client = TestClient(create_app(pipeline))

    full = client.get("/api/norms").json()
    assert full["page"]["total"] == len(full["norms"])
    assert full["page"]["has_more"] is False

    window = client.get("/api/norms?limit=5&offset=0").json()
    assert len(window["norms"]) == 5
    assert window["page"]["total"] == full["page"]["total"]
    assert window["page"]["has_more"] is True

    tail = client.get(f"/api/norms?limit=5&offset={full['page']['total'] - 2}").json()
    assert len(tail["norms"]) == 2
    assert tail["page"]["has_more"] is False


def test_cases_list_is_paginated(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    first = client.get("/api/cases?limit=3&offset=0").json()
    second = client.get("/api/cases?limit=3&offset=3").json()

    assert len(first["cases"]) == 3
    assert first["page"]["has_more"] is True
    # Окна не пересекаются: смещение действительно смещает.
    assert {c["case_id"] for c in first["cases"]} & {
        c["case_id"] for c in second["cases"]
    } == set()


def test_bad_window_is_refused(pipeline) -> None:
    """Отрицательное смещение — ошибка запроса, а не тихая нормализация."""
    client = TestClient(create_app(pipeline))
    assert client.get("/api/norms?offset=-1").status_code == 422
    assert client.get("/api/norms?limit=0").status_code == 422


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
