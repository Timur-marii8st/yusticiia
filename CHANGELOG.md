# CHANGELOG

Формат: краткие сводки по итерациям разработки. Проект в активной
разработке (конкурс «Алгоритм правосудия. Второе мнение»); версии
условны.

## 0.18 — метрики конвейера, ревёрт R-009 (2026-09-02)

- `src/second_opinion/metrics.py` — внутренние метрики конвейера
  (счётчики, гистограммы; потокобезопасно, без внешних зависимостей).
  Включается через `SO_METRICS_ENABLED=1`. Интегрировано в
  `pipeline.analyze` — обёртки вокруг стадий extract/rule_engine/
  retrieval/analyze; per-rule `RULE_EVALUATIONS`.
- `tests/unit/test_metrics.py` (8 тестов) + `tests/integration/
  test_metrics_pipeline.py` (4 теста).
- R-009 (`SpecialProcedureTenYearLimitRule`) **откачен** — правило
  требовало юридической сверки точной статьи УПК и порога санкции,
  регрессионных тестов не было (нарушение AGENTS.md). ENGINE_VERSION
  1.2.0 → 1.1.0. `data/fixtures/norms/upk_rf_special_procedure.json`
  очищен от битого хвоста (текст ст. 314 был в неверной кодировке).
- `.gitignore`: добавлены `.last_eval.json`, `.eval_stdout.json`,
  `.eval_stderr.txt` (артефакты прогона `make eval`).
- Итог: **242 теста зелёных**, 7 skipped (gated), ruff зелёный,
  `make eval` exit 0; метрики прежние (macro-F1=0.978, evidence=1.0,
  Recall@5=1.0/0.933, MRR=0.893/0.833, nDCG=0.921/0.875,
  citation_integrity=1.0, temporal=1.0).

## 0.17 — уборка по итогам аудита (2026-08-30)

- `ruff check` зелёный (0 ошибок): удалены `tests/integration/test_debug_auth.py`,
  `debug_login.py`, `chat-gpt-answer.md`, переэкспорт `src/second_opinion/auth/routes.py`.
- `auth_routes.py` переписан: исправлен сломанный `POST /auth/users` (обращался
  к несуществующей `credentials`), B008 на `Depends` устранён через
  `Annotated[AuthService, Depends(get_auth_service)]`, добавлен `POST /auth/users`
  с моделью `CreateUserRequest`, `PATCH /auth/users/{id}`, `DELETE /auth/users/{id}`.
- `AuthService.delete_user` добавлен (раньше DELETE эндпоинт падал бы в
  рантайме).
- 15 admin-тестов в `test_auth_admin.py` закрывают регрессию.
- `test_auth.py` очищен от неиспользуемых импортов и дубликатов.
- `conftest.py` подменяет `SO_AUTH_TOKEN` на 32+ байт, чтобы JWT-предупреждение
  pyjwt (RFC 7518) не шумело.
- `pyproject.toml`: `per-file-ignores` для `domain/users.py` (`UP042` —
  совместимость с Pydantic v2, миграция на `StrEnum` — отдельная задача).
- `docs/AUDIT_REPORT.md` — комплексный аудит проекта.
- Итог: **233 теста зелёных**, 7 skipped (gated), 1 warning (внешний starlette/httpx).

## 0.16 — OCR для сканированных PDF (2026-08-26)

- Опциональный OCR-фолбэк для PDF без текстового слоя: Tesseract
  (`pytesseract` + `pdf2image` + `tesseract-ocr-rus/eng` + `poppler`);
  группа зависимостей `ocr`, Docker ставит системные пакеты, отсутствие —
  честный отказ с подсказкой. 5 unit-тестов (mock) + 1 интеграционный (skip без tesseract).

## 0.15 — корпус Пленума и исторические редакции (2026-08-26)

- Два разъяснения Пленума ВС РФ № 58 (п. 1 к ст. 60, п. 34 к ст. 73) как
  черновые фикстуры; поиск по источникам теперь покрывает ППВС.
- Историческая редакция ст. 158 ч. 1 (1996–2011 / с 07.12.2011) как демо
  временных интервалов реальных норм; датасет retrieval 13 → 15 запросов,
  temporal 4 → 6 кейсов (метрики lexical 1.0/0.893, hybrid 0.933/0.833).

## 0.14 — pgvector для семантики норм (2026-08-26)

- `PgVectorNormStore` (pgvector HNSW, `vector(1024)`, cosine): sync всех
  редакций, `rank`/`vector_search` через `<=>`; образ
  `pgvector/pgvector:pg16`; `SO_RAG_MODE=hybrid_pgvector` (RRF + ANN).
- Гибридный реранкинг теперь опционально через БД, fallback in-memory;
  gated-тесты pgvector — skip без `SO_TEST_DATABASE_URL`.

## 0.13 — PostgreSQL-бэкенд документов/отчётов (2026-08-26)

- `PostgresJsonRepository` (JSONB) за тем же интерфейсом, что файловый;
  выбор `SO_STORAGE_BACKEND=file|postgres` + `SO_DATABASE_URL`;
  `docker-compose --profile postgres` (+ healthcheck); gated-тесты против
  реального PostgreSQL.

## 0.12 — авторизация API и производительность (2026-08-26)

- Опциональная Bearer-авторизация `SO_AUTH_TOKEN` для `/api/*` (401 без
  токена; `/health`/статика/UI открыты); форма входа в UI (sessionStorage).
- Смок-тест большого документа ~175 тыс. символов (<1 с, порог 15 с против
  регрессий).

## 0.11 — надёжность хранилища и UX отчёта (2026-08-26)

- Тесты конкурентного `ingest`/`analyze` (threadpool FastAPI); печатная
  версия `@media print` + выгрузка JSON отчёта; `docs/API.md`.

## 0.10 — право на забвение и supply-chain (2026-08-26)

- `DELETE /api/documents/{id}` (каскад отчётов) и `DELETE /api/analyses/{id}`
  с аудитом; PRIVACY.md обновлён.
- pip-audit в CI переведён в блокирующий режим; зависимости чисты.
- Печатная версия отчёта (`@media print`) и скачивание JSON из UI.
- docs/API.md; тесты конкурентного доступа к файловому хранилищу (+6).

## 0.9 — человек в контуре: добавление фактов (2026-08-26)

- `POST /api/analyses/{id}/facts`: судья добавляет обстоятельство
  «с нуля»; цитата обязана дословно находиться в документе; схемная
  валидация значений; форма в UI. База дел 22 → 26.

## 0.8 — групповое деяние и временное демо (2026-08-26)

- Факт `group_offense` (группа лиц / сговор / организованная группа),
  критерий отбора сопоставимых дел, признак в аналитике.
- Сквозное демо временных редакций: один документ по синтетической
  ст. 999 даёт разные исходы R-001 на разных датах.

## 0.7 — краевые случаи и наблюдаемость (2026-08-26)

- Образцы-конфликты для R-007/R-008 и границы ч. 3 ст. 73; исправлены
  паттерны («рецидив» в конце строки, «ранее дважды судим»).
- Связь «факт → проверки» в UI; структурные логи стадий конвейера;
  тест воспроизводимости между экземплярами конвейера.

## 0.6 — гибридный поиск и правила R-007/R-008 (2026-08-26)

- Опциональный гибридный реранкинг (RRF + офлайн HashingTfidfEmbedder);
  дефолт — лексический по измерению (ADR-003 обновлён).
- Правило R-007 (ч. 2 ст. 420 УПК: несовершеннолетние × особый порядок)
  и R-008 (ч. 2 ст. 63 УК: рецидив × пп. «и»/«к»); норма УПК в хранилище.
- Негативный контроль и per-type метрики в evaluation; dependabot +
  pip-audit в CI.

## 0.5 — evaluation: retrieval и temporal (2026-08-25)

- Метрики Recall@5 / MRR / nDCG@10, целостность цитирования, точность
  выбора редакции на дату; пороги приёмки в `make eval`.
- SECURITY.md (модель угроз), PRIVACY.md, DEPLOYMENT.md,
  DEVELOPMENT.md, RULE_ENGINE.md, LEGAL_RAG.md, DATA_SOURCES.md,
  EVALUATION.md, GLOSSARY.md, ROADMAP.md; Dockerfile + compose;
  фикс упаковки промптов для pip/Docker.

## 0.4 — M6: база практики (2026-08-25)

- 22 синтетических дела; строгий отбор по содержательным признакам;
  суд присяжных; распределение признаков в аналитике.

## 0.3 — M5 Legal RAG + human-in-the-loop (2026-08-25)

- Лексический поиск по базе источников с обязательным цитированием
  (`GET /api/search`, раздел «Источники»).
- Подтверждение/исключение/правка фактов судьёй с пересчётом отчёта.

## 0.2 — вертикальный срез (2026-08-25)

- Ingestion TXT/MD/DOCX/PDF; паттерновый экстрактор с evidence;
  NormStore с временными редакциями; правило-движок (6 правил);
  поиск сопоставимых дел; аналитика; API + UI; аудит-журнал;
  первый evaluation-набор.

## 0.1 — foundation (2026-08-25)

- Каркас проекта, доменные модели, ADR-001…007, CI, документация.
