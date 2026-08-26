# Второе мнение

Верифицируемая система юридического аудита и поддержки принятия решений для
конкурса Верховного Суда РФ «Алгоритм правосудия. Второе мнение».

## Что это

Система «второго мнения» для судьи: помогает проверить проект судебного акта
по формальным ограничениям назначения наказания и подобрать сопоставимые
материалы.

Конвейер:

```
Судебный документ
  → извлечение юридически значимых фактов (с цитатами-доказательствами)
  → детерминированные проверки правил (пределы санкций, ст. 62/66/73 УК РФ)
  → нормы в редакции, действовавшей на юридически значимую дату
  → поиск норм по базе источников с цитированием (/api/search)
  → сопоставимая практика + описательная статистика
  → проверяемое второе мнение с полной цепочкой обоснования
```

## Чем это НЕ является

- не «ИИ-судья» и не генератор приговора;
- не система, назначающая «правильное» наказание;
- не чёрный ящик: каждое утверждение раскрывается до факта, цитаты, нормы,
  редакции и источника;
- статистика по делам — только описательная, не предписание.

Подробнее: [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md),
принципы безопасности: [docs/LEGAL_SAFETY_PRINCIPLES.md](docs/LEGAL_SAFETY_PRINCIPLES.md).

## Архитектура

Модульный монолит (Python 3.11+, FastAPI, Pydantic v2). Схема и описание
компонентов — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Ключевые решения
зафиксированы в [docs/ADR/](docs/ADR/).

## Быстрый старт

Требования: Python 3.11+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\pip install -e ".[dev]"
# Linux/macOS:
.venv/bin/pip install -e ".[dev]"

# запуск сервера (по умолчанию офлайн: LLM-провайдер — мок)
.venv\Scripts\second-opinion serve          # Windows
.venv/bin/second-opinion serve              # Linux/macOS
# → http://127.0.0.1:8000
```

Эквивалент через make (Linux/macOS): `make install && make dev`.

Без установки: `python -m second_opinion.cli serve` из корня репозитория
(при доступном `src/`).

Внешний LLM по умолчанию ВЫКЛЮЧЕН. Включение — только явно через переменные
окружения (см. `.env.example`), реальные документы во внешний контур без
осознанного решения оператора не отправляются.

## Пример рабочего сценария

1. Откройте `http://127.0.0.1:8000` и нажмите «Демо-образец»
   (синтетический приговор по ч. 2 ст. 158 УК РФ, особый порядок).
2. Нажмите «Проанализировать».
3. Раскройте любое обстоятельство — увидите точную цитату и оффсеты в тексте.
4. Раскройте проверку норм — увидите цепочку: правило → факты → норма →
   редакция → объяснение.
5. Посмотрите сопоставимые дела и описательную статистику.

Консольный анализ файла:

```bash
second-opinion analyze data/fixtures/sample_documents/sample_228_attempt_violation.txt
```

## Структура проекта

```
src/second_opinion/
    domain/            # Pydantic-модели: факты, нормы, оценки, отчёт
    ingestion/         # парсеры TXT/MD/DOCX/PDF(текстовый слой + опциональный OCR)
    fact_extraction/   # паттерновый + LLM экстракторы, сборка обстоятельств
    legal_sources/     # NormStore: нормы с временными редакциями, SHA-256
    rule_engine/       # детерминированные проверки (ст. 60–73 УК РФ)
    legal_rag/         # лексический поиск по базе источников (с цитированием)
    retrieval/         # поиск сопоставимых дел (структурные фильтры)
    analytics/         # описательная статистика
    llm/               # провайдеро-независимый LLM-слой (мок по умолчанию)
    audit/             # JSONL аудит-журнал (версии моделей/промптов/правил)
    api/               # FastAPI + статический экран анализа
    static/            # UI (vanilla JS, без шага сборки)
    prompts/           # версионируемые промпты (часть пакета)
data/fixtures/         # фикстуры: нормы (draft), СИНТЕТИЧЕСКИЕ дела, образцы
tests/                 # unit + integration
evaluation/            # метрики и датасеты (make eval)
docs/                  # документация и ADR
```

## Тестирование

```bash
pytest          # или: make test
ruff check .    # или: make lint
```

## Оценка качества (evaluation)

```bash
second-opinion eval     # или: make eval
```

Три раздела метрик:

- **извлечение фактов:** precision/recall/F1, корректность и покрытие
  доказательств, доля подтверждённых утверждений без доказательств
  (`unsupported_claim_rate`, цель 0);
- **поиск по источникам:** Recall@5, MRR, nDCG@10; честный пустой ответ;
  целостность цитирования (фрагмент = реально хранящаяся редакция,
  SHA-256 сходится);
- **временна́я корректность:** правильность выбора редакции нормы на дату.

Отчёты — в `evaluation/reports/`; пороги приёмки и методика —
[docs/EVALUATION.md](docs/EVALUATION.md).

## Документация

- [docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md) — видение и non-goals
- [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) — аудит репозитория
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — архитектура
- [docs/API.md](docs/API.md) — справочник HTTP API
- [docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) — доменная модель
- [docs/RULE_ENGINE.md](docs/RULE_ENGINE.md) — реестр правил и как добавлять новые
- [docs/LEGAL_RAG.md](docs/LEGAL_RAG.md) — поиск по базе источников
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — источники данных и процесс наполнения
- [docs/EVALUATION.md](docs/EVALUATION.md) — метрики и пороги
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — план (M0–M10)
- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — фактический статус
- [docs/GLOSSARY.md](docs/GLOSSARY.md) — глоссарий
- [CHANGELOG.md](CHANGELOG.md) — история итераций
- [docs/ADR/](docs/ADR/) — архитектурные решения

## Безопасность

Текст документа — недоверенные данные: защита от prompt injection (документ
подаётся модели как данные с явными маркерами, системные инструкции
отделены, цитаты проверяются по тексту). Ключи — только из переменных
окружения, аудит-журнал не хранит сырые тексты. Модель угроз и статус мер —
[docs/SECURITY.md](docs/SECURITY.md); обращение с ПДн —
[docs/PRIVACY.md](docs/PRIVACY.md).

## Статус данных (важно)

- Нормы в `data/fixtures/norms/` — черновые (`verification_status: draft`):
  перед демонстрацией обязательна сверка с официальным источником.
- Дела в `data/fixtures/cases/` — СИНТЕТИЧЕСКИЕ и явно маркируются.

## Запуск в Docker

```bash
docker compose up          # или: docker build -t second-opinion . && docker run ...
# PostgreSQL (опционально, ADR-006): docker compose --profile postgres up
# → http://127.0.0.1:8000 (только loopback; см. SO_AUTH_TOKEN в DEPLOYMENT.md)
```

Подробности профилей развёртывания — [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md),
для разработчиков — [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Дорожная карта

Выполнено M0–M9 и основная часть M10 — см.
[docs/ROADMAP.md](docs/ROADMAP.md) и
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md).
