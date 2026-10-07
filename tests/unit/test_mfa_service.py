"""Второй фактор в ``AuthService``: вход, блокировка, политика администратора, CLI."""

from __future__ import annotations

import pytest

from second_opinion.auth import totp
from second_opinion.auth.repository import InMemoryUserStore
from second_opinion.auth.service import (
    AuthService,
    InvalidSecondFactorError,
    MfaEnrollmentRequiredError,
    MfaRequiredError,
)
from second_opinion.config import load_config
from second_opinion.domain.users import UserRole

PASSWORD = "correct-horse-battery"


class Clock:
    """Управляемое время для шагов TOTP."""

    def __init__(self, step: int = 100_000) -> None:
        self.step = step

    def now(self, _moment: float | None = None) -> int:
        return self.step


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(totp, "current_step", fake.now)
    return fake


def make_service(**kwargs) -> AuthService:
    return AuthService(store=InMemoryUserStore(), **kwargs)


def enabled_user(service: AuthService, clock: Clock, role: UserRole = UserRole.JUDGE):
    user = service.create_user("judge@example.com", PASSWORD, "Судья", role)
    secret, _uri = service.mfa_begin_setup(user)
    service.mfa_enable(user, totp.hotp(secret, clock.step))
    return service.get_user(user.user_id), secret


def test_login_without_a_second_factor_is_unchanged(clock) -> None:
    service = make_service()
    service.create_user("judge@example.com", PASSWORD, "Судья")

    assert service.authenticate("judge@example.com", PASSWORD) is not None


def test_enable_requires_a_correct_code_for_the_pending_secret(clock) -> None:
    service = make_service()
    user = service.create_user("judge@example.com", PASSWORD, "Судья")
    secret, _uri = service.mfa_begin_setup(user)

    with pytest.raises(ValueError, match="Код не подошёл"):
        service.mfa_enable(
            user, "000000" if totp.hotp(secret, clock.step) != "000000" else "111111"
        )
    assert service.get_user(user.user_id).totp_enabled is False

    service.mfa_enable(user, totp.hotp(secret, clock.step))
    assert service.get_user(user.user_id).totp_enabled is True


def test_enable_without_setup_is_refused(clock) -> None:
    service = make_service()
    user = service.create_user("judge@example.com", PASSWORD, "Судья")

    with pytest.raises(ValueError, match="Сначала запросите секрет"):
        service.mfa_enable(user, "123456")


def test_password_alone_is_not_enough_once_enabled(clock) -> None:
    service = make_service()
    enabled_user(service, clock)

    with pytest.raises(MfaRequiredError):
        service.authenticate("judge@example.com", PASSWORD)


def test_correct_code_logs_in_and_the_same_code_cannot_be_replayed(clock) -> None:
    service = make_service()
    _user, secret = enabled_user(service, clock)
    clock.step += 1
    code = totp.hotp(secret, clock.step)

    assert service.authenticate("judge@example.com", PASSWORD, code) is not None
    with pytest.raises(InvalidSecondFactorError):
        service.authenticate("judge@example.com", PASSWORD, code)


def test_wrong_codes_count_towards_the_lockout_even_with_the_right_password(clock) -> None:
    service = make_service(login_max_attempts=3)
    _user, secret = enabled_user(service, clock)
    clock.step += 1
    good = totp.hotp(secret, clock.step)
    bad = "000000" if good != "000000" else "111111"

    for _ in range(3):
        with pytest.raises(InvalidSecondFactorError):
            service.authenticate("judge@example.com", PASSWORD, bad)

    from second_opinion.auth.service import AccountLockedError

    with pytest.raises(AccountLockedError):
        service.authenticate("judge@example.com", PASSWORD, good)


def test_a_missing_code_does_not_reset_the_failure_counter(clock) -> None:
    service = make_service(login_max_attempts=3)
    user, secret = enabled_user(service, clock)
    clock.step += 1
    bad = "000000" if totp.hotp(secret, clock.step) != "000000" else "111111"
    with pytest.raises(InvalidSecondFactorError):
        service.authenticate("judge@example.com", PASSWORD, bad)

    with pytest.raises(MfaRequiredError):
        service.authenticate("judge@example.com", PASSWORD)

    assert service.get_user(user.user_id).failed_login_attempts == 1


