"""Команды корпуса норм: покрытие, импорт, опечатывание, правка редакций."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import load_config
from .redactions import pravo_source


def norms_coverage(strict: bool) -> int:
    """Отчёт о редакционном покрытии норм."""
    from ..api.deps import build_norm_store
    from ..legal_sources.coverage import analyze_store, format_report, needs_legal_review

    config = load_config()
    # Аудит должен отражать носитель, который реально обслуживает запросы.
    try:
        store = build_norm_store(config)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    if config.norms_backend != "file":
        print(f"Носитель норм: {config.norms_backend}", file=sys.stderr)
    coverages = analyze_store(store)
    print(format_report(coverages))
    if strict and needs_legal_review(coverages):
        return 1
    return 0
def norms_import() -> int:
    """Первичный импорт файлового корпуса норм в PostgreSQL."""
    from ..legal_sources.postgres_store import PostgresNormStore
    from ..legal_sources.store import NormStore

    config = load_config()
    if not config.database_url:
        print(
            "ОШИБКА: SO_DATABASE_URL не задан — импортировать некуда.",
            file=sys.stderr,
        )
        return 2
    try:
        source = NormStore.from_directory(config.fixtures_dir / "norms")
        target = PostgresNormStore(config.database_url)
        count = target.sync_from(source)
    except Exception as exc:  # noqa: BLE001 — сообщение пользователю, не трассировка
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    print(f"Импортировано норм: {count}")
    print(
        "Целостность в базе обеспечивают аудит-журнал и права доступа, "
        "а не манифест: контрольные суммы файлов на этот носитель не распространяются."
    )
    return 0
def norms_confirm_history(
    norm_id: str, checked_through: str, source: str, source_document: str | None
) -> int:
    """Зафиксировать сверку истории изменений нормы.

    С ``--source-document`` заодно ставится точка отсчёта для обнаружения
    устаревания: номер текущей редакции документа спрашивается у ИПС и
    записывается в действующую редакцию нормы. Без этой отметки
    ``norms-check-updates`` о норме сказать нечего.
    """
    from datetime import date as _date

    from ..legal_sources.editing import NormEditError, confirm_history
    from ..legal_sources.freshness import edition_stamp
    from ..legal_sources.pravo_gov import prepared_editions
    from ..legal_sources.redaction_source import RedactionSourceError

    try:
        checked = _date.fromisoformat(checked_through)
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    stamp = ""
    if source_document:
        pravo, code = pravo_source()
        if pravo is None:
            return code
        try:
            editions = prepared_editions(pravo.editions(source_document))
        except RedactionSourceError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 2
        if not editions:
            print(
                f"ОШИБКА: у документа {source_document} нет ни одной подготовленной "
                "редакции — отмечать нечего",
                file=sys.stderr,
            )
            return 2
        latest = editions[-1]
        try:
            stamp = edition_stamp(
                pravo.name,
                source_document,
                latest.number or 0,
                checked_through=checked,
                act_date=latest.act_date,
            )
        except ValueError as exc:
            print(f"ОШИБКА: {exc}", file=sys.stderr)
            return 2

    try:
        path = confirm_history(
            load_config().fixtures_dir / "norms",
            norm_id,
            checked_through=checked,
            source=source,
            source_edition=stamp,
        )
    except (NormEditError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    print(f"История сверена: {norm_id} → {path}")
    if stamp:
        print(f"Точка отсчёта записана: {stamp} — устаревание теперь отслеживается")
    else:
        print(
            "Точка отсчёта не записана: без --source-document команда "
            "norms-check-updates об этой норме сказать ничего не сможет"
        )
    print("Опечатайте корпус: second-opinion norms-seal")
    return 0
def norms_add_version(args: argparse.Namespace) -> int:
    """Добавить редакцию нормы с фактическими границами."""
    from datetime import date as _date

    from ..legal_sources.editing import NormEditError, add_version

    text_path = Path(args.text_file)
    if not text_path.exists():
        print(
            f"ОШИБКА: файл с текстом редакции не найден: {text_path}\n"
            "--text-file указывает на файл, который готовите вы: в нём текст\n"
            "нормы в этой редакции, взятый из официального источника.",
            file=sys.stderr,
        )
        return 2
    try:
        text = text_path.read_text(encoding="utf-8")
        path = add_version(
            load_config().fixtures_dir / "norms",
            args.norm_id,
            version_id=args.version_id or f"v-{args.effective_from[:4]}",
            text=text,
            effective_from=_date.fromisoformat(args.effective_from),
            effective_to=_date.fromisoformat(args.effective_to) if args.effective_to else None,
            source_url=args.source_url,
            source_edition=args.source_edition,
        )
    except (NormEditError, ValueError, OSError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    print(f"Редакция добавлена: {args.norm_id} → {path}")
    print("Статус draft; пределы санкции (sanctions) внесите вручную.")
    print("Затем: second-opinion norms-seal")
    return 0
def norms_seal(check_only: bool) -> int:
    """Опечатать нормативный корпус или сверить его с манифестом."""
    from ..legal_sources.sealing import check, seal

    config = load_config()
    if config.norms_backend != "file":
        print(
            f"ВНИМАНИЕ: норму обслуживает носитель «{config.norms_backend}», "
            "а опечатывание работает с файлами — проверка ничего не говорит "
            "о том, что отдаётся из базы.",
            file=sys.stderr,
        )
    directory = config.fixtures_dir / "norms"
    report = check(directory) if check_only else seal(directory)
    print(report.describe())
    if check_only and not report.ok:
        print(
            "\nПосле юридической сверки опечатайте корпус: "
            "`second-opinion norms-seal`.",
            file=sys.stderr,
        )
        return 1
    return 0
