"""API эндпоинты аутентификации."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from ..auth.service import (
    AccountLockedError,
    AuthService,
    InvalidSecondFactorError,
    LastAdminError,
    MfaEnrollmentRequiredError,
    MfaRequiredError,
    PasswordExpiredError,
    get_auth_service,
)
from ..config import load_config
from ..domain.users import User, UserRole
from .schemas import (
    LoginAttemptResponse,
    LoginResponse,
    MeResponse,
    MessageResponse,
    MfaSetupResponse,
    RefreshResponse,
    UserResponse,
    UserSummary,
)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "so_refresh"
REFRESH_COOKIE_PATH = "/auth"
CSRF_HEADER = "x-so-csrf"

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


def _client_key(request: Request) -> str:
    """Адрес клиента. За обратным прокси все запросы придут с одного
    адреса — там лимит должен стоять на самом прокси; доверять
    ``X-Forwarded-For`` без списка доверенных прокси нельзя, его
    подделывает кто угодно."""
    return request.client.host if request.client else "unknown"


def _enforce_login_rate_limit(request: Request) -> None:
    """Отклонить вход, если с этого клиента их слишком много.

    Блокировка учётной записи считает неудачи по пользователю и потому
    молчит при переборе по списку адресов: каждая попытка приходится на
    новый email.
    """
    limiter = getattr(request.app.state, "login_rate_limiter", None)
    if limiter is None:
        return
    retry_after = limiter.retry_after(_client_key(request))
    if retry_after is None:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Слишком много попыток входа — повторите позже",
        headers={"Retry-After": str(retry_after)},
    )


class LoginRequest(BaseModel):
    """Запрос на вход."""

    email: EmailStr
    password: str = Field(min_length=1)
    otp: str | None = Field(
        default=None,
        max_length=16,
        description="код из приложения-аутентификатора, если включён второй фактор",
    )
    cookie: bool = Field(
        default=False,
        description="выдать refresh-токен в HttpOnly cookie, а не в теле ответа",
    )


class RefreshRequest(BaseModel):
    """Запрос на обновление токена: без тела токен берётся из cookie."""

    refresh_token: str | None = Field(default=None, min_length=1)


class MfaCodeRequest(BaseModel):
    """Код из приложения-аутентификатора."""

    code: str = Field(min_length=1, max_length=16)


class MfaDisableRequest(BaseModel):
    """Отключение второго фактора: пароль и действующий код."""

    password: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=16)


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


def _set_refresh_cookie(response: Response, request: Request, token: str) -> None:
    """Refresh-токен в cookie, недоступной скриптам страницы.

    ``Path=/auth``: браузер присылает её только на ``/auth/*``. ``SameSite=Strict``
    не пускает её в межсайтовые запросы. Без ``Max-Age``: cookie живёт до закрытия
    браузера, на общем рабочем месте судьи вход не переживёт сессию.
    """
    secure = request.url.scheme == "https" or load_config().cookie_secure
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


BAD_CREDENTIALS = (
    "Неверный email или пароль. После нескольких неудачных попыток вход "
    "временно блокируется."
)
MFA_HEADERS = {"X-MFA-Required": "1"}


@router.post("/login")
async def login(
    payload: LoginRequest,
    auth_service: AuthSvc,
    request: Request,
    response: Response,
) -> LoginResponse:
    """Вход в систему (email + пароль)."""
    _enforce_login_rate_limit(request)
    try:
        user = auth_service.authenticate(payload.email, payload.password, payload.otp)
    except MfaRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers=MFA_HEADERS,
        ) from exc
    except InvalidSecondFactorError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный код. После нескольких неудачных попыток вход временно блокируется.",
            headers=MFA_HEADERS,
        ) from exc
    except MfaEnrollmentRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except PasswordExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=BAD_CREDENTIALS,
        ) from exc
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=BAD_CREDENTIALS,
        )

    auth_service.update_last_login(user.user_id)
    access_token, refresh_token, _expires = auth_service.create_tokens(user)
    if payload.cookie:
        _set_refresh_cookie(response, request, refresh_token)

    return {
        "user": {
            "user_id": user.user_id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
        },
        "tokens": {
            "access_token": access_token,
            "refresh_token": None if payload.cookie else refresh_token,
            "expires_in": 30 * 60,
        },
    }


@router.post("/refresh")
async def refresh_token(
    auth_service: AuthSvc,
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
) -> RefreshResponse:
    """Обновить access токен по refresh токену.

    Токен берётся из тела; если тела нет, из cookie ``so_refresh`` и тогда
    обязателен заголовок ``X-SO-CSRF``: его нельзя добавить к межсайтовому запросу
    без предварительного разрешения сервера.
    """
    body_token = payload.refresh_token if payload else None
    if payload is not None and body_token is None:
        raise HTTPException(
            status_code=422,
            detail="refresh_token: обязательное поле; без тела токен берётся из cookie",
        )
    from_cookie = body_token is None
    token = request.cookies.get(REFRESH_COOKIE) if body_token is None else body_token
    if from_cookie and token and not request.headers.get(CSRF_HEADER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Для обновления по cookie нужен заголовок X-SO-CSRF",
        )
    tokens = auth_service.refresh_tokens(token) if token else None
    if not tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или истёкший refresh токен",
        )
    if from_cookie:
        _set_refresh_cookie(response, request, tokens.refresh_token)

    return {
        "access_token": tokens.access_token,
        "refresh_token": None if from_cookie else tokens.refresh_token,
        "expires_in": 30 * 60,
    }


@router.get("/me")
async def me(user: CurrentUser) -> MeResponse:
    """Получить информацию о текущем пользователе."""
    return {
        "user_id": user.user_id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value,
        "is_active": user.is_active,
        "mfa_enabled": user.totp_enabled,
    }


@router.post("/mfa/setup")
async def mfa_setup(user: CurrentUser, auth_service: AuthSvc) -> MfaSetupResponse:
    """Выпустить секрет второго фактора; действует после подтверждения кодом."""
    try:
        secret, uri = auth_service.mfa_begin_setup(user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/enable")
async def mfa_enable(
    payload: MfaCodeRequest, user: CurrentUser, auth_service: AuthSvc
) -> MessageResponse:
    """Включить второй фактор, подтвердив секрет кодом из приложения."""
    try:
        auth_service.mfa_enable(user, payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"message": "Второй фактор включён"}


@router.post("/mfa/disable")
async def mfa_disable(
    payload: MfaDisableRequest, user: CurrentUser, auth_service: AuthSvc
) -> MessageResponse:
    """Отключить второй фактор: пароль и действующий код."""
    try:
        auth_service.mfa_disable(user, payload.password, payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"message": "Второй фактор отключён"}


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    auth_service: AuthSvc,
) -> MessageResponse:
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
    request: Request,
    response: Response,
    payload: LogoutPayload | None = None,
) -> MessageResponse:
    """Выход из системы.

    Без тела — как раньше (клиент удаляет токены сам). Если передан
    ``refresh_token`` или есть cookie ``so_refresh`` — токен отзывается
    (blacklist, ротация его инвалидирует), cookie удаляется.
    """
    body_token = payload.refresh_token if payload else None
    for token in (body_token, request.cookies.get(REFRESH_COOKIE)):
        if token:
            auth_service.revoke_refresh_token(token)
    _clear_refresh_cookie(response)
    return {"message": "Успешный выход"}



def _user_to_dict(u: User) -> dict:
    return {
        "user_id": u.user_id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role.value,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat(),
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "mfa_enabled": u.totp_enabled,
    }


@router.get("/users")
async def list_users(
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> list[UserResponse]:
    """Список всех пользователей (только admin)."""
    return [_user_to_dict(u) for u in auth_service.list_users()]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> UserSummary:
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


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> UserResponse:
    """Обновить пользователя (только admin)."""
    user_obj = auth_service.get_user(user_id)
    if not user_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    try:
        auth_service.ensure_admin_remains(
            user_id, role=payload.role, is_active=payload.is_active
        )
    except LastAdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if payload.password:
        try:
            refreshed = auth_service.set_password(user_id, payload.password)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if refreshed is not None:
            user_obj = refreshed

    if payload.full_name is not None:
        user_obj.full_name = payload.full_name
    if payload.role is not None:
        user_obj.role = payload.role
    if payload.is_active is not None:
        user_obj.is_active = payload.is_active

    auth_service.save_user(user_obj)
    return _user_to_dict(user_obj)


@router.post("/users/{user_id}/mfa/reset")
async def reset_user_mfa(
    user_id: str,
    admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> MessageResponse:
    """Снять второй фактор с чужой учётной записи (потерян телефон); свой так не снимается."""
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Свой второй фактор отключается кодом: POST /auth/mfa/disable",
        )
    if auth_service.mfa_reset(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    return {"message": "Второй фактор снят"}


@router.get("/audit")
async def login_audit(
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
    limit: int = 100,
) -> list[LoginAttemptResponse]:
    """Аудит входов (только admin, новые первыми, без паролей)."""
    return [a.model_dump(mode="json") for a in auth_service.list_login_attempts(limit=limit)]


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    _admin: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    auth_service: AuthSvc,
) -> None:
    """Удалить пользователя (только admin)."""
    if auth_service.get_user(user_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    try:
        auth_service.ensure_admin_remains(user_id, is_active=False)
    except LastAdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not auth_service.delete_user(user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
