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
    rag_mode: str = "lexical"  # "lexical" | "hybrid" (ADR-003)
    embeddings_provider: str = "hashing"  # "hashing" | "openai_compatible"
    embedding_model: str = ""
    #: Общий секрет для /api/*; пусто — авторизация выключена (loopback-MVP).
    auth_token: str = ""
    #: Носитель документов/отчётов: "file" (по умолчанию) | "postgres" (ADR-006).
    storage_backend: str = "file"
    #: DSN PostgreSQL для storage_backend="postgres".
    database_url: str = ""

    @property
    def store_dir(self) -> Path:
        return self.data_dir / "store"

    @property
    def audit_path(self) -> Path:
        return self.store_dir / "audit.jsonl"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _default_data_dir() -> Path:
    """Каталог данных по умолчанию.

    В репозитории (есть исходники/pyproject) — ``<root>/data``; при работе
    установленного пакета (pip/Docker) путь от корня пакета некорректен,
    поэтому данные берутся относительно рабочего каталога: ``./data``.
    """
    if (PROJECT_ROOT / "pyproject.toml").exists():
        return PROJECT_ROOT / "data"
    return Path.cwd() / "data"


@lru_cache
def load_config() -> AppConfig:
    default_data = _default_data_dir()
    fixtures = Path(_env("SO_FIXTURES_DIR") or (default_data / "fixtures"))
    data_dir = Path(_env("SO_DATA_DIR") or default_data)
    return AppConfig(
        data_dir=data_dir,
        fixtures_dir=fixtures,
        llm_provider=_env("SO_LLM_PROVIDER", "mock") or "mock",
        openai_base_url=_env("SO_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        openai_api_key=_env("SO_OPENAI_API_KEY"),
        llm_model=_env("SO_LLM_MODEL", "gpt-4o-mini"),
        max_upload_bytes=int(_env("SO_MAX_UPLOAD_BYTES") or DEFAULT_MAX_UPLOAD_BYTES),
        rag_mode=_env("SO_RAG_MODE", "lexical") or "lexical",
        embeddings_provider=_env("SO_EMBEDDINGS_PROVIDER", "hashing") or "hashing",
        embedding_model=_env("SO_EMBEDDING_MODEL"),
        auth_token=_env("SO_AUTH_TOKEN"),
        storage_backend=_env("SO_STORAGE_BACKEND", "file") or "file",
        database_url=_env("SO_DATABASE_URL"),
    )


def reset_config_cache() -> None:
    load_config.cache_clear()
