"""Unit-тесты PostgreSQL user store (in-memory часть): ротация, blacklist, expiry, аудит."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app
from second_opinion.auth.repository import InMemoryUserStore
from second_opinion.auth.service import (
    AccountLockedError,
    AuthService,
    PasswordExpiredError,
)


def _service(**kwargs) -> AuthService:
    return AuthService(store=InMemoryUserStore(), **kwargs)


def test_refresh_rotation_invalidates_old_token() -> None:
    auth = _service()
    user = auth.create_user("u@example.com", "longpassword1", "U")
    _, refresh1, _ = auth.create_tokens(user)

    pair = auth.refresh_tokens(refresh1)
    assert pair is not None
    # старый refresh отозван — повтор запрещён
    assert auth.refresh_tokens(refresh1) is None
    # новый работает
    assert auth.refresh_tokens(pair.refresh_token) is not None


def test_revoke_refresh_token_blocks_refresh() -> None:
    auth = _service()
    user = auth.create_user("u@example.com", "longpassword1", "U")
    _, refresh, _ = auth.create_tokens(user)
    assert auth.revoke_refresh_token(refresh) is True
    assert auth.refresh_tokens(refresh) is None
    assert auth.revoke_refresh_token("garbage") is False


def test_password_expiry_blocks_login() -> None:
    auth = _service(password_max_age_days=30)
    user = auth.create_user("u@example.com", "longpassword1", "U")
    # искусственно состариваем пароль
    user.password_changed_at = datetime.now(UTC) - timedelta(days=31)
    auth.save_user(user)

    assert auth.is_password_expired(auth.get_user(user.user_id)) is True
    with pytest.raises(PasswordExpiredError):
        auth.authenticate("u@example.com", "longpassword1")


def test_password_expiry_disabled_by_default() -> None:
    auth = _service(password_max_age_days=0)
    user = auth.create_user("u@example.com", "longpassword1", "U")
    user.password_changed_at = datetime.now(UTC) - timedelta(days=365)
    auth.save_user(user)
    assert auth.is_password_expired(auth.get_user(user.user_id)) is False
    assert auth.authenticate("u@example.com", "longpassword1") is not None


def test_set_password_refreshes_expiry_marker() -> None:
    auth = _service(password_max_age_days=30)
    user = auth.create_user("u@example.com", "longpassword1", "U")
    user.password_changed_at = datetime.now(UTC) - timedelta(days=31)
    auth.save_user(user)
    assert auth.is_password_expired(user) is True
    auth.set_password(user.user_id, "newlongpass2")
    assert auth.is_password_expired(auth.get_user(user.user_id)) is False


def test_login_audit_records_success_and_failure() -> None:
    auth = _service()
    auth.create_user("u@example.com", "longpassword1", "U")
    assert auth.authenticate("u@example.com", "longpassword1") is not None
    assert auth.authenticate("u@example.com", "wrong") is None
    assert auth.authenticate("missing@example.com", "x") is None

    log = auth.list_login_attempts()
    assert len(log) == 3
    assert log[0].email == "missing@example.com" and log[0].success is False
    assert log[1].success is False and log[1].reason == "bad_password"
    assert log[2].success is True
    # пароли в аудите не хранятся
    assert "longpassword1" not in "".join(a.model_dump_json() for a in log)


def test_api_logout_with_refresh_revokes(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="test-secret"))
    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    admin = login.json()["tokens"]["access_token"]
    refresh = login.json()["tokens"]["refresh_token"]
    headers = {"Authorization": f"Bearer {admin}"}

    # logout с refresh отзывает его
    out = client.post("/auth/logout", json={"refresh_token": refresh}, headers=headers)
    assert out.status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401


def test_api_audit_requires_admin(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token="test-secret"))
    admin = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    ).json()["tokens"]["access_token"]

    # создаём судью и фиксируем неудачный вход для аудита
    client.post(
        "/auth/users",
        json={"email": "judge2@example.com", "password": "judgepass1", "full_name": "J"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    client.post("/auth/login", json={"email": "judge2@example.com", "password": "wrong"})

    audit = client.get("/auth/audit", headers={"Authorization": f"Bearer {admin}"})
    assert audit.status_code == 200
    emails = {a["email"] for a in audit.json()}
    assert "judge2@example.com" in emails

    judge = client.post(
        "/auth/login", json={"email": "judge2@example.com", "password": "judgepass1"}
    ).json()["tokens"]["access_token"]
    forbidden = client.get("/auth/audit", headers={"Authorization": f"Bearer {judge}"})
    assert forbidden.status_code == 403


def test_api_admin_password_reset_refreshes_expiry(pipeline, monkeypatch) -> None:
    from second_opinion.auth.service import get_auth_service

    monkeypatch.setenv("SO_PASSWORD_MAX_AGE_DAYS", "30")
    client = TestClient(create_app(pipeline, auth_token="test-secret"))
    admin = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    ).json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {admin}"}

    created = client.post(
        "/auth/users",
        json={"email": "exp@example.com", "password": "longpass12", "full_name": "E"},
        headers=headers,
    )
    uid = created.json()["user_id"]

    auth = get_auth_service()
    user = auth.get_user(uid)
    assert user is not None
    user.password_changed_at = datetime.now(UTC) - timedelta(days=60)
    auth.save_user(user)

    # сброс пароля админом снимает просрочку
    resp = client.patch(f"/auth/users/{uid}", json={"password": "newlongpass3"}, headers=headers)
    assert resp.status_code == 200
    assert auth.is_password_expired(auth.get_user(uid)) is False


# -- парольные политики: история и блокировка ----------------------------------


def test_password_history_rejects_reuse() -> None:
    auth = _service()
    user = auth.create_user("u@example.com", "longpassword1", "U")
    auth.change_password(user, "longpassword1", "longpassword2")
    with pytest.raises(ValueError, match="последних паролей"):
        auth.change_password(auth.get_user(user.user_id), "longpassword2", "longpassword1")


def test_password_history_depth_zero_disables_check() -> None:
    auth = AuthService(store=InMemoryUserStore(), password_history_depth=0)
    user = auth.create_user("u@example.com", "longpassword1", "U")
    auth.change_password(user, "longpassword1", "longpassword2")
    # возврат к исходному разрешён, когда история выключена
    auth.change_password(auth.get_user(user.user_id), "longpassword2", "longpassword1")
    assert auth.authenticate("u@example.com", "longpassword1") is not None


def test_lockout_after_max_attempts() -> None:
    auth = AuthService(store=InMemoryUserStore(), login_max_attempts=3, login_lockout_minutes=15)
    auth.create_user("u@example.com", "longpassword1", "U")
    assert auth.authenticate("u@example.com", "wrong") is None
    assert auth.authenticate("u@example.com", "wrong") is None
    assert auth.authenticate("u@example.com", "wrong") is None
    with pytest.raises(AccountLockedError):
        auth.authenticate("u@example.com", "longpassword1")


def test_successful_login_resets_attempts() -> None:
    auth = AuthService(store=InMemoryUserStore(), login_max_attempts=3, login_lockout_minutes=15)
    auth.create_user("u@example.com", "longpassword1", "U")
    assert auth.authenticate("u@example.com", "wrong") is None
    assert auth.authenticate("u@example.com", "longpassword1") is not None
    assert auth.get_user_by_email("u@example.com").failed_login_attempts == 0


def test_lockout_expires_and_admin_reset_unlocks() -> None:
    from datetime import timedelta

    auth = AuthService(store=InMemoryUserStore(), login_max_attempts=1, login_lockout_minutes=15)
    user = auth.create_user("u@example.com", "longpassword1", "U")
    assert auth.authenticate("u@example.com", "wrong") is None
    # блокировка истекла — вход снова возможен
    user = auth.get_user(user.user_id)
    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    auth.save_user(user)
    assert auth.authenticate("u@example.com", "longpassword1") is not None

    # повторная блокировка снимается сбросом пароля админом
    assert auth.authenticate("u@example.com", "wrong") is None
    with pytest.raises(AccountLockedError):
        auth.authenticate("u@example.com", "longpassword1")
    auth.set_password(user.user_id, "brandnewpass9")
    assert auth.authenticate("u@example.com", "brandnewpass9") is not None


def test_api_locked_login_returns_403(pipeline) -> None:
    from second_opinion.auth.service import get_auth_service

    auth = get_auth_service()
    auth._login_max_attempts = 1
    client = TestClient(create_app(pipeline, auth_token="test-secret"))
    assert client.post("/auth/login", json={"email": "admin@example.com", "password": "bad"}).status_code == 401
    locked = client.post("/auth/login", json={"email": "admin@example.com", "password": "admin123"})
    assert locked.status_code == 403
    assert "заблокирован" in locked.json()["detail"]
