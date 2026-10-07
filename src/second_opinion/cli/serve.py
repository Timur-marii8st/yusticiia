"""Запуск веб-приложения, заведение администратора и его второй фактор."""

from __future__ import annotations

import sys

from ..config import load_config
from .bind import check_bind_safety


def create_admin(email: str, full_name: str, password: str | None) -> int:
    """Завести первого администратора (бутстрап личного кабинета)."""
    import getpass
    import os

    from ..auth.service import AdminAlreadyExistsError, bootstrap_admin

    password = password or os.environ.get("SO_ADMIN_PASSWORD") or ""
    if not password:
        if not sys.stdin.isatty():
            print(
                "ОШИБКА: пароль не задан. Передайте --password, задайте "
                "SO_ADMIN_PASSWORD или запустите в интерактивном терминале.",
                file=sys.stderr,
            )
            return 2
        password = getpass.getpass("Пароль администратора: ")
        if password != getpass.getpass("Повторите пароль: "):
            print("ОШИБКА: пароли не совпадают.", file=sys.stderr)
            return 2

    try:
        user = bootstrap_admin(email=email, password=password, full_name=full_name)
    except AdminAlreadyExistsError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    config = load_config()
    print(f"Администратор создан: {user.email} (user_id={user.user_id})")
    print(f"Хранилище: {config.storage_backend} ({config.store_dir / 'auth'})")
    return 0


def mfa_setup(email: str, code: str | None) -> int:
    """Привязать приложение-аутентификатор к учётной записи (действие оператора).

    Без ``--code`` печатает секрет и адрес ``otpauth://``; с ``--code`` включает
    второй фактор, если код из приложения подошёл.
    """
    from ..auth.service import get_auth_service

    service = get_auth_service()
    user = service.get_user_by_email(email)
    if user is None:
        print(f"ОШИБКА: учётная запись {email} не найдена.", file=sys.stderr)
        return 1
    try:
        if code is None:
            secret, uri = service.mfa_begin_setup(user)
            print(f"Секрет для приложения-аутентификатора: {secret}")
            print(f"Адрес для импорта: {uri}")
            print("Затем: second-opinion mfa-setup --email ... --code <код из приложения>")
            return 0
        service.mfa_enable(user, code)
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    print(f"Второй фактор включён для {email}")
    return 0


def mfa_reset(email: str) -> int:
    """Снять второй фактор (потерян телефон); запускает оператор сервера."""
    from ..auth.service import get_auth_service

    service = get_auth_service()
    user = service.get_user_by_email(email)
    if user is None:
        print(f"ОШИБКА: учётная запись {email} не найдена.", file=sys.stderr)
        return 1
    service.mfa_reset(user.user_id)
    print(f"Второй фактор снят для {email}")
    return 0


def serve(host: str, port: int) -> int:
    import uvicorn

    code = check_bind_safety(host)
    if code != 0:
        return code

    uvicorn.run(
        "second_opinion.api.app:make_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )
    return 0
