"""Проверка связки «адрес привязки ↔ авторизация» перед запуском сервера."""

from __future__ import annotations

import sys

from ..config import load_config


def is_loopback(host: str) -> bool:
    """Привязка видна только с самой машины?"""
    import ipaddress

    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
def check_bind_safety(host: str) -> int:
    """Проверить связку «адрес привязки ↔ авторизация».

    При пустом ``SO_AUTH_TOKEN`` весь ``/api/*`` открыт: на loopback это
    осознанный режим демонстрации, на внешнем адресе — публичный доступ к
    текстам судебных актов. Вернуть код выхода (0 — можно продолжать).
    """
    config = load_config()
    from ..api.auth_mode import AUTH_MODE_ACCOUNTS, resolve_auth_mode

    mode = resolve_auth_mode(config.auth_mode, config.auth_token)
    if mode == AUTH_MODE_ACCOUNTS or config.auth_token or is_loopback(host):
        return 0
    message = (
        f"привязка к {host} без SO_AUTH_TOKEN: /api/* будет доступен из сети "
        "без авторизации, включая тексты документов"
    )
    if config.require_auth:
        print(f"ОШИБКА: {message}", file=sys.stderr)
        print("Задайте SO_AUTH_TOKEN либо снимите SO_REQUIRE_AUTH.", file=sys.stderr)
        return 2
    print(f"ВНИМАНИЕ: {message}.", file=sys.stderr)
    print(
        "Задайте SO_AUTH_TOKEN либо SO_AUTH_MODE=accounts (доступ только по "
        "личным учётным записям); для отказа от запуска без защиты — "
        "SO_REQUIRE_AUTH=1.",
        file=sys.stderr,
    )
    return 0
