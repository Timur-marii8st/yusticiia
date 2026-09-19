"""Запуск веб-приложения и заведение первого администратора."""

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

    # О незаданном SO_JWT_SECRET предупреждает сам auth-сервис при
    # создании — здесь не повторяем.
    config = load_config()
    print(f"Администратор создан: {user.email} (user_id={user.user_id})")
    print(f"Хранилище: {config.storage_backend} ({config.store_dir / 'auth'})")
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
