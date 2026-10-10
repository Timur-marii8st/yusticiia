"""Хеширование паролей поверх bcrypt.

Напрямую поверх ``bcrypt``, без passlib: тот не поддерживается с 2020 года
и ломается на bcrypt 5.x. Формат хэша стандартный (``$2a$``/``$2b$``).
"""

from __future__ import annotations

import bcrypt

MAX_PASSWORD_BYTES = 72

_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-constant-time", bcrypt.gensalt())


class PasswordTooLongError(ValueError):
    """Пароль длиннее предела bcrypt."""


def _encode(password: str) -> bytes:
    """Пароль в байтах; слишком длинный отвергается, а не усекается молча."""
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(
            f"пароль длиннее {MAX_PASSWORD_BYTES} байт — bcrypt отбросил бы остаток"
        )
    return raw


def hash_password(password: str) -> str:
    """Вернуть bcrypt-хэш пароля."""
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Проверить пароль против хэша. Битый хэш — «не совпало», не сбой."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_encode(password), hashed.encode("ascii"))
    except (PasswordTooLongError, ValueError):
        return False


def waste_time_like_verify() -> None:
    """Потратить время одной проверки пароля.

    Вызывается там, где пользователь не найден: иначе время ответа
    отличает существующий адрес от несуществующего.
    """
    bcrypt.checkpw(b"dummy-password-for-constant-time", _DUMMY_HASH)
