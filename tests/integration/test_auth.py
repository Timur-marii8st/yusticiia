from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app


def test_open_mode_by_default(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    assert client.get("/api/v1/norms").status_code == 200


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
    response = client.get("/api/v1/norms", headers=headers)
    assert response.status_code == 401
    assert "авторизация" in response.json()["detail"]


def test_api_accepts_correct_token(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    headers = {"Authorization": "Bearer secret-token"}
    assert client.get("/api/v1/norms", headers=headers).status_code == 200
    assert client.post(
        "/api/v1/documents",
        json={"filename": "a.txt", "text": "Текст документа для проверки."},
        headers=headers,
    ).status_code == 200


def test_write_operations_are_protected_too(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    response = client.post(
        "/api/v1/documents",
        json={"filename": "a.txt", "text": "Содержимое."},
    )
    assert response.status_code == 401


def _login_admin(client: TestClient) -> str:
    """Логин под заранее созданным conftest-админом, возврат access_token."""
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


def test_platform_has_no_password_or_account_management_routes(pipeline) -> None:
    """Пароли и учётные записи меняются командами сервера, а не через HTTP."""
    client = TestClient(create_app(pipeline, auth_token="secret-token"))
    headers = {"Authorization": f"Bearer {_login_admin(client)}"}

    attempts = [
        client.post(
            "/auth/change-password",
            json={"current_password": "admin123", "new_password": "newpassword123"},
            headers=headers,
        ),
        client.post(
            "/auth/users",
            json={"email": "x@example.com", "password": "longpassword12", "full_name": "Х"},
            headers=headers,
        ),
        client.patch("/auth/users/admin-001", json={"role": "judge"}, headers=headers),
        client.delete("/auth/users/admin-001", headers=headers),
    ]

    assert all(r.status_code in (404, 405) for r in attempts), [r.status_code for r in attempts]
    paths = client.app.openapi()["paths"]
    assert "/auth/change-password" not in paths
    assert set(paths.get("/auth/users", {})) <= {"get"}
    assert "/auth/users/{user_id}" not in paths
    login = client.post("/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert login.status_code == 200
