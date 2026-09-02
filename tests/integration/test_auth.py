from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


def test_open_mode_by_default(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    assert client.get("/api/norms").status_code == 200


def test_health_and_static_stay_public_when_secured(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    assert client.get("/health").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/").status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "secret-token"},
    ],
    ids=["no-header", "empty", "wrong-token", "no-bearer-scheme"],
)
def test_api_requires_valid_bearer(pipeline, headers: dict) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    response = client.get("/api/norms", headers=headers)
    assert response.status_code == 401
    assert "авторизация" in response.json()["detail"]


def test_api_accepts_correct_token(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    headers = {"Authorization": "Bearer secret-token"}
    assert client.get("/api/norms", headers=headers).status_code == 200
    assert client.post(
        "/api/documents",
        json={"filename": "a.txt", "text": "Текст документа для проверки."},
        headers=headers,
    ).status_code == 200


def test_write_operations_are_protected_too(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    response = client.post(
        "/api/documents",
        json={"filename": "a.txt", "text": "Содержимое."},
    )
    assert response.status_code == 401


def test_change_password_requires_auth(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    response = client.post(
        "/auth/change-password",
        json={"current_password": "old", "new_password": "newpass123"},
    )
    assert response.status_code == 401
    assert "авторизация" in response.json()["detail"]


def _login_admin(client: TestClient) -> str:
    """Логин под заранее созданным conftest-админом, возврат access_token."""
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


def test_change_password_wrong_current(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/auth/change-password",
        json={"current_password": "wrong", "new_password": "newpass123"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "неверен" in response.json()["detail"]


def test_change_password_same_as_current_fails(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/auth/change-password",
        json={"current_password": "admin123", "new_password": "admin123"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "совпадать" in response.json()["detail"].lower()


def test_change_password_short_new_fails(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    token = _login_admin(client)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/auth/change-password",
        json={"current_password": "admin123", "new_password": "123"},
        headers=headers,
    )
    assert response.status_code == 422  # pydantic validation: min_length=8
