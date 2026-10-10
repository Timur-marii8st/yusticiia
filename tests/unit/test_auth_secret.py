"""Ключ подписи JWT: отделён от общего Bearer и не имеет дефолта в коде."""

from __future__ import annotations

from pathlib import Path

import pytest

from second_opinion.auth.service import (
    MIN_JWT_SECRET_BYTES,
    AuthService,
    resolve_jwt_secret,
)
from second_opinion.config import AppConfig


def _config(**overrides) -> AppConfig:
    base = {
        "data_dir": Path("/tmp/so-test"),
        "fixtures_dir": Path("/tmp/so-test/fixtures"),
        "llm_provider": "mock",
        "openai_base_url": "",
        "openai_api_key": "",
        "llm_model": "",
        "max_upload_bytes": 1000,
    }
    base.update(overrides)
    return AppConfig(**base)


def test_explicit_secret_is_used() -> None:
    secret = "x" * MIN_JWT_SECRET_BYTES
    assert resolve_jwt_secret(_config(jwt_secret=secret)) == secret


def test_short_secret_rejected() -> None:
    with pytest.raises(ValueError, match="SO_JWT_SECRET"):
        resolve_jwt_secret(_config(jwt_secret="x" * (MIN_JWT_SECRET_BYTES - 1)))


def test_missing_secret_is_random_not_constant() -> None:
    """Без SO_JWT_SECRET ключ случайный на процесс, а не общеизвестный."""
    first = resolve_jwt_secret(_config())
    second = resolve_jwt_secret(_config())
    assert first != second
    assert len(first.encode("utf-8")) >= MIN_JWT_SECRET_BYTES


def test_missing_secret_warns(caplog) -> None:
    with caplog.at_level("WARNING"):
        resolve_jwt_secret(_config())
    assert "SO_JWT_SECRET" in caplog.text


def test_shared_bearer_is_not_the_signing_key() -> None:
    """Знание SO_AUTH_TOKEN не должно позволять подписать чужой токен."""
    shared = "shared-bearer-token-known-to-every-operator"
    resolved = resolve_jwt_secret(_config(auth_token=shared))
    assert resolved != shared


def test_no_hardcoded_secret_in_source() -> None:
    """Регрессия: захардкоженный ключ подписи не должен вернуться в код."""
    source = Path(AuthService.__module__.replace(".", "/"))
    text = (Path(__file__).resolve().parents[2] / "src" / f"{source}.py").read_text(
        encoding="utf-8"
    )
    assert "dev-secret-change-in-production" not in text
