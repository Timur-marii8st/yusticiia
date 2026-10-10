"""Сброс пароля оператором: ``second-opinion reset-password``."""

from __future__ import annotations

import json

import pytest

from second_opinion.auth.repository import InMemoryUserStore
from second_opinion.auth.service import AuthService
from second_opinion.cli.serve import reset_password
from second_opinion.config import load_config
from second_opinion.domain.users import UserRole

OLD = "old-password-123"
NEW = "brand-new-password-456"


@pytest.fixture()
def service(monkeypatch) -> AuthService:
    svc = AuthService(store=InMemoryUserStore(), login_max_attempts=3)
    monkeypatch.setattr("second_opinion.auth.service.get_auth_service", lambda: svc)
    monkeypatch.delenv("SO_NEW_PASSWORD", raising=False)
    return svc


def test_reset_replaces_the_password_and_unlocks_the_account(service, capsys) -> None:
    service.create_user("judge@example.com", OLD, "Судья")
    for _ in range(3):
        service.authenticate("judge@example.com", "wrong-password")
    assert service.get_user_by_email("judge@example.com").locked_until is not None

    assert reset_password("judge@example.com", NEW) == 0

    assert service.authenticate("judge@example.com", NEW) is not None
    assert service.authenticate("judge@example.com", OLD) is None
    after = service.get_user_by_email("judge@example.com")
    assert after.failed_login_attempts <= 1 and after.locked_until is None
    assert "SO_JWT_SECRET" in capsys.readouterr().out


def test_unknown_account_is_reported(service, capsys) -> None:
    assert reset_password("nobody@example.com", NEW) == 1
    assert "не найдена" in capsys.readouterr().err


def test_admin_password_must_meet_the_admin_minimum(service, capsys) -> None:
    service.create_user("root@example.com", OLD, "Админ", UserRole.ADMIN)

    assert reset_password("root@example.com", "short-pass") == 2

    assert "короче 12" in capsys.readouterr().err
    assert service.authenticate("root@example.com", OLD) is not None


def test_a_recent_password_cannot_be_reused(service, capsys) -> None:
    service.create_user("judge@example.com", OLD, "Судья")

    assert reset_password("judge@example.com", OLD) == 2

    assert "последних паролей" in capsys.readouterr().err


def test_password_comes_from_the_environment(service, monkeypatch) -> None:
    service.create_user("judge@example.com", OLD, "Судья")
    monkeypatch.setenv("SO_NEW_PASSWORD", NEW)

    assert reset_password("judge@example.com", None) == 0
    assert service.authenticate("judge@example.com", NEW) is not None


def test_without_a_tty_and_a_password_the_command_refuses(service, monkeypatch, capsys) -> None:
    service.create_user("judge@example.com", OLD, "Судья")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert reset_password("judge@example.com", None) == 2

    assert "пароль не задан" in capsys.readouterr().err


def test_interactive_mismatch_changes_nothing(service, monkeypatch, capsys) -> None:
    service.create_user("judge@example.com", OLD, "Судья")
    answers = iter([NEW, NEW + "-typo"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(answers))

    assert reset_password("judge@example.com", None) == 2

    assert "не совпадают" in capsys.readouterr().err
    assert service.authenticate("judge@example.com", OLD) is not None


def test_interactive_entry_sets_the_password(service, monkeypatch) -> None:
    service.create_user("judge@example.com", OLD, "Судья")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": NEW)

    assert reset_password("judge@example.com", None) == 0
    assert service.authenticate("judge@example.com", NEW) is not None


def test_the_reset_is_audited_without_the_password(service) -> None:
    service.create_user("judge@example.com", OLD, "Судья")

    reset_password("judge@example.com", NEW)

    lines = load_config().audit_path.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    reset = [e for e in events if e["operation"] == "password_reset"]
    assert len(reset) == 1 and reset[0]["actor"] == "cli"
    assert NEW not in json.dumps(events, ensure_ascii=False)
