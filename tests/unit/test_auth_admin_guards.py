"""Правка пользователя админом: сохранность полей и последний админ."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app
from second_opinion.auth.repository import InMemoryUserStore
from second_opinion.auth.service import AuthService, LastAdminError
from second_opinion.domain.users import User, UserRole


class CopyingUserStore(InMemoryUserStore):
    """In-memory store с семантикой внешней БД: чтение отдаёт копию.

    PostgresUserStore восстанавливает пользователя из JSONB, то есть
    возвращает новый объект. In-memory отдаёт тот же самый, из-за чего
    ошибки «правки потерялись между чтением и записью» на файловом и
    памятном бэкендах невидимы. Здесь они воспроизводятся без БД.
    """

    def get_user(self, user_id: str) -> User | None:
        user = super().get_user(user_id)
        return user.model_copy(deep=True) if user else None

    def get_user_by_email(self, email: str) -> User | None:
        user = super().get_user_by_email(email)
        return user.model_copy(deep=True) if user else None

    def list_users(self) -> list[User]:
        return [u.model_copy(deep=True) for u in super().list_users()]


def _admin_token(client: TestClient) -> str:
    response = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


@pytest.fixture()
def client(pipeline) -> TestClient:
    return TestClient(create_app(pipeline, auth_token="test-secret"))


@pytest.fixture()
def copying_backend(monkeypatch):
    """Подменить хранилище глобального auth-сервиса на копирующее."""
    import second_opinion.auth.service as svc

    auth = AuthService(store=CopyingUserStore())
    auth._store.save_user(
        User(
            user_id="admin-001",
            email="admin@example.com",
            full_name="Admin User",
            role=UserRole.ADMIN,
            is_active=True,
            password_hash=svc.hash_password("admin123"),
        )
    )
    monkeypatch.setattr(svc, "_auth_service", auth)
    return auth


# --- сохранность полей при одновременной смене пароля -----------------------


def test_patch_password_with_other_fields_keeps_them(client, copying_backend) -> None:
    """Пароль и прочие поля в одном PATCH — сохраняются оба.

    Регрессия: смена пароля перечитывала пользователя из хранилища, и на
    Postgres правки full_name/role/is_active затирались старым снимком.
    """
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    created = client.post(
        "/auth/users",
        json={"email": "judge@example.com", "password": "longpass12", "full_name": "Было"},
        headers=headers,
    )
    uid = created.json()["user_id"]

    response = client.patch(
        f"/auth/users/{uid}",
        json={"password": "newlongpass34", "full_name": "Стало", "role": "clerk"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Стало"
    assert response.json()["role"] == "clerk"

    stored = copying_backend.get_user(uid)
    assert stored.full_name == "Стало"
    assert stored.role is UserRole.CLERK
    assert copying_backend.authenticate("judge@example.com", "newlongpass34") is not None


def test_patch_without_password_still_saves_fields(client, copying_backend) -> None:
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    created = client.post(
        "/auth/users",
        json={"email": "judge@example.com", "password": "longpass12", "full_name": "Было"},
        headers=headers,
    )
    uid = created.json()["user_id"]

    client.patch(f"/auth/users/{uid}", json={"full_name": "Стало"}, headers=headers)
    assert copying_backend.get_user(uid).full_name == "Стало"


# --- последний администратор -------------------------------------------------


def test_service_blocks_removing_last_admin() -> None:
    auth = AuthService(store=InMemoryUserStore())
    admin = auth.create_user("admin@example.com", "longpassword1", "Админ", UserRole.ADMIN)

    with pytest.raises(LastAdminError):
        auth.ensure_admin_remains(admin.user_id, is_active=False)
    with pytest.raises(LastAdminError):
        auth.ensure_admin_remains(admin.user_id, role=UserRole.JUDGE)


def test_service_allows_when_another_admin_exists() -> None:
    auth = AuthService(store=InMemoryUserStore())
    first = auth.create_user("a@example.com", "longpassword1", "Первый", UserRole.ADMIN)
    auth.create_user("b@example.com", "longpassword1", "Второй", UserRole.ADMIN)

    auth.ensure_admin_remains(first.user_id, is_active=False)  # не должно бросать


def test_service_ignores_non_admin_users() -> None:
    auth = AuthService(store=InMemoryUserStore())
    auth.create_user("admin@example.com", "longpassword1", "Админ", UserRole.ADMIN)
    judge = auth.create_user("j@example.com", "longpassword1", "Судья")

    auth.ensure_admin_remains(judge.user_id, is_active=False)


def test_api_refuses_to_delete_last_admin(client) -> None:
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    response = client.delete("/auth/users/admin-001", headers=headers)

    assert response.status_code == 409
    assert "единственный активный администратор" in response.json()["detail"]
    assert client.get("/auth/me", headers=headers).status_code == 200


def test_api_refuses_to_demote_last_admin(client) -> None:
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    response = client.patch(
        "/auth/users/admin-001", json={"role": "judge"}, headers=headers
    )
    assert response.status_code == 409


def test_api_refuses_to_deactivate_last_admin(client) -> None:
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    response = client.patch(
        "/auth/users/admin-001", json={"is_active": False}, headers=headers
    )
    assert response.status_code == 409


def test_api_allows_demotion_when_second_admin_exists(client) -> None:
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    client.post(
        "/auth/users",
        json={
            "email": "admin2@example.com",
            "password": "longpass12",
            "full_name": "Второй",
            "role": "admin",
        },
        headers=headers,
    )
    response = client.patch(
        "/auth/users/admin-001", json={"role": "judge"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["role"] == "judge"


def test_api_delete_unknown_user_still_404(client) -> None:
    headers = {"Authorization": f"Bearer {_admin_token(client)}"}
    assert client.delete("/auth/users/нет-такого", headers=headers).status_code == 404
