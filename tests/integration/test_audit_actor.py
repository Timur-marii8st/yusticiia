"""Аудит должен записывать, кто выполнил действие.

Для судебной системы «кто подтвердил обстоятельство» — часть провенанса
наравне с «что было подтверждено». До этого в записи аудита стояли только
операция, компонент и хэши: восстановить исполнителя было невозможно.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app
from second_opinion.audit.trail import ANONYMOUS_ACTOR, SHARED_TOKEN_ACTOR

SHARED = "test-secret"


@pytest.fixture()
def client(pipeline) -> TestClient:
    return TestClient(create_app(pipeline, auth_token=SHARED))


DEMO = (
    Path(__file__).resolve().parents[2]
    / "src/second_opinion/static/demo_document.txt"
).read_text(encoding="utf-8")


def _create_document(client: TestClient, headers: dict | None = None):
    return client.post(
        "/api/v1/documents",
        json={"filename": "d.txt", "text": DEMO},
        headers=headers or {},
    )


def _last_actor(pipeline, operation: str) -> str:
    events = [e for e in pipeline._audit.recent if e.operation == operation]
    assert events, f"нет записи аудита об операции {operation}"
    return events[-1].actor


def test_personal_account_is_recorded(client, pipeline) -> None:
    token = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    ).json()["tokens"]["access_token"]

    assert _create_document(client, {"Authorization": f"Bearer {token}"}).status_code == 200
    assert _last_actor(pipeline, "document_ingest") == "admin-001"


def test_shared_token_is_distinguishable(client, pipeline) -> None:
    """Общий секрет не называет человека — это должно быть видно в журнале."""
    assert _create_document(client, {"Authorization": f"Bearer {SHARED}"}).status_code == 200
    assert _last_actor(pipeline, "document_ingest") == SHARED_TOKEN_ACTOR


def test_open_mode_records_anonymous(pipeline) -> None:
    open_client = TestClient(create_app(pipeline, auth_token=""))
    assert _create_document(open_client).status_code == 200
    assert _last_actor(pipeline, "document_ingest") == ANONYMOUS_ACTOR


def test_open_mode_still_recognises_personal_account(pipeline) -> None:
    """Без общего секрета вход по учётной записи всё равно опознаётся."""
    open_client = TestClient(create_app(pipeline, auth_token=""))
    token = open_client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    ).json()["tokens"]["access_token"]

    _create_document(open_client, {"Authorization": f"Bearer {token}"})
    assert _last_actor(pipeline, "document_ingest") == "admin-001"


def test_fact_correction_records_actor(client, pipeline) -> None:
    """Правка обстоятельства судьёй — та запись, где исполнитель важнее всего."""
    headers = {"Authorization": f"Bearer {SHARED}"}
    document_id = _create_document(client, headers).json()["document_id"]
    analysis = client.post(
        f"/api/v1/documents/{document_id}/analyze", json={}, headers=headers
    ).json()
    fact_id = analysis["facts"][0]["id"]

    response = client.patch(
        f"/api/v1/analyses/{analysis['analysis_id']}/facts/{fact_id}",
        json={"status": "NOT_FOUND"},
        headers=headers,
    )
    assert response.status_code == 200
    assert _last_actor(pipeline, "fact_update") == SHARED_TOKEN_ACTOR
