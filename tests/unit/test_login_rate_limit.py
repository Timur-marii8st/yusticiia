"""Ограничение частоты входа по клиенту."""

from __future__ import annotations

from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.auth.rate_limit import LoginRateLimiter
from second_opinion.config import reset_config_cache


def _limiter(max_attempts: int = 3, window: int = 60, **kwargs) -> LoginRateLimiter:
    return LoginRateLimiter(max_attempts, window, **kwargs)



def test_attempts_within_limit_pass() -> None:
    limiter = _limiter(max_attempts=3)
    assert [limiter.retry_after("10.0.0.1", now=t) for t in (0, 1, 2)] == [None] * 3


def test_attempt_over_limit_returns_wait() -> None:
    limiter = _limiter(max_attempts=3, window=60)
    for t in (0, 1, 2):
        limiter.retry_after("10.0.0.1", now=t)

    wait = limiter.retry_after("10.0.0.1", now=3)
    assert wait is not None and 0 < wait <= 60


def test_window_slides_and_frees_up() -> None:
    limiter = _limiter(max_attempts=3, window=60)
    for t in (0, 1, 2):
        limiter.retry_after("10.0.0.1", now=t)
    assert limiter.retry_after("10.0.0.1", now=3) is not None

    assert limiter.retry_after("10.0.0.1", now=100) is None


def test_hammering_extends_the_wait() -> None:
    """Непрерывный перебор не должен сбрасывать ожидание."""
    limiter = _limiter(max_attempts=2, window=60)
    for t in (0, 1):
        limiter.retry_after("10.0.0.1", now=t)

    first = limiter.retry_after("10.0.0.1", now=2)
    later = limiter.retry_after("10.0.0.1", now=30)
    assert first is not None and later is not None
    assert later >= first - 30


def test_clients_are_counted_separately() -> None:
    limiter = _limiter(max_attempts=2)
    for t in (0, 1, 2):
        limiter.retry_after("10.0.0.1", now=t)

    assert limiter.retry_after("10.0.0.1", now=3) is not None
    assert limiter.retry_after("10.0.0.2", now=3) is None


def test_zero_limit_disables_check() -> None:
    limiter = _limiter(max_attempts=0)
    assert limiter.enabled is False
    assert all(limiter.retry_after("10.0.0.1", now=t) is None for t in range(50))


def test_tracked_clients_are_bounded() -> None:
    """Перебор с меняющихся адресов не должен раздувать память."""
    limiter = _limiter(max_attempts=1, max_clients=10)
    for i in range(100):
        limiter.retry_after(f"10.0.0.{i}", now=i)
    assert len(limiter._hits) <= 10



def test_login_returns_429_with_retry_after(pipeline, monkeypatch) -> None:
    monkeypatch.setenv("SO_LOGIN_RATE_LIMIT", "3")
    monkeypatch.setenv("SO_LOGIN_RATE_WINDOW_SECONDS", "60")
    reset_config_cache()
    client = TestClient(create_app(pipeline, auth_token="test-secret"))
    body = {"email": "admin@example.com", "password": "неверный"}

    for _ in range(3):
        assert client.post("/auth/login", json=body).status_code == 401

    blocked = client.post("/auth/login", json=body)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "попыток входа" in blocked.json()["detail"]


def test_rate_limit_applies_before_credentials(pipeline, monkeypatch) -> None:
    """Исчерпав лимит, верный пароль тоже не проходит."""
    monkeypatch.setenv("SO_LOGIN_RATE_LIMIT", "2")
    reset_config_cache()
    client = TestClient(create_app(pipeline, auth_token="test-secret"))

    for _ in range(2):
        client.post("/auth/login", json={"email": "admin@example.com", "password": "x"})

    correct = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert correct.status_code == 429


def test_separate_apps_do_not_share_counters(pipeline, monkeypatch) -> None:
    """Лимитер привязан к приложению, а не к модулю."""
    monkeypatch.setenv("SO_LOGIN_RATE_LIMIT", "2")
    reset_config_cache()
    body = {"email": "admin@example.com", "password": "неверный"}

    first = TestClient(create_app(pipeline, auth_token="test-secret"))
    for _ in range(3):
        first.post("/auth/login", json=body)
    assert first.post("/auth/login", json=body).status_code == 429

    second = TestClient(create_app(pipeline, auth_token="test-secret"))
    assert second.post("/auth/login", json=body).status_code == 401


def test_default_limit_does_not_disturb_normal_login(pipeline) -> None:
    """Обычный вход при дефолтных настройках не упирается в лимит."""
    client = TestClient(create_app(pipeline, auth_token="test-secret"))
    response = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "admin123"}
    )
    assert response.status_code == 200
