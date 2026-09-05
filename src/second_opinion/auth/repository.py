"""Хранилища пользователей и auth-служебных данных (ADR-006).

In-memory — дефолт для loopback-MVP и тестов; Postgres — для
многопользовательского режима (``SO_STORAGE_BACKEND=postgres`` +
``SO_DATABASE_URL``). Интерфейс одинаковый, бизнес-логика в
``AuthService`` от носителя не зависит.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Protocol

from ..domain.users import LoginAttempt, User


class UserStore(Protocol):
    """Минимальный интерфейс персистентности auth-данных."""

    def save_user(self, user: User) -> None: ...
    def get_user(self, user_id: str) -> User | None: ...
    def get_user_by_email(self, email: str) -> User | None: ...
    def list_users(self) -> list[User]: ...
    def delete_user(self, user_id: str) -> bool: ...

    def is_refresh_revoked(self, jti: str) -> bool: ...
    def revoke_refresh(self, jti: str, user_id: str) -> None: ...

    def record_login(self, attempt: LoginAttempt) -> None: ...
    def list_login_attempts(self, limit: int = 100) -> list[LoginAttempt]: ...


class InMemoryUserStore:
    """In-memory реализация (MVP, один процесс)."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._revoked_refresh: dict[str, str] = {}
        self._logins: list[LoginAttempt] = []
        self._lock = threading.Lock()

    def save_user(self, user: User) -> None:
        with self._lock:
            self._users[user.user_id] = user

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def list_users(self) -> list[User]:
        return list(self._users.values())

    def delete_user(self, user_id: str) -> bool:
        with self._lock:
            return self._users.pop(user_id, None) is not None

    def is_refresh_revoked(self, jti: str) -> bool:
        return jti in self._revoked_refresh

    def revoke_refresh(self, jti: str, user_id: str) -> None:
        with self._lock:
            self._revoked_refresh[jti] = user_id

    def record_login(self, attempt: LoginAttempt) -> None:
        with self._lock:
            self._logins.append(attempt)

    def list_login_attempts(self, limit: int = 100) -> list[LoginAttempt]:
        with self._lock:
            return list(reversed(self._logins[-limit:]))


class PostgresUserStore:
    """PostgreSQL-реализация: users (JSONB) + blacklist + аудит входов.

    Таблицы создаются автоматически (``CREATE TABLE IF NOT EXISTS``).
    Префикс таблиц задаётся параметром, чтобы gated-тесты могли работать
    на изолированных таблицах в общей БД.
    """

    def __init__(self, dsn: str, *, table_prefix: str = "") -> None:
        import psycopg

        self._psycopg = psycopg
        self._dsn = dsn
        p = table_prefix
        self._users_table = f"{p}users" if p else "users"
        self._blacklist_table = f"{p}auth_refresh_blacklist" if p else "auth_refresh_blacklist"
        self._audit_table = f"{p}auth_login_audit" if p else "auth_login_audit"
        self._lock = threading.Lock()
        self._ensure_tables()

    def _connect(self):
        return self._psycopg.connect(self._dsn)

    def _ensure_tables(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._users_table} ("  # noqa: S608
                "id text PRIMARY KEY, data jsonb NOT NULL)"
            )
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._blacklist_table} ("  # noqa: S608
                "jti text PRIMARY KEY, user_id text NOT NULL, "
                "revoked_at timestamptz NOT NULL)"
            )
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._audit_table} ("  # noqa: S608
                "id text PRIMARY KEY, data jsonb NOT NULL)"
            )

    # -- пользователи ----------------------------------------------------

    def save_user(self, user: User) -> None:
        payload = user.model_dump_json()
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self._users_table} (id, data) VALUES (%s, %s) "  # noqa: S608
                "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (user.user_id, payload),
            )

    def _parse_user(self, data) -> User:
        if isinstance(data, str):
            data = json.loads(data)
        return User.model_validate(data)

    def get_user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._users_table} WHERE id = %s",  # noqa: S608
                (user_id,),
            ).fetchone()
        return self._parse_user(row[0]) if row else None

    def get_user_by_email(self, email: str) -> User | None:
        # Email уникален на уровне сервиса; поиск перебором по JSONB
        # достаточен для масштаба MVP (индекс — отдельная задача).
        for user in self.list_users():
            if user.email == email:
                return user
        return None

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._users_table} ORDER BY id"  # noqa: S608
            ).fetchall()
        return [self._parse_user(data) for (data,) in rows]

    def delete_user(self, user_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                f"DELETE FROM {self._users_table} WHERE id = %s",  # noqa: S608
                (user_id,),
            )
            return (cursor.rowcount or 0) > 0

    # -- refresh blacklist -------------------------------------------------

    def is_refresh_revoked(self, jti: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM {self._blacklist_table} WHERE jti = %s",  # noqa: S608
                (jti,),
            ).fetchone()
        return row is not None

    def revoke_refresh(self, jti: str, user_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self._blacklist_table} (jti, user_id, revoked_at) "  # noqa: S608
                "VALUES (%s, %s, %s) ON CONFLICT (jti) DO NOTHING",
                (jti, user_id, datetime.now(UTC)),
            )

    # -- аудит входов --------------------------------------------------------

    def record_login(self, attempt: LoginAttempt) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self._audit_table} (id, data) VALUES (%s, %s) "  # noqa: S608
                "ON CONFLICT (id) DO NOTHING",
                (attempt.attempt_id, attempt.model_dump_json()),
            )

    def list_login_attempts(self, limit: int = 100) -> list[LoginAttempt]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT data FROM {self._audit_table} "  # noqa: S608
                "ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        items: list[LoginAttempt] = []
        for (data,) in rows:
            if isinstance(data, str):
                data = json.loads(data)
            items.append(LoginAttempt.model_validate(data))
        return items
