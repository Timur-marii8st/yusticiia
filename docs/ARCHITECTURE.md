# ARCHITECTURE

Модульный монолит на Python 3.11+. Один процесс, слои разделены интерфейсами;
модули не ходят друг к другу в обход контрактов. Выбор против микросервисов —
см. `QWEN.md` («modular monolith > premature microservices») и ADR-001/ADR-006.

## Конвейер

```mermaid
graph TD
    A[Судебный документ: TXT / MD / DOCX / PDF] --> B[Document Ingestion<br/>парсеры + нормализованное представление]
    B --> C[Fact Extraction<br/>паттерны + LLM, schema validation]
    C --> D[Case Model<br/>CaseFacts + LegalFact с evidence]
    D --> E[Rule Engine<br/>детерминированные проверки]
    D --> F[Case Retrieval<br/>структурные фильтры + лексика/эмбеддинги]
    G[(Legal Source Store<br/>нормы с версиями, checksum)] --> E
    G --> H[Legal RAG<br/>поиск норм и разъяснений]
    F --> I[Analytics<br/>описательная статистика]
    E --> J[Analysis Report<br/>provenance-цепочки]
    H --> J
    I --> J
    J --> K[Explanation Layer / API]
    K --> L[UI судьи: progressive disclosure]
    M[Audit Trail] -. фиксирует каждый шаг .-> C
    M -.-> E
    M -.-> H
```

## Слои

| Слой | Модуль | Ответственность | Запрещено |
|---|---|---|---|
| Представление | `api`, `static` | HTTP DTO, раздача UI | Бизнес-логика в хендлерах |
| Оркестрация | `pipeline.py` | Последовательность этапов анализа | Собственные юридические утверждения |
| Домен | `domain` | Pydantic-модели фактов, норм, оценок | I/O, сеть, файлы |
| Приложение | `fact_extraction`, `rule_engine`, `legal_sources`, `retrieval`, `analytics` | Реализация этапов | Прямые LLM-вызовы вне `llm` |
| Инфраструктура | `storage`, `llm`, `audit` | Репозитории, LLM-провайдеры, журнал | Юридическая семантика |

## Компоненты

### Document ingestion (`ingestion/`)
Парсеры TXT, Markdown, DOCX, PDF (текстовый слой) → нормализованный
`Document`: `Section`/фрагменты с `start_offset`/`end_offset` (и `page` для
PDF). Все последующие ссылки на текст документа идут только через оффсеты.

### Fact extraction (`fact_extraction/`)
Два экстрактора за общим интерфейсом:
- **паттерновый** (детерминированный): квалификации «ч. 2 ст. 158 УК РФ»,
  возраст, смягчающие/отягчающие маркеры, вид/размер наказания, особый
  порядок. Каждый результат несёт evidence с оффсетами совпадения.
- **LLM-экстрактор**: структурированный вывод по схеме (повторная попытка при
  невалидном JSON), валидация Pydantic-схемой.

Результаты объединяются; факт без evidence не может получить статус
`VERIFIED`.

### Legal ontology / Case model (`domain/`)
Разделены: факт (`LegalFact`), обстоятельство, норма (`LegalNorm`), версия
нормы (`NormVersion`), применение (`RuleEvaluation`), вывод, источник,
судебный акт (`ComparableCase`). Подробности — `DOMAIN_MODEL.md`.

### Temporal legal source storage (`legal_sources/`)
`NormStore.get_norm(norm_id, applicable_at)` возвращает редакцию, действовавшую
на дату. Каждая редакция: `effective_from/to`, источник, `retrieved_at`,
SHA-256 контрольная сумма текста. ADR-002.

### Rule engine (`rule_engine/`)
Принимает `CaseFacts` + применимые редакции норм; выдаёт `RuleEvaluation[]`
со статусами `PASS / WARNING / FAIL / UNKNOWN`, списками использованных
фактов и норм, объяснением и provenance. Правила — версионируемые
Python-классы с декларативными метаданными (без самописного DSL). ADR-001.

