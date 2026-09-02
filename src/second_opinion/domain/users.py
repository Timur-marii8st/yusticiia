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
