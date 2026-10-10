"""Ограничения на запуск анализа: частота и число одновременных запусков.

Анализ с моделью занимает видеокарту на десятки секунд; клиент без лимита
способен занять её целиком, как и перегрузить обычный разбор.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.config import load_config


def _document(client: TestClient, text: str) -> str:
    created = client.post("/api/v1/documents", json={"filename": "c.txt", "text": text})
    assert created.status_code == 200, created.text
    return created.json()["document_id"]


@pytest.fixture()
def limited(monkeypatch):
    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        load_config.cache_clear()

    yield _set
    load_config.cache_clear()


def test_analyze_rate_limit_answers_429_with_retry_after(pipeline, sample_clean_text, limited) -> None:
    limited(SO_ANALYZE_RATE_LIMIT="2", SO_ANALYZE_RATE_WINDOW_SECONDS="60")
    client = TestClient(create_app(pipeline))
    document_id = _document(client, sample_clean_text)

    codes = [client.post(f"/api/v1/documents/{document_id}/analyze", json={}).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    refused = client.post(f"/api/v1/documents/{document_id}/analyze", json={})
    assert int(refused.headers["Retry-After"]) >= 1


def test_busy_server_answers_503_instead_of_queueing_forever(pipeline, sample_clean_text, limited) -> None:
    limited(SO_ANALYZE_MAX_CONCURRENCY="1", SO_ANALYZE_QUEUE_SECONDS="0.2")
    app = create_app(pipeline)
    client = TestClient(app)
    document_id = _document(client, sample_clean_text)

    assert app.state.analyze_slots.acquire(timeout=1)
    try:
        busy = client.post(f"/api/v1/documents/{document_id}/analyze", json={})
    finally:
        app.state.analyze_slots.release()

    assert busy.status_code == 503
    assert "Retry-After" in busy.headers
    assert client.post(f"/api/v1/documents/{document_id}/analyze", json={}).status_code == 200


def test_slot_is_released_after_a_failed_analysis(pipeline, limited) -> None:
    limited(SO_ANALYZE_MAX_CONCURRENCY="1", SO_ANALYZE_QUEUE_SECONDS="0.2")
    app = create_app(pipeline)
    client = TestClient(app)
    missing = "0" * 32

    assert client.post(f"/api/v1/documents/{missing}/analyze", json={}).status_code == 404
    assert app.state.analyze_slots.acquire(timeout=0.1)
    app.state.analyze_slots.release()


def test_limits_can_be_turned_off(pipeline, sample_clean_text, limited) -> None:
    limited(SO_ANALYZE_RATE_LIMIT="0", SO_ANALYZE_MAX_CONCURRENCY="0")
    app = create_app(pipeline)
    client = TestClient(app)
    document_id = _document(client, sample_clean_text)

    assert app.state.analyze_slots is None
    codes = {client.post(f"/api/v1/documents/{document_id}/analyze", json={}).status_code for _ in range(5)}
    assert codes == {200}
