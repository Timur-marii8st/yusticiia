from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
SAMPLE_CLEAN = FIXTURES_DIR / "sample_documents" / "sample_158_special_clean.txt"
SAMPLE_VIOLATION = FIXTURES_DIR / "sample_documents" / "sample_228_attempt_violation.txt"
SAMPLE_NEGATIVE = FIXTURES_DIR / "sample_documents" / "sample_negative_appeal.txt"


@pytest.fixture()
def sample_clean_text() -> str:
    return SAMPLE_CLEAN.read_text(encoding="utf-8")


@pytest.fixture()
def sample_violation_text() -> str:
    return SAMPLE_VIOLATION.read_text(encoding="utf-8")


@pytest.fixture()
def sample_negative_text() -> str:
    return SAMPLE_NEGATIVE.read_text(encoding="utf-8")


@pytest.fixture()
def norm_store():
    from second_opinion.legal_sources.store import NormStore

    return NormStore.from_directory(FIXTURES_DIR / "norms")


@pytest.fixture()
def pipeline(tmp_path):
    from second_opinion.api.deps import build_pipeline
    from second_opinion.config import AppConfig

    config = AppConfig(
        data_dir=tmp_path,
        fixtures_dir=FIXTURES_DIR,
        llm_provider="mock",
        openai_base_url="",
        openai_api_key="",
        llm_model="",
        max_upload_bytes=1_000_000,
    )
    return build_pipeline(config)


@pytest.fixture(autouse=True)
def reset_auth_service(monkeypatch):
    """Сброс глобального auth_service перед каждым тестом.

    Создаём заново AuthService с предзаполненным админом, чтобы тесты
    авторизации могли выполнять логин под `admin@example.com / admin123`.
    SO_AUTH_TOKEN задаётся в 32+ байт, чтобы JWT-предупреждение
    pyjwt (RFC 7518) не шумело в прогоне; кэш load_config сбрасывается.
    """
    monkeypatch.setenv("SO_AUTH_TOKEN", "test-secret-key-with-at-least-32-bytes-for-hmac")
    from second_opinion.config import reset_config_cache

    reset_config_cache()

    from second_opinion.auth.service import AuthService, pwd_context
    from second_opinion.domain.users import User, UserRole

    auth = AuthService()
    auth._users["admin-001"] = User(
        user_id="admin-001",
        email="admin@example.com",
        full_name="Admin User",
        role=UserRole.ADMIN,
        is_active=True,
        password_hash=pwd_context.hash("admin123"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    import second_opinion.auth.service

    second_opinion.auth.service._auth_service = auth
    yield
    second_opinion.auth.service._auth_service = None
    reset_config_cache()
