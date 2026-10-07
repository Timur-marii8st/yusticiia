"""Проверка владельца объекта: судья видит и меняет только своё.

Чужой объект отвечает так же, как несуществующий: ``404`` с тем же текстом,
иначе по ответу можно было бы перебирать идентификаторы и узнавать, какие
дела есть в системе. Администратор видит всё. Записи, сделанные до появления
поля ``owner`` (пустой владелец), доступны в режимах ``open`` и ``shared``,
где различить пользователей нельзя, а в режиме ``accounts`` только
администратору.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..domain.users import UserRole
from .auth_mode import AUTH_MODE_ACCOUNTS
from .request_context import actor


def is_admin(requester: str) -> bool:
    from ..auth.service import get_auth_service

    user = get_auth_service().get_user(requester)
    return user is not None and user.role is UserRole.ADMIN


def require_owner(owner: str, request: Request, not_found: str) -> None:
    requester = actor(request)
    if owner == requester:
        return
    if not owner and getattr(request.app.state, "auth_mode", "") != AUTH_MODE_ACCOUNTS:
        return
    if is_admin(requester):
        return
    raise HTTPException(status_code=404, detail=not_found)
