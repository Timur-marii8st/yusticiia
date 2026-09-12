from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

from .config import load_config


def _is_loopback(host: str) -> bool:
    """Привязка видна только с самой машины?"""
    import ipaddress

    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Имя хоста вместо адреса — считаем внешним: безопаснее ошибиться
        # в сторону предупреждения.
        return False


def _check_bind_safety(host: str) -> int:
    """Проверить связку «адрес привязки ↔ авторизация».

    При пустом ``SO_AUTH_TOKEN`` весь ``/api/*`` открыт: на loopback это
    осознанный режим демонстрации, на внешнем адресе — публичный доступ к
    текстам судебных актов. Вернуть код выхода (0 — можно продолжать).
    """
    config = load_config()
    if config.auth_token or _is_loopback(host):
        return 0
    message = (
        f"привязка к {host} без SO_AUTH_TOKEN: /api/* будет доступен из сети "
        "без авторизации, включая тексты документов"
    )
    if config.require_auth:
        print(f"ОШИБКА: {message}", file=sys.stderr)
        print("Задайте SO_AUTH_TOKEN либо снимите SO_REQUIRE_AUTH.", file=sys.stderr)
        return 2
    print(f"ВНИМАНИЕ: {message}.", file=sys.stderr)
    print(
        "Задайте SO_AUTH_TOKEN, а для отказа от запуска без него — "
        "SO_REQUIRE_AUTH=1.",
        file=sys.stderr,
    )
    return 0


def _create_admin(email: str, full_name: str, password: str | None) -> int:
    """Завести первого администратора (бутстрап личного кабинета)."""
    import getpass
    import os

    from .auth.service import AdminAlreadyExistsError, bootstrap_admin

    password = password or os.environ.get("SO_ADMIN_PASSWORD") or ""
    if not password:
        if not sys.stdin.isatty():
            print(
                "ОШИБКА: пароль не задан. Передайте --password, задайте "
                "SO_ADMIN_PASSWORD или запустите в интерактивном терминале.",
                file=sys.stderr,
            )
            return 2
        password = getpass.getpass("Пароль администратора: ")
        if password != getpass.getpass("Повторите пароль: "):
            print("ОШИБКА: пароли не совпадают.", file=sys.stderr)
            return 2

    try:
        user = bootstrap_admin(email=email, password=password, full_name=full_name)
    except AdminAlreadyExistsError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2

    # О незаданном SO_JWT_SECRET предупреждает сам auth-сервис при
    # создании — здесь не повторяем.
    config = load_config()
    print(f"Администратор создан: {user.email} (user_id={user.user_id})")
    print(f"Хранилище: {config.storage_backend} ({config.store_dir / 'auth'})")
    return 0


