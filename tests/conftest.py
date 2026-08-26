from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
SAMPLE_CLEAN = FIXTURES_DIR / "sample_documents" / "sample_158_special_clean.txt"
SAMPLE_VIOLATION = FIXTURES_DIR / "sample_documents" / "sample_228_attempt_violation.txt"
SAMPLE_NEGATIVE = FIXTURES_DIR / "sample_documents" / "sample_negative_appeal.txt"


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


@pytest.fixture()
def sample_clean_text() -> str:
    return SAMPLE_CLEAN.read_text(encoding="utf-8")


@pytest.fixture()
def sample_violation_text() -> str:
    return SAMPLE_VIOLATION.read_text(encoding="utf-8")


@pytest.fixture()
def sample_negative_text() -> str:
    return SAMPLE_NEGATIVE.read_text(encoding="utf-8")
