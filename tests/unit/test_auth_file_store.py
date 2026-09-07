"""Файловое хранилище auth-данных: пользователи переживают перезапуск."""

from __future__ import annotations

from second_opinion.auth.repository import JsonFileUserStore
from second_opinion.domain.users import LoginAttempt, User, UserRole


def _user(email: str = "u@example.com", **kwargs) -> User:
    return User(email=email, full_name="Тест", **kwargs)


def test_user_survives_new_store_instance(tmp_path) -> None:
    """Главное свойство: перезапуск процесса не теряет учётки."""
    saved = _user(role=UserRole.ADMIN)
    JsonFileUserStore(tmp_path).save_user(saved)

    reopened = JsonFileUserStore(tmp_path).get_user(saved.user_id)
    assert reopened is not None
    assert reopened.email == saved.email
    assert reopened.role is UserRole.ADMIN


def test_lookup_by_email_and_delete(tmp_path) -> None:
    store = JsonFileUserStore(tmp_path)
    user = _user("judge@example.com")
    store.save_user(user)

    assert store.get_user_by_email("judge@example.com") is not None
    assert store.get_user_by_email("нет@example.com") is None
    assert [u.user_id for u in store.list_users()] == [user.user_id]

    assert store.delete_user(user.user_id) is True
    assert store.delete_user(user.user_id) is False
    assert store.list_users() == []


def test_unsafe_user_id_never_escapes_directory(tmp_path) -> None:
    """Идентификатор попадает в имя файла — обход каталога недопустим."""
    store = JsonFileUserStore(tmp_path)
    assert store.get_user("../../etc/passwd") is None
    assert store.delete_user("../../etc/passwd") is False


def test_revoked_refresh_persists(tmp_path) -> None:
    JsonFileUserStore(tmp_path).revoke_refresh("jti-1", "user-1")

    reopened = JsonFileUserStore(tmp_path)
    assert reopened.is_refresh_revoked("jti-1") is True
    assert reopened.is_refresh_revoked("jti-2") is False


def test_corrupt_blacklist_does_not_crash(tmp_path) -> None:
    store = JsonFileUserStore(tmp_path)
    store.revoke_refresh("jti-1", "user-1")
    (tmp_path / "refresh_blacklist.json").write_text("{битый", encoding="utf-8")

    assert store.is_refresh_revoked("jti-1") is False  # файл нечитаем
    store.revoke_refresh("jti-2", "user-1")  # и перезаписывается штатно
    assert store.is_refresh_revoked("jti-2") is True


def test_login_audit_newest_first_and_limited(tmp_path) -> None:
    store = JsonFileUserStore(tmp_path)
    for i in range(5):
        store.record_login(LoginAttempt(email=f"u{i}@example.com", success=True))

    recent = store.list_login_attempts(limit=2)
    assert [a.email for a in recent] == ["u4@example.com", "u3@example.com"]
    assert len(JsonFileUserStore(tmp_path).list_login_attempts(limit=100)) == 5


def test_empty_audit_returns_empty_list(tmp_path) -> None:
    assert JsonFileUserStore(tmp_path).list_login_attempts() == []
