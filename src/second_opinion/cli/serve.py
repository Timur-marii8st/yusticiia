"""Запуск веб-приложения, заведение администратора и сброс пароля."""

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


def _obtain_password(password: str | None) -> str | None:
    """Пароль из аргумента, из ``SO_NEW_PASSWORD`` либо двукратного ввода в терминале.

    Возвращает ``None`` и печатает причину, если пароль получить не удалось.
    """
    import getpass
    import os

    password = password or os.environ.get("SO_NEW_PASSWORD") or ""
    if password:
        return password
    if not sys.stdin.isatty():
        print(
            "ОШИБКА: пароль не задан. Передайте --password, задайте SO_NEW_PASSWORD "
            "или запустите в интерактивном терминале.",
            file=sys.stderr,
        )
        return None
    password = getpass.getpass("Пароль: ")
    if password != getpass.getpass("Повторите пароль: "):
        print("ОШИБКА: пароли не совпадают.", file=sys.stderr)
        return None
    return password


def create_user(email: str, full_name: str, role: str, password: str | None) -> int:
    """Завести учётную запись судьи, помощника или администратора (действие оператора)."""
    from ..audit.trail import CLI_ACTOR, AuditTrail
    from ..auth.service import get_auth_service, validate_admin_password
    from ..domain.users import UserRole

    user_role = UserRole(role)
    secret = _obtain_password(password)
    if secret is None:
        return 2
    try:
        if user_role is UserRole.ADMIN:
            validate_admin_password(secret)
        user = get_auth_service().create_user(email, secret, full_name, user_role)
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    AuditTrail(load_config().audit_path).log(
        operation="user_create",
        component="cli",
        request_id=user.user_id,
        actor=CLI_ACTOR,
        input_hash=user.user_id,
        details={"role": user_role.value},
    )
    print(f"Учётная запись создана: {user.email}, роль {user_role.value} (user_id={user.user_id})")
    print("Пароль пользователю выдаёт и меняет оператор: second-opinion reset-password.")
    return 0


def update_user(email: str, role: str | None, full_name: str | None, active: str | None) -> int:
    """Сменить роль, имя или признак «активна» у существующей записи (действие оператора).

    Последнего активного администратора понизить, заблокировать и удалить нельзя:
    иначе системой не сможет управлять никто, кроме оператора с доступом к хранилищу.
    """
    from ..audit.trail import CLI_ACTOR, AuditTrail
    from ..auth.service import LastAdminError, get_auth_service
    from ..domain.users import UserRole

    if role is None and full_name is None and active is None:
        print("ОШИБКА: укажите --role, --full-name или --active.", file=sys.stderr)
        return 2
    service = get_auth_service()
    user = service.get_user_by_email(email)
    if user is None:
        print(f"ОШИБКА: учётная запись {email} не найдена.", file=sys.stderr)
        return 1
    new_role = UserRole(role) if role else None
    new_active = None if active is None else active == "yes"
    try:
        service.ensure_admin_remains(user.user_id, role=new_role, is_active=new_active)
    except LastAdminError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    changes: dict[str, object] = {}
    if new_role is not None and new_role is not user.role:
        changes["role"] = new_role.value
        user.role = new_role
    if full_name is not None and full_name != user.full_name:
        changes["full_name"] = True
        user.full_name = full_name
    if new_active is not None and new_active != user.is_active:
        changes["is_active"] = new_active
        user.is_active = new_active
    if not changes:
        print("Изменений нет: запись уже в таком состоянии.")
        return 0
    service.save_user(user)
    AuditTrail(load_config().audit_path).log(
        operation="user_update",
        component="cli",
        request_id=user.user_id,
        actor=CLI_ACTOR,
        input_hash=user.user_id,
        details=changes,
    )
    print(f"Запись {email} обновлена: {', '.join(changes)}.")
    return 0


def delete_user(email: str) -> int:
    """Удалить учётную запись (действие оператора); документы и отчёты пользователя остаются."""
    from ..audit.trail import CLI_ACTOR, AuditTrail
    from ..auth.service import LastAdminError, get_auth_service

    service = get_auth_service()
    user = service.get_user_by_email(email)
    if user is None:
        print(f"ОШИБКА: учётная запись {email} не найдена.", file=sys.stderr)
        return 1
    try:
        service.ensure_admin_remains(user.user_id, is_active=False)
    except LastAdminError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    service.delete_user(user.user_id)
    AuditTrail(load_config().audit_path).log(
        operation="user_delete",
        component="cli",
        request_id=user.user_id,
        actor=CLI_ACTOR,
        input_hash=user.user_id,
        details={"role": user.role.value},
    )
    print(f"Учётная запись {email} удалена.")
    return 0


def list_users() -> int:
    """Показать заведённые учётные записи: адрес, роль, активна ли, последний вход."""
    from ..auth.service import get_auth_service

    users = sorted(get_auth_service().list_users(), key=lambda u: u.created_at)
    if not users:
        print("Учётных записей нет: первого администратора заводит `second-opinion create-admin`.")
        return 0
    width = max(len(u.email) for u in users)
    print(f"{'адрес':{width}}  {'роль':6}  {'активна':7}  последний вход")
    for u in users:
        last = u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "-"
        print(f"{u.email:{width}}  {u.role.value:6}  {'да' if u.is_active else 'нет':7}  {last}")
    return 0


def reset_password(email: str, password: str | None) -> int:
    """Задать новый пароль существующей учётной записи (действие оператора сервера).

    Пароль берётся из ``--password``, из ``SO_NEW_PASSWORD`` либо запрашивается
    дважды в терминале. Действуют те же требования, что при создании: у администратора
    не короче 12 символов, у остальных ``SO_PASSWORD_MIN_LENGTH``; недавние пароли
    повторно не принимаются. Блокировка после неудачных попыток снимается. Уже выданные
    токены действуют до истечения: для немедленного отзыва нужно сменить ``SO_JWT_SECRET``.
    """
    from ..audit.trail import CLI_ACTOR, AuditTrail
    from ..auth.service import get_auth_service, validate_admin_password
    from ..domain.users import UserRole

    service = get_auth_service()
    user = service.get_user_by_email(email)
    if user is None:
        print(f"ОШИБКА: учётная запись {email} не найдена.", file=sys.stderr)
        return 1

    password = _obtain_password(password)
    if password is None:
        return 2

    try:
        if user.role is UserRole.ADMIN:
            validate_admin_password(password)
        service.set_password(user.user_id, password)
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    AuditTrail(load_config().audit_path).log(
        operation="password_reset",
        component="cli",
        request_id=user.user_id,
        actor=CLI_ACTOR,
        input_hash=user.user_id,
        details={"role": user.role.value},
    )
    print(f"Пароль заменён для {email}; блокировка входа снята.")
    print(
        "Выданные ранее токены действуют до истечения; чтобы отозвать их сразу, смените SO_JWT_SECRET."
    )
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
