"""Консольный интерфейс «Второго мнения».

Разбор аргументов живёт здесь, сами команды — в соседних модулях по
предмету: :mod:`.serve` (веб-приложение и учётные записи), :mod:`.documents`
(разбор файла, обезличивание, метрики), :mod:`.norms` (корпус норм),
:mod:`.redactions` (сверка с ИПС).
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Callable
from pathlib import Path

from .documents import analyze, anonymize, run_evaluation
from .norms import (
    norms_add_version,
    norms_confirm_history,
    norms_coverage,
    norms_import,
    norms_seal,
)
from .redactions import norms_check_amendments, norms_check_updates, redactions_report
from .serve import create_admin, serve

__all__ = ["build_parser", "main"]


def _add_service_commands(sub: argparse._SubParsersAction) -> None:
    serve_p = sub.add_parser("serve", help="запуск веб-приложения")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8000)

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


def _add_document_commands(sub: argparse._SubParsersAction) -> None:
    anon_p = sub.add_parser(
        "anonymize", help="подготовить обезличенную копию документа (Д-06)"
    )
    anon_p.add_argument("file", type=Path, help="файл судебного акта")
    anon_p.add_argument(
        "--output", type=Path, help="куда записать (по умолчанию рядом с исходным)"
    )

    analyze_p = sub.add_parser("analyze", help="анализ файла в консоли")
    analyze_p.add_argument("file", type=Path)
    analyze_p.add_argument("--applicable-at", default=None, help="ISO-дата (ГГГГ-ММ-ДД)")

    sub.add_parser("eval", help="запуск evaluation-набора")


def _add_norm_commands(sub: argparse._SubParsersAction) -> None:
    coverage_p = sub.add_parser(
        "norms-coverage",
        help="отчёт о редакционном покрытии норм (что требует юридической сверки)",
    )
    coverage_p.add_argument(
        "--strict",
        action="store_true",
        help="ненулевой код выхода, если есть нормы без зафиксированной истории",
    )

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
        "--checked-through",
        required=True,
        help="дата, по которую сверена история (ГГГГ-ММ-ДД)",
    )
    confirm_p.add_argument(
        "--source", required=True, help="чем сверялась: КонсультантПлюс, ГАРАНТ, pravo.gov.ru"
    )
    confirm_p.add_argument(
        "--source-document",
        help=(
            "идентификатор документа в ИПС (nd, например 102041891 — УК РФ). "
            "Номер текущей редакции будет запрошен у источника и записан "
            "точкой отсчёта: без неё устаревание нормы не отследить"
        ),
    )

    add_p = sub.add_parser(
        "norms-add-version", help="добавить редакцию существующей норме"
    )
    add_p.add_argument("--norm-id", required=True)
    add_p.add_argument("--text-file", required=True)
    add_p.add_argument("--effective-from", required=True, help="ГГГГ-ММ-ДД")
    add_p.add_argument("--effective-to", default=None)
    add_p.add_argument("--version-id", default=None, help="по умолчанию v-<год>")
    add_p.add_argument("--source-url", default="")
    add_p.add_argument(
        "--source-edition",
        default="",
        metavar="ИСТОЧНИК:ДОКУМЕНТ:РЕДАКЦИЯ",
        help=(
            "какой редакции источника соответствует текст "
            "(например pravo:102041891:365); без неё norms-check-updates "
            "не сможет заметить, что норму изменили"
        ),
    )

    seal_p = sub.add_parser(
        "norms-seal",
        help="опечатать нормативный корпус (контрольные суммы + манифест)",
    )
    seal_p.add_argument(
        "--check",
        action="store_true",
        help="только сверить с манифестом, ничего не записывая (для CI)",
    )


def _add_redaction_commands(sub: argparse._SubParsersAction) -> None:
    red_p = sub.add_parser(
        "redactions",
        help="план редакций документа по внешнему источнику (SO_REDACTION_SOURCE)",
    )
    red_p.add_argument(
        "--topic", required=True, help="идентификатор документа в источнике"
    )

    sub.add_parser(
        "norms-check-updates",
        help="сверить действующие редакции корпуса с последними у источника",
    )

    check_p = sub.add_parser(
        "norms-check-amendments",
        help="сверить по ИПС pravo.gov.ru, менялся ли текст статей",
    )
    check_p.add_argument(
        "--topic",
        required=True,
        help="идентификатор документа в ИПС (для УК РФ — 102041891)",
    )
    check_p.add_argument(
        "--article",
        action="append",
        required=True,
        metavar="НОМЕР",
        help="номер статьи; повторяйте для нескольких (--article 66 --article 60)",
    )


#: Команда → что выполнить. Таблица вместо цепочки условий: в ней сразу видно,
#: какие команды есть, и новая не может потеряться между ветками.
COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "serve": lambda a: serve(a.host, a.port),
    "create-admin": lambda a: create_admin(a.email, a.full_name, a.password),
    "anonymize": lambda a: anonymize(a.file, a.output),
    "analyze": lambda a: analyze(a.file, a.applicable_at),
    "eval": lambda _: run_evaluation(),
    "norms-coverage": lambda a: norms_coverage(a.strict),
    "norms-import": lambda _: norms_import(),
    "norms-confirm-history": lambda a: norms_confirm_history(
        a.norm_id, a.checked_through, a.source, a.source_document
    ),
    "norms-add-version": norms_add_version,
    "norms-seal": lambda a: norms_seal(a.check),
    "redactions": lambda a: redactions_report(a.topic),
    "norms-check-updates": lambda _: norms_check_updates(),
    "norms-check-amendments": lambda a: norms_check_amendments(a.topic, a.article),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="second-opinion",
        description="«Второе мнение»: юридический аудит проекта судебного акта",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    _add_service_commands(sub)
    _add_document_commands(sub)
    _add_norm_commands(sub)
    _add_redaction_commands(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Консоли с не-UTF-8 кодировкой не должны ронять вывод.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(errors="replace")

    args = build_parser().parse_args(argv)
    handler = COMMANDS.get(args.command)
    if handler is None:
        return 2
    return handler(args)
