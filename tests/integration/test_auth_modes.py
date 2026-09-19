"""Режимы авторизации и минимальная длина пароля (ФТ-40).

До появления режимов защита включалась ровно одним способом — заданием
`SO_AUTH_TOKEN`. Пока его нет, `/api/*` был открыт даже там, где заведены
учётные записи: личный кабинет оказывался надстройкой над открытой
дверью. Режим `accounts` делает рубильником учётную запись, а не строку,
которую знают все.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app, resolve_auth_mode
from second_opinion.auth.service import ensure_password_length
from second_opinion.config import load_config


@pytest.fixture()
def auth_mode(monkeypatch):
    """Задать SO_AUTH_MODE с учётом кэша конфигурации.

    ``load_config`` кэширован (`lru_cache`), поэтому переменная окружения,
    выставленная после первого чтения, иначе не видна — и тест проверял бы
    не то, что думает.
    """

    def _set(mode: str) -> None:
        monkeypatch.setenv("SO_AUTH_MODE", mode)
        load_config.cache_clear()

    yield _set
    load_config.cache_clear()


def _token(client: TestClient) -> str:
    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert login.status_code == 200, login.text
    return login.json()["tokens"]["access_token"]


def test_mode_is_derived_from_the_shared_secret_when_not_set() -> None:
    """Молчание сохраняет прежнее поведение развёртывания."""
    assert resolve_auth_mode("", "") == "open"
    assert resolve_auth_mode("", "secret") == "shared"


def test_explicit_mode_wins_over_the_secret() -> None:
    assert resolve_auth_mode("accounts", "secret") == "accounts"
    assert resolve_auth_mode("open", "secret") == "open"


def test_unknown_mode_is_refused_loudly() -> None:
    """Опечатка в режиме не должна тихо открывать доступ."""
    with pytest.raises(ValueError, match="SO_AUTH_MODE"):
        resolve_auth_mode("acounts", "")


def test_accounts_mode_rejects_the_shared_secret(pipeline, auth_mode) -> None:
    auth_mode("accounts")
    client = TestClient(create_app(pipeline, auth_token="shared-secret"))

    assert client.get("/api/v1/norms").status_code == 401
    assert (
        client.get("/api/v1/norms", headers={"Authorization": "Bearer shared-secret"}).status_code
        == 401
    ), "общий секрет принят в режиме, который его не принимает"

    access = _token(client)
    assert client.get("/api/v1/norms", headers={"Authorization": f"Bearer {access}"}).status_code == 200


def test_shared_mode_keeps_both_ways(pipeline, auth_mode) -> None:
    auth_mode("shared")
    client = TestClient(create_app(pipeline, auth_token="shared-secret"))

    assert (
        client.get("/api/v1/norms", headers={"Authorization": "Bearer shared-secret"}).status_code
        == 200
    )
    access = _token(client)
    assert client.get("/api/v1/norms", headers={"Authorization": f"Bearer {access}"}).status_code == 200


def test_health_reports_the_mode(pipeline, auth_mode) -> None:
    """Интерфейсу нужен режим, чтобы не предлагать неработающий вход."""
    auth_mode("accounts")
    client = TestClient(create_app(pipeline, auth_token=""))
    assert client.get("/health").json()["auth_mode"] == "accounts"


def test_interface_hides_the_shared_secret_form_in_accounts_mode(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text
    assert "loadAuthMode" in js
    assert 'authMode === "accounts"' in js


def test_password_length_is_enforced() -> None:
    with pytest.raises(ValueError, match="короче"):
        ensure_password_length("korotko", 8)
    ensure_password_length("dostatochno-dlinnyi", 8)


def test_new_user_password_must_be_long_enough(pipeline) -> None:
    """Короткий пароль пользователя, заводимого админом, раньше проходил."""
    from second_opinion.auth.service import get_auth_service

    service = get_auth_service()
    with pytest.raises(ValueError, match="короче"):
        service.create_user(email="judge@example.com", password="123", full_name="Судья")


def test_password_change_is_checked_too(pipeline) -> None:
    """Политика бессмысленна, если её обходит смена пароля."""
    from second_opinion.auth.service import get_auth_service

    service = get_auth_service()
    user = service.get_user_by_email("admin@example.com")
    with pytest.raises(ValueError, match="короче"):
        service.change_password(user, "admin123", "korotk")
