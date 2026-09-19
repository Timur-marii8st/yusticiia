"""Краткое изложение дела и проблемные места (ФТ-29, ФТ-31 из docs/TZ.md).

Оба раздела собираются **детерминированно из извлечённых обстоятельств и
результатов проверок**. Соблазн сгенерировать изложение моделью велик —
и он же ломает главный инвариант проекта: изложение стало бы
единственной частью отчёта, которую нельзя раскрыть до цитаты в
документе. Поэтому каждая строка изложения несёт идентификаторы фактов,
из которых собрана, а каждое проблемное место — ссылку на правило или
на конкретную нехватку данных.

Второе следствие того же выбора: система не пишет «суд правильно
учёл…» и не оценивает решение. Изложение перечисляет, что извлечено;
проблемные места называют, что не сошлось или чего не хватило.
"""

from __future__ import annotations

from .domain.analysis import AnalysisReport, CaseSummary, Concern, SummaryLine
from .domain.enums import FactStatus, RuleStatus
from .domain.facts import CaseFacts, LegalFact

STAGE_LABELS = {
    "completed": "оконченное преступление",
    "attempt": "покушение на преступление",
    "preparation": "приготовление к преступлению",
}

PUNISHMENT_LABELS = {
    "imprisonment": "лишение свободы",
    "fine": "штраф",
    "correctional_labor": "исправительные работы",
    "compulsory_labor": "обязательные работы",
    "restriction_of_liberty": "ограничение свободы",
    "other": "наказание",
}

#: Порядок разделов проблемных мест: сначала то, что не сошлось по праву.
#: Статистическое наблюдение идёт последним и намеренно отделено от
#: остальных: это не нарушение и не предупреждение движка.
SEVERITY_ORDER = {"violation": 0, "attention": 1, "data": 2, "statistics": 3}


def _ids(facts: list[LegalFact], *types: str) -> list[str]:
    """Идентификаторы действующих фактов указанных типов.

    Исключённые судьёй факты (``NOT_FOUND``) не участвуют: изложение
    должно совпадать с тем, на чём считались проверки.
    """
    wanted = set(types)
    return [
        fact.id
        for fact in facts
        if fact.type in wanted and fact.status is not FactStatus.NOT_FOUND
    ]


def _months(value: float) -> str:
    """Срок в месяцах и, если получается ровно, в годах."""
    months = int(value) if float(value).is_integer() else value
    if isinstance(months, int) and months >= 12 and months % 12 == 0:
        return f"{months} мес. ({months // 12} г.)"
    return f"{months} мес."


def build_summary(case_facts: CaseFacts, facts: list[LegalFact]) -> CaseSummary:
    """Собрать краткое изложение дела из извлечённых обстоятельств."""
    lines: list[SummaryLine] = []

    qualifications = case_facts.offense.qualifications
    stage = case_facts.offense.stage
    headline_parts: list[str] = []

    if qualifications:
        refs = ", ".join(q.ref for q in qualifications)
        headline_parts.append(refs)
        stage_text = STAGE_LABELS.get(stage or "", "")
        text = f"Квалификация: {refs}"
        if stage_text:
            text += f"; {stage_text}"
        lines.append(SummaryLine(text=text, fact_ids=_ids(facts, "qualification", "offense_stage")))
    else:
        lines.append(
            SummaryLine(text="Квалификация из документа не извлечена.", fact_ids=[])
        )

    if stage and stage != "completed":
        headline_parts.append(STAGE_LABELS.get(stage, stage))

    defendant = case_facts.defendant
    about: list[str] = []
    if defendant.age is not None:
        about.append(f"возраст {defendant.age}")
    if defendant.prior_convictions is not None:
        about.append("ранее судим" if defendant.prior_convictions else "ранее не судим")
    if defendant.minor_dependents:
        about.append("на иждивении несовершеннолетние дети")
    if defendant.health_factors:
        about.append("состояние здоровья учтено")
    if about:
        lines.append(
            SummaryLine(
                text="Подсудимый: " + ", ".join(about) + ".",
                fact_ids=_ids(
                    facts,
                    "defendant_age",
                    "prior_convictions",
                    "minor_dependents",
                    "health_factor",
                ),
            )
        )

    procedural = case_facts.procedural
    procedure: list[str] = []
    if procedural.special_procedure:
        procedure.append("особый порядок судебного разбирательства")
        headline_parts.append("особый порядок")
    if procedural.jury_trial:
        procedure.append("рассмотрение с участием присяжных заседателей")
        headline_parts.append("присяжные")
    if procedural.guilty_plea:
        procedure.append("вину признал")
    if procedure:
        lines.append(
            SummaryLine(
                text="Процедура: " + ", ".join(procedure) + ".",
                fact_ids=_ids(facts, "special_procedure", "jury_trial", "guilty_plea"),
            )
        )

    if case_facts.mitigating:
        titles = ", ".join(factor.title for factor in case_facts.mitigating)
        ids = [fid for factor in case_facts.mitigating for fid in factor.fact_ids]
        lines.append(SummaryLine(text=f"Смягчающие: {titles}.", fact_ids=ids))
    if case_facts.aggravating:
        titles = ", ".join(factor.title for factor in case_facts.aggravating)
        ids = [fid for factor in case_facts.aggravating for fid in factor.fact_ids]
        lines.append(SummaryLine(text=f"Отягчающие: {titles}.", fact_ids=ids))

    sentence = case_facts.sentence
    if sentence.punishment_type or sentence.term_months is not None:
        kind = PUNISHMENT_LABELS.get(sentence.punishment_type or "", "наказание")
        text = f"Назначено: {kind}"
        if sentence.term_months is not None:
            text += f" на срок {_months(sentence.term_months)}"
        if sentence.suspended:
            text += ", условно"
        lines.append(
            SummaryLine(
                # «мес.» уже заканчивается точкой — вторая выглядит опечаткой.
                text=text.rstrip(".") + ".",
                fact_ids=_ids(
                    facts, "punishment_type", "punishment_term", "suspended_sentence"
                ),
            )
        )

    headline = " · ".join(headline_parts) if headline_parts else "Дело"
    return CaseSummary(headline=headline, lines=lines)