### Legal RAG (`legal_rag/`)
Поиск норм и разъяснений: токенизация запроса (стоп-слова, префиксный учёт
словоформ) → фильтры метаданных (`код`, `статья`) → выбор редакции
(`applicable_at`) → IDF-ранжирование по корпусу редакций. Каждый результат
несёт фрагмент реального источника с редакцией, периодом действия,
источником и SHA-256; пустой результат не подменяется генерацией.
Эмбеддинги и реранкинг добавляются позже поверх этого же интерфейса
(решение отложено, см. ADR-003). MVP-часть «поиска по базе» закрыта
`NormStore` + `/api/search`; полные тексты Пленумов/обзоров — майлстоуны
наполнения данных.

### Case retrieval (`retrieval/`)
Поиск сопоставимых дел: структурные фильтры (статья, часть, покушение,
рецидив, процедура) → ранжирование. В будущем — гибридный поиск (ADR-003).
Сходство — справочный сигнал, не основание для вывода о наказании.

### Analytics (`analytics/`)
Описательная статистика по выборке: N, медиана/перцентили, распределение
видов наказания, доля условного. Только дескриптивные формулировки.

### Audit trail (`audit/`)
JSONL-журнал: время, операция, компонент, хэши входа/выхода, модель и её
версия, версия промпта, версия правил, версии норм. Позволяет воспроизвести
любой отчёт.

### LLM (`llm/`)
Интерфейс `LLMProvider` (`generate_structured`, `generate_text`, `embed`),
адаптеры: `MockLLMProvider` (тесты/офлайн), `OpenAICompatibleProvider`
(любой OpenAI-совместимый контур). Ключи — только из переменных окружения.
ADR-004.

### Evaluation (`evaluation/`)
Метрики: precision/recall/F1 извлечения + корректность и покрытие evidence;
Recall@K/MRR/nDCG поиска; полнота цитирования; корректность выбора редакции;
регрессия Rule Engine; `unsupported_claim_rate` (целевое → 0).

### Auth (`auth/`, `api/auth_routes.py`)
`AuthService` (bcrypt, JWT access/refresh, in-memory `dict[str, User]`,
`AuthUser`/`TokenPayload`/`TokenPair` модели), RBAC через `require_role`.
Эндпоинты `/api/auth/{login,refresh,me,change-password,logout,users}`.
MVP без БД; миграция на PostgreSQL — ROADMAP (нужны refresh-токены
с ротацией, blacklist, истечение паролей).

### Metrics (`metrics.py`)
Внутренний `MetricsRegistry` (потокобезопасные счётчики и гистограммы;
включается `SO_METRICS_ENABLED=1`). Снимок — `/metrics` endpoint.
Метрики: `analyses_total`, `analyses_failed_total`, длительности
`extract/analyze/retrieval/rule_engine_duration_seconds`, per-rule
`rule_evaluations`. Без внешних зависимостей (без prometheus_client);
прометей-совместимая модель данных.

## Технологический стек MVP

- Python 3.11+, FastAPI, Pydantic v2, uvicorn.
- Хранение: файловые репозитории (по умолчанию) или PostgreSQL
  (`SO_STORAGE_BACKEND=postgres` + `SO_DATABASE_URL`, JSONB); pgvector
  для семантики (`PgVectorNormStore`, `vector(1024)`, HNSW cosine,
  `SO_RAG_MODE=hybrid_pgvector`). ADR-006.
- Аутентификация: loopback-MVP через `SO_AUTH_TOKEN` (общий секрет)
  и JWT (роли `judge`/`clerk`/`admin`, in-memory). Двухслойная модель —
  работающая, но требует ADR-008 для формализации.
- Тесты: pytest; линт: ruff; CI: GitHub Actions (lint + tests + eval +
  pip-audit блокирующий).

## Развёртывание

Одна команда: `make dev` (или `.venv\Scripts\second-opinion serve`).
Docker Compose (`docker compose up` / `--profile postgres up`) с
healthcheck и публикацией только на loopback. Подробности —
`docs/DEPLOYMENT.md`.
