"""Тесты эндпоинтов входа и чтения учётных записей.

Учётные записи создаёт и меняет оператор командами сервера; через HTTP
администратор только читает список записей и журнал входов, а вход, обновление
токена и выход доступны всем.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.app import create_app


def _client_with_admin(pipeline) -> TestClient:
    return TestClient(create_app(pipeline, auth_token="test-secret"))


def _admin_token(client: TestClient) -> str:
    """Логин под предзаполненным conftest-админом, возврат access_token."""
    response = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


def _judge_token(client: TestClient) -> str:
    """Судью заводит оператор (здесь напрямую через сервис), затем судья входит."""
    from second_opinion.auth.service import get_auth_service

    get_auth_service().create_user("judge@example.com", "judgepass1", "Судья Иван")
    login = client.post(
        "/auth/login",
        json={"email": "judge@example.com", "password": "judgepass1"},
    )
    assert login.status_code == 200
    return login.json()["tokens"]["access_token"]


def test_list_users_requires_admin(pipeline) -> None:
    client = _client_with_admin(pipeline)
    judge = _judge_token(client)

    response = client.get("/auth/users", headers={"Authorization": f"Bearer {judge}"})
    assert response.status_code == 403


def test_list_users_returns_all(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)
    _judge_token(client)

    response = client.get("/auth/users", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert {u["email"] for u in body} == {"admin@example.com", "judge@example.com"}
    assert all("password" not in u and "password_hash" not in u for u in body)


def test_refresh_token_success(pipeline) -> None:
    client = _client_with_admin(pipeline)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123"},
    )
    assert login.status_code == 200
    refresh = login.json()["tokens"]["refresh_token"]

    response = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["expires_in"] == 30 * 60


def test_refresh_token_invalid_returns_401(pipeline) -> None:
    client = _client_with_admin(pipeline)
    response = client.post("/auth/refresh", json={"refresh_token": "garbage"})
    assert response.status_code == 401


def test_refresh_token_missing_field_returns_422(pipeline) -> None:
    client = _client_with_admin(pipeline)
    response = client.post("/auth/refresh", json={})
    assert response.status_code == 422


def test_logout_returns_200(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)
    response = client.post("/auth/logout", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200


def test_logout_requires_auth(pipeline) -> None:
    client = _client_with_admin(pipeline)
    response = client.post("/auth/logout")
    assert response.status_code == 401
