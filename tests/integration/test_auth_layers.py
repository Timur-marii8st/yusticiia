"""ADR-008: /api/* принимает SO_AUTH_TOKEN ИЛИ JWT access-токен."""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


def _jwt(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


def test_api_jwt_accepted_when_token_set(pipeline) -> None:
    """Регрессия: раньше middleware резал JWT при заданном SO_AUTH_TOKEN."""
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    token = _jwt(client)
    assert client.get("/api/norms", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # общий секрет по-прежнему работает
    assert (
        client.get("/api/norms", headers={"Authorization": "Bearer secret-token"}).status_code
        == 200
    )


def test_api_rejects_garbage_when_token_set(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    assert client.get("/api/norms", headers={"Authorization": "Bearer garbage"}).status_code == 401
    assert client.get("/api/norms").status_code == 401


def test_login_stays_open_when_token_set(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200
