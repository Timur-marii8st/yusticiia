"""Неоконченное преступление: ст. 66 УК РФ (R-002) и её сочетание
с особым порядком, п. 14 ППВС № 60 (R-010)."""

from __future__ import annotations

from ...domain.enums import FactType, OffenseStage, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import NormRef
from ...domain.rules import RuleEvaluation
from .base import (
    _BaseSanctionRule,
    _facts_of_type,
    _versioned_ref,
)


class StageLimitRule(_BaseSanctionRule):
    """Пределы при неоконченном преступлении: покушение ≤ 3/4, приготовление ≤ 1/2 (ст. 66 УК РФ)."""

    rule_id = "R-002"
    version = "1.0.0"
    title = "Предел при неоконченном преступлении (ст. 66 УК РФ)"
    description = (
        "Срок за покушение не может превышать 3/4, за приготовление — 1/2 "
        "максимального срока наиболее строгого вида наказания."
    )
    norm_refs = ["uk-rf:art-66"]

    _FRACTIONS = {
        OffenseStage.ATTEMPT.value: (0.75, "покушение"),
        OffenseStage.PREPARATION.value: (0.5, "приготовление"),
    }

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        stage = case.offense.stage
        if stage not in self._FRACTIONS:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Преступление квалифицировано как оконченное; предел ст. 66 УК РФ не применяется.",
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        fraction, stage_title = self._FRACTIONS[stage]
        limit = data["max_months"] * fraction
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-66", version_id="", ref="УК РФ ст. 66")
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.OFFENSE_STAGE)
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
                    f"Стадия — {stage_title}; назначено {term:.0f} мес., что выше "
                    f"предела {limit:.0f} мес. ({fraction:.0%} от максимума санкции "
                    f"{data['max_months']:.0f} мес.)."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Стадия — {stage_title}; назначено {term:.0f} мес. при пределе "
                f"{limit:.0f} мес. ({fraction:.0%} от {data['max_months']:.0f} мес.)."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )


class SequentialLimitRule(_BaseSanctionRule):
    """Неоконченное преступление в особом порядке: M × доля ст. 66 × 2/3 (п. 14 ППВС № 60)."""

    rule_id = "R-010"
    version = "1.0.0"
    title = "Комбинированный предел: неоконченное в особом порядке"
    description = (
        "При неоконченном преступлении, рассмотренном в особом порядке, "
        "пределы применяются последовательно: сначала ст. 66 УК РФ, затем "
        "2/3 ч. 5 ст. 62 УК РФ (п. 14 ППВС № 60). Покушение: M × 3/4 × 2/3; "
        "приготовление: M × 1/2 × 2/3."
    )
    norm_refs = ["uk-rf:art-66", "uk-rf:art-62", "ppvs-rf:n60-p14"]

    _FRACTIONS = {
        OffenseStage.ATTEMPT.value: (0.75, "покушение"),
        OffenseStage.PREPARATION.value: (0.5, "приготовление"),
    }

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        stage = case.offense.stage
        if stage not in self._FRACTIONS:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Преступление квалифицировано как оконченное; комбинированный предел не применяется.",
            )
        if case.procedural.special_procedure is None:
            return self._unknown(
                ["признак рассмотрения дела в особом порядке"],
                "Неоконченное преступление обнаружено, но признак особого порядка "
                "не извлечён; комбинированную проверку выполнить нельзя.",
            )
        if not case.procedural.special_procedure:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Дело рассмотрено не в особом порядке; применяется только предел ст. 66 УК РФ (см. R-002).",
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        fraction, stage_title = self._FRACTIONS[stage]
        limit = data["max_months"] * fraction * 2 / 3
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            _versioned_ref(context, "uk-rf:art-66", "УК РФ ст. 66"),
            _versioned_ref(context, "uk-rf:art-62", "УК РФ ст. 62 ч. 5"),
            _versioned_ref(context, "ppvs-rf:n60-p14", "ППВС № 60 п. 14"),
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.OFFENSE_STAGE)
            + _facts_of_type(case, FactType.SPECIAL_PROCEDURE)
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
                    f"Стадия — {stage_title}, особый порядок: назначено {term:.0f} мес. "
                    f"при пределе {limit:.0f} мес. ({fraction:.0%} × 2/3 от максимума "
                    f"санкции {data['max_months']:.0f} мес.)."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Стадия — {stage_title}, особый порядок: назначено {term:.0f} мес. "
                f"при пределе {limit:.0f} мес."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )
