from __future__ import annotations

from ..domain.cases import CaseMatch, ComparableCase
from ..domain.facts import CaseFacts

#: Совпадение только по статье (без содержательных признаков) не делает дело
#: сопоставимым, если по документу извлечены содержательные признаки.
_ARTICLE_ONLY_NOTE = (
    "сопоставление только по статье: остальные признаки дела не извлечены"
)


class CaseRetriever:
    """Поиск сопоставимых дел: структурные фильтры + взвешенное ранжирование.

    Правила отбора:
    - статья — обязательный фильтр;
    - содержательные признаки (часть, стадия, процедура, рецидив,
      признание вины, присяжные, смягчающие) дают причины отбора;
    - если по документу извлечены содержательные признаки, но ни один не
      совпал, дело НЕ считается сопоставимым (лучше честно «нет практики»,
      чем ложная близость);
    - сходство — справочный сигнал, не основание для вывода о наказании
      (см. ADR-003).
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
        substance_in_criteria = self._criteria_has_substance(criteria)
        matches: list[CaseMatch] = []
        for case in candidates:
            score, reasons, substantive = self._score(
                case, criteria, qualification.part
            )
            if not substantive and substance_in_criteria:
                continue
            if not substantive:
                reasons.append(_ARTICLE_ONLY_NOTE)
            matches.append(CaseMatch(case=case, reasons=reasons, score=score))
        matches.sort(key=lambda m: (-m.score, m.case.case_id))
        return matches[:limit]

    # -- внутреннее -----------------------------------------------------------

    @staticmethod
    def _criteria_has_substance(criteria: CaseFacts) -> bool:
        procedural = criteria.procedural
        return (
            criteria.offense.stage is not None
            or procedural.special_procedure is not None
            or procedural.guilty_plea is not None
            or procedural.jury_trial is not None
            or bool(criteria.mitigating)
            or bool(criteria.aggravating)
        )

    def _score(
        self, case: ComparableCase, criteria: CaseFacts, part: int | None
    ) -> tuple[float, list[str], bool]:
        """Вернуть (балл, причины, есть ли содержательное совпадение)."""
        score = 2.0
        reasons = [f"та же статья: ст. {case.article} УК РФ"]
        substantive = False

        if part is not None and case.part == part:
            score += 2.0
            reasons.append(f"та же часть: ч. {part}")
            substantive = True
        if criteria.offense.stage and case.stage == criteria.offense.stage:
            score += 1.5
            reasons.append(f"та же стадия: {case.stage}")
            substantive = True
        if criteria.procedural.special_procedure and case.special_procedure:
            score += 1.5
            reasons.append("также рассмотрено в особом порядке")
            substantive = True
        if criteria.procedural.guilty_plea and case.guilty_plea:
            score += 1.0
            reasons.append("также имеется признание вины")
            substantive = True
        if criteria.procedural.jury_trial and case.jury_trial:
            score += 1.5
            reasons.append("также рассмотрено с участием присяжных")
            substantive = True

        aggravating_codes = {a.code for a in criteria.aggravating}
        recidivism = "63.1.а" in aggravating_codes
        if recidivism and case.recidivism:
            score += 1.5
            reasons.append("в обоих случаях установлен рецидив")
            substantive = True
        elif not recidivism and not case.recidivism:
            score += 1.0
            reasons.append("рецидив отсутствует в обоих случаях")

        overlap = criteria.mitigating_codes() & set(case.mitigating_codes)
        if overlap:
            score += 0.5 * len(overlap)
            reasons.append(f"общие смягчающие: {', '.join(sorted(overlap))}")
            substantive = True
        return score, reasons, substantive
