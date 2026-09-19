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
    from ..legal_sources.freshness import CURRENT, check_updates
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
        print("УСТАРЕЛИ — источник знает более новую редакцию:")
        for item in report.outdated:
            print(
                f"  {item.ref} ({item.version_id}): у нас {item.recorded}, "
                f"у источника {item.latest}"
            )
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
    print(f"Сравнение: «{report.compared_from}» → «{report.compared_to}»")
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
        print(f"  {version.version_id:<10} {version.effective_from} → {end}")
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
