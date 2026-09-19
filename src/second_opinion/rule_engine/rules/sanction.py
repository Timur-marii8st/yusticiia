"""Пределы санкции статьи (R-001)."""

from __future__ import annotations

from ...domain.enums import FactType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import NormRef, NormVersion
from ...domain.rules import RuleEvaluation
from .base import (
    IMPRISONMENT,
    _BaseSanctionRule,
    _facts_of_type,
)


class SanctionRangeRule(_BaseSanctionRule):
    """Назначенный срок должен лежать в пределах санкции статьи (ст. 60 УК РФ)."""

    rule_id = "R-001"
    version = "1.0.0"
    title = "Пределы санкции статьи"
    description = (
        "Проверяет, что назначенный срок лишения свободы не превышает максимум "
        "санкции статьи квалификации и не ниже минимума без специальных оснований."
    )
    norm_refs = ["uk-rf:art-60"]

    def evaluate(self, context) -> RuleEvaluation:
        early, data = self._prepare(context)
        if early is not None:
            return early
        case: CaseFacts = data["case"]
        version: NormVersion = data["version"]
        max_months: float = data["max_months"]
        term = float(case.sentence.term_months or 0)
        norms_used = self._norm_refs(version, data["norm"].ref) + [
            NormRef(norm_id="uk-rf:art-60", version_id="", ref="УК РФ ст. 60")
        ]
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.PUNISHMENT_TERM)
            + _facts_of_type(case, FactType.PUNISHMENT_TYPE)
            + _facts_of_type(case, FactType.QUALIFICATION)
        ]
        numbers = {"term_months": term, "max_months": max_months}
        if term > max_months:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно превышение максимума санкции",
                explanation=(
                    f"Назначено {term:.0f} мес. лишения свободы, максимум санкции "
                    f"{data['qualification'].ref} — {max_months:.0f} мес. "
                    "Проверьте квалификацию и основания выхода за предел санкции."
                ),
                facts_used=facts_used,
                norms_used=norms_used,
                numbers=numbers,
            )
        sanction_min = next(
            (
                s.min_months
                for s in version.sanctions
                if s.punishment_type.value == IMPRISONMENT
            ),
            None,
        )
        if sanction_min and term < sanction_min:
            return self._evaluation(
                status=RuleStatus.WARNING,
                headline=f"{self.title}: срок ниже минимума санкции",
                explanation=(
                    f"Назначено {term:.0f} мес. при минимуме санкции "
                    f"{sanction_min:.0f} мес. Назначение ниже низшего предела "
                    "возможно только по основаниям ст. 64 УК РФ — проверьте, "
                    "указаны ли они в акте."
                ),
                facts_used=facts_used,
                norms_used=norms_used + [
                    NormRef(norm_id="uk-rf:art-64", version_id="", ref="УК РФ ст. 64")
                ],
                numbers=numbers | {"min_months": float(sanction_min)},
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: в пределах санкции",
            explanation=(
                f"Назначенный срок {term:.0f} мес. находится в пределах санкции "
                f"{data['qualification'].ref} (максимум {max_months:.0f} мес.)."
            ),
            facts_used=facts_used,
            norms_used=norms_used,
            numbers=numbers,
        )
