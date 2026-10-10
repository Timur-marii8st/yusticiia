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
    llm_provider: str
    openai_base_url: str
    openai_api_key: str
    llm_model: str
    max_upload_bytes: int
    rag_mode: str = "lexical"
    embeddings_provider: str = "hashing"
    embedding_model: str = ""
    auth_token: str = ""
    jwt_secret: str = ""
    require_auth: bool = False
    cookie_secure: bool = False
    auth_mode: str = ""
    password_min_length: int = 8
    storage_backend: str = "file"
    database_url: str = ""
    password_max_age_days: int = 0
    password_history_depth: int = 5
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    norms_backend: str = "file"
    redaction_source: str = "none"
    pravo_base_url: str = "http://pravo.gov.ru/proxy/ips/"
    pravo_timeout_seconds: float = 180.0
    garant_base_url: str = ""
    garant_token: str = ""
    garant_stub_dir: str = ""
    llm_contour_hosts: tuple[str, ...] = ()
    login_rate_limit: int = 20
    login_rate_window_seconds: int = 60
    llm_max_tokens: int = 3000
    analyze_rate_limit: int = 30
    analyze_rate_window_seconds: int = 60
    analyze_max_concurrency: int = 4
    analyze_queue_seconds: float = 30.0
    api_docs: str = ""
    dynamic_norm_source: str = "none"
    source_cache_ttl_seconds: float = 86400.0
    source_request_timeout_seconds: float = 15.0

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
        openai_base_url=_env("SO_OPENAI_BASE_URL"),
        openai_api_key=_env("SO_OPENAI_API_KEY"),
        llm_model=_env("SO_LLM_MODEL"),
        max_upload_bytes=int(_env("SO_MAX_UPLOAD_BYTES") or DEFAULT_MAX_UPLOAD_BYTES),
        rag_mode=_env("SO_RAG_MODE", "lexical") or "lexical",
        embeddings_provider=_env("SO_EMBEDDINGS_PROVIDER", "hashing") or "hashing",
        embedding_model=_env("SO_EMBEDDING_MODEL"),
        auth_token=_env("SO_AUTH_TOKEN"),
        jwt_secret=_env("SO_JWT_SECRET"),
        require_auth=_env("SO_REQUIRE_AUTH") in ("1", "true", "yes"),
        cookie_secure=_env("SO_COOKIE_SECURE") in ("1", "true", "yes"),
        auth_mode=(_env("SO_AUTH_MODE") or "").strip().lower(),
        password_min_length=int(_env("SO_PASSWORD_MIN_LENGTH") or 8),
        storage_backend=_env("SO_STORAGE_BACKEND", "file") or "file",
        database_url=_env("SO_DATABASE_URL"),
        password_max_age_days=int(_env("SO_PASSWORD_MAX_AGE_DAYS") or 0),
        password_history_depth=int(_env("SO_PASSWORD_HISTORY_DEPTH") or 5),
        login_max_attempts=int(_env("SO_LOGIN_MAX_ATTEMPTS") or 5),
        login_lockout_minutes=int(_env("SO_LOGIN_LOCKOUT_MINUTES") or 15),
        norms_backend=_env("SO_NORMS_BACKEND", "file") or "file",
        redaction_source=_env("SO_REDACTION_SOURCE", "none") or "none",
        pravo_base_url=_env("SO_PRAVO_BASE_URL")
        or "http://pravo.gov.ru/proxy/ips/",
        pravo_timeout_seconds=float(_env("SO_PRAVO_TIMEOUT") or 180.0),
        garant_base_url=_env("SO_GARANT_BASE_URL"),
        garant_token=_env("SO_GARANT_TOKEN"),
        garant_stub_dir=_env("SO_GARANT_STUB_DIR"),
        dynamic_norm_source=_env("SO_DYNAMIC_NORM_SOURCE", "none") or "none",
        source_cache_ttl_seconds=float(_env("SO_SOURCE_CACHE_TTL") or 86400),
        source_request_timeout_seconds=float(_env("SO_SOURCE_REQUEST_TIMEOUT") or 15),
        llm_contour_hosts=tuple(
            host.strip().lower()
            for host in (_env("SO_LLM_CONTOUR_HOSTS") or "").split(",")
            if host.strip()
        ),
        login_rate_limit=int(_env("SO_LOGIN_RATE_LIMIT") or 20),
        login_rate_window_seconds=int(_env("SO_LOGIN_RATE_WINDOW_SECONDS") or 60),
        llm_max_tokens=int(_env("SO_LLM_MAX_TOKENS") or 3000),
        analyze_rate_limit=int(_env("SO_ANALYZE_RATE_LIMIT") or 30),
        analyze_rate_window_seconds=int(_env("SO_ANALYZE_RATE_WINDOW_SECONDS") or 60),
        analyze_max_concurrency=int(_env("SO_ANALYZE_MAX_CONCURRENCY") or 4),
        analyze_queue_seconds=float(_env("SO_ANALYZE_QUEUE_SECONDS") or 30.0),
        api_docs=_env("SO_API_DOCS"),
    )


def reset_config_cache() -> None:
    load_config.cache_clear()
