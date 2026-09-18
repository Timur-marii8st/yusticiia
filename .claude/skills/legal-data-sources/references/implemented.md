# Что из этого уже работает в коде

Карта соответствия «решение → реализация». Подробности процедуры — в
документации репозитория, здесь только куда смотреть.

Состояние на 18.09.2026 (итерация 0.40).

## Источники

| Что | Где | Включается |
|---|---|---|
| Интерфейс источника редакций | `src/second_opinion/legal_sources/redaction_source.py` | — |
| ИПС pravo.gov.ru | `legal_sources/pravo_gov.py` | `SO_REDACTION_SOURCE=pravo` |
| ГАРАНТ (вживую не наблюдался) | `legal_sources/garant.py` | `SO_REDACTION_SOURCE=garant` + `SO_GARANT_TOKEN` |
| Заглушка ГАРАНТ (`assumed: true`) | `legal_sources/stub_source.py` | `SO_GARANT_STUB_DIR` |
| Выбор источника | `legal_sources/source_factory.py` | — |
| Офлайн-инжест публикаций | `scripts/fetch_pravo.py` | — |

Таймаут ИПС — `SO_PRAVO_TIMEOUT`, по умолчанию 180 секунд: список
редакций отвечает около 16 секунд, полный текст УК РФ весит 2,6 МБ.

## Команды

```bash
second-opinion redactions --topic 102041891          # список редакций у источника
second-opinion norms-check-amendments --topic 102041891 --article 66
                                                      # менялась ли статья
second-opinion norms-check-updates                    # не устарел ли корпус
second-opinion norms-coverage                         # наряд на юридическую сверку
second-opinion norms-confirm-history --norm-id … --checked-through … \
    --source КонсультантПлюс --source-document 102041891
second-opinion norms-add-version --norm-id … --text-file … --effective-from …
second-opinion norms-seal                             # опечатать корпус
```

`norms-check-updates` возвращает **1**, если норма устарела, и **2**, если
источник недоступен: разные события, разная реакция.

## Автоматика

`.github/workflows/norms-watch.yml` — еженедельно (понедельник):
целостность корпуса, наряд на сверку, сверка с ИПС. При расхождении
заводит issue с меткой `norms-watch`. Корпус не трогает.

## Что ещё не сделано

- **Точки отсчёта заполнены не у всех норм.** `source_edition` есть у тех,
  по которым прошла сверка с `--source-document`; у остальных проверка
  честно отвечает «проверить не удалось». Пока это так, еженедельный
  мониторинг работает вхолостую по большинству норм.
- **Автоматический черновик редакции** (скачать текст новой редакции из
  ИПС и положить `draft`) — обсуждался, не реализован.
- **База приговоров** — ни один из разобранных источников её не даёт.
  В корпусе только синтетические дела, помеченные `synthetic: true`.

## Документация репозитория

- `docs/DATA_SOURCES.md` — процедура наполнения корпуса;
- `docs/NORM_COVERAGE.md` — наряд на сверку, еженедельная проверка;
- `docs/ADR/ADR-009-normative-source-ips-pravo-gov.md` — решение по
  источнику;
- `docs/OPEN_QUESTIONS.md` — незакрытые вопросы, в том числе по ГАРАНТ.
