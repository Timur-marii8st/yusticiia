"""Refresh-токен в HttpOnly cookie: скрипт страницы его не читает.

Токены в ``sessionStorage`` доступны любому скрипту страницы, поэтому долгоживущий
refresh браузерный клиент получает в cookie с ``HttpOnly`` и ``SameSite=Strict``.
Клиентам без браузера по-прежнему доступен refresh в теле ответа.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.config import load_config

CSRF = {"X-SO-CSRF": "1"}


@pytest.fixture()
def client(pipeline, monkeypatch) -> TestClient:
    monkeypatch.setenv("SO_AUTH_MODE", "accounts")
    load_config.cache_clear()
    yield TestClient(create_app(pipeline))
    load_config.cache_clear()


def _login(client: TestClient, cookie: bool):
    return client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "admin123", "cookie": cookie},
    )


def _set_cookie(response) -> str:
    return response.headers.get("set-cookie", "")


def test_cookie_login_hides_refresh_from_the_body_and_sets_a_protected_cookie(client) -> None:
    response = _login(client, cookie=True)

    assert response.status_code == 200
    assert response.json()["tokens"]["refresh_token"] is None
    assert response.json()["tokens"]["access_token"]
    header = _set_cookie(response).lower()
    assert header.startswith("so_refresh=")
    assert "httponly" in header
    assert "samesite=strict" in header
    assert "path=/auth" in header
    assert "max-age" not in header and "expires" not in header


def test_default_login_keeps_refresh_in_the_body_and_sets_no_cookie(client) -> None:
    response = _login(client, cookie=False)

    assert response.json()["tokens"]["refresh_token"]
    assert "set-cookie" not in response.headers


def test_refresh_from_cookie_requires_the_csrf_header(client) -> None:
    _login(client, cookie=True)

    response = client.post("/auth/refresh")

    assert response.status_code == 403
    assert "X-SO-CSRF" in response.json()["detail"]


def test_refresh_from_cookie_rotates_the_cookie_and_returns_an_access_token(client) -> None:
    _login(client, cookie=True)
    old_cookie = client.cookies.get("so_refresh")

    response = client.post("/auth/refresh", headers=CSRF)

    assert response.status_code == 200
    body = response.json()
    assert body["refresh_token"] is None
    assert _set_cookie(response).lower().startswith("so_refresh=")
    assert client.cookies.get("so_refresh") != old_cookie
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200


def test_used_cookie_value_is_rejected_after_rotation(client) -> None:
    _login(client, cookie=True)
    old_cookie = client.cookies.get("so_refresh")
    assert client.post("/auth/refresh", headers=CSRF).status_code == 200

    client.cookies.clear()
    client.cookies.set("so_refresh", old_cookie, path="/auth")
    response = client.post("/auth/refresh", headers=CSRF)

    assert response.status_code == 401


def test_refresh_without_any_token_is_rejected(client) -> None:
    assert client.post("/auth/refresh", headers=CSRF).status_code == 401


def test_empty_json_body_is_a_format_error_not_a_cookie_request(client) -> None:
    _login(client, cookie=True)

    assert client.post("/auth/refresh", json={}, headers=CSRF).status_code == 422


def test_body_refresh_still_works_and_sets_no_cookie(client) -> None:
    login = _login(client, cookie=False)
    token = login.json()["tokens"]["refresh_token"]

    response = client.post("/auth/refresh", json={"refresh_token": token})

    assert response.status_code == 200
    assert response.json()["refresh_token"]
    assert "set-cookie" not in response.headers


def test_logout_revokes_the_cookie_token_and_clears_the_cookie(client) -> None:
    login = _login(client, cookie=True)
    cookie_value = client.cookies.get("so_refresh")
    access = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}

    response = client.post("/auth/logout", headers=access)

    assert response.status_code == 200
    header = _set_cookie(response).lower()
    assert header.startswith("so_refresh=") and "max-age=0" in header

    client.cookies.set("so_refresh", cookie_value, path="/auth")
    assert client.post("/auth/refresh", headers=CSRF).status_code == 401


def test_cookie_is_secure_when_the_deployment_asks_for_it(pipeline, monkeypatch) -> None:
    monkeypatch.setenv("SO_AUTH_MODE", "accounts")
    monkeypatch.setenv("SO_COOKIE_SECURE", "1")
    load_config.cache_clear()
    try:
        response = _login(TestClient(create_app(pipeline)), cookie=True)
    finally:
        load_config.cache_clear()

    assert "secure" in _set_cookie(response).lower()


def test_cookie_is_not_marked_secure_over_plain_http_by_default(client) -> None:
    assert "secure" not in _set_cookie(_login(client, cookie=True)).lower()
