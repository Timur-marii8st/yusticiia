"""Одноразовые коды по времени (TOTP, RFC 6238) на стандартной библиотеке.

Совместимо с приложениями-аутентификаторами (SHA-1, шесть цифр, шаг 30 секунд).
Внешних сервисов и сторонних зависимостей нет: закрытый контур суда не должен
ходить за вторым фактором в интернет.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
SECRET_BYTES = 20
ISSUER = "Второе мнение"


def generate_secret() -> str:
    """Новый секрет: 160 бит случайности в base32 без дополнения."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**digits).zfill(digits)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(
    secret: str,
    code: str,
    *,
    now: float | None = None,
    window: int = 1,
    after_step: int = 0,
) -> int | None:
    """Номер шага, на который пришёлся верный код, или ``None``.

    ``window`` допускает расхождение часов на шаг в обе стороны. Шаги не позже
    ``after_step`` отклоняются: код, уже принятый для входа, повторно не работает.
    """
    candidate = "".join(code.split())
    if len(candidate) != DIGITS or not (candidate.isascii() and candidate.isdigit()):
        return None
    step = current_step(now)
    matched: int | None = None
    for offset in range(-window, window + 1):
        counter = step + offset
        if counter <= after_step:
            continue
        if hmac.compare_digest(hotp(secret, counter), candidate):
            matched = counter
    return matched


def provisioning_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    """Адрес ``otpauth://`` для импорта секрета в приложение-аутентификатор."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
