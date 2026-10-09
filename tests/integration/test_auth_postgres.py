"""Gated-тесты Postgres user store (skip без SO_TEST_DATABASE_URL)."""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("SO_TEST_DATABASE_URL"),
    reason="SO_TEST_DATABASE_URL не задан — пропускаем postgres-тесты",
)


@pytest.fixture()
def pg_dsn() -> str:
    return os.environ["SO_TEST_DATABASE_URL"]


def _prefix() -> str:
    return f"auth_test_{uuid.uuid4().hex[:8]}_"


def test_postgres_users_roundtrip(pg_dsn: str) -> None:
    from second_opinion.auth.repository import PostgresUserStore
    from second_opinion.auth.service import AuthService

    auth = AuthService(store=PostgresUserStore(pg_dsn, table_prefix=_prefix()))
    user = auth.create_user("pg@example.com", "longpassword1", "PG User")
    assert auth.get_user(user.user_id) is not None
    assert auth.get_user_by_email("pg@example.com") is not None
    assert len(auth.list_users()) == 1
    assert auth.authenticate("pg@example.com", "longpassword1") is not None
    assert auth.delete_user(user.user_id) is True
    assert auth.get_user(user.user_id) is None


def test_postgres_refresh_rotation_persists(pg_dsn: str) -> None:
    from second_opinion.auth.repository import PostgresUserStore
    from second_opinion.auth.service import AuthService

    prefix = _prefix()
    auth = AuthService(store=PostgresUserStore(pg_dsn, table_prefix=prefix))
    user = auth.create_user("rot@example.com", "longpassword1", "Rot")
    _, refresh1, _ = auth.create_tokens(user)

    pair = auth.refresh_tokens(refresh1)
    assert pair is not None
    auth2 = AuthService(store=PostgresUserStore(pg_dsn, table_prefix=prefix))
    assert auth2.refresh_tokens(refresh1) is None
    assert auth2.refresh_tokens(pair.refresh_token) is not None


def test_postgres_login_audit_roundtrip(pg_dsn: str) -> None:
    from second_opinion.auth.repository import PostgresUserStore
    from second_opinion.auth.service import AuthService

    store_prefix = _prefix()
    auth = AuthService(store=PostgresUserStore(pg_dsn, table_prefix=store_prefix))
    auth.create_user("audit@example.com", "longpassword1", "Audit")
    assert auth.authenticate("audit@example.com", "longpassword1") is not None
    assert auth.authenticate("audit@example.com", "wrong") is None

    log = auth.list_login_attempts()
    assert {a.email for a in log} == {"audit@example.com"}
    assert {a.success for a in log} == {True, False}


def test_postgres_view_shows_accounts_with_roles(pg_dsn: str) -> None:
    import psycopg

    from second_opinion.auth.repository import PostgresUserStore
    from second_opinion.auth.service import AuthService
    from second_opinion.domain.users import UserRole

    prefix = _prefix()
    auth = AuthService(store=PostgresUserStore(pg_dsn, table_prefix=prefix))
    auth.create_user("judge@example.com", "longpassword1", "Судья")
    auth.create_user("root@example.com", "longpassword12", "Админ", UserRole.ADMIN)

    with psycopg.connect(pg_dsn) as conn:
        rows = conn.execute(
            f"SELECT email, role, is_active FROM {prefix}user_accounts ORDER BY email"
        ).fetchall()

    assert rows == [("judge@example.com", "judge", True), ("root@example.com", "admin", True)]


def test_postgres_rejects_a_second_account_with_the_same_address(pg_dsn: str) -> None:
    import psycopg

    from second_opinion.auth.repository import PostgresUserStore
    from second_opinion.domain.users import User

    store = PostgresUserStore(pg_dsn, table_prefix=_prefix())
    store.save_user(User(user_id="a1", email="same@example.com", full_name="А"))

    with pytest.raises(psycopg.errors.UniqueViolation):
        store.save_user(User(user_id="a2", email="Same@Example.com", full_name="Б"))
