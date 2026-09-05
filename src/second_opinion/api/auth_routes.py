"""API эндпоинты аутентификации."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from ..auth.service import (
    AccountLockedError,
    AuthService,
    PasswordExpiredError,
    get_auth_service,
)
from ..domain.users import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])

AuthSvc = Annotated[AuthService, Depends(get_auth_service)]


def get_current_user(
    request: Request,
    auth_service: AuthSvc,
) -> User:
    """Получить текущего пользователя из токена."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
        )

    token = auth_header.split(" ", 1)[1]
    payload = auth_service.decode_token(token)
    if not payload or payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или истёкший токен",
        )

    user = auth_service.get_user(payload.sub)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не найден или неактивен",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


class LoginResponse(BaseModel):
    """Ответ при успешном входе."""

    user: dict
    tokens: dict


class MeResponse(BaseModel):
    """Информация о текущем пользователе."""

    user_id: str
    email: str
    full_name: str
    role: str
    is_active: bool


class LoginRequest(BaseModel):
    """Запрос на вход."""

    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    """Запрос на обновление токена."""

    refresh_token: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """Запрос на смену пароля."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=72)


class LogoutPayload(BaseModel):
    """Тело выхода: опционально отозвать refresh токен."""

    refresh_token: str | None = None


class CreateUserRequest(BaseModel):
    """Запрос на создание пользователя (admin)."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    full_name: str = Field(min_length=1)
    role: UserRole = UserRole.JUDGE


def require_role(*allowed_roles: UserRole):
    """Зависимость FastAPI: пользователь должен иметь одну из указанных ролей."""

    def checker(user: CurrentUser) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав доступа",
            )
        return user

    return checker


@router.post("/login", response_model=dict)
async def login(
    payload: LoginRequest,
    auth_service: AuthSvc,
) -> dict:
    """Вход в систему (email + пароль)."""
    try:
        user = auth_service.authenticate(payload.email, payload.password)
    except PasswordExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    auth_service.update_last_login(user.user_id)
    access_token, refresh_token, _expires = auth_service.create_tokens(user)

    return {
        "user": {
            "user_id": user.user_id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
        },
        "tokens": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": 30 * 60,
        },
    }


@router.post("/refresh", response_model=dict)
async def refresh_token(
    payload: RefreshRequest,
    auth_service: AuthSvc,
) -> dict:
    """Обновить access токен по refresh токену."""
    tokens = auth_service.refresh_tokens(payload.refresh_token)
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или истёкший refresh токен",
        )

    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_in": 30 * 60,
    }


@router.get("/me", response_model=dict)
async def me(user: CurrentUser) -> dict:
    """Получить информацию о текущем пользователе."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
    }


@router.post("/change-password", response_model=dict)
async def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    auth_service: AuthSvc,
) -> dict:
    """Смена пароля текущим пользователем."""
    try:
        auth_service.change_password(user, payload.current_password, payload.new_password)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return {"message": "Пароль успешно изменён"}


@router.post("/logout")
async def logout(
    _user: CurrentUser,
    auth_service: AuthSvc,
    payload: LogoutPayload | None = None,
) -> dict:
    """Выход из системы.

    Без тела — как раньше (клиент удаляет токены сам). Если передан
    ``refresh_token`` — он отзывается (blacklist, ротация его инвалидирует).
    """
    body_token = payload.refresh_token if payload else None
    if body_token:
        auth_service.revoke_refresh_token(body_token)
    return {"message": "Успешный выход"}


# --- Управление пользователями (только admin) ----------------------------------


def _user_to_dict(u: User) -> dict:
    return {
        "user_id": u.user_id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role.value,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat(),
        "last_login": u.last_login.isoformat() if u.last_login else None,
    }


@router.get("/users", response_model=list[dict])
async def list_users(
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> list[dict]:
    """Список всех пользователей (только admin)."""
    return [_user_to_dict(u) for u in auth_service.list_users()]


@router.post("/users", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> dict:
    """Создать нового пользователя (только admin)."""
    try:
        user = auth_service.create_user(
            email=payload.email,
            password=payload.password,
            full_name=payload.full_name,
            role=payload.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "user_id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
    }


class UpdateUserRequest(BaseModel):
    """Запрос на обновление пользователя (admin)."""

    full_name: str | None = Field(default=None, min_length=1)
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=72)


@router.patch("/users/{user_id}", response_model=dict)
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> dict:
    """Обновить пользователя (только admin)."""
    user_obj = auth_service.get_user(user_id)
    if not user_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    if payload.full_name is not None:
        user_obj.full_name = payload.full_name
    if payload.role is not None:
        user_obj.role = payload.role
    if payload.is_active is not None:
        user_obj.is_active = payload.is_active
    if payload.password:
        try:
            updated = auth_service.set_password(user_id, payload.password)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        # set_password уже сохранил; подтянем свежий объект
        user_obj = updated if updated else user_obj
        # прочие поля могли измениться выше — сохраняем их тоже
        if payload.full_name is not None or payload.role is not None or payload.is_active is not None:
            auth_service.save_user(user_obj)
        return _user_to_dict(user_obj)

    auth_service.save_user(user_obj)
    return _user_to_dict(user_obj)


@router.get("/audit", response_model=list[dict])
async def login_audit(
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
    limit: int = 100,
) -> list[dict]:
    """Аудит входов (только admin, новые первыми, без паролей)."""
    return [a.model_dump(mode="json") for a in auth_service.list_login_attempts(limit=limit)]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> None:
    """Удалить пользователя (только admin)."""
    if not auth_service.delete_user(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