def _serve(host: str, port: int) -> int:
    import uvicorn

    code = _check_bind_safety(host)
    if code != 0:
        return code

    uvicorn.run(
        "second_opinion.api.app:make_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )
    return 0


def _analyze(file_path: Path, applicable_at: str | None) -> int:
    from .api.deps import build_pipeline

    pipeline = build_pipeline(load_config())
    content = file_path.read_bytes()
    document = pipeline.ingest(file_path.name, content)
    report = pipeline.analyze(document.document_id, applicable_at=applicable_at)

    print(f"Документ: {document.filename} ({len(document.text)} символов)")
    print(f"Юридически значимая дата: {report.applicable_at}"
          + (" (предположительно)" if report.applicable_at_assumed else ""))
    print(f"\nИзвлечено фактов: {len(report.facts)}")
    for fact in report.facts:
        evidence = fact.evidence[0].quote if fact.evidence else "—"
        print(f"  [{fact.status.value}] {fact.type.value}: {fact.value!r}  «{evidence[:60]}»")
    print(f"\nПроверок: {len(report.evaluations)}")
    for evaluation in report.evaluations:
        print(f"  [{evaluation.status.value}] {evaluation.headline}")
    print(f"\nСопоставимых дел: {len(report.comparable_cases)}")
    if report.analytics:
        print(f"  медиана срока: {report.analytics.median_months} мес. "
              f"(N={report.analytics.n_cases})")
    return 0


def _norms_coverage(strict: bool) -> int:
    """Отчёт о редакционном покрытии норм."""
    from .api.deps import build_norm_store
    from .legal_sources.coverage import analyze_store, format_report, needs_legal_review

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


def _redactions(topic: str) -> int:
    """Показать план редакций документа по внешнему источнику."""
    from .legal_sources.redaction_source import RedactionSourceError, plan_versions
    from .legal_sources.source_factory import build_redaction_source

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


def _norms_import() -> int:
    """Первичный импорт файлового корпуса норм в PostgreSQL."""
    from .legal_sources.postgres_store import PostgresNormStore
    from .legal_sources.store import NormStore

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


def _norms_confirm(norm_id: str, checked_through: str, source: str) -> int:
    """Зафиксировать сверку истории изменений нормы."""
    from datetime import date as _date

    from .legal_sources.editing import NormEditError, confirm_history

    try:
        path = confirm_history(
            load_config().fixtures_dir / "norms",
            norm_id,
            checked_through=_date.fromisoformat(checked_through),
            source=source,
        )
    except (NormEditError, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    print(f"История сверена: {norm_id} → {path}")
    print("Опечатайте корпус: second-opinion norms-seal")
    return 0


def _norms_add_version(args: argparse.Namespace) -> int:
    """Добавить редакцию нормы с фактическими границами."""
    from datetime import date as _date

    from .legal_sources.editing import NormEditError, add_version

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
        )
    except (NormEditError, ValueError, OSError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    print(f"Редакция добавлена: {args.norm_id} → {path}")
    print("Статус draft; пределы санкции (sanctions) внесите вручную.")
    print("Затем: second-opinion norms-seal")
    return 0


def _norms_seal(check_only: bool) -> int:
    """Опечатать нормативный корпус или сверить его с манифестом."""
    from .legal_sources.sealing import check, seal

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


def _eval() -> int:
    from .config import PROJECT_ROOT

    sys.path.insert(0, str(PROJECT_ROOT))
    from evaluation.run import main as eval_main

    return eval_main()


def main(argv: list[str] | None = None) -> int:
    # Консоли с не-UTF-8 кодировкой не должны ронять вывод.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="second-opinion",
        description="«Второе мнение»: юридический аудит проекта судебного акта",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="запуск веб-приложения")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)

    analyze_p = sub.add_parser("analyze", help="анализ файла в консоли")
    analyze_p.add_argument("file", type=Path)
    analyze_p.add_argument("--applicable-at", default=None, help="ISO-дата (ГГГГ-ММ-ДД)")

    sub.add_parser("eval", help="запуск evaluation-набора")

    coverage_p = sub.add_parser(
        "norms-coverage",
        help="отчёт о редакционном покрытии норм (что требует юридической сверки)",
    )
    coverage_p.add_argument(
        "--strict",
        action="store_true",
        help="ненулевой код выхода, если есть нормы без зафиксированной истории",
    )

    red_p = sub.add_parser(
        "redactions",
        help="план редакций документа по внешнему источнику (SO_REDACTION_SOURCE)",
    )
    red_p.add_argument("--topic", required=True, help="идентификатор документа в источнике")

    sub.add_parser(
        "norms-import",
        help="импортировать файловый корпус норм в PostgreSQL (SO_DATABASE_URL)",
    )

    confirm_p = sub.add_parser(
        "norms-confirm-history",
        help="зафиксировать, что история изменений нормы сверена",
    )
    confirm_p.add_argument("--norm-id", required=True)
    confirm_p.add_argument(
        "--checked-through", required=True, help="дата, по которую сверена история (ГГГГ-ММ-ДД)"
    )
    confirm_p.add_argument(
        "--source", required=True, help="чем сверялась: КонсультантПлюс, ГАРАНТ, pravo.gov.ru"
    )

    add_p = sub.add_parser("norms-add-version", help="добавить редакцию существующей норме")
    add_p.add_argument("--norm-id", required=True)
    add_p.add_argument("--text-file", required=True)
    add_p.add_argument("--effective-from", required=True, help="ГГГГ-ММ-ДД")
    add_p.add_argument("--effective-to", default=None)
    add_p.add_argument("--version-id", default=None, help="по умолчанию v-<год>")
    add_p.add_argument("--source-url", default="")

    seal_p = sub.add_parser(
        "norms-seal",
        help="опечатать нормативный корпус (контрольные суммы + манифест)",
    )
    seal_p.add_argument(
        "--check",
        action="store_true",
        help="только сверить с манифестом, ничего не записывая (для CI)",
    )

    admin_p = sub.add_parser(
        "create-admin",
        help="завести первого администратора (бутстрап личного кабинета)",
    )
    admin_p.add_argument("--email", required=True)
    admin_p.add_argument("--full-name", default="Системный администратор")
    admin_p.add_argument(
        "--password",
        default=None,
        help=(
            "пароль; по умолчанию берётся из SO_ADMIN_PASSWORD либо "
            "запрашивается в терминале (в историю оболочки не попадает)"
        ),
    )

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(args.host, args.port)
    if args.command == "analyze":
        return _analyze(args.file, args.applicable_at)
    if args.command == "eval":
        return _eval()
    if args.command == "redactions":
        return _redactions(args.topic)
    if args.command == "norms-import":
        return _norms_import()
    if args.command == "norms-confirm-history":
        return _norms_confirm(args.norm_id, args.checked_through, args.source)
    if args.command == "norms-add-version":
        return _norms_add_version(args)
    if args.command == "norms-seal":
        return _norms_seal(args.check)
    if args.command == "norms-coverage":
        return _norms_coverage(args.strict)
    if args.command == "create-admin":
        return _create_admin(args.email, args.full_name, args.password)
    return 2


if __name__ == "__main__":
    sys.exit(main())
