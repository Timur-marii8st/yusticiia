from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    fixtures_dir: Path
    llm_provider: str  # "mock" | "openai_compatible"
    openai_base_url: str
    openai_api_key: str
    llm_model: str
    max_upload_bytes: int

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def audit_path(self) -> Path:
        return self.store_dir / "audit.jsonl"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@lru_cache
def load_config() -> AppConfig:
    fixtures = Path(_env("SO_FIXTURES_DIR") or (PROJECT_ROOT / "data" / "fixtures"))
    data_dir = Path(_env("SO_DATA_DIR") or (PROJECT_ROOT / "data"))
    return AppConfig(
        data_dir=data_dir,
        fixtures_dir=fixtures,
        llm_provider=_env("SO_LLM_PROVIDER", "mock") or "mock",
        openai_base_url=_env("SO_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_api_key=_env("SO_OPENAI_API_KEY"),
        llm_model=_env("SO_LLM_MODEL", "gpt-4o-mini"),
        max_upload_bytes=int(_env("SO_MAX_UPLOAD_BYTES") or DEFAULT_MAX_UPLOAD_BYTES),
    )


def reset_config_cache() -> None:
    load_config.cache_clear()
