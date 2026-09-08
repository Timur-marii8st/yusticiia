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
    from .legal_sources.coverage import analyze_store, format_report, needs_legal_review
    from .legal_sources.store import NormStore

    config = load_config()
    store = NormStore.from_directory(config.fixtures_dir / "norms")
    coverages = analyze_store(store)
    print(format_report(coverages))
    if strict and needs_legal_review(coverages):
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
    if args.command == "norms-coverage":
        return _norms_coverage(args.strict)
    if args.command == "create-admin":
        return _create_admin(args.email, args.full_name, args.password)
    return 2


if __name__ == "__main__":
    sys.exit(main())
