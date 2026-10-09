"""Владелец объекта: судья A не видит и не меняет дела судьи B.

Идентификаторы случайные, но это не защита: ссылка попадает в письма и логи.
Чужой объект обязан отвечать так же, как несуществующий.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.config import load_config


@pytest.fixture()
def accounts_client(pipeline, monkeypatch) -> TestClient:
    monkeypatch.setenv("SO_AUTH_MODE", "accounts")
    load_config.cache_clear()
    yield TestClient(create_app(pipeline))
    load_config.cache_clear()


def _login(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


def _judge(client: TestClient, admin: dict[str, str], email: str) -> dict[str, str]:
    from second_opinion.auth.service import get_auth_service

    get_auth_service().create_user(email, "judgepass1", "Судья")
    return _login(client, email, "judgepass1")


@pytest.fixture()
def world(accounts_client, sample_clean_text):
    client = accounts_client
    admin = _login(client, "admin@example.com", "admin123")
    judge_a = _judge(client, admin, "a@example.com")
    judge_b = _judge(client, admin, "b@example.com")
    created = client.post(
        "/api/v1/documents",
        json={"filename": "case.txt", "text": sample_clean_text},
        headers=judge_a,
    )
    document_id = created.json()["document_id"]
    report = client.post(f"/api/v1/documents/{document_id}/analyze", json={}, headers=judge_a)
    assert report.status_code == 200, report.text
    body = report.json()
    return {
        "client": client,
        "admin": admin,
        "a": judge_a,
        "b": judge_b,
        "document_id": document_id,
        "analysis_id": body["analysis_id"],
        "fact_id": body["facts"][0]["id"],
    }


def _requests(world: dict, headers: dict[str, str]) -> list[tuple[str, int]]:
    client, doc, ana, fact = world["client"], world["document_id"], world["analysis_id"], world["fact_id"]
    return [
        ("get document", client.get(f"/api/v1/documents/{doc}", headers=headers).status_code),
        ("analyze", client.post(f"/api/v1/documents/{doc}/analyze", json={}, headers=headers).status_code),
        ("get analysis", client.get(f"/api/v1/analyses/{ana}", headers=headers).status_code),
        ("download", client.get(f"/api/v1/analyses/{ana}/document", headers=headers).status_code),
        (
            "add fact",
            client.post(
                f"/api/v1/analyses/{ana}/facts",
                json={"type": "health_factor", "value": "x", "quote": "ранее не судим"},
                headers=headers,
            ).status_code,
        ),
        (
            "update fact",
            client.patch(
                f"/api/v1/analyses/{ana}/facts/{fact}",
                json={"status": "UNCERTAIN"},
                headers=headers,
            ).status_code,
        ),
    ]


def test_other_judge_gets_not_found_on_every_route(world) -> None:
    results = _requests(world, world["b"])
    assert all(code == 404 for _, code in results), results


def test_other_judge_cannot_delete(world) -> None:
    client = world["client"]
    assert client.delete(f"/api/v1/analyses/{world['analysis_id']}", headers=world["b"]).status_code == 404
    assert client.delete(f"/api/v1/documents/{world['document_id']}", headers=world["b"]).status_code == 404
    assert client.get(f"/api/v1/analyses/{world['analysis_id']}", headers=world["a"]).status_code == 200


def test_other_judge_response_does_not_reveal_that_the_object_exists(world) -> None:
    client = world["client"]
    foreign = client.get(f"/api/v1/analyses/{world['analysis_id']}", headers=world["b"])
    missing = client.get("/api/v1/analyses/00000000000000000000000000000000", headers=world["b"])
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() | {"error": foreign.json()["error"]}
    assert foreign.json()["detail"] == missing.json()["detail"]


def test_owner_keeps_access_to_everything(world) -> None:
    results = _requests(world, world["a"])
    assert all(code == 200 for _, code in results), results


def test_admin_has_access_to_every_case(world) -> None:
    client = world["client"]
    assert client.get(f"/api/v1/analyses/{world['analysis_id']}", headers=world["admin"]).status_code == 200
    assert client.get(f"/api/v1/documents/{world['document_id']}", headers=world["admin"]).status_code == 200


def test_legacy_record_without_owner_is_for_admin_only_in_accounts_mode(
    accounts_client, pipeline, sample_clean_text
) -> None:
    legacy = pipeline.ingest("old.txt", sample_clean_text.encode("utf-8"), actor="")
    admin = _login(accounts_client, "admin@example.com", "admin123")
    judge = _judge(accounts_client, admin, "c@example.com")
    path = f"/api/v1/documents/{legacy.document_id}"
    assert accounts_client.get(path, headers=judge).status_code == 404
    assert accounts_client.get(path, headers=admin).status_code == 200


def test_legacy_record_without_owner_stays_reachable_in_open_mode(pipeline, sample_clean_text) -> None:
    legacy = pipeline.ingest("old.txt", sample_clean_text.encode("utf-8"), actor="")
    client = TestClient(create_app(pipeline))
    assert client.get(f"/api/v1/documents/{legacy.document_id}").status_code == 200
