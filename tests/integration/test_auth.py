from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


def _open_client(pipeline):
    return TestClient(create_app(pipeline))


def _secured_client(pipeline):
    return TestClient(create_app(pipeline, auth_token="secret-token"))


def test_open_mode_by_default(pipeline) -> None:
    client = _open_client(pipeline)
    assert client.get("/api/norms").status_code == 200


def test_health_and_static_stay_public_when_secured(pipeline) -> None:
    client = _secured_client(pipeline)
    assert client.get("/health").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "secret-token"},  # без схемы Bearer
    ],
    ids=["no-header", "empty", "wrong-token", "no-bearer-scheme"],
)
def test_api_requires_valid_bearer(pipeline, headers: dict) -> None:
    client = _secured_client(pipeline)
    response = client.get("/api/norms", headers=headers)
    assert response.status_code == 401
    assert "авторизация" in response.json()["detail"]


def test_api_accepts_correct_token(pipeline) -> None:
    client = _secured_client(pipeline)
    headers = {"Authorization": "Bearer secret-token"}
    assert client.get("/api/norms", headers=headers).status_code == 200
    assert client.post(
        "/api/documents",
        json={"filename": "a.txt", "text": "Текст документа для проверки."},
        headers=headers,
    ).status_code == 200


def test_write_operations_are_protected_too(pipeline) -> None:
    client = _secured_client(pipeline)
    # upload без токена запрещён
    response = client.post(
        "/api/documents",
        json={"filename": "a.txt", "text": "Содержимое."},
    )
    assert response.status_code == 401
