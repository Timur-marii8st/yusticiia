"""Лист заключения: одно содержание для выгрузки в .docx и для печати.

Лист собирается из готового отчёта и ничего к нему не добавляет: каждая строка
таблиц есть на экране. Заключение в .docx и печатная форма получаются из
одного листа, поэтому разойтись между собой не могут.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.analysis import AnalysisReport, AnalyticsSummary
from ..domain.documents import Document
from ..domain.facts import LegalFact
from ..privacy import detect_personal_data, mask_text
from ..reporting import FACT_TYPE_LABELS, PUNISHMENT_LABELS, format_fact_value

TITLE = "Второе мнение: заключение по проекту судебного акта"

STATUS_LABELS = {
    "PASS": "проверка пройдена",
    "WARNING": "требует внимания",
    "FAIL": "нарушение",
    "UNKNOWN": "данных недостаточно",
    "NOT_APPLICABLE": "не применимо к этому делу",
    "VERIFIED": "подтверждено",
    "LIKELY": "вероятно, не подтверждено",
    "UNCERTAIN": "требует проверки",
    "CONFLICT": "противоречие в документе",
    "NOT_FOUND": "исключено из проверок",
}

METHOD_LABELS = {
    "pattern": "разбор по шаблонам",
    "llm": "языковая модель",
    "user": "внесено судьёй",
}

SEVERITY_LABELS = {
    "violation": "Проверка не сошлась",
    "attention": "Требует внимания",
    "data": "Ограничение данных",
    "statistics": "Статистическое наблюдение",
}

STAGE_SHORT = {
    "completed": "оконченное",
    "attempt": "покушение",
    "preparation": "приготовление",
}

FEATURE_LABELS = {
    "special_procedure": "Особый порядок",
    "guilty_plea": "Признание вины",
    "recidivism": "Рецидив",
    "jury_trial": "Суд присяжных",
    "group": "Групповое деяние",
    "suspended": "Условное осуждение",
}

ROLE_LABELS = {"judge": "Судья", "clerk": "Помощник судьи", "admin": "Администратор"}


@dataclass(frozen=True)
class Preparer:
    """Данные личного кабинета того, кто выполнил анализ."""

    full_name: str
    email: str
    role: str

    @property
    def line(self) -> str:
        role = ROLE_LABELS.get(self.role, self.role)
        return f"{self.full_name}, {role}, {self.email}"


@dataclass(frozen=True)
class Table:
    headers: list[str]
    rows: list[list[str]]
    widths: list[float] = field(default_factory=list)
    key_value: bool = False


@dataclass(frozen=True)
class Block:
    kind: str
    text: str = ""
    table: Table | None = None


@dataclass(frozen=True)
class Sheet:
    title: str
    blocks: list[Block]


def _heading(text: str) -> Block:
    return Block("heading", text)


def _paragraph(text: str, kind: str = "paragraph") -> Block:
    return Block(kind, text)


def _table(table: Table) -> Block:
    return Block("table", table=table)


def _number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}"


def _percent(share: float) -> str:
    return f"{share * 100:.1f}%"


def _fact_line(fact: LegalFact) -> str:
    return f"{FACT_TYPE_LABELS.get(fact.type, fact.type)}: {format_fact_value(fact)}"


def build_sheet(
    report: AnalysisReport,
    document: Document | None = None,
    mask_personal_data: bool = False,
    prepared_by: Preparer | None = None,
) -> Sheet:
    """Собрать лист заключения по готовому отчёту.

    ``mask_personal_data`` скрывает персональные данные в цитатах и в изложении
    (Д-06): обезличивается то, что уходит из системы, а не то, что хранится.
    """

    def visible(value: str) -> str:
        return mask_text(value) if mask_personal_data else value

    facts_by_id = {fact.id: fact for fact in report.facts}
    used_in: dict[str, list[str]] = {}
    for evaluation in report.evaluations:
        for fact_id in evaluation.facts_used:
            used_in.setdefault(fact_id, []).append(evaluation.headline)

    blocks: list[Block] = []

    applicable = report.applicable_at or "не определена"
    if report.applicable_at_assumed:
        applicable += " (предположительно: дата в документе не найдена, взята текущая)"
    card: list[list[str]] = []
    if document is not None:
        card += [
            ["Файл", document.filename],
            ["Контрольная сумма документа (SHA-256)", document.sha256],
        ]
    card += [
        ["Идентификатор документа", report.document_id],
        ["Идентификатор анализа", report.analysis_id],
        ["Дата анализа", report.created_at.isoformat()],
        ["Дата применения норм", applicable],
        ["Извлечено фактов", str(len(report.facts))],
        ["Проверок выполнено", str(len(report.evaluations))],
        ["Сопоставимых дел", str(len(report.comparable_cases))],
    ]
    if document is not None:
        found = detect_personal_data(document.text)
        if found.total:
            card.append(
                [
                    "Обнаружено возможных персональных данных",
                    f"{found.total}"
                    + (" (скрыты в этом файле)" if mask_personal_data else " (в файле не скрыты)"),
                ]
            )
    if prepared_by is not None:
        card.append(["Анализ выполнил (личный кабинет)", prepared_by.line])
    blocks += [
        _heading("1. Дело и документ"),
        _table(Table(["Показатель", "Значение"], card, [1.2, 2.8], key_value=True)),
    ]

    blocks.append(_heading("2. Краткое изложение дела"))
    if report.summary.headline:
        blocks.append(_paragraph(visible(report.summary.headline), "lead"))
    if report.summary.lines:
        rows = []
        for line in report.summary.lines:
            basis = [
                visible(_fact_line(facts_by_id[fact_id]))
                for fact_id in line.fact_ids
                if fact_id in facts_by_id
            ]
            rows.append(
                [
                    visible(line.text),
                    str(len(line.fact_ids)),
                    "\n".join(basis) if basis else "—",
                ]
            )
        blocks.append(
            _table(
                Table(
                    ["Строка изложения", "Обстоятельств в основе", "Какие обстоятельства"],
                    rows,
                    [2.0, 0.8, 2.2],
                )
            )
        )
    else:
        blocks.append(_paragraph("Обстоятельства из документа не извлечены."))

    blocks.append(_heading("3. Проблемные места"))
    if report.concerns:
        rows = [
            [
                SEVERITY_LABELS.get(concern.severity, concern.severity),
                visible(concern.title),
                visible(concern.detail) if concern.detail else "—",
                concern.rule_id or "—",
            ]
            for concern in report.concerns
        ]
        blocks.append(
            _table(Table(["Тип", "Замечание", "Подробности", "Правило"], rows, [1.1, 1.9, 2.4, 0.6]))
        )
    else:
        blocks.append(_paragraph("Проверки прошли, данных хватило: отдельных замечаний нет."))

    blocks.append(_heading("4. Проверка норм"))
    rows = []
    for evaluation in report.evaluations:
        used = [
            _fact_line(facts_by_id[fact_id]) if fact_id in facts_by_id else fact_id
            for fact_id in evaluation.facts_used
        ]
        norms = [
            norm.ref + (f" (редакция {norm.version_id})" if norm.version_id else "")
            for norm in evaluation.norms_used
        ]
        extra = []
        if evaluation.missing:
            extra.append("Не хватает данных: " + "; ".join(evaluation.missing))
        if evaluation.numbers:
            extra.append(
                "Расчёт: " + ", ".join(f"{key} = {value:g}" for key, value in evaluation.numbers.items())
            )
        rows.append(
            [
                evaluation.headline,
                STATUS_LABELS.get(str(evaluation.status.value), str(evaluation.status)),
                "\n".join([visible(evaluation.explanation), *extra]),
                "\n".join(visible(item) for item in used) if used else "—",
                "\n".join(norms) if norms else "—",
                f"{evaluation.rule_id} (версия {evaluation.rule_version})",
            ]
        )
    if rows:
        blocks.append(
            _table(
                Table(
                    ["Проверка", "Результат", "Объяснение и расчёт", "Использованные факты",
                     "Применённые нормы", "Правило"],
                    rows,
                    [1.3, 0.8, 2.2, 1.4, 1.3, 0.8],
                )
            )
        )
    else:
        blocks.append(_paragraph("Проверки не выполнялись."))

    blocks.append(_heading("5. Обстоятельства и доказательства"))
    rows = []
    for fact in report.facts:
        quotes = []
        for evidence in fact.evidence:
            place = f"символы {evidence.start_offset}–{evidence.end_offset}"
            if evidence.page:
                place += f", страница {evidence.page}"
            quotes.append(f"«{visible(evidence.quote)}» ({place})")
        status = [
            STATUS_LABELS.get(str(fact.status.value), str(fact.status)),
            f"метод: {METHOD_LABELS.get(fact.extraction_method, fact.extraction_method)}",
            f"уверенность: {fact.confidence}",
        ]
        if fact.prompt_version:
            status.append(f"промпт: {fact.prompt_version}")
        if fact.rejection_reason:
            status.append(f"причина исключения: {visible(fact.rejection_reason)}")
        checks = used_in.get(fact.id, [])
        rows.append(
            [
                FACT_TYPE_LABELS.get(fact.type, fact.type),
                format_fact_value(fact),
                "\n".join(status),
                "\n".join(quotes) if quotes else "—",
                "\n".join(checks) if checks else "ни одна проверка не использует",
            ]
        )
    if rows:
        blocks.append(
            _table(
                Table(
                    ["Обстоятельство", "Значение", "Статус и происхождение", "Цитата в документе",
                     "Учитывается в проверках"],
                    rows,
                    [1.3, 1.1, 1.5, 2.2, 1.4],
                )
            )
        )
    else:
        blocks.append(_paragraph("Обстоятельства не извлечены."))

    blocks.append(_heading("6. Сопоставимая практика"))
    if report.comparable_cases:
        blocks.append(
            _paragraph(
                "Справочный материал. Сходство не является основанием для вывода о наказании.",
                "note",
            )
        )
        rows = []
        for match in report.comparable_cases:
            case = match.case
            article = f"ст. {case.article}" + (f".{case.article_suffix}" if case.article_suffix else "")
            if case.part:
                article += f" ч. {case.part}"
            term = f"{case.term_months:g} мес." if case.term_months is not None else "—"
            if case.suspended:
                term += ", условно"
            rows.append(
                [
                    case.title + (" (синтетическое дело)" if case.synthetic else ""),
                    f"{article} УК РФ",
                    STAGE_SHORT.get(case.stage, case.stage),
                    term,
                    f"{case.court}; {case.date.isoformat()}",
                    case.summary or "—",
                    "\n".join(match.reasons) if match.reasons else "—",
                ]
            )
        blocks.append(
            _table(
                Table(
                    ["Дело", "Статья", "Стадия", "Наказание", "Суд и дата", "Кратко", "Почему отобрано"],
                    rows,
                    [1.3, 0.8, 0.7, 0.8, 1.2, 1.6, 1.6],
                )
            )
        )
    else:
        blocks.append(_paragraph("Сопоставимые дела в базе практики не найдены."))

    blocks.append(_heading("7. Описательная статистика"))
    blocks += _analytics_blocks(report.analytics)

    if report.norms_applied:
        blocks.append(_heading("8. Применённые нормы"))
        norm_rows = [
            [norm.ref, norm.version_id or "—"] for norm in report.norms_applied
        ]
        blocks.append(
            _table(Table(["Норма", "Редакция"], norm_rows, [3.0, 1.0]))
        )

    if mask_personal_data:
        blocks.append(
            _paragraph(
                "Персональные данные в этом файле скрыты автоматически. Полнота обезличивания "
                "не гарантирована: перед передачей документа проверьте его глазами.",
                "note",
            )
        )
    return Sheet(TITLE, blocks)


def _analytics_blocks(analytics: AnalyticsSummary | None) -> list[Block]:
    if analytics is None:
        return [_paragraph("Недостаточно сопоставимых дел для статистики.")]
    blocks: list[Block] = []
    position = analytics.sentence_position
    if position is not None:
        share = round(position.share_at_or_below * 100)
        blocks.append(
            _paragraph(
                f"{position.note} Срок не ниже, чем у {share}% дел выборки "
                f"(сравнивалось дел: {position.n_compared}).",
                "lead",
            )
        )
        blocks.append(
            _paragraph(
                "Выборка неслучайна и мала, обстоятельства дел различаются: наблюдение не "
                "является основанием для изменения наказания.",
                "note",
            )
        )
    stats = [
        ["Дел в выборке", str(analytics.n_cases)],
        ["Дел с назначенным сроком лишения свободы", str(analytics.n_with_term)],
        ["Медиана срока, мес.", _number(analytics.median_months)],
        ["Средний срок, мес.", _number(analytics.mean_months)],
        ["25-й перцентиль, мес.", _number(analytics.p25_months)],
        ["75-й перцентиль, мес.", _number(analytics.p75_months)],
        ["Доля условного осуждения", _number(analytics.suspended_share)],
    ]
    blocks.append(_table(Table(["Показатель", "Значение"], stats, [3.0, 1.0], key_value=True)))
    distribution = analytics.punishment_type_distribution
    if distribution:
        rows = [
            [PUNISHMENT_LABELS.get(kind, kind).capitalize(), str(count)]
            for kind, count in distribution.items()
        ]
        blocks.append(_table(Table(["Вид наказания", "Дел"], rows, [3.0, 1.0])))
    else:
        blocks.append(_paragraph("Виды наказания: нет данных."))
    if analytics.feature_spread:
        rows = [
            [FEATURE_LABELS.get(name, name), _percent(share)]
            for name, share in analytics.feature_spread.items()
        ]
        blocks.append(_table(Table(["Признак в выборке", "Доля дел"], rows, [3.0, 1.0])))
    blocks.append(
        _paragraph("Статистика описательная и не является рекомендацией о наказании.", "note")
    )
    return blocks


__all__ = [
    "Block",
    "Preparer",
    "Sheet",
    "Table",
    "TITLE",
    "build_sheet",
]
