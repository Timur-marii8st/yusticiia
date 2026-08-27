"""Сервис аутентификации и управления токенами."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from ..config import load_config
from ..domain.users import TokenPair, TokenPayload, User, UserRole

# Контекст для хеширования паролей (bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Алгоритм и параметры JWT
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class AuthService:
    """Сервис аутентификации и управления токенами."""

    def __init__(self):
        self._config = load_config()
        self._secret_key = self._config.auth_token or "dev-secret-change-in-production"
        self._users: dict[str, User] = {}  # In-memory storage (MVP)

    # --- Управление пользователями ---

    def create_user(
        self,
        email: str,
        password: str,
        full_name: str,
        role: UserRole = UserRole.JUDGE,
    ) -> User:
        """Создать нового пользователя."""
        if any(u.email == email for u in self._users.values()):
            raise ValueError(f"Пользователь с email {email} уже существует")

        user = User(
            email=email,
            full_name=full_name,
            role=role,
            password_hash=pwd_context.hash(password),
        )
        self._users[user.user_id] = user
        return user

    def get_user(self, user_id: str) -> User | None:
        """Получить пользователя по ID."""
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        """Получить пользователя по email."""
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def authenticate(self, email: str, password: str) -> User | None:
        """Проверить учётные данные пользователя."""
        user = self.get_user_by_email(email)
        if not user or not user.is_active:
            return None
        if not pwd_context.verify(password, user.password_hash):
            return None
        return user

    def update_last_login(self, user_id: str) -> None:
        """Обновить время последнего входа."""
        user = self.get_user(user_id)
        if user:
            user.last_login = datetime.now(UTC)
            user.updated_at = datetime.now(UTC)

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
        """Обновить пару токенов по refresh токену."""
        payload = self.decode_token(refresh_token)
        if not payload or payload.type != "refresh":
            return None

        user = self.get_user(payload.sub)
        if not user or not user.is_active:
            return None

        access_token, refresh_token, expires_in = self.create_tokens(user)
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    def revoke_refresh_token(self, refresh_token: str) -> bool:
        """Отозвать refresh токен (добавить в blacklist - упрощённо)."""
        # В полноценной реализации нужно хранить отозванные токены в Redis/БД
        return True


# Глобальный экземпляр сервиса (MVP)
_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service


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
