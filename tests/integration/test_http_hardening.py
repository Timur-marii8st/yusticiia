"""Заголовки безопасности, лимит тела, документация API, непредвиденные ошибки."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.config import load_config


@pytest.fixture()
def env(monkeypatch):
    def _set(**values: str) -> None:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        load_config.cache_clear()

    yield _set
    load_config.cache_clear()


def test_security_headers_on_interface_and_api(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    for path in ("/", "/api/v1/analyses"):
        headers = client.get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert client.get("/api/v1/analyses").headers["Cache-Control"] == "no-store"
    assert "Cache-Control" not in client.get("/").headers


def test_interface_needs_no_inline_scripts_or_styles(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    js = client.get("/static/app.js").text
    assert "<script>" not in html and 'style="' not in html
    assert 'style="' not in js
    assert "script-src 'self'" in client.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in client.get("/").headers["Content-Security-Policy"]


def test_docs_pages_keep_their_own_scripts_without_csp(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    response = client.get("/docs")
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


def test_body_larger_than_limit_is_refused_before_parsing(pipeline, env) -> None:
    env(SO_MAX_UPLOAD_BYTES="1000")
    client = TestClient(create_app(pipeline))
    big = {"filename": "c.txt", "text": "а" * 5000}
    refused = client.post("/api/v1/documents", json=big)
    assert refused.status_code == 413
    ok = client.post("/api/v1/documents", json={"filename": "c.txt", "text": "Приговор. ч. 1 ст. 158 УК РФ"})
    assert ok.status_code == 200


def test_chunked_body_without_content_length_is_limited_too(pipeline, env) -> None:
    env(SO_MAX_UPLOAD_BYTES="1000")
    client = TestClient(create_app(pipeline))

    def chunks():
        for _ in range(10):
            yield b'{"filename": "c.txt", "text": "' + b"a" * 1000 + b'"}'

    refused = client.post(
        "/api/v1/documents", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert refused.status_code == 413


def test_docs_are_hidden_with_personal_accounts_unless_enabled(pipeline, env) -> None:
    env(SO_AUTH_MODE="accounts")
    client = TestClient(create_app(pipeline))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path

    env(SO_AUTH_MODE="accounts", SO_API_DOCS="1")
    assert TestClient(create_app(pipeline)).get("/openapi.json").status_code == 200


def test_docs_can_be_switched_off_in_open_mode(pipeline, env) -> None:
    env(SO_API_DOCS="0")
    assert TestClient(create_app(pipeline)).get("/openapi.json").status_code == 404


def test_unexpected_error_hides_internals_and_names_the_request(pipeline, caplog) -> None:
    app = create_app(pipeline)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("секрет внутри: password=hunter2")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        response = client.get("/boom", headers={"X-Request-Id": "req-123"})

    assert response.status_code == 500
    body = response.text
    assert "hunter2" not in body and "секрет внутри" not in body and "Traceback" not in body
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"] == "req-123"
    assert response.headers["X-Request-Id"] == "req-123"
    assert any("req-123" in record.getMessage() for record in caplog.records)
    assert any(record.exc_info for record in caplog.records)


def test_suspicious_status_codes_are_counted(pipeline, env) -> None:
    from second_opinion.metrics import get_metrics, reset_metrics_for_tests

    env(SO_METRICS_ENABLED="1", SO_AUTH_MODE="accounts")
    reset_metrics_for_tests()
    app = create_app(pipeline)
    client = TestClient(app, raise_server_exceptions=False)

    client.get("/api/v1/analyses")
    client.get("/api/v1/analyses")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("x")

    client.get("/boom")

    snapshot = get_metrics().snapshot()["counters"]
    assert snapshot["second_opinion_http_401_total"] == 2
    assert snapshot["second_opinion_http_5xx_total"] == 1
    reset_metrics_for_tests()


def test_unauthorized_response_carries_security_headers_too(pipeline, env) -> None:
    env(SO_AUTH_MODE="accounts")
    response = TestClient(create_app(pipeline)).get("/api/v1/analyses")
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Request-Id"]
