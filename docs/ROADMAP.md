# ROADMAP

Приоритеты (не менять порядок): юридическая корректность → проверяемость →
воспроизводимость → надёжность → UX → скорость → ML sophistication.

## Выполнено

- M0 Foundation: каркас, доменные модели, ADR-001…007, CI
- M1 Document ingestion: TXT/MD/DOCX/PDF(текстовый слой)
- M2 Legal facts: паттерновый + LLM экстракторы, evidence, статусы
- M3 Rule engine: 8 правил Общей части УК и УПК с регрессионными тестами
- M4 Legal sources: NormStore с временными редакциями и SHA-256
- M5 Legal RAG: лексический поиск с обязательным цитированием
- M6 Comparable cases: база 26 синтетических дел, строгий отбор с причинами
- M7 Analytics: описательная статистика + распределение признаков
- M8 UI: прогрессивное раскрытие, коррекция фактов человеком
- M9 Evaluation: P/R/F1 + evidence + unsupported; Recall@5/MRR/nDCG@10;
  целостность цитирования; временная корректность (`make eval`)

## Текущий этап — M10 Hardening

- [x] Модель угроз (docs/SECURITY.md) со статусами мер
- [x] docs/PRIVACY.md, DEPLOYMENT.md, DEVELOPMENT.md
- [x] Dockerfile + docker-compose (локальный профиль)
- [x] pip-audit / dependabot в CI — блокирующий режим (зависимости чисты)
- [x] structured request-id логирование стадий конвейера
  (`logging_utils`, без текстов документов)
- [x] право на забвение: DELETE документа/отчёта через API (каскад)
- [x] OCR-фолбэк для сканов без текстового слоя (0.16, Tesseract,
  опциональная группа `ocr`)
- [x] PostgreSQL + pgvector (0.13–0.14, ADR-006): JSONB-репозиторий,
  `PgVectorNormStore`, `SO_RAG_MODE=hybrid_pgvector`
- [x] JWT/RBAC + пользователи + admin-CRUD (0.17): `/api/auth/*`,
  `AuthService`, in-memory MVP. Миграция в БД — отдельная задача
- [x] Внутренние метрики конвейера (0.18): `metrics.py`, `/metrics` endpoint
- [x] PostgreSQL user store (0.20): `UserStore` за интерфейсом
  (in-memory / `PostgresUserStore` при `SO_STORAGE_BACKEND=postgres`);
  refresh-ротация с blacklist (`jti`), истечение паролей
  (`SO_PASSWORD_MAX_AGE_DAYS`), аудит входов (`GET /api/auth/audit`)

## Далее по приоритету

1. **Наполнение корпуса источников** — ✅ частично (0.22): юр. сверка
   по КП 03.09.2026 — `verified` для УК Общей/Особенной, УПК 316/420,
   ППВС № 58 (пп. 1/34/36) и № 60 (пп. 7/13/14); история ст. 62/158/159/
   111. Остаток: обзоры практики сверх этого — только после доп. сверки
   (см. IMPLEMENTATION_STATUS, Known limitations п. 1).
2. **Эмбеддинги + реранкинг** поверх лексического поиска: реализованы
   как опция `SO_RAG_MODE=hybrid` / `hybrid_pgvector` (см. ADR-003);
   переключение дефолта — по метрикам после роста корпуса.
3. **Расширение evaluation-корпуса** — ✅ частично (0.21): 12 → 15
   образцов (присяжные+приготовление ст. 163, ст. 112 с детьми/условным,
   2-й негативный контроль); macro-F1 0.978 → 0.982. Дальнейший рост —
   по мере появления размеченных материалов.
4. ~~**PostgreSQL user store**~~ — **готово (0.20)**. Парольные политики
   сверх max-age — ✅ готово (0.21): история (`SO_PASSWORD_HISTORY_DEPTH`),
   блокировка (`SO_LOGIN_MAX_ATTEMPTS`/`SO_LOGIN_LOCKOUT_MINUTES`).
5. **Расширение домена** — ✅ частично (0.21–0.22): R-009 структурное,
   R-010 (п. 14 ППВС № 60); ложные R-005/R-008 удалены после сверки.
   Новые материально-правовые правила — только с `verified`-нормами.
6. **ADR-008 «Двухслойная авторизация»** — ✅ готово (0.21):
   `SO_AUTH_TOKEN` (loopback-MVP) и JWT сосуществуют через «или» на
   `/api/*` (см. `docs/ADR/ADR-008-two-layer-auth.md`).
7. **UI: дизайн-паспорт, адаптивность, a11y** — ✅ частично (0.21):
   skip-link, подписи, `role=alert`/`aria-live`, клавиатурные раскрытия
   (`role=button`, `aria-expanded`), `:focus-visible`, `@media ≤720px`.
   Полный дизайн-проход — отдельная задача.
8. **Реальный OCR-язык** — тонкая настройка под судебные сканы
   (на текущем этапе `tesseract-ocr-rus+eng`).

## Осознанно НЕ делаем

Kubernetes, Kafka, микросервисы, multi-agent LLM-архитектуры,
генерация решений и «процентов сходства» дел — см. PRODUCT_VISION
(non-goals) и ADR.
