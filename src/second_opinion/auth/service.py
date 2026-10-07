"""Сервис аутентификации и управления токенами."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from ..config import AppConfig, load_config
from ..domain.users import LoginAttempt, TokenPair, TokenPayload, User, UserRole
from . import totp
from .passwords import (
    MAX_PASSWORD_BYTES,
    hash_password,
    verify_password,
    waste_time_like_verify,
)
from .repository import (
    InMemoryUserStore,
    JsonFileUserStore,
    PostgresUserStore,
    UserStore,
)

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

MIN_JWT_SECRET_BYTES = 32
MIN_ADMIN_PASSWORD_LENGTH = 12


def ensure_password_length(password: str, minimum: int) -> None:
    """Отклонить пароль короче порога.

    Проверка живёт отдельной функцией, потому что путей задать пароль
    несколько — создание пользователя админом, смена владельцем, сброс, —
    и пропуск хотя бы одного из них обнуляет всю политику.
    """
    if len(password) < minimum:
        raise ValueError(
            f"пароль короче {minimum} символов; это минимальная длина, "
            "заданная SO_PASSWORD_MIN_LENGTH"
        )


def resolve_jwt_secret(config: AppConfig) -> str:
    """Ключ подписи JWT: только из ``SO_JWT_SECRET`` либо эфемерный.

    Захардкоженного значения по умолчанию быть не может: зная его, кто
    угодно выпускает себе токен с ``role: admin``. Если секрет не задан,
    берётся случайный на время жизни процесса — подделать нельзя, но
    выданные токены не переживут перезапуск, о чём пишем в лог.

    ``SO_AUTH_TOKEN`` здесь намеренно не используется: общий Bearer для
    ``/api/*`` раздаётся операторам, и знание его не должно давать
    возможности подписать токен любой роли.
    """
    secret = config.jwt_secret
    if secret:
        if len(secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"SO_JWT_SECRET короче {MIN_JWT_SECRET_BYTES} байт — "
                "для HS256 это недопустимо слабый ключ "
                "(сгенерировать: python -c \"import secrets;"
                "print(secrets.token_urlsafe(48))\")"
            )
        return secret
    logger.warning(
        "SO_JWT_SECRET не задан — используется случайный ключ на время "
        "жизни процесса: все выданные токены станут недействительны после "
        "перезапуска. Для постоянной работы задайте SO_JWT_SECRET."
    )
    return secrets.token_urlsafe(48)


class PasswordExpiredError(ValueError):
    """Пароль истёк (SO_PASSWORD_MAX_AGE_DAYS) — требуется смена."""


class AccountLockedError(ValueError):
    """Вход временно заблокирован после неудачных попыток."""


class LastAdminError(ValueError):
    """Действие оставило бы систему без активного администратора."""


class MfaRequiredError(ValueError):
    """Пароль верен, но у учётной записи включён второй фактор, а код не передан."""


class MfaEnrollmentRequiredError(ValueError):
    """Администратору обязателен второй фактор, а он ещё не включён."""


class InvalidSecondFactorError(ValueError):
    """Код второго фактора неверен или уже использован."""


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
        self._secret_key = resolve_jwt_secret(self._config)
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
        self._password_min_length = cfg.password_min_length
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PostgresUserStore недоступен (%s) — временный переход на "
                    "in-memory: пользователи не переживут перезапуск",
                    exc,
                )
                return InMemoryUserStore()
        return JsonFileUserStore(cfg.store_dir / "auth")

    def create_user(
        self,
        email: str,
        password: str,
        full_name: str,
        role: UserRole = UserRole.JUDGE,
    ) -> User:
        """Создать нового пользователя."""
        ensure_password_length(password, self._password_min_length)
        if self._store.get_user_by_email(email):
            raise ValueError(f"Пользователь с email {email} уже существует")

        now = datetime.now(UTC)
        user = User(
            email=email,
            full_name=full_name,
            role=role,
            password_hash=hash_password(password),
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
            if old_hash and verify_password(new_password, old_hash):
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
        ensure_password_length(new_password, self._password_min_length)
        self._check_password_reuse(user, new_password)
        self._remember_password(user)
        user.password_hash = hash_password(new_password)
        user.password_changed_at = datetime.now(UTC)
        user.failed_login_attempts = 0
        user.locked_until = None
        self.save_user(user)

    def authenticate(self, email: str, password: str, otp: str | None = None) -> User | None:
        """Проверить учётные данные пользователя.

        При истёкшем пароле — ``PasswordExpiredError``, при активной
        блокировке — ``AccountLockedError`` (вход запрещён, но существующая
        сессия может сменить пароль). Если включён второй фактор, а код не
        передан, — ``MfaRequiredError``; неверный код считается неудачной
        попыткой наравне с неверным паролем.
        """
        user = self.get_user_by_email(email)
        if not user or not user.is_active:
            waste_time_like_verify()
            self._store.record_login(
                LoginAttempt(email=email, user_id=None, success=False, reason="not_found")
            )
            return None
        if self.is_locked(user):
            waste_time_like_verify()
            self._store.record_login(
                LoginAttempt(
                    email=email, user_id=user.user_id, success=False, reason="locked"
                )
            )
            raise AccountLockedError("Вход временно заблокирован — повторите позже")
        if not verify_password(password, user.password_hash):
            self._register_failure(user)
            self._store.record_login(
                LoginAttempt(
                    email=email, user_id=user.user_id, success=False, reason="bad_password"
                )
            )
            return None
        self._check_second_factor(user, email, otp)
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

    def _register_failure(self, user: User) -> None:
        user.failed_login_attempts += 1
        if self._login_max_attempts > 0 and user.failed_login_attempts >= self._login_max_attempts:
            user.locked_until = datetime.now(UTC) + timedelta(minutes=self._login_lockout_minutes)
        self.save_user(user)

    def _check_second_factor(self, user: User, email: str, otp: str | None) -> None:
        """Проверить код после верного пароля; счётчик неудач сбрасывается только после него."""
        if not user.totp_enabled:
            if self._config.admin_mfa_required and user.role is UserRole.ADMIN:
                self._store.record_login(
                    LoginAttempt(
                        email=email, user_id=user.user_id, success=False, reason="mfa_enrollment"
                    )
                )
                raise MfaEnrollmentRequiredError(
                    "Для администратора обязателен второй фактор: попросите оператора "
                    "выполнить `second-opinion mfa-setup`"
                )
            return
        if not otp:
            self._store.record_login(
                LoginAttempt(
                    email=email, user_id=user.user_id, success=False, reason="mfa_required"
                )
            )
            raise MfaRequiredError("Требуется код из приложения-аутентификатора")
        step = totp.verify(user.totp_secret, otp, after_step=user.totp_last_step)
        if step is None:
            self._register_failure(user)
            self._store.record_login(
                LoginAttempt(email=email, user_id=user.user_id, success=False, reason="bad_otp")
            )
            raise InvalidSecondFactorError
        user.totp_last_step = step

    def mfa_begin_setup(self, user: User) -> tuple[str, str]:
        """Выпустить секрет для привязки приложения: действует после ``mfa_enable``."""
        if user.totp_enabled:
            raise ValueError("Второй фактор уже включён")
        secret = totp.generate_secret()
        user.totp_pending_secret = secret
        self.save_user(user)
        return secret, totp.provisioning_uri(secret, user.email)

    def mfa_enable(self, user: User, code: str) -> None:
        """Включить второй фактор, подтвердив его кодом из приложения."""
        if user.totp_enabled:
            raise ValueError("Второй фактор уже включён")
        if not user.totp_pending_secret:
            raise ValueError("Сначала запросите секрет: POST /auth/mfa/setup")
        step = totp.verify(user.totp_pending_secret, code)
        if step is None:
            raise ValueError("Код не подошёл: проверьте время на устройстве и повторите")
        user.totp_secret = user.totp_pending_secret
        user.totp_pending_secret = ""
        user.totp_enabled = True
        user.totp_last_step = step
        self.save_user(user)

    def mfa_disable(self, user: User, password: str, code: str) -> None:
        """Отключить второй фактор: нужны пароль и действующий код."""
        if not user.totp_enabled:
            raise ValueError("Второй фактор не включён")
        if self._config.admin_mfa_required and user.role is UserRole.ADMIN:
            raise ValueError("Для администратора второй фактор обязателен и не отключается")
        if not verify_password(password, user.password_hash):
            raise ValueError("Пароль неверен")
        step = totp.verify(user.totp_secret, code, after_step=user.totp_last_step)
        if step is None:
            raise ValueError("Код не подошёл")
        self.mfa_reset(user.user_id)

    def mfa_reset(self, user_id: str) -> User | None:
        """Снять второй фактор без проверки кода: действие администратора или оператора."""
        user = self.get_user(user_id)
        if not user:
            return None
        user.totp_enabled = False
        user.totp_secret = ""
        user.totp_pending_secret = ""
        user.totp_last_step = 0
        self.save_user(user)
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
        if not verify_password(current_password, user.password_hash):
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

    def _active_admin_ids(self) -> set[str]:
        return {
            u.user_id
            for u in self.list_users()
            if u.role is UserRole.ADMIN and u.is_active
        }

    def ensure_admin_remains(
        self,
        user_id: str,
        *,
        role: UserRole | None = None,
        is_active: bool | None = None,
    ) -> None:
        """Проверить, что после изменения останется активный администратор.

        Снятие роли, деактивация и удаление последнего администратора
        делают управление учётными записями недоступным никому: завести
        нового админа через API уже нельзя, останется только бутстрап с
        остановкой сервиса. Удаление здесь равнозначно деактивации.
        """
        admins = self._active_admin_ids()
        if user_id not in admins:
            return
        stays_admin = (role is None or role is UserRole.ADMIN) and is_active is not False
        if stays_admin or admins != {user_id}:
            return
        raise LastAdminError(
            "это единственный активный администратор — сначала назначьте другого"
        )

    def delete_user(self, user_id: str) -> bool:
        """Удалить пользователя; вернуть True, если он существовал."""
        return self._store.delete_user(user_id)

    def list_login_attempts(self, limit: int = 100) -> list[LoginAttempt]:
        """Аудит входов (новые первыми)."""
        return self._store.list_login_attempts(limit=limit)

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


class AdminAlreadyExistsError(ValueError):
    """В системе уже есть активный администратор."""


def validate_admin_password(password: str) -> None:
    """Проверить пароль первого администратора.

    Требования жёстче, чем у обычной учётки (``min_length=8`` в API):
    это единственная запись, через которую заводятся все остальные.
    """
    length = len(password.encode("utf-8"))
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise ValueError(
            f"пароль администратора короче {MIN_ADMIN_PASSWORD_LENGTH} символов"
        )
    if length > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"пароль длиннее {MAX_PASSWORD_BYTES} байт — bcrypt отбросит остаток"
        )


def bootstrap_admin(
    email: str,
    password: str,
    full_name: str = "Системный администратор",
) -> User:
    """Завести первого администратора.

    Без этого личный кабинет недостижим: ``POST /auth/users`` сам требует
    роль ``admin``, то есть первую учётку через API не создать. Пароля по
    умолчанию нет намеренно — он задаётся оператором.

    Повторный вызов при уже существующем активном администраторе
    отклоняется: заводить следующих админов следует через API, где
    действие попадает в аудит.
    """
    auth = get_auth_service()
    existing = [u for u in auth.list_users() if u.role is UserRole.ADMIN and u.is_active]
    if existing:
        raise AdminAlreadyExistsError(
            "активный администратор уже существует "
            f"({existing[0].email}) — создавайте учётки через POST /auth/users"
        )
    validate_admin_password(password)
    return auth.create_user(
        email=email,
        password=password,
        full_name=full_name,
        role=UserRole.ADMIN,
    )
