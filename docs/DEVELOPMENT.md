# DEVELOPMENT

## Первичная настройка

```bash
git clone <repo> && cd ai-court
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"   # Windows
.venv/bin/pip install -e ".[dev]"       # Linux/macOS
```

## Ежедневный цикл

| Действие | Windows | Linux/macOS |
|---|---|---|
| Запуск приложения | `.venv\Scripts\second-opinion serve` | `make dev` |
| Тесты | `.venv\Scripts\pytest` | `make test` |
| Линтер | `.venv\Scripts\ruff check .` | `make lint` |
| Автофикс линтера | `.venv\Scripts\ruff check --fix .` | `make fmt` |
| Evaluation | `.venv\Scripts\second-opinion eval` | `make eval` |

Перед каждым коммитом: `ruff check` чистый, `pytest` зелёный, при изменении
логики извлечения/поиска/правил — `eval` без нарушения порогов.

## Структура исходников

```
src/second_opinion/
    domain/          # Pydantic-модели (факты, нормы, правила, отчёт)
    ingestion/       # парсеры TXT/MD/DOCX/PDF → Document с оффсетами
    fact_extraction/ # паттерновый и LLM-экстракторы, слияние, статусы
    legal_sources/   # NormStore: редакции норм, get_norm(id, date)
    rule_engine/     # детерминированные проверки (без LLM)
    legal_rag/       # лексический поиск по базе источников
    retrieval/       # поиск сопоставимых дел
    analytics/       # описательная статистика
    audit/           # JSONL-аудит (хэши/версии, без текстов)
    llm/             # LLMProvider + мок/OpenAI-совместимый адаптеры
    prompts/         # версионируемые промпты (часть пакета)
    api/            # FastAPI-роуты (без бизнес-логики)
    storage/         # файловые репозитории за интерфейсом
    static/          # UI (ванильный JS)
evaluation/         # метрики, датасеты, отчёты (см. docs/EVALUATION.md)
data/fixtures/      # синтетические данные (нормы, дела, образцы)
docs/               # документация и ADR
tests/{unit,integration}
```

## Правила разработки

1. **Слои не смешиваются:** бизнес-логика — в модулях, роуты только
   транслируют HTTP ↔ pipeline; LLM-вызовы — только через `LLMProvider`.
2. **Юридическое правило = код + тесты.** Новое правило движка добавляется
   вместе с регрессионными сценариями в `tests/unit/test_rule_engine.py`.
3. **Факт без цитаты не существует.** Любой новый экстрактор обязан
   выдавать evidence с оффсетами; цитата обязана находиться в документе.
4. **Промпты** живут в `prompts/`, версионируются (`*_v1.md`), версия
   пишется в аудит.
5. **Данные:** в `data/fixtures` — только синтетические/обезличенные
   материалы с пометкой; реальные источники — отдельный процесс юридической
   сверки (docs/DATA_SOURCES.md).
6. **Секреты** — только через окружение.

## Работа с ADR

Фундаментальные решения фиксируются в `docs/ADR/ADR-NNN-*.md`: контекст →
решение → последствия. Новое решение, которое трудно обратить (хранение,
провайдеры, модель данных), обязано получить ADR до реализации.

## Definition of Done

- код написан и запускался;
- есть тесты (юнит; для сквозных изменений — интеграционные);
- lint и тесты зелёные;
- документация отражает реальное поведение;
- обновлён `docs/IMPLEMENTATION_STATUS.md`.
