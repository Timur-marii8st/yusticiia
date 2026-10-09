"""Хранилища пользователей и auth-служебных данных.

Файловое — дефолт (``SO_STORAGE_BACKEND=file``): пользователи переживают
перезапуск, поэтому личный кабинет работает «из коробки». In-memory —
для тестов и явного эфемерного режима. Postgres — многопользовательский
режим (``SO_STORAGE_BACKEND=postgres`` + ``SO_DATABASE_URL``). Интерфейс
одинаковый, бизнес-логика в ``AuthService`` от носителя не зависит.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ..domain.users import LoginAttempt, User
from ..storage.repositories import atomic_write_json
from ..storage.sql import safe_sql_identifier

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

logger = logging.getLogger(__name__)


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
        p = safe_sql_identifier(table_prefix) if table_prefix else ""
        self._users_table = f"{p}users" if p else "users"
        self._blacklist_table = f"{p}auth_refresh_blacklist" if p else "auth_refresh_blacklist"
        self._audit_table = f"{p}auth_login_audit" if p else "auth_login_audit"
        self._view = f"{p}user_accounts" if p else "user_accounts"
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
            self._ensure_account_views(conn)

    def _ensure_account_views(self, conn) -> None:
        """Уникальный адрес на уровне базы и читаемое представление учётных записей.

        Источник истины остаётся ``data`` (JSONB). Представление ``user_accounts``
        показывает адрес, роль и состояние обычными столбцами для запросов
        администратора базы; менять записи нужно командами ``second-opinion``.
        """
        try:
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {self._users_table}_email_key "  # noqa: S608
                f"ON {self._users_table} ((lower(data->>'email')))"
            )
        except self._psycopg.Error as exc:
            logger.warning("уникальный индекс адресов не создан: %s", exc)
        conn.execute(
            f"CREATE OR REPLACE VIEW {self._view} AS "  # noqa: S608
            "SELECT id AS user_id, data->>'email' AS email, data->>'full_name' AS full_name, "
            "data->>'role' AS role, (data->>'is_active')::boolean AS is_active, "
            "data->>'created_at' AS created_at, data->>'last_login' AS last_login "
            f"FROM {self._users_table}"
        )

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


class JsonFileUserStore:
    """Файловое хранилище auth-данных — дефолт для ``storage_backend=file``.

    Раскладка внутри каталога (``<data_dir>/store/auth``)::

        users/<user_id>.json   — по файлу на пользователя (атомарная запись)
        refresh_blacklist.json — отозванные refresh-токены: jti -> user_id
        login_audit.jsonl      — журнал входов, дозапись строками

    In-memory теряет пользователей при перезапуске, из-за чего личный
    кабинет был недостижим на дефолтной конфигурации. Postgres остаётся
    вариантом для многопользовательского режима.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._users_dir = self._dir / "users"
        self._blacklist_path = self._dir / "refresh_blacklist.json"
        self._audit_path = self._dir / "login_audit.jsonl"
        self._lock = threading.RLock()
        self._users_dir.mkdir(parents=True, exist_ok=True)

    def _user_path(self, user_id: str) -> Path:
        if not _SAFE_ID.fullmatch(user_id):
            raise ValueError(f"недопустимый user_id: {user_id!r}")
        return self._users_dir / f"{user_id}.json"

    def save_user(self, user: User) -> None:
        with self._lock:
            atomic_write_json(self._user_path(user.user_id), user.model_dump_json(indent=2))

    def get_user(self, user_id: str) -> User | None:
        try:
            path = self._user_path(user_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        with self._lock:
            return User.model_validate_json(path.read_text(encoding="utf-8"))

    def get_user_by_email(self, email: str) -> User | None:
        for user in self.list_users():
            if user.email == email:
                return user
        return None

    def list_users(self) -> list[User]:
        users: list[User] = []
        with self._lock:
            for path in sorted(self._users_dir.glob("*.json")):
                users.append(User.model_validate_json(path.read_text(encoding="utf-8")))
        return users

    def delete_user(self, user_id: str) -> bool:
        try:
            path = self._user_path(user_id)
        except ValueError:
            return False
        with self._lock:
            if not path.exists():
                return False
            path.unlink()
            return True

    def _read_blacklist(self) -> dict[str, str]:
        if not self._blacklist_path.exists():
            return {}
        try:
            return json.loads(self._blacklist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def is_refresh_revoked(self, jti: str) -> bool:
        with self._lock:
            return jti in self._read_blacklist()

    def revoke_refresh(self, jti: str, user_id: str) -> None:
        with self._lock:
            blacklist = self._read_blacklist()
            blacklist[jti] = user_id
            atomic_write_json(self._blacklist_path, json.dumps(blacklist, ensure_ascii=False))

    def record_login(self, attempt: LoginAttempt) -> None:
        with self._lock:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(attempt.model_dump_json() + "\n")

    def list_login_attempts(self, limit: int = 100) -> list[LoginAttempt]:
        if not self._audit_path.exists():
            return []
        with self._lock:
            lines = self._audit_path.read_text(encoding="utf-8").splitlines()
        items: list[LoginAttempt] = []
        for line in reversed(lines):
            if len(items) >= limit:
                break
            if line.strip():
                items.append(LoginAttempt.model_validate_json(line))
        return items
