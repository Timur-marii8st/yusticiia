"""Хеширование паролей поверх bcrypt.

Раньше использовался ``passlib.CryptContext``. Passlib не поддерживается
с 2020 года и несовместим с bcrypt 5.x: определение бэкенда падает на
проверке 72-байтного предела, из-за чего приходилось держать пин
``bcrypt<5``, а на каждом старте в stderr печатался трейсбек
``module 'bcrypt' has no attribute '__about__'``. Прямой вызов bcrypt
снимает и то, и другое; формат хэша тот же (``$2b$``), поэтому ранее
сохранённые пароли проверяются без миграции.
"""

from __future__ import annotations

import bcrypt

#: bcrypt учитывает только первые 72 байта пароля. Молчаливое усечение
#: означало бы, что длинный пароль проверяется не целиком, — отклоняем явно.
MAX_PASSWORD_BYTES = 72

#: Хэш заведомо несуществующего пароля: нужен, чтобы проверка при
#: неизвестном пользователе стоила столько же, сколько при известном.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-constant-time", bcrypt.gensalt())


class PasswordTooLongError(ValueError):
    """Пароль длиннее предела bcrypt."""


def _encode(password: str) -> bytes:
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
    """Проверить пароль против хэша; на любом повреждённом хэше — False."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_encode(password), hashed.encode("ascii"))
    except (PasswordTooLongError, ValueError):
        # Слишком длинный пароль и битый хэш — оба «не совпало», а не сбой.
        return False


def waste_time_like_verify() -> None:
    """Потратить столько же времени, сколько занимает проверка пароля.

    Вызывается там, где пользователь не найден: без этого время ответа
    отличает существующий адрес от несуществующего.
    """
    bcrypt.checkpw(b"dummy-password-for-constant-time", _DUMMY_HASH)
