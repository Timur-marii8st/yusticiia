"""Смягчающие пп. «и»/«к» ч. 1 ст. 61 УК РФ: предел ч. 1 ст. 62 (R-004)."""

from __future__ import annotations

from ...domain.enums import FactType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import NormRef
from ...domain.rules import RuleEvaluation
from .base import (
    MITIGATING_IK_CODES,
    _BaseSanctionRule,
    _facts_of_type,
)


class MitigatingTwoThirdsRule(_BaseSanctionRule):
    """Смягчающие пп. «и»/«к» без отягчающих: срок не более 2/3 максимума (ст. 62 ч. 1 УК РФ)."""

    rule_id = "R-004"
    version = "1.0.0"
    title = "Предел при смягчающих пп. «и»/«к» (ст. 62 ч. 1 УК РФ)"
    description = (
        "При наличии смягчающих обстоятельств пп. «и» и (или) «к» ч. 1 ст. 61 УК РФ "
        "и отсутствии отягчающих наказание не может превышать 2/3 максимума санкции."
    )
    norm_refs = ["uk-rf:art-62", "uk-rf:art-61"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        has_ik = bool(case.mitigating_codes() & MITIGATING_IK_CODES)
        if not has_ik:
            return self._evaluation(
                status=RuleStatus.NOT_APPLICABLE,
                headline=f"{self.title}: не применимо",
                explanation=(
                    "Смягчающие обстоятельства пп. «и»/«к» ч. 1 ст. 61 УК РФ "
                    "в извлечённых фактах не обнаружены."
                ),
            )
        if case.has_aggravating:
            return self._evaluation(
                status=RuleStatus.NOT_APPLICABLE,
                headline=f"{self.title}: не применимо из-за отягчающих",
                explanation=(
                    "Имеются отягчающие обстоятельства; при них предел ст. 62 ч. 1 УК РФ не действует."
                ),
                facts_used=[
                    factor_id
                    for aggravating in case.aggravating
                    for factor_id in aggravating.fact_ids
                ],
            )
        early, data = self._prepare(context)
        if early is not None:
            return early
        limit = data["max_months"] * 2 / 3
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(data["version"], data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-62", version_id="", ref="УК РФ ст. 62 ч. 1"),
            NormRef(norm_id="uk-rf:art-61", version_id="", ref="УК РФ ст. 61 ч. 1"),
        ]
        mitigating_fact_ids = [
            fact_id
            for mitigating in case.mitigating
            if mitigating.code in MITIGATING_IK_CODES
            for fact_id in mitigating.fact_ids
        ]
        facts_used = mitigating_fact_ids + [
            f.id
            for f in _facts_of_type(case, FactType.PUNISHMENT_TERM)
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
                    f"Назначено {term:.0f} мес. при пределе {limit:.0f} мес. "
                    "(2/3 максимума санкции) — при наличии смягчающих "
                    "пп. «и»/«к» и отсутствии отягчающих."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=f"Назначено {term:.0f} мес. при пределе {limit:.0f} мес.",
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )
