# CHANGELOG

Формат: краткие сводки по итерациям разработки. Проект в активной
разработке (конкурс «Алгоритм правосудия. Второе мнение»); версии
условны.

## 0.22 — юридическая сверка норм, удаление ложных R-005/R-008 (2026-09-03)

Юр. сверка по КонсультантПлюс (ред. УК от 04.08.2026, УПК от 26.07.2026,
ППВС № 58 от 23.12.2025, ППВС № 60 от 29.06.2021) выявила ложные нормы
в движке — исправлено до демонстраций:

- **Удалён R-005** («совокупный предел 1/3 по ч. 3 ст. 62»): такой дроби
  в ст. 62 нет (1/3 относится к минимуму при рецидиве по ч. 2 ст. 68).
- **Удалён R-008** («неучёт рецидива по ч. 2 ст. 63»): ч. 2 ст. 63 о
  запрете двойного учёта квалифицирующего признака, а не о соотношении
  с пп. «и»/«к»; корректное поведение (рецидив блокирует ч. 1 ст. 62)
  уже даёт R-004 (п. 36 ППВС № 58).
- **Новый R-010** вместо R-005: неоконченное в особом порядке —
  M × доля ст. 66 × 2/3 (п. 14 ППВС № 60; 7 регрессионных сценариев).
- **R-003:** предел 2/3 привязан к ч. 5 ст. 62 УК + ч. 7 ст. 316 УПК
  (было: ч. 2 ст. 62). **R-006:** порог 8 лет — ч. 1 ст. 73 (было: ч. 3;
  поведение то же). **R-007:** добавлено оперативное основание —
  п. 7 ППВС № 60 (ч. 2 ст. 420 сама запрета не содержит).
- ENGINE_VERSION 1.2.0 → 2.0.0; правил 9 → 8; регрессий 41 → 39.
- **Фикстуры → `verified`:** Общая часть (60/61/62/63/64/66/73, у 62 —
  история 3/4 до 07.2009), Особенная (максимумы подтверждены; история
  158/159 с 12.2003, 111 с 03.2011; новые 112 ч. 1, 163 ч. 1),
  УПК (420 переписан, добавлен 316 ч. 7), ППВС № 58 (п. 34 исправлен —
  порядок исчисления пределов, не условное; добавлен п. 36), новый файл
  ППВС № 60 (пп. 7/13/14). Сознательно не реконструированы: альтернативы
  ФЗ № 26-ФЗ, дореформенная ст. 228, исключения ч. 1 ст. 73.
- **Eval:** retrieval 15 → 18 запросов (q16–q18 по новым пленумным;
  q13 заменён — «взятка» стала находиться через «лицу»),
  temporal 6 → 8 кейсов (история ст. 62). Пороги — exit 0:
  Recall@5=1.0/0.944, temporal=1.0.
- Процедура добавления правил (`RULE_ENGINE.md`) ужесточена: ссылки
  только на `verified`-редакции.
- Итог: **271 тест зелёный**, 10 skipped (gated), ruff зелёный.

## 0.21 — ADR-008, UI a11y, eval-корпус, R-009, парольные политики (2026-09-03)

- **ADR-008** (`docs/ADR/ADR-008-two-layer-auth.md`): `/api/*` при заданном
  `SO_AUTH_TOKEN` принимает общий секрет **или** JWT access-токен
  (исправлена взаимоисключаемость слоёв); `login/refresh` открыты.
  Тесты: `tests/integration/test_auth_layers.py` (3).
- **UI a11y/адаптив:** skip-link, скрытые подписи, `role=alert`/`aria-live`,
  клавиатурные раскрытия (`role=button`, `aria-expanded`), `:focus-visible`,
  `@media (max-width: 720px)`. Тесты: `test_ui_a11y.py` (3).
- **Eval-корпус 12 → 15:** `sample-163-jury-preparation` (присяжные +
  приготовление — первое покрытие `jury_trial`), `sample-112-…`
  (дети + возмещение + условное), `sample-negative-expertise` (2-й
  негативный контроль). macro-F1 0.978 → 0.982, пороги — exit 0.
- **Фикс сроков:** «2 (два) года» → 24 мес. (раньше не парсилось);
  календарные годы («2025 года») больше не дают 24300 мес. (дефект бил и по
  эталонному sample_111). Тесты: 4 в `test_pattern_extractor.py`.
