"""Тесты админ-эндпоинтов управления пользователями.

Закрывает регрессию: до уборки `create_user` обращался к несуществующей
переменной `credentials` (F821) и падал бы в рантайме при попытке
создать пользователя. Сейчас код починен, эндпоинт работает по
контракту — эти тесты фиксируют поведение.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


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
    """Создаём судью через admin и логинимся под ним."""
    admin = _admin_token(client)
    response = client.post(
        "/auth/users",
        json={"email": "judge@example.com", "password": "judgepass1", "full_name": "Судья Иван"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert response.status_code == 201, response.text
    login = client.post(
        "/auth/login",
        json={"email": "judge@example.com", "password": "judgepass1"},
    )
    assert login.status_code == 200
    return login.json()["tokens"]["access_token"]


# -- POST /auth/users ---------------------------------------------------------


def test_create_user_requires_admin(pipeline) -> None:
    client = _client_with_admin(pipeline)
    judge = _judge_token(client)

    response = client.post(
        "/auth/users",
        json={"email": "x@example.com", "password": "longenough", "full_name": "X"},
        headers={"Authorization": f"Bearer {judge}"},
    )
    assert response.status_code == 403
    assert "прав" in response.json()["detail"].lower()


def test_create_user_success_returns_201(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)

    response = client.post(
        "/auth/users",
        json={
            "email": "newjudge@example.com",
            "password": "newjudge1",
            "full_name": "Судья Новый",
            "role": "judge",
        },
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "newjudge@example.com"
    assert body["role"] == "judge"
    assert "user_id" in body


def test_create_user_duplicate_email_returns_400(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)
    headers = {"Authorization": f"Bearer {admin}"}

    first = client.post(
        "/auth/users",
        json={"email": "dup@example.com", "password": "longenough", "full_name": "Первый"},
        headers=headers,
    )
    assert first.status_code == 201

    second = client.post(
        "/auth/users",
        json={"email": "dup@example.com", "password": "longenough", "full_name": "Второй"},
        headers=headers,
    )
    assert second.status_code == 400
    assert "уже" in second.json()["detail"].lower()


def test_create_user_short_password_rejected_by_schema(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)

    response = client.post(
        "/auth/users",
        json={"email": "weak@example.com", "password": "short", "full_name": "Слабый пароль"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert response.status_code == 422  # pydantic: min_length=8


# -- GET /auth/users ---------------------------------------------------------


def test_list_users_requires_admin(pipeline) -> None:
    client = _client_with_admin(pipeline)
    judge = _judge_token(client)

    response = client.get("/auth/users", headers={"Authorization": f"Bearer {judge}"})
    assert response.status_code == 403


def test_list_users_returns_all(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)

    response = client.get("/auth/users", headers={"Authorization": f"Bearer {admin}"})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    emails = {u["email"] for u in body}
    assert "admin@example.com" in emails


# -- PATCH /auth/users/{id} ---------------------------------------------------


def test_update_user_changes_full_name(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)
    headers = {"Authorization": f"Bearer {admin}"}

    created = client.post(
        "/auth/users",
        json={"email": "toupdate@example.com", "password": "longenough", "full_name": "Был"},
        headers=headers,
    )
    user_id = created.json()["user_id"]

    response = client.patch(
        f"/auth/users/{user_id}",
        json={"full_name": "Стало"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["full_name"] == "Стало"


def test_update_user_unknown_id_returns_404(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)

    response = client.patch(
        "/auth/users/no-such-id",
        json={"full_name": "x"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    assert response.status_code == 404


# -- DELETE /auth/users/{id} --------------------------------------------------


def test_delete_user_success(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)
    headers = {"Authorization": f"Bearer {admin}"}

    created = client.post(
        "/auth/users",
        json={"email": "todel@example.com", "password": "longenough", "full_name": "На удаление"},
        headers=headers,
    )
    user_id = created.json()["user_id"]

    response = client.delete(f"/auth/users/{user_id}", headers=headers)
    assert response.status_code == 204

    list_after = client.get("/auth/users", headers=headers).json()
    assert all(u["user_id"] != user_id for u in list_after)


def test_delete_user_unknown_id_returns_404(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)

    response = client.delete(
        "/auth/users/no-such-id", headers={"Authorization": f"Bearer {admin}"}
    )
    assert response.status_code == 404


# -- /auth/refresh -----------------------------------------------------------


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


# -- /auth/logout ------------------------------------------------------------


def test_logout_returns_200(pipeline) -> None:
    client = _client_with_admin(pipeline)
    admin = _admin_token(client)
    response = client.post(
        "/auth/logout", headers={"Authorization": f"Bearer {admin}"}
    )
    assert response.status_code == 200


def test_logout_requires_auth(pipeline) -> None:
    client = _client_with_admin(pipeline)
    response = client.post("/auth/logout")
    assert response.status_code == 401
