"""Второй фактор через HTTP: включение, вход с кодом, сброс администратором."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.auth import totp
from second_opinion.config import load_config

ADMIN = {"email": "admin@example.com", "password": "admin123"}
JUDGE = {"email": "judge@example.com", "password": "judgepass12345"}


class Clock:
    def __init__(self) -> None:
        self.step = 200_000

    def now(self, _moment: float | None = None) -> int:
        return self.step

    def code(self, secret: str) -> str:
        return totp.hotp(secret, self.step)


@pytest.fixture()
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(totp, "current_step", fake.now)
    return fake


@pytest.fixture()
def client(pipeline, monkeypatch) -> TestClient:
    monkeypatch.setenv("SO_AUTH_MODE", "accounts")
    load_config.cache_clear()
    yield TestClient(create_app(pipeline))
    load_config.cache_clear()


def _login(client: TestClient, **body):
    return client.post("/auth/login", json=body)


def _bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


def _enable(client: TestClient, clock: Clock, credentials: dict) -> tuple[dict[str, str], str]:
    headers = _bearer(_login(client, **credentials))
    secret = client.post("/auth/mfa/setup", headers=headers).json()["secret"]
    enabled = client.post("/auth/mfa/enable", json={"code": clock.code(secret)}, headers=headers)
    assert enabled.status_code == 200, enabled.text
    return headers, secret


def test_setup_returns_a_secret_and_a_provisioning_uri(client) -> None:
    headers = _bearer(_login(client, **ADMIN))

    body = client.post("/auth/mfa/setup", headers=headers).json()

    assert len(body["secret"]) == 32
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["secret"] in body["otpauth_uri"]


def test_mfa_endpoints_require_a_login(client) -> None:
    for path in ("/auth/mfa/setup", "/auth/mfa/enable", "/auth/mfa/disable"):
        assert client.post(path, json={"code": "123456", "password": "x"}).status_code == 401


def test_enable_rejects_a_wrong_code_and_accepts_the_right_one(client, clock) -> None:
    headers = _bearer(_login(client, **ADMIN))
    secret = client.post("/auth/mfa/setup", headers=headers).json()["secret"]
    wrong = "000000" if clock.code(secret) != "000000" else "111111"

    assert client.post("/auth/mfa/enable", json={"code": wrong}, headers=headers).status_code == 400
    assert client.get("/auth/me", headers=headers).json()["mfa_enabled"] is False
    assert (
        client.post(
            "/auth/mfa/enable", json={"code": clock.code(secret)}, headers=headers
        ).status_code
        == 200
    )
    assert client.get("/auth/me", headers=headers).json()["mfa_enabled"] is True


def test_setup_is_refused_when_the_factor_is_already_on(client, clock) -> None:
    headers, _secret = _enable(client, clock, ADMIN)

    assert client.post("/auth/mfa/setup", headers=headers).status_code == 409


def test_login_asks_for_a_code_and_flags_the_response(client, clock) -> None:
    _enable(client, clock, ADMIN)

    response = _login(client, **ADMIN)

    assert response.status_code == 401
    assert response.headers["x-mfa-required"] == "1"
    assert "tokens" not in response.json()


def test_login_with_a_wrong_code_is_refused_with_the_same_flag(client, clock) -> None:
    _headers, secret = _enable(client, clock, ADMIN)
    clock.step += 1
    wrong = "000000" if clock.code(secret) != "000000" else "111111"

    response = _login(client, **ADMIN, otp=wrong)

    assert response.status_code == 401
    assert response.headers["x-mfa-required"] == "1"


def test_login_with_the_right_code_succeeds_once(client, clock) -> None:
    _headers, secret = _enable(client, clock, ADMIN)
    clock.step += 1
    code = clock.code(secret)

    assert _login(client, **ADMIN, otp=code).status_code == 200
    assert _login(client, **ADMIN, otp=code).status_code == 401


def test_a_non_digit_code_is_a_plain_refusal_not_a_server_error(client, clock) -> None:
    _enable(client, clock, ADMIN)

    response = _login(client, **ADMIN, otp="١٢٣٤٥٦")

    assert response.status_code == 401


def test_disable_with_password_and_code(client, clock) -> None:
    headers, secret = _enable(client, clock, ADMIN)
    clock.step += 1

    wrong_password = client.post(
        "/auth/mfa/disable",
        json={"password": "not-the-password", "code": clock.code(secret)},
        headers=headers,
    )
    done = client.post(
        "/auth/mfa/disable",
        json={"password": ADMIN["password"], "code": clock.code(secret)},
        headers=headers,
    )

    assert wrong_password.status_code == 400
    assert done.status_code == 200
    assert _login(client, **ADMIN).status_code == 200


def _create_judge(client: TestClient, admin: dict[str, str]) -> str:
    created = client.post("/auth/users", json={**JUDGE, "full_name": "Судья"}, headers=admin)
    assert created.status_code == 201, created.text
    return created.json()["user_id"]


def test_admin_can_reset_another_users_factor_but_not_their_own(client, clock) -> None:
    admin = _bearer(_login(client, **ADMIN))
    judge_id = _create_judge(client, admin)
    _enable(client, clock, JUDGE)
    assert _login(client, **JUDGE).status_code == 401

    reset = client.post(f"/auth/users/{judge_id}/mfa/reset", headers=admin)
    own = client.post("/auth/users/admin-001/mfa/reset", headers=admin)

    assert reset.status_code == 200
    assert own.status_code == 400
    assert _login(client, **JUDGE).status_code == 200


def test_reset_is_for_admins_only_and_knows_missing_users(client) -> None:
    admin = _bearer(_login(client, **ADMIN))
    judge_id = _create_judge(client, admin)
    judge = _bearer(_login(client, **JUDGE))

    assert client.post(f"/auth/users/{judge_id}/mfa/reset", headers=judge).status_code == 403
    assert client.post("/auth/users/nobody/mfa/reset", headers=admin).status_code == 404


def test_user_list_shows_who_has_the_factor(client, clock) -> None:
    admin = _bearer(_login(client, **ADMIN))
    judge_id = _create_judge(client, admin)

    before = {
        u["user_id"]: u["mfa_enabled"] for u in client.get("/auth/users", headers=admin).json()
    }
    _enable(client, clock, JUDGE)
    after = {
        u["user_id"]: u["mfa_enabled"] for u in client.get("/auth/users", headers=admin).json()
    }

    assert before[judge_id] is False and after[judge_id] is True


def test_required_policy_turns_an_admin_without_the_factor_away(pipeline, monkeypatch) -> None:
    monkeypatch.setenv("SO_AUTH_MODE", "accounts")
    monkeypatch.setenv("SO_ADMIN_MFA_REQUIRED", "1")
    load_config.cache_clear()
    import second_opinion.auth.service as service_module
    from second_opinion.auth.service import AuthService

    old = service_module._auth_service
    service_module._auth_service = AuthService(store=old._store)
    try:
        response = _login(TestClient(create_app(pipeline)), **ADMIN)
    finally:
        service_module._auth_service = old
        load_config.cache_clear()

    assert response.status_code == 403
    assert "второй фактор" in response.json()["detail"]
