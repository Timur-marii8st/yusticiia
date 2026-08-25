from __future__ import annotations

from ..domain.cases import CaseMatch, ComparableCase
from ..domain.facts import CaseFacts


class CaseRetriever:
    """Поиск сопоставимых дел: структурные фильтры + взвешенное ранжирование.

    Сходство — справочный сигнал; система не выдаёт «процент похожести»
    как основание для вывода о наказании (см. ADR-003).
    """

    def __init__(self, cases: list[ComparableCase]) -> None:
        self._cases = cases

    @property
    def cases(self) -> list[ComparableCase]:
        return list(self._cases)

    @property
    def total(self) -> int:
        return len(self._cases)

    def search(self, criteria: CaseFacts, limit: int = 10) -> list[CaseMatch]:
        qualification = criteria.primary_qualification
        if qualification is None:
            return []
        candidates = [
            case
            for case in self._cases
            if case.article == qualification.article
        ]
        matches: list[CaseMatch] = []
        for case in candidates:
            score, reasons = self._score(case, criteria, qualification.part)
            matches.append(CaseMatch(case=case, reasons=reasons, score=score))
        matches.sort(key=lambda m: (-m.score, m.case.case_id))
        return matches[:limit]

    def _score(
        self, case: ComparableCase, criteria: CaseFacts, part: int | None
    ) -> tuple[float, list[str]]:
        score = 2.0
        reasons = [f"та же статья: ст. {case.article} УК РФ"]
        if part is not None and case.part == part:
            score += 2.0
            reasons.append(f"та же часть: ч. {part}")
        if criteria.offense.stage and case.stage == criteria.offense.stage:
            score += 1.5
            reasons.append(f"та же стадия: {case.stage}")
        if criteria.procedural.special_procedure and case.special_procedure:
            score += 1.5
            reasons.append("также рассмотрено в особом порядке")
        if criteria.procedural.guilty_plea and case.guilty_plea:
            score += 1.0
            reasons.append("также имеется признание вины")
        aggravating_codes = {a.code for a in criteria.aggravating}
        recidivism = "63.1.а" in aggravating_codes
        if recidivism == case.recidivism:
            score += 1.0
            reasons.append(
                "совпадает признак рецидива" if recidivism else "рецидив отсутствует в обоих случаях"
            )
        overlap = criteria.mitigating_codes() & set(case.mitigating_codes)
        if overlap:
            score += 0.5 * len(overlap)
            reasons.append(f"общие смягчающие: {', '.join(sorted(overlap))}")
        return score, reasons


def build_criteria(criteria: CaseFacts) -> CaseFacts:
    """Явный конструктор-идентичность (удобство и читаемость вызова)."""
    return criteria