def build_concerns(
    report: AnalysisReport, unverified_norms: list[str] | None = None
) -> list[Concern]:
    """Собрать раздел проблемных мест.

    Разделение по природе замечания важнее их количества: нарушение
    предела и нехватка данных требуют от судьи разных действий. Первое —
    посмотреть расчёт, второе — дополнить документ или обстоятельства.
    """
    concerns: list[Concern] = []

    for evaluation in report.evaluations:
        if evaluation.status is RuleStatus.FAIL:
            concerns.append(
                Concern(
                    severity="violation",
                    title=evaluation.headline,
                    detail=evaluation.explanation,
                    rule_id=evaluation.rule_id,
                    fact_ids=list(evaluation.facts_used),
                )
            )
        elif evaluation.status is RuleStatus.WARNING:
            concerns.append(
                Concern(
                    severity="attention",
                    title=evaluation.headline,
                    detail=evaluation.explanation,
                    rule_id=evaluation.rule_id,
                    fact_ids=list(evaluation.facts_used),
                )
            )

    # Непроверенное не растворяется среди выполненного: проверки в статусе
    # UNKNOWN — это не «всё хорошо», а «сказать нечего». Собираются в одну
    # строку, иначе семь однотипных замечаний вытеснят настоящее нарушение.
    unknown = [e for e in report.evaluations if e.status is RuleStatus.UNKNOWN]
    if unknown:
        concerns.append(
            Concern(
                severity="data",
                title=f"Проверок не выполнено из-за нехватки данных: {len(unknown)}",
                detail="; ".join(e.headline for e in unknown),
            )
        )

    if report.applicable_at_assumed:
        concerns.append(
            Concern(
                severity="data",
                title="Дата события в документе не найдена",
                detail=(
                    f"Нормы применены на {report.applicable_at} — это допущение, "
                    "а не извлечённый факт. Если деяние совершено раньше, "
                    "редакции норм могут быть другими."
                ),
            )
        )

    if unverified_norms:
        concerns.append(
            Concern(
                severity="data",
                title="Применены нормы без юридической сверки",
                detail=(
                    "Редакции со статусом draft: "
                    + ", ".join(sorted(set(unverified_norms)))
                    + ". Проверьте их по официальному источнику."
                ),
            )
        )

    position = report.analytics.sentence_position if report.analytics else None
    if position is not None and position.band == "outside":
        # В раздел попадает только выход за весь наблюдаемый диапазон:
        # «выше верхней четверти» — обычное дело и замечанием не является.
        concerns.append(
            Concern(
                severity="statistics",
                title="Срок вне диапазона по сопоставимым делам",
                detail=position.note,
            )
        )

    concerns.sort(key=lambda c: SEVERITY_ORDER.get(c.severity, 99))
    return concerns
