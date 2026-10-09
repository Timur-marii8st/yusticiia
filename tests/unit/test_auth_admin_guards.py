"""Правка учётных записей оператором: сохранность полей и последний администратор."""

from __future__ import annotations

import pytest

from second_opinion.auth.repository import InMemoryUserStore
from second_opinion.auth.service import AuthService, LastAdminError
from second_opinion.cli.serve import reset_password, update_user
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


@pytest.fixture()
def copying_backend(monkeypatch) -> AuthService:
    auth = AuthService(store=CopyingUserStore())
    monkeypatch.setattr("second_opinion.auth.service.get_auth_service", lambda: auth)
    return auth


def test_role_and_name_survive_a_password_reset(copying_backend) -> None:
    """Смена роли и имени, затем сброс пароля: ни одна правка не теряется.

    Регрессия: сброс пароля перечитывал пользователя из хранилища, и на PostgreSQL
    правки имени и роли затирались старым снимком.
    """
    created = copying_backend.create_user("judge@example.com", "longpassword12", "Было")

    assert update_user("judge@example.com", "clerk", "Стало", None) == 0
    assert reset_password("judge@example.com", "newlongpass34") == 0

    stored = copying_backend.get_user(created.user_id)
    assert stored.full_name == "Стало"
    assert stored.role is UserRole.CLERK
    assert copying_backend.authenticate("judge@example.com", "newlongpass34") is not None


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

    auth.ensure_admin_remains(first.user_id, is_active=False)


def test_service_ignores_non_admin_users() -> None:
    auth = AuthService(store=InMemoryUserStore())
    auth.create_user("admin@example.com", "longpassword1", "Админ", UserRole.ADMIN)
    judge = auth.create_user("j@example.com", "longpassword1", "Судья")

    auth.ensure_admin_remains(judge.user_id, is_active=False)
