"""Доменные модели пользователей и аутентификации."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field


class UserRole(StrEnum):
    """Роли пользователей системы."""

    JUDGE = "judge"
    CLERK = "clerk"
    ADMIN = "admin"


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
    password_changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    password_history: list[str] = Field(default_factory=list)
    failed_login_attempts: int = 0
    locked_until: datetime | None = None

    password_hash: str = ""


class TokenPayload(BaseModel):
    """Полезная нагрузка JWT токена."""

    sub: str
    email: EmailStr
    role: UserRole
    exp: datetime
    iat: datetime
    type: str = "access"
    jti: str = Field(default_factory=lambda: uuid4().hex)


class TokenPair(BaseModel):
    """Пара токенов доступа и обновления."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    """Запрос на вход в систему."""

    email: EmailStr
    password: str


class LoginAttempt(BaseModel):
    """Запись аудита входов (без паролей)."""

    attempt_id: str = Field(default_factory=lambda: uuid4().hex)
    email: EmailStr
    user_id: str | None = None
    success: bool = False
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
