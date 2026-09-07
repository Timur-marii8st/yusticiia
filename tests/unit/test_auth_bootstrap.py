"""Бутстрап первого администратора и проверка безопасности привязки."""

from __future__ import annotations

import pytest

from second_opinion.auth.service import (
    AdminAlreadyExistsError,
    bootstrap_admin,
    get_auth_service,
)
from second_opinion.cli import _check_bind_safety, _is_loopback, main
from second_opinion.config import reset_config_cache
from second_opinion.domain.users import UserRole


@pytest.fixture()
def empty_users():
    """Убрать предзаполненного админа из conftest — система «с нуля»."""
    auth = get_auth_service()
    for user in auth.list_users():
        auth.delete_user(user.user_id)
    return auth


def test_bootstrap_creates_first_admin(empty_users) -> None:
    user = bootstrap_admin("admin@court.example.ru", "надёжный-пароль-2026")

    assert user.role is UserRole.ADMIN
    assert user.is_active is True
    stored = empty_users.get_user_by_email("admin@court.example.ru")
    assert stored is not None and stored.user_id == user.user_id


def test_bootstrap_password_is_verifiable(empty_users) -> None:
    bootstrap_admin("admin@court.example.ru", "надёжный-пароль-2026")
    assert empty_users.authenticate("admin@court.example.ru", "надёжный-пароль-2026")
    assert empty_users.authenticate("admin@court.example.ru", "неверный-пароль") is None


def test_bootstrap_refuses_when_admin_exists() -> None:
    """Второй админ заводится через API, чтобы попасть в аудит."""
    with pytest.raises(AdminAlreadyExistsError):
        bootstrap_admin("second@court.example.ru", "надёжный-пароль-2026")


def test_bootstrap_rejects_short_password(empty_users) -> None:
    with pytest.raises(ValueError, match="короче"):
        bootstrap_admin("admin@court.example.ru", "коротко")


def test_bootstrap_has_no_default_password() -> None:
    """Регрессия: пароля admin123 в коде быть не должно."""
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[2]
        / "src/second_opinion/auth/service.py"
    ).read_text(encoding="utf-8")
    assert "admin123" not in text


# --- CLI -------------------------------------------------------------------


def test_cli_create_admin_succeeds(empty_users, capsys) -> None:
    code = main(
        [
            "create-admin",
            "--email",
            "admin@court.example.ru",
            "--password",
            "надёжный-пароль-2026",
        ]
    )
    assert code == 0
    assert "Администратор создан" in capsys.readouterr().out
    assert empty_users.get_user_by_email("admin@court.example.ru") is not None


def test_cli_create_admin_reports_existing_admin(capsys) -> None:
    code = main(
        ["create-admin", "--email", "a@court.example.ru", "--password", "надёжный-пароль-2026"]
    )
    assert code == 1
    assert "уже существует" in capsys.readouterr().err


def test_cli_create_admin_rejects_weak_password(empty_users, capsys) -> None:
    code = main(["create-admin", "--email", "a@court.example.ru", "--password", "коротко"])
    assert code == 2
    assert "короче" in capsys.readouterr().err


def test_cli_create_admin_takes_password_from_env(empty_users, monkeypatch) -> None:
    monkeypatch.setenv("SO_ADMIN_PASSWORD", "надёжный-пароль-2026")
    assert main(["create-admin", "--email", "admin@court.example.ru"]) == 0


def test_cli_create_admin_without_password_fails_non_interactive(
    empty_users, monkeypatch, capsys
) -> None:
    monkeypatch.delenv("SO_ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    assert main(["create-admin", "--email", "admin@court.example.ru"]) == 2
    assert "пароль не задан" in capsys.readouterr().err.lower()


# --- безопасность привязки --------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("10.0.0.5", False),
        ("court.example.ru", False),
    ],
)
def test_is_loopback(host: str, expected: bool) -> None:
    assert _is_loopback(host) is expected


def test_bind_safety_allows_loopback_without_token(monkeypatch) -> None:
    monkeypatch.delenv("SO_AUTH_TOKEN", raising=False)
    reset_config_cache()
    assert _check_bind_safety("127.0.0.1") == 0


def test_bind_safety_warns_on_external_without_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv("SO_AUTH_TOKEN", raising=False)
    reset_config_cache()

    assert _check_bind_safety("0.0.0.0") == 0
    assert "ВНИМАНИЕ" in capsys.readouterr().err


def test_bind_safety_blocks_external_when_auth_required(monkeypatch, capsys) -> None:
    monkeypatch.delenv("SO_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("SO_REQUIRE_AUTH", "1")
    reset_config_cache()

    assert _check_bind_safety("0.0.0.0") == 2
    assert "ОШИБКА" in capsys.readouterr().err


def test_bind_safety_silent_when_token_set(capsys) -> None:
    """SO_AUTH_TOKEN задан в conftest — внешняя привязка допустима."""
    assert _check_bind_safety("0.0.0.0") == 0
    assert capsys.readouterr().err == ""
