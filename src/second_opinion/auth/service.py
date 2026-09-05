"""Сервис аутентификации и управления токенами."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from ..config import load_config
from ..domain.users import LoginAttempt, TokenPair, TokenPayload, User, UserRole
from .repository import InMemoryUserStore, PostgresUserStore, UserStore

# Контекст для хеширования паролей (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Алгоритм и параметры JWT
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class PasswordExpiredError(ValueError):
    """Пароль истёк (SO_PASSWORD_MAX_AGE_DAYS) — требуется смена."""


class AccountLockedError(ValueError):
    """Вход временно заблокирован после неудачных попыток."""


class AuthService:
    """Сервис аутентификации и управления токенами.

    Персистентность — за интерфейсом ``UserStore``: in-memory по умолчанию
    (loopback-MVP), PostgreSQL при ``SO_STORAGE_BACKEND=postgres``.
    """

    def __init__(
        self,
        store: UserStore | None = None,
        password_max_age_days: int | None = None,
        password_history_depth: int | None = None,
        login_max_attempts: int | None = None,
        login_lockout_minutes: int | None = None,
    ):
        self._config = load_config()
        self._secret_key = self._config.auth_token or "dev-secret-change-in-production"
        self._store: UserStore = store or self._default_store()
        cfg = self._config
        self._password_max_age_days = (
            cfg.password_max_age_days if password_max_age_days is None else password_max_age_days
        )
        self._password_history_depth = (
            cfg.password_history_depth
            if password_history_depth is None
            else password_history_depth
        )
        self._login_max_attempts = (
            cfg.login_max_attempts if login_max_attempts is None else login_max_attempts
        )
        self._login_lockout_minutes = (
            cfg.login_lockout_minutes if login_lockout_minutes is None else login_lockout_minutes
        )

    def _default_store(self) -> UserStore:
        cfg = self._config
        if cfg.storage_backend == "postgres" and cfg.database_url:
            try:
                return PostgresUserStore(cfg.database_url)
            except Exception:  # noqa: BLE001 — fallback к in-memory
                return InMemoryUserStore()
        return InMemoryUserStore()

    # Совместимость: старый код/тесты обращались к ``auth._users`` напрямую.
    # Для in-memory возвращаем живой словарь; для Postgres — read-only снимок
    # в dict-обёртке (запись через ``save_user``).
    @property
    def _users(self) -> dict:  # type: ignore[no-redef]
        store = self._store
        if isinstance(store, InMemoryUserStore):
            return store._users
        return _ReadOnlyUsersView(store)

    @_users.setter
    def _users(self, value: dict) -> None:
        if isinstance(self._store, InMemoryUserStore):
            self._store._users = value
        else:
            for user in value.values():
                self._store.save_user(user)

    # --- Управление пользователями ---

    def create_user(
        self,
        email: str,
        password: str,
        full_name: str,
        role: UserRole = UserRole.JUDGE,
    ) -> User:
        """Создать нового пользователя."""
        if self._store.get_user_by_email(email):
            raise ValueError(f"Пользователь с email {email} уже существует")

        now = datetime.now(UTC)
        user = User(
            email=email,
            full_name=full_name,
            role=role,
            password_hash=pwd_context.hash(password),
            password_changed_at=now,
            created_at=now,
            updated_at=now,
        )
        self._store.save_user(user)
        return user

    def save_user(self, user: User) -> None:
        """Сохранить изменения пользователя."""
        user.updated_at = datetime.now(UTC)
        self._store.save_user(user)

    def get_user(self, user_id: str) -> User | None:
        """Получить пользователя по ID."""
        return self._store.get_user(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        """Получить пользователя по email."""
        return self._store.get_user_by_email(email)

    def list_users(self) -> list[User]:
        """Список всех пользователей."""
        return self._store.list_users()

    def is_password_expired(self, user: User) -> bool:
        """Проверить истечение пароля по SO_PASSWORD_MAX_AGE_DAYS."""
        if self._password_max_age_days <= 0:
            return False
        age = datetime.now(UTC) - user.password_changed_at
        return age > timedelta(days=self._password_max_age_days)

    def is_locked(self, user: User) -> bool:
        """Проверить активную блокировку входа."""
        return user.locked_until is not None and datetime.now(UTC) < user.locked_until

    def _check_password_reuse(self, user: User, new_password: str) -> None:
        """Запретить повторное использование недавних паролей."""
        if self._password_history_depth <= 0:
            return
        recent = [user.password_hash, *user.password_history]
        for old_hash in recent[: self._password_history_depth]:
            if old_hash and pwd_context.verify(new_password, old_hash):
                raise ValueError("Не используйте один из последних паролей")

    def _remember_password(self, user: User) -> None:
        """Сохранить текущий хэш в историю (с обрезкой по глубине)."""
        if self._password_history_depth <= 0:
            user.password_history = []
            return
        user.password_history = [
            user.password_hash,
            *user.password_history,
        ][: self._password_history_depth]

    def _apply_new_password(self, user: User, new_password: str) -> None:
        self._check_password_reuse(user, new_password)
        self._remember_password(user)
        user.password_hash = pwd_context.hash(new_password)
        user.password_changed_at = datetime.now(UTC)
        user.failed_login_attempts = 0
        user.locked_until = None
        self.save_user(user)

    def authenticate(self, email: str, password: str) -> User | None:
        """Проверить учётные данные пользователя.

        При истёкшем пароле — ``PasswordExpiredError``, при активной
        блокировке — ``AccountLockedError`` (вход запрещён, но существующая
        сессия может сменить пароль).
        """
        user = self.get_user_by_email(email)
        if not user or not user.is_active:
            self._store.record_login(
                LoginAttempt(email=email, user_id=None, success=False, reason="not_found")
            )
            return None
        if self.is_locked(user):
            self._store.record_login(
                LoginAttempt(
                    email=email, user_id=user.user_id, success=False, reason="locked"
                )
            )
            raise AccountLockedError("Вход временно заблокирован — повторите позже")
        if not pwd_context.verify(password, user.password_hash):
            user.failed_login_attempts += 1
            if (
                self._login_max_attempts > 0
                and user.failed_login_attempts >= self._login_max_attempts
            ):
                user.locked_until = datetime.now(UTC) + timedelta(
                    minutes=self._login_lockout_minutes
                )
            self.save_user(user)
            self._store.record_login(
                LoginAttempt(
                    email=email, user_id=user.user_id, success=False, reason="bad_password"
                )
            )
            return None
        user.failed_login_attempts = 0
        user.locked_until = None
        self.save_user(user)
        if self.is_password_expired(user):
            self._store.record_login(
                LoginAttempt(
                    email=email, user_id=user.user_id, success=False, reason="password_expired"
                )
            )
            raise PasswordExpiredError("Срок действия пароля истёк — смените пароль")
        self._store.record_login(
            LoginAttempt(email=email, user_id=user.user_id, success=True, reason="ok")
        )
        return user

    def set_password(self, user_id: str, new_password: str) -> User | None:
        """Установить новый пароль (admin-сброс): обновляет метку смены."""
        user = self.get_user(user_id)
        if not user:
            return None
        self._apply_new_password(user, new_password)
        return user

    def change_password(self, user: User, current_password: str, new_password: str) -> None:
        """Смена пароля владельцем с проверкой текущего."""
        if not pwd_context.verify(current_password, user.password_hash):
            raise ValueError("Текущий пароль неверен")
        if new_password == current_password:
            raise ValueError("Новый пароль не должен совпадать с текущим")
        self._apply_new_password(user, new_password)

    def update_last_login(self, user_id: str) -> None:
        """Обновить время последнего входа."""
        user = self.get_user(user_id)
        if user:
            user.last_login = datetime.now(UTC)
            self.save_user(user)

    def delete_user(self, user_id: str) -> bool:
        """Удалить пользователя; вернуть True, если он существовал."""
        return self._store.delete_user(user_id)

    def list_login_attempts(self, limit: int = 100) -> list[LoginAttempt]:
        """Аудит входов (новые первыми)."""
        return self._store.list_login_attempts(limit=limit)

    # --- Работа с токенами ---

    def _create_token(
        self,
        user: User,
        expires_delta: timedelta,
        token_type: str = "access",
    ) -> str:
        """Создать JWT токен."""
        now = datetime.now(UTC)
        expire = now + expires_delta
        payload = TokenPayload(
            sub=user.user_id,
            email=user.email,
            role=user.role,
            exp=expire,
            iat=now,
            type=token_type,
        )
        return jwt.encode(payload.model_dump(), self._secret_key, algorithm=ALGORITHM)

    def create_tokens(self, user: User) -> tuple[str, str, int]:
        """Создать пару access/refresh токенов."""
        access_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        refresh_expires = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

        access_token = self._create_token(user, access_expires, "access")
        refresh_token = self._create_token(user, refresh_expires, "refresh")
        expires_in = ACCESS_TOKEN_EXPIRE_MINUTES * 60

        return access_token, refresh_token, expires_in

    def decode_token(self, token: str) -> TokenPayload | None:
        """Декодировать и проверить JWT токен."""
        try:
            payload = jwt.decode(token, self._secret_key, algorithms=[ALGORITHM])
            return TokenPayload(**payload)
        except jwt.PyJWTError:
            return None

    def refresh_tokens(self, refresh_token: str) -> TokenPair | None:
        """Обновить пару токенов по refresh токену (с ротацией).

        Старый refresh отзывается (blacklist): повторное использование
        отозванного токена возвращает ``None``.
        """
        payload = self.decode_token(refresh_token)
        if not payload or payload.type != "refresh":
            return None
        if payload.jti and self._store.is_refresh_revoked(payload.jti):
            return None

        user = self.get_user(payload.sub)
        if not user or not user.is_active:
            return None

        # Ротация: отзываем предъявленный refresh до выпуска нового.
        if payload.jti:
            self._store.revoke_refresh(payload.jti, user.user_id)
        access_token, new_refresh, expires_in = self.create_tokens(user)
        return TokenPair(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_in=expires_in,
        )

    def revoke_refresh_token(self, refresh_token: str) -> bool:
        """Отозвать refresh токен (blacklist)."""
        payload = self.decode_token(refresh_token)
        if not payload or payload.type != "refresh":
            return False
        if payload.jti:
            self._store.revoke_refresh(payload.jti, payload.sub)
        return True


class _ReadOnlyUsersView(dict):
    """Dict-совместимый снимок пользователей из внешнего store (для чтения)."""

    def __init__(self, store: UserStore) -> None:
        self._store_ref = store
        super().__init__({u.user_id: u for u in store.list_users()})


# Глобальный экземпляр сервиса (MVP)
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


def reset_auth_service() -> None:
    """Сбросить глобальный экземпляр (для тестов)."""
    global _auth_service
    _auth_service = None


def create_default_admin() -> None:
    """Создать администратора по умолчанию (для демо/разработки)."""
    auth = get_auth_service()
    if not auth.get_user_by_email("admin@court.local"):
        auth.create_user(
            email="admin@court.local",
            password="admin123",  # В продакшене — только через безопасную генерацию!
            full_name="Системный администратор",
            role=UserRole.ADMIN,
        )