- **R-009** «Полнота данных о наказании» (структурное, без толкования норм;
  `norms_used` пусто — честно): WARNING при виде без размера и наоборот.
  ENGINE_VERSION 1.1.0 → 1.2.0; регрессий 37 → 41; `RULE_ENGINE.md`,
  `EVALUATION.md` обновлены. Материально-правовые правила сверх этого —
  только после юр. сверки (фикстуры — draft).
- **Парольные политики:** история (`SO_PASSWORD_HISTORY_DEPTH=5`),
  блокировка (`SO_LOGIN_MAX_ATTEMPTS=5`/`SO_LOGIN_LOCKOUT_MINUTES=15`,
  403; сброс — успех/истечение/смена пароля). Тесты: 6 в
  `test_auth_store.py`; `.env.example`, `API.md` обновлены.
- Итог: **273 теста зелёных**, 10 skipped (gated), ruff зелёный,
  `make eval` exit 0.

## 0.20 — PostgreSQL user store (2026-09-03)

- `UserStore` за интерфейсом (`src/second_opinion/auth/repository.py`):
  `InMemoryUserStore` (дефолт) / `PostgresUserStore` (JSONB `users` +
  `auth_refresh_blacklist` + `auth_login_audit`; автовыбор при
  `SO_STORAGE_BACKEND=postgres` + `SO_DATABASE_URL`).
- Refresh-ротация: `jti` в `TokenPayload`; предъявленный refresh
  отзывается до выпуска новой пары; повтор — 401. `POST /auth/logout`
  принимает опциональный `{"refresh_token"}` для явного отзыва.
- Истечение паролей: `User.password_changed_at` +
  `SO_PASSWORD_MAX_AGE_DAYS` (0 — выкл.); просрочка — 403 на login,
  снимается сменой (`POST /auth/change-password`) или сбросом admin
  (`PATCH /api/auth/users/{id}` с `{"password"}`).
- Аудит входов без паролей: `LoginAttempt`, `GET /api/auth/audit`
  (admin, `?limit=`).
- Тесты: `tests/unit/test_auth_store.py` (9) + gated
  `tests/integration/test_auth_postgres.py` (3, skip без
  `SO_TEST_DATABASE_URL`). Доки: `API.md`, `ROADMAP.md`,
  `IMPLEMENTATION_STATUS.md`, `.env.example`.
- Итог: **253 теста зелёных**, 10 skipped (gated), ruff зелёный,
  `make eval` exit 0 (метрики без изменений).

## 0.19 — актуализация документации, /metrics endpoint (2026-09-02)

- `GET /metrics` (без auth, loopback-MVP): снимок внутреннего
  `MetricsRegistry`. Если `SO_METRICS_ENABLED != 1`, возвращает
  `{"enabled": false}`. Тесты: `test_metrics_disabled_by_default`,
  `test_metrics_enabled_after_increments`.
- Документация приведена в соответствие с кодом:
  - `docs/API.md` — добавлены эндпоинты `/api/auth/*` (login/refresh/me/
    change-password/logout/users CRUD) и `/metrics`;
  - `docs/DEVELOPMENT.md` — структура исходников (auth/, metrics.py,
    pipeline.py, cli.py, config.py);
  - `docs/DEPLOYMENT.md` — `SO_METRICS_ENABLED`, раздел JWT в сетевом
    развёртывании, `curl /metrics` в проверке;
  - `docs/ARCHITECTURE.md` — секции `auth/`, `metrics.py`; убраны
    нерелевантные пометки «M5/M6» для PostgreSQL/Docker;
  - `docs/SECURITY.md` п.6 — двухслойная авторизация (loopback-MVP +
    JWT), п.8 — Dependabot и pip-audit-блокирующий закрыты;
  - `docs/ROADMAP.md` — закрыты пункты OCR, PostgreSQL, JWT/RBAC,
    метрики; в `Далее` — миграция user store в БД, ADR-008;
  - `docs/IMPLEMENTATION_STATUS.md` — убрано «Личный кабинет» из
    `Not implemented` (уже реализован 0.17); 242 → 244 теста;
  - `docs/EVALUATION.md` — 24 → 37 регрессионных сценариев;
  - `.env.example` — `SO_METRICS_ENABLED`.
- `docs/AUDIT_REPORT.md` закоммичен ранее (0.17), ссылка из STATUS.

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
