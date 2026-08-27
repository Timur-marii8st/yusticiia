"""API эндпоинты аутентификации."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth.service import AuthService, get_auth_service
from ..domain.users import User, UserRole

router = APIRouter(prefix="/auth", tags=["auth"])


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


def get_current_user(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> User:
    """Получить текущего пользователя из токена."""
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
        )

    token = auth_header.split(" ")[1]
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


def require_role(*allowed_roles: UserRole):
    """Декоратор для проверки роли пользователя."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав доступа",
            )
        return user

    return checker


@router.post("/login", response_model=dict)
async def login(
    credentials: dict,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Вход в систему (email + пароль)."""
    email = credentials.get("email")
    password = credentials.get("password")

    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Требуются email и пароль",
        )

    user = auth_service.authenticate(credentials["email"], credentials["password"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    auth_service.update_last_login(user.user_id)
    access_token, refresh_token, expires_in = auth_service.create_tokens(user)

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
            "expires_in": 30 * 60,  # секунды
        },
    }


@router.post("/refresh", response_model=dict)
async def refresh_token(
    credentials: dict,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """Обновить access токен по refresh токену."""
    refresh_token = credentials.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Требуется refresh_token",
        )

    tokens = auth_service.refresh_tokens(refresh_token)
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
async def me(user: User = Depends(get_current_user)) -> dict:
    """Получить информацию о текущем пользователе."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
    }


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
) -> dict:
    """Выход из системы (клиент должен удалить токены)."""
    # В более полной реализации здесь можно добавить токен в blacklist
    return {"message": "Успешный выход"}


# Эндпоинты для управления пользователями (только admin)

@router.get("/users", response_model=list[dict])
async def list_users(
    user: User = Depends(require_role(UserRole.ADMIN)),
    auth_service=Depends(get_auth_service),
) -> list[dict]:
    """Список всех пользователей (только admin)."""
    users = list(auth_service._users.values())
    return [
        {
            "user_id": u.user_id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role.value,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat(),
            "last_login": u.last_login.isoformat() if u.last_login else None,
        }
        for u in users
    ]


@router.post("/users", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: dict,
    user: User = Depends(require_role(UserRole.ADMIN)),
    auth_service=Depends(get_auth_service),
) -> dict:
    """Создать нового пользователя (только admin)."""
    try:
        user = auth_service.create_user(
            email=credentials["email"],
            password=credentials["password"],
            full_name=credentials["full_name"],
            role=UserRole(credentials.get("role", "judge")),
        )
        return {
            "user_id": user.user_id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/users/{user_id}", response_model=dict)
async def update_user(
    user_id: str,
    user_data: dict,
    user: User = Depends(require_role(UserRole.ADMIN)),
    auth_service=Depends(get_auth_service),
) -> dict:
    """Обновить пользователя (только admin)."""
    user_obj = auth_service.get_user(user_id)
    if not user_obj:
        raise HTTPException(404, "Пользователь не найден")

    # Обновляем поля
    if "full_name" in user_data:
        user_obj.full_name = user_data["full_name"]
    if "role" in user_data:
        user_obj.role = UserRole(user_data["role"])
    if "is_active" in user_data:
        user_obj.is_active = user_data["is_active"]
    if "password" in user_data and user_data["password"]:
        from ...auth.service import pwd_context
        user_obj.password_hash = pwd_context.hash(user_data["password"])

    user_obj.updated_at = datetime.now(UTC)
    return {
        "user_id": user_obj.user_id,
        "email": user_obj.email,
        "full_name": user_obj.full_name,
        "role": user_obj.role.value,
        "is_active": user_obj.is_active,
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    user: User = Depends(require_role(UserRole.ADMIN)),
    auth_service=Depends(get_auth_service),
) -> None:
    """Удалить пользователя (только admin)."""
    if not auth_service.delete_user(user_id):
        raise HTTPException(404, "Пользователь не найден")
