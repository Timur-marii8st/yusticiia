"""Доменные модели пользователей и аутентификации."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field


class UserRole(StrEnum):
    """Роли пользователей системы."""

    JUDGE = "judge"      # Судья - полный доступ к анализам
    CLERK = "clerk"      # Судебный пристав/секретарь - создание/редактирование документов
    ADMIN = "admin"      # Администратор - управление пользователями, настройки системы


class User(BaseModel):
    """Пользователь системы."""

    user_id: str = Field(default_factory=lambda: uuid4().hex)
    email: EmailStr
    full_name: str
    role: UserRole = UserRole.JUDGE
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_login: datetime | None = None
    #: Момент последней смены пароля (для истечения по SO_PASSWORD_MAX_AGE_DAYS).
    password_changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: Хэши предыдущих паролей (глубина — SO_PASSWORD_HISTORY_DEPTH).
    password_history: list[str] = Field(default_factory=list)
    #: Счётчик последовательных неудачных входов (сбрасывается успехом).
    failed_login_attempts: int = 0
    #: Вход заблокирован до момента (SO_LOGIN_MAX_ATTEMPTS / SO_LOGIN_LOCKOUT_MINUTES).
    locked_until: datetime | None = None

    # Хэш пароля (не хранится в открытом виде)
    password_hash: str = ""


class TokenPayload(BaseModel):
    """Полезная нагрузка JWT токена."""

    sub: str  # user_id
    email: EmailStr
    role: UserRole
    exp: datetime
    iat: datetime
    type: str = "access"  # "access" или "refresh"
    #: Уникальный идентификатор токена (для ротации/blacklist refresh).
    jti: str = Field(default_factory=lambda: uuid4().hex)


class TokenPair(BaseModel):
    """Пара токенов доступа и обновления."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # секунды до истечения access token


class LoginRequest(BaseModel):
    """Запрос на вход в систему."""

    email: EmailStr
    password: str


class TokenRefreshRequest(BaseModel):
    """Запрос на обновление токена."""

    refresh_token: str


class UserProfile(BaseModel):
    """Профиль пользователя (без пароля)."""

    user_id: str
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login: datetime | None = None


class LoginAttempt(BaseModel):
    """Запись аудита входов (без паролей)."""

    attempt_id: str = Field(default_factory=lambda: uuid4().hex)
    email: EmailStr
    user_id: str | None = None
    success: bool = False
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