def test_successful_login_resets_the_failure_counter(clock) -> None:
    service = make_service(login_max_attempts=3)
    user, secret = enabled_user(service, clock)
    clock.step += 1
    bad = "000000" if totp.hotp(secret, clock.step) != "000000" else "111111"
    with pytest.raises(InvalidSecondFactorError):
        service.authenticate("judge@example.com", PASSWORD, bad)

    assert service.authenticate("judge@example.com", PASSWORD, totp.hotp(secret, clock.step))
    assert service.get_user(user.user_id).failed_login_attempts == 0


def test_login_audit_records_missing_and_wrong_codes(clock) -> None:
    service = make_service()
    _user, secret = enabled_user(service, clock)
    clock.step += 1
    with pytest.raises(MfaRequiredError):
        service.authenticate("judge@example.com", PASSWORD)
    with pytest.raises(InvalidSecondFactorError):
        service.authenticate(
            "judge@example.com",
            PASSWORD,
            "000000" if totp.hotp(secret, clock.step) != "000000" else "111111",
        )

    reasons = [a.reason for a in service.list_login_attempts()]
    assert "mfa_required" in reasons and "bad_otp" in reasons


def test_disable_needs_password_and_a_fresh_code(clock) -> None:
    service = make_service()
    user, secret = enabled_user(service, clock)
    clock.step += 1
    code = totp.hotp(secret, clock.step)

    with pytest.raises(ValueError, match="Пароль неверен"):
        service.mfa_disable(user, "wrong-password-123", code)
    service.mfa_disable(user, PASSWORD, code)

    after = service.get_user(user.user_id)
    assert (after.totp_enabled, after.totp_secret) == (False, "")


def test_admin_reset_clears_the_second_factor_without_a_code(clock) -> None:
    service = make_service()
    user, _secret = enabled_user(service, clock)

    service.mfa_reset(user.user_id)

    assert service.authenticate("judge@example.com", PASSWORD) is not None


def test_required_policy_blocks_an_admin_without_a_second_factor(monkeypatch, clock) -> None:
    monkeypatch.setenv("SO_ADMIN_MFA_REQUIRED", "1")
    load_config.cache_clear()
    service = make_service()
    service.create_user("root@example.com", PASSWORD, "Админ", UserRole.ADMIN)
    service.create_user("judge@example.com", PASSWORD, "Судья")

    with pytest.raises(MfaEnrollmentRequiredError):
        service.authenticate("root@example.com", PASSWORD)
    assert service.authenticate("judge@example.com", PASSWORD) is not None


def test_required_policy_forbids_an_admin_to_disable_the_factor(monkeypatch, clock) -> None:
    monkeypatch.setenv("SO_ADMIN_MFA_REQUIRED", "1")
    load_config.cache_clear()
    service = make_service()
    admin, secret = enabled_user(service, clock, UserRole.ADMIN)
    clock.step += 1

    with pytest.raises(ValueError, match="обязателен"):
        service.mfa_disable(admin, PASSWORD, totp.hotp(secret, clock.step))


def test_cli_setup_prints_the_secret_then_enables_with_a_code(monkeypatch, capsys, clock) -> None:
    service = make_service()
    service.create_user("judge@example.com", PASSWORD, "Судья")
    monkeypatch.setattr("second_opinion.auth.service.get_auth_service", lambda: service)
    from second_opinion.cli.serve import mfa_reset, mfa_setup

    assert mfa_setup("judge@example.com", None) == 0
    printed = capsys.readouterr().out
    secret = printed.split("Секрет для приложения-аутентификатора: ")[1].split()[0]

    assert (
        mfa_setup(
            "judge@example.com", "000000" if totp.hotp(secret, clock.step) != "000000" else "111111"
        )
        == 1
    )
    assert mfa_setup("judge@example.com", totp.hotp(secret, clock.step)) == 0
    assert service.get_user_by_email("judge@example.com").totp_enabled is True

    assert mfa_reset("judge@example.com") == 0
    assert service.get_user_by_email("judge@example.com").totp_enabled is False
    assert mfa_setup("nobody@example.com", None) == 1
