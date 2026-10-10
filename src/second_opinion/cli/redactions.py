"""Сверка корпуса с ИПС pravo.gov.ru: редакции, обновления, изменения."""

from __future__ import annotations

import sys

from ..config import load_config


def pravo_source():
    """Собрать источник ИПС или объяснить, почему не вышло.

    Возвращает ``(источник, None)`` либо ``(None, код возврата)``. Обе
    команды сверки требуют именно ИПС: им нужны тексты редакций, которых
    общий интерфейс ``RedactionSource`` не отдаёт.
    """
    from ..legal_sources.pravo_gov import PravoGovSource
    from ..legal_sources.redaction_source import RedactionSourceError
    from ..legal_sources.source_factory import build_redaction_source

    try:
        source = build_redaction_source(load_config())
    except (RedactionSourceError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return None, 2

    if not isinstance(source, PravoGovSource):
        print(
            f"ОШИБКА: команда работает только с ИПС, а выбран источник "
            f"{getattr(source, 'name', '?')!r}. Задайте SO_REDACTION_SOURCE=pravo",
            file=sys.stderr,
        )
        return None, 2
    return source, None
def norms_check_updates() -> int:
    """Сверить действующие редакции корпуса с последними у источника."""
    from ..api.deps import build_norm_store
    from ..legal_sources.freshness import CURRENT, UNRECORDED, check_updates
    from ..legal_sources.redaction_source import RedactionSourceError

    source, failure = pravo_source()
    if source is None:
        return failure
    try:
        store = build_norm_store(load_config())
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    items = [(norm, store.versions(norm.norm_id)) for norm in store.list_norms()]
    try:
        report = check_updates(source, items)
    except RedactionSourceError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    print(f"Источник: {report.source}. Проверено действующих редакций: {report.checked}")
    print()

    if report.outdated:
        print("ТРЕБУЮТ СВЕРКИ — редакция документа отличается от записанной:")
        for item in report.outdated:
            print(
                f"  {item.ref} ({item.version_id}): у нас {item.recorded}, "
                f"у источника {item.latest}"
            )
            if item.note:
                print(f"    {item.note}")
        print("Изменение конкретной статьи этим сравнением не установлено.")
        print()

    current = report.by_status(CURRENT)
    if current:
        print(f"Актуальны: {len(current)}")

    if report.unverifiable:
        print()
        print("Проверить не удалось:")
        for item in report.unverifiable:
            print(f"  {item.ref} ({item.version_id}) — {item.note or item.status}")
        print()

    if report.by_status(UNRECORDED):
        print(
            "Редакция источника записывается двумя способами:\n"
            "  сверили текст действующей редакции —\n"
            "    second-opinion norms-confirm-history --norm-id НОРМА \\\n"
            "        --checked-through ГГГГ-ММ-ДД --source КонсультантПлюс \\\n"
            "        --source-document 102041891\n"
            "  вносите новую редакцию —\n"
            "    second-opinion norms-add-version --norm-id НОРМА \\\n"
            "        --text-file ФАЙЛ.txt --effective-from ГГГГ-ММ-ДД \\\n"
            "        --source-edition pravo:102041891:365"
        )

    return 0 if report.ok else 1
def norms_check_amendments(topic: str, articles: list[str]) -> int:
    """Сверить по ИПС, менялся ли текст статей с первой редакции.

    Отвечает на вопрос аудита покрытия: верно ли записанное в фикстуре
    утверждение «норма с момента принятия не менялась».
    """
    from ..legal_sources.pravo_gov import check_amendments
    from ..legal_sources.redaction_source import RedactionSourceError

    source, failure = pravo_source()
    if source is None:
        return failure

    try:
        report = check_amendments(source, topic, articles)
    except RedactionSourceError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    print(f"Документ {report.topic}, редакций: {report.editions_total}")
    if report.editions_unprepared:
        print(
            f"Не подготовлено в ИПС: {report.editions_unprepared} — "
            "их текст недоступен"
        )
    print(f"Сравнение: «{report.compared_from}» -> «{report.compared_to}»")
    print()

    unknown = 0
    for verdict in report.verdicts:
        if verdict.changed is None:
            unknown += 1
            print(f"  ст. {verdict.article:<8} НЕ СВЕРЕНА — {verdict.note}")
        elif verdict.changed:
            print(f"  ст. {verdict.article:<8} МЕНЯЛАСЬ — текст отличается от первой редакции")
        else:
            print(f"  ст. {verdict.article:<8} не менялась — текст совпадает с первой редакцией")

    print()
    print(
        "Сравниваются крайние редакции. Изменение с последующим возвратом\n"
        "к прежней формулировке так не видно, поэтому вывод — черновик\n"
        "для юриста, а не заключение. Подтверждение: norms-confirm-history."
    )
    return 1 if unknown else 0
def redactions_report(topic: str) -> int:
    """Показать план редакций документа по внешнему источнику."""
    from ..legal_sources.redaction_source import RedactionSourceError, plan_versions
    from ..legal_sources.source_factory import build_redaction_source

    try:
        source = build_redaction_source(load_config())
        history = source.redactions(topic)
    except (RedactionSourceError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    if not history.live:
        print(
            "ВНИМАНИЕ: источник — заглушка. Данные являются предположением "
            "по документации, а не ответом сервера.",
            file=sys.stderr,
        )

    planned = plan_versions(history)
    print(f"Документ {history.topic}, источник: {history.source}")
    print(f"Редакций: {len(planned)}")
    print()
    for version in planned:
        end = version.effective_to.isoformat() if version.effective_to else "н/в"
        print(f"  {version.version_id:<10} {version.effective_from} -> {end}")
        if version.introduced_by:
            print(f"  {'':<10} введена: {version.introduced_by}")

    if history.uncertain:
        print()
        print("Периоды неопределённости (источник не уверен):")
        for period in history.uncertain:
            start = period.valid_from.isoformat() if period.valid_from else "?"
            end = period.valid_to.isoformat() if period.valid_to else "?"
            print(f"  {start} … {end} — {period.description or 'без пояснения'}")

    print()
    print(
        "Это границы редакций, а не готовые нормы: текст и пределы санкции\n"
        "(sanctions) вносит юрист — из источника они не извлекаются."
    )
    return 0


def norms_crosscheck(topic: str) -> int:
    """Сверить пределы лишения свободы в корпусе с текстом статей в ИПС."""
    from ..api.deps import build_norm_store
    from ..legal_sources.crosscheck import crosscheck_store, format_crosscheck
    from ..legal_sources.redaction_source import RedactionSourceError

    source, failure = pravo_source()
    if source is None:
        return failure

    try:
        store = build_norm_store(load_config())
        label, results = crosscheck_store(store, source, topic)
    except (RedactionSourceError, ValueError, OSError, RuntimeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    print(format_crosscheck(label, results))
    print()
    print(
        "Сверяется только лишение свободы на срок в действующих версиях УК РФ.\n"
        "«Не сверено» не означает совпадение: формулировку разобрать не удалось.\n"
        "Расхождение - повод проверить и данные корпуса, и разбор текста."
    )
    return 1 if any(r.status == "mismatch" for r in results) else 0


def norms_trace_history(
    topic: str,
    articles: list[str],
    out: str,
    exhaustive: bool = False,
    cache: str | None = None,
) -> int:
    """Найти по ИПС редакции, в которых менялся текст статей, и записать черновик для юриста."""
    import hashlib
    import json
    from datetime import date
    from pathlib import Path

    from ..legal_sources.crosscheck import parse_imprisonment, split_parts
    from ..legal_sources.history_trace import trace_article_history
    from ..legal_sources.redaction_source import RedactionSourceError

    source, failure = pravo_source()
    if source is None:
        return failure

    def progress(index: int) -> None:
        print(f"  загружена редакция с индексом {index}", file=sys.stderr, flush=True)

    try:
        history = trace_article_history(
            source,
            topic,
            articles,
            on_fetch=progress,
            cache_path=Path(cache) if cache else Path(out).with_suffix(".cache.json"),
            exhaustive=exhaustive,
        )
    except (RedactionSourceError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    method = (
        "сравнены все соседние подготовленные редакции"
        if exhaustive
        else "редакции сравнивались делением пополам, возврат к прежней формулировке не виден"
    )
    payload: dict = {
        "verification_status": "draft",
        "note": (
            "Черновик истории изменений статей по консолидированным редакциям ИПС. "
            "Не сверен юристом. Изменение локализовано между соседними подготовленными "
            f"редакциями ({method}); акты без подготовленного текста не видны; дата в "
            "подписи редакции - дата акта, а не вступления в силу."
        ),
        "source": {"system": "ИПС «Законодательство России»", "document_id": topic},
        "retrieved_at": date.today().isoformat(),
        "articles": {},
    }
    for article, versions in history.items():
        entries = []
        for version in versions:
            parts = split_parts(version.text) if version.text else {}
            entries.append(
                {
                    "edition": version.edition,
                    "label": version.label,
                    "act_date": version.act_date.isoformat() if version.act_date else None,
                    "sha256": hashlib.sha256(version.text.encode("utf-8")).hexdigest(),
                    "imprisonment_by_part": {
                        str(number): [str(term) for term in parse_imprisonment(text)]
                        for number, text in parts.items()
                    },
                    "text": version.text,
                }
            )
        payload["articles"][article] = entries
        print(f"ст. {article}: версий {len(versions)}")
        for version in versions:
            print(f"  с редакции {version.edition}: {version.label}")
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Черновик записан: {target}")
    return 0
