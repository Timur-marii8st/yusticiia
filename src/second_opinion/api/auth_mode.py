"""Режимы авторизации API (ФТ-40, ADR-008). Описание — в docs/SECURITY.md."""

from __future__ import annotations

AUTH_MODE_OPEN = "open"
AUTH_MODE_SHARED = "shared"
AUTH_MODE_ACCOUNTS = "accounts"

AUTH_MODES = (AUTH_MODE_OPEN, AUTH_MODE_SHARED, AUTH_MODE_ACCOUNTS)


def resolve_auth_mode(configured: str, auth_token: str) -> str:
    """Определить режим авторизации.

    Пустое значение сохраняет прежнее поведение развёртываний, где о режиме
    никто не объявлял: есть общий секрет — принимаются оба способа, нет —
    доступ открыт. Молчаливое ужесточение сломало бы работающие установки,
    молчаливое ослабление — открыло бы их.
    """
    mode = (configured or "").strip().lower()
    if mode in AUTH_MODES:
        return mode
    if mode:
        raise ValueError(
            f"SO_AUTH_MODE: ожидается одно из {', '.join(AUTH_MODES)} "
            f"(получено: {mode!r})"
        )
    return AUTH_MODE_SHARED if auth_token else AUTH_MODE_OPEN
