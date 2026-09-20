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
    #: Секрет подписи JWT (SO_JWT_SECRET). Пусто — эфемерный ключ на процесс:
    #: токены действительны только до перезапуска. Захардкоженного значения
    #: нет и быть не должно — иначе подделывается токен любой роли.
    jwt_secret: str = ""
    #: Требовать авторизацию жёстко: при пустом auth_token сервер не стартует.
    require_auth: bool = False
    #: Режим авторизации /api/*:
    #:   "open"     — доступ без авторизации (демонстрация на loopback);
    #:   "shared"   — общий секрет ИЛИ личный токен (ADR-008);
    #:   "accounts" — только личная учётная запись; общий секрет отвергается.
    #: Пусто — режим выводится из наличия SO_AUTH_TOKEN, как было до
    #: появления параметра: пустой секрет означает "open", заданный — "shared".
    auth_mode: str = ""
    #: Минимальная длина пароля пользователя. Для первого администратора
    #: действует отдельный, более строгий порог (см. auth/service.py).
    password_min_length: int = 8
    #: Носитель документов/отчётов: "file" (по умолчанию) | "postgres" (ADR-006).
    storage_backend: str = "file"
    #: DSN PostgreSQL для storage_backend="postgres".
    database_url: str = ""
    #: Максимальный возраст пароля в днях (0 — без ограничения).
    #: При превышении вход отклоняется с требованием смены пароля.
    password_max_age_days: int = 0
    #: Глубина истории паролей (0 — не проверять повторы).
    password_history_depth: int = 5
    #: Максимум последовательных неудачных входов до блокировки (0 — без блокировки).
    login_max_attempts: int = 5
    #: Длительность блокировки входа в минутах.
    login_lockout_minutes: int = 15
    #: Носитель норм: "file" (каталог фикстур, ревью через git) |
    #: "postgres" (обновление без выпуска релиза). Требует SO_DATABASE_URL.
    norms_backend: str = "file"
    #: Источник редакционной истории норм: "none" | "garant" | "stub".
    #: Заглушка отвечает из подготовленных файлов, пока нет токена ГАРАНТ.
    redaction_source: str = "none"
    #: Адрес ИПС «Законодательство России». Значение по умолчанию —
    #: рабочий публичный адрес: источник государственный и не требует
    #: ни токена, ни подписки.
    pravo_base_url: str = "http://pravo.gov.ru/proxy/ips/"
    #: Таймаут запроса к ИПС, секунды. Сервер отвечает медленно —
    #: замеры дают 16 с на страницу списка редакций, а полный текст
    #: УК РФ весит 2,6 МБ, поэтому значение заведомо щедрое.
    pravo_timeout_seconds: float = 180.0
    #: Базовый URL API ГАРАНТ. Не угадывается: документация приводит только
    #: пути вида /v2/..., хост задаёт обслуживающая организация.
    garant_base_url: str = ""
    #: OAuth-токен ГАРАНТ, выдаётся по заявке.
    garant_token: str = ""
    #: Каталог файлов заглушки.
    garant_stub_dir: str = ""
    #: Попыток входа с одного клиента за окно (0 — без ограничения).
    #: Блокировка учётки считает неудачи по пользователю и потому не
    #: сдерживает перебор по списку адресов — этот лимит считает по клиенту.
    login_rate_limit: int = 20
    #: Ширина окна для login_rate_limit, секунды.
    login_rate_window_seconds: int = 60

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
        login_rate_limit=int(_env("SO_LOGIN_RATE_LIMIT") or 20),
        login_rate_window_seconds=int(_env("SO_LOGIN_RATE_WINDOW_SECONDS") or 60),
    )


def reset_config_cache() -> None:
    load_config.cache_clear()
