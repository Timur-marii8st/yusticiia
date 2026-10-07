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
from .domain.enums import FactStatus, FactType, RuleStatus
from .domain.facts import CaseFacts, LegalFact, Qualification
from .legal_sources.coverage import HistoryCaveat

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

GROUP_OFFENSE_LABELS = {
    "group_of_persons": "группой лиц",
    "group_with_conspiracy": "группой лиц по предварительному сговору",
    "organized_group": "организованной группой",
}

FACT_TYPE_LABELS = {
    FactType.QUALIFICATION: "Квалификация",
    FactType.DEFENDANT_AGE: "Возраст подсудимого",
    FactType.PRIOR_CONVICTIONS: "Наличие судимостей",
    FactType.MINOR_DEPENDENTS: "Несовершеннолетние дети",
    FactType.HEALTH_FACTOR: "Состояние здоровья",
    FactType.OFFENSE_STAGE: "Стадия преступления",
    FactType.GROUP_OFFENSE: "Групповой характер деяния",
    FactType.GUILTY_PLEA: "Признание вины",
    FactType.SURRENDER_OR_CONFESSION: "Явка с повинной / способствование расследованию",
    FactType.RESTITUTION: "Возмещение ущерба / заглаживание вреда",
    FactType.AGGRAVATING_RECIDIVISM: "Рецидив (отягчающее)",
    FactType.SPECIAL_PROCEDURE: "Особый порядок судопроизводства",
    FactType.JURY_TRIAL: "Суд присяжных",
    FactType.PUNISHMENT_TYPE: "Вид наказания",
    FactType.PUNISHMENT_TERM: "Размер наказания (мес.)",
    FactType.FINAL_PUNISHMENT_TERM: "Окончательный срок по совокупности (мес.)",
    FactType.SUSPENDED_SENTENCE: "Условное осуждение",
    FactType.DATE_OF_OFFENSE: "Дата совершения",
    FactType.EXCEPTIONAL_CIRCUMSTANCES: "Ст. 64 УК РФ упомянута в тексте",
}


def format_fact_value(fact: LegalFact) -> str:
    """Значение факта по-русски, а не `str()` от сырого хранимого значения.

    ``LegalFact.value`` типизировано как ``Any`` (домен намеренно не
    валидирует его форму на этом уровне — см. ``domain/facts.py``), и для
    квалификации это словарь, а не модель ``Qualification``: распознаём
    по типу факта, а не по классу значения.
    """
    value = fact.value
    if fact.type is FactType.QUALIFICATION and isinstance(value, dict):
        return Qualification(**value).ref
    if fact.type is FactType.OFFENSE_STAGE:
        return STAGE_LABELS.get(value, str(value))
    if fact.type is FactType.PUNISHMENT_TYPE:
        return PUNISHMENT_LABELS.get(value, str(value))
    if fact.type is FactType.GROUP_OFFENSE:
        return GROUP_OFFENSE_LABELS.get(value, str(value))
    if isinstance(value, bool):
        return "да" if value else "нет"
    return str(value)

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
        text = (
            "Квалификация из документа не извлечена."
            if case_facts.has_criminal_content
            else "Статья УК РФ и назначенное наказание в документе не найдены."
        )
        lines.append(SummaryLine(text=text, fact_ids=[]))

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
        if sentence.final_term_months is not None:
            text += f", окончательно по совокупности {_months(sentence.final_term_months)}"
        if sentence.suspended:
            text += ", условно"
        lines.append(
            SummaryLine(
                text=text.rstrip(".") + ".",
                fact_ids=_ids(
                    facts,
                    "punishment_type",
                    "punishment_term",
                    "final_punishment_term",
                    "suspended_sentence",
                ),
            )
        )

    headline = " · ".join(headline_parts) if headline_parts else "Дело"
    return CaseSummary(headline=headline, lines=lines)


def _history_concerns(report: AnalysisReport, caveats: list[HistoryCaveat]) -> list[Concern]:
    concerns: list[Concern] = []
    unrecorded = [c for c in caveats if c.kind == "unrecorded"]
    if unrecorded:
        listed = "; ".join(f"{c.ref} (с {c.since.isoformat()})" for c in unrecorded)
        concerns.append(
            Concern(
                severity="data",
                title="Редакционная история норм не сверена",
                detail=(
                    f"Применена единственная известная редакция: {listed}. Изменения "
                    "после этой даты в корпусе не зафиксированы; если норма "
                    f"менялась, на {report.applicable_at} могла действовать другая редакция."
                ),
            )
        )
    near = [c for c in caveats if c.kind == "near_boundary"]
    if near:
        listed = "; ".join(
            f"{c.ref}: редакция с {c.since.isoformat()}, прошло {c.days_since} дн." for c in near
        )
        concerns.append(
            Concern(
                severity="data",
                title="Дата применения близка к границе редакции",
                detail=(
                    f"{listed}. Граница редакции в источнике может быть датой акта, а "
                    "не вступления в силу: проверьте дату вступления в силу изменяющего закона."
                ),
            )
        )
    return concerns


def build_concerns(
    report: AnalysisReport,
    unverified_norms: list[str] | None = None,
    history_caveats: list[HistoryCaveat] | None = None,
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

    if not report.case_facts.has_criminal_content:
        concerns.append(
            Concern(
                severity="data",
                title="Документ не распознан как приговор по уголовному делу",
                detail=(
                    "В тексте не найдены ни статья УК РФ, ни назначенное наказание. "
                    "Система проверяет приговоры по уголовным делам; решения по "
                    "гражданским, трудовым и административным делам она не "
                    "анализирует. Если это приговор, возможно, текст распознан с "
                    "ошибками: проверьте исходный файл."
                ),
            )
        )

    pending = [f for f in report.facts if f.status is FactStatus.LIKELY]
    if pending:
        kinds = ", ".join(sorted({FACT_TYPE_LABELS.get(f.type, f.type.value) for f in pending}))
        concerns.append(
            Concern(
                severity="data",
                title=f"Факты модели ждут подтверждения: {len(pending)}",
                detail=(
                    f"{kinds}. В проверках эти факты не участвуют, пока судья не "
                    "подтвердил их: подтвердите факт, если он верен, и проверки пересчитаются."
                ),
                fact_ids=[f.id for f in pending],
            )
        )

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

    concerns += _history_concerns(report, history_caveats or [])

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
        concerns.append(
            Concern(
                severity="statistics",
                title="Срок вне диапазона по сопоставимым делам",
                detail=position.note,
            )
        )

    concerns.sort(key=lambda c: SEVERITY_ORDER.get(c.severity, 99))
    return concerns
