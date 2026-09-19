"""Особый порядок: ч. 5 ст. 62 УК и ч. 7 ст. 316 УПК (R-003),
и запрет особого порядка по делам несовершеннолетних (R-007)."""

from __future__ import annotations

from ...domain.enums import FactType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.rules import LegalRule, RuleEvaluation
from .base import (
    _BaseSanctionRule,
    _facts_of_type,
    _versioned_ref,
)


class SpecialProcedureLimitRule(_BaseSanctionRule):
    """Особый порядок: срок не более 2/3 максимума санкции (ч. 5 ст. 62 УК РФ, ч. 7 ст. 316 УПК РФ)."""

    rule_id = "R-003"
    version = "1.0.0"
    title = "Предел при особом порядке (ч. 5 ст. 62 УК РФ, ч. 7 ст. 316 УПК РФ)"
    description = (
        "При постановлении приговора в особом порядке (гл. 40 УПК РФ) наказание "
        "не может превышать 2/3 максимального срока наиболее строгого вида наказания "
        "(ч. 7 ст. 316 УПК РФ, материально-правовой дубль — ч. 5 ст. 62 УК РФ; "
        "см. п. 13 ППВС № 60)."
    )
    norm_refs = ["uk-rf:art-62", "upk-rf:art-316"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        if case.procedural.special_procedure is None:
            return self._unknown(
                ["признак рассмотрения дела в особом порядке"],
                "Из документа не удалось достоверно извлечь признак особого порядка. "
                "Проверьте раздел о порядке судопроизводства.",
            )
        if not case.procedural.special_procedure:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Дело рассмотрено не в особом порядке; ограничение ч. 5 ст. 62 УК РФ (ч. 7 ст. 316 УПК РФ) не применяется.",
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        limit = data["max_months"] * 2 / 3
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            _versioned_ref(context, "uk-rf:art-62", "УК РФ ст. 62 ч. 5"),
            _versioned_ref(context, "upk-rf:art-316", "УПК РФ ст. 316 ч. 7"),
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.SPECIAL_PROCEDURE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        numbers = {
            "term_months": term,
            "max_months": data["max_months"],
            "limit_months": limit,
        }
        if term > limit:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение предела",
                explanation=(
                    f"Особый порядок: назначено {term:.0f} мес. при пределе "
                    f"{limit:.0f} мес. (2/3 от {data['max_months']:.0f} мес.)."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Особый порядок: назначено {term:.0f} мес. при пределе {limit:.0f} мес."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class MinorSpecialProcedureRule(LegalRule):
    """Особый порядок недопустим по делам о преступлениях несовершеннолетних
    (ч. 2 ст. 420 УПК РФ)."""

    rule_id = "R-007"
    version = "1.0.0"
    title = "Особый порядок и несовершеннолетие (ч. 2 ст. 420 УПК РФ, п. 7 ППВС № 60)"
    description = (
        "Производство по делам о преступлениях несовершеннолетних ведется "
        "в общем порядке (ч. 2 ст. 420 УПК РФ); невыделенное дело с участием "
        "несовершеннолетнего рассматривается в общем порядке (п. 7 ППВС № 60)."
    )
    norm_refs = ["upk-rf:art-420", "ppvs-rf:n60-p7"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        special = case.procedural.special_procedure
        if special is None:
            return self._unknown(
                ["признак особого порядка"],
                "Не извлечено, рассматривалось ли дело в особом порядке; "
                "проверка невозможна без предположений.",
            )

        facts_used = [
            f.id for f in _facts_of_type(case, FactType.SPECIAL_PROCEDURE)
        ]
        if not special:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Дело рассматривалось без особого порядка.",
                facts_used=facts_used,
                norms_used=[
                    _versioned_ref(context, "upk-rf:art-420", "УПК РФ ст. 420 ч. 2"),
                    _versioned_ref(context, "ppvs-rf:n60-p7", "ППВС № 60 п. 7"),
                ],
            )

        age_facts = _facts_of_type(case, FactType.DEFENDANT_AGE)
        facts_used += [f.id for f in age_facts]
        age = case.defendant.age
        if age is None:
            return self._unknown(
                ["возраст подсудимого на момент деяния"],
                "Особый порядок обнаружен, но возраст не извлечён; проверить "
                "недопустимость по возрасту нельзя.",
            )
        norms_used = [
            _versioned_ref(context, "upk-rf:art-420", "УПК РФ ст. 420 ч. 2"),
            _versioned_ref(context, "ppvs-rf:n60-p7", "ППВС № 60 п. 7"),
        ]
        numbers = {"age": float(age)}
        if age < 18:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение",
                explanation=(
                    f"Дело рассмотрено в особом порядке, при этом из документа "
                    f"следует возраст {age} год(а)/лет. Ч. 2 ст. 420 УПК РФ требует "
                    "производства по делам несовершеннолетних в общем порядке "
                    "(с изъятиями гл. 50), а п. 7 ППВС № 60 — рассмотрения всего "
                    "невыделенного дела в общем порядке. Проверьте возраст лица "
                    "на момент деяния и дату его достижения совершеннолетия."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: противоречий не выявлено",
            explanation=(
                f"Возраст {age} год(а)/лет — ограничение ч. 2 ст. 420 УПК РФ "
                "не задействовано. Дополнительная проверка: возраст указан на "
                "момент деяния или на момент рассмотрения дела."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )
