"""Команды оператора: ``create-user`` и ``list-users``."""

from __future__ import annotations

import json

import pytest

from second_opinion.auth.repository import InMemoryUserStore
from second_opinion.auth.service import AuthService
from second_opinion.cli.serve import create_user, delete_user, list_users, update_user
from second_opinion.config import load_config
from second_opinion.domain.users import UserRole

PASSWORD = "judge-password-123"


@pytest.fixture()
def service(monkeypatch) -> AuthService:
    svc = AuthService(store=InMemoryUserStore())
    monkeypatch.setattr("second_opinion.auth.service.get_auth_service", lambda: svc)
    monkeypatch.delenv("SO_NEW_PASSWORD", raising=False)
    return svc


def test_create_user_makes_a_judge_who_can_log_in(service, capsys) -> None:
    assert create_user("judge@example.com", "Иванов И.И.", "judge", PASSWORD) == 0

    user = service.authenticate("judge@example.com", PASSWORD)
    assert user is not None and user.role is UserRole.JUDGE and user.full_name == "Иванов И.И."
    assert "reset-password" in capsys.readouterr().out


@pytest.mark.parametrize("role", ["clerk", "admin"])
def test_create_user_accepts_other_roles(service, role: str) -> None:
    assert create_user("u@example.com", "Пользователь", role, "long-enough-password-1") == 0

    assert service.get_user_by_email("u@example.com").role.value == role


def test_an_admin_needs_the_longer_password(service, capsys) -> None:
    assert create_user("root@example.com", "Админ", "admin", "short-pass") == 2

    assert "короче 12" in capsys.readouterr().err
    assert service.get_user_by_email("root@example.com") is None


def test_a_short_password_is_refused_for_everyone(service, capsys) -> None:
    assert create_user("judge@example.com", "Судья", "judge", "short") == 2

    assert "короче" in capsys.readouterr().err
    assert service.get_user_by_email("judge@example.com") is None


def test_a_duplicate_address_is_refused(service, capsys) -> None:
    service.create_user("judge@example.com", PASSWORD, "Судья")

    assert create_user("judge@example.com", "Другой", "judge", PASSWORD) == 2

    assert "уже существует" in capsys.readouterr().err


def test_password_comes_from_the_environment(service, monkeypatch) -> None:
    monkeypatch.setenv("SO_NEW_PASSWORD", PASSWORD)

    assert create_user("judge@example.com", "Судья", "judge", None) == 0
    assert service.authenticate("judge@example.com", PASSWORD) is not None


def test_without_a_tty_and_a_password_nothing_is_created(service, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert create_user("judge@example.com", "Судья", "judge", None) == 2

    assert "пароль не задан" in capsys.readouterr().err
    assert service.get_user_by_email("judge@example.com") is None


def test_the_creation_is_audited_without_the_password(service) -> None:
    create_user("judge@example.com", "Судья", "judge", PASSWORD)

    events = [
        json.loads(line)
        for line in load_config().audit_path.read_text(encoding="utf-8").splitlines()
    ]
    created = [e for e in events if e["operation"] == "user_create"]
    assert len(created) == 1 and created[0]["actor"] == "cli"
    assert PASSWORD not in json.dumps(events, ensure_ascii=False)


def test_list_users_shows_role_and_state(service, capsys) -> None:
    assert list_users() == 0
    assert "create-admin" in capsys.readouterr().out

    service.create_user("judge@example.com", PASSWORD, "Судья")
    admin = service.create_user("root@example.com", "admin-password-123", "Админ", UserRole.ADMIN)
    admin.is_active = False
    service.save_user(admin)

    assert list_users() == 0
    out = capsys.readouterr().out
    assert "judge@example.com" in out and "judge" in out
    assert "root@example.com" in out and "admin" in out and "нет" in out
    assert PASSWORD not in out and "bcrypt" not in out and "$2" not in out


def _two_admins(service: AuthService) -> None:
    service.create_user("a@example.com", "admin-password-1", "Первый", UserRole.ADMIN)
    service.create_user("b@example.com", "admin-password-2", "Второй", UserRole.ADMIN)


def test_update_user_changes_the_role_name_and_state(service, capsys) -> None:
    service.create_user("judge@example.com", PASSWORD, "Судья")

    assert update_user("judge@example.com", "clerk", "Помощник Иванов", "no") == 0

    user = service.get_user_by_email("judge@example.com")
    assert (user.role, user.full_name, user.is_active) == (UserRole.CLERK, "Помощник Иванов", False)
    assert service.authenticate("judge@example.com", PASSWORD) is None
    assert "role" in capsys.readouterr().out

    assert update_user("judge@example.com", None, None, "yes") == 0
    assert service.authenticate("judge@example.com", PASSWORD) is not None


def test_update_user_without_changes_is_a_noop(service, capsys) -> None:
    service.create_user("judge@example.com", PASSWORD, "Судья")

    assert update_user("judge@example.com", "judge", None, None) == 0
    assert "Изменений нет" in capsys.readouterr().out
    assert update_user("judge@example.com", None, None, None) == 2


def test_update_user_reports_an_unknown_account(service, capsys) -> None:
    assert update_user("nobody@example.com", "judge", None, None) == 1
    assert "не найдена" in capsys.readouterr().err


def test_the_last_administrator_cannot_be_demoted_blocked_or_deleted(service, capsys) -> None:
    service.create_user("root@example.com", "admin-password-1", "Админ", UserRole.ADMIN)

    assert update_user("root@example.com", "judge", None, None) == 2
    assert update_user("root@example.com", None, None, "no") == 2
    assert delete_user("root@example.com") == 2

    assert "единственный активный администратор" in capsys.readouterr().err
    kept = service.get_user_by_email("root@example.com")
    assert kept.role is UserRole.ADMIN and kept.is_active


def test_one_of_two_administrators_can_be_demoted_and_deleted(service) -> None:
    _two_admins(service)

    assert update_user("a@example.com", "judge", None, None) == 0
    assert delete_user("b@example.com") == 2
    assert update_user("a@example.com", "admin", None, None) == 0
    assert delete_user("a@example.com") == 0
    assert service.get_user_by_email("a@example.com") is None


def test_delete_user_removes_the_account_and_audits_it(service, capsys) -> None:
    service.create_user("judge@example.com", PASSWORD, "Судья")

    assert delete_user("judge@example.com") == 0

    assert service.get_user_by_email("judge@example.com") is None
    assert delete_user("judge@example.com") == 1
    events = [
        json.loads(line)
        for line in load_config().audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [e["operation"] for e in events if e["operation"] == "user_delete"] == ["user_delete"]
