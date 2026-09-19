"""Наказание: предел условного осуждения (R-006) и полнота
извлечённых данных о наказании (R-009)."""

from __future__ import annotations

from ...domain.enums import FactType, RuleStatus
from ...domain.facts import CaseFacts
from ...domain.norms import NormRef
from ...domain.rules import LegalRule, RuleEvaluation
from .base import (
    SUSPENDED_MAX_MONTHS,
    _facts_of_type,
)


class SuspendedLimitRule(LegalRule):
    """Условное осуждение возможно при лишении свободы до 8 лет (ч. 1 ст. 73 УК РФ)."""

    rule_id = "R-006"
    version = "1.0.0"
    title = "Предел условного осуждения (ч. 1 ст. 73 УК РФ)"
    description = (
        "Условное осуждение назначается при лишении свободы на срок до 8 лет "
        "(ч. 1 ст. 73 УК РФ)."
    )
    norm_refs = ["uk-rf:art-73"]

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        norm_ref = NormRef(norm_id="uk-rf:art-73", version_id="", ref="УК РФ ст. 73 ч. 1")
        if case.sentence.suspended is None:
            return self._unknown(
                ["признак условного осуждения"],
                "Не удалось извлечь, назначено ли условное осуждение. "
                "Проверьте резолютивную часть.",
            )
        if not case.sentence.suspended:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation="Условное осуждение не назначалось.",
            )
        if case.sentence.term_months is None:
            return self._unknown(
                ["размер назначенного наказания"],
                "Условное осуждение обнаружено, но срок наказания не извлечён.",
            )
        term = float(case.sentence.term_months)
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.SUSPENDED_SENTENCE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
        ]
        numbers = {"term_months": term, "limit_months": float(SUSPENDED_MAX_MONTHS)}
        if term > SUSPENDED_MAX_MONTHS:
            return self._evaluation(
                status=RuleStatus.FAIL,
                headline=f"{self.title}: возможно нарушение",
                explanation=(
                    f"Условное осуждение при сроке {term:.0f} мес. превышает предел "
                    f"{SUSPENDED_MAX_MONTHS} мес. (8 лет)."
                ),
                facts_used=facts_used,
                norms_used=[norm_ref],
                numbers=numbers,
            )
        return self._evaluation(
            status=RuleStatus.PASS,
            headline=f"{self.title}: предел соблюдён",
            explanation=(
                f"Условное осуждение при сроке {term:.0f} мес. — в пределах "
                f"{SUSPENDED_MAX_MONTHS} мес. (8 лет)."
            ),
            facts_used=facts_used,
            norms_used=[norm_ref],
            numbers=numbers,
        )


class SentenceCompletenessRule(LegalRule):
    """Полнота данных о наказании: вид и размер извлекаются только парой.

    Структурная проверка качества извлечения, а не нормативное ограничение:
    если извлечён размер срока без вида наказания (или наоборот), проверки
    пределов (R-001, R-002, R-006) не могут отработать корректно. Статус
    WARNING подсвечивает неполноту, а не нарушение закона.
    """

    rule_id = "R-009"
    version = "1.0.0"
    title = "Полнота данных о наказании"
    description = (
        "Вид назначенного наказания и его размер должны присутствовать "
        "вместе: одиночный факт без пары означает неполное извлечение "
        "и делает проверки пределов недостоверными."
    )
    norm_refs: list[str] = []

    def evaluate(self, context) -> RuleEvaluation:
        case: CaseFacts = context.case_facts
        has_type = case.sentence.punishment_type is not None
        has_term = case.sentence.term_months is not None
        facts_used = [
            f.id
            for f in _facts_of_type(case, FactType.PUNISHMENT_TYPE)
            + _facts_of_type(case, FactType.PUNISHMENT_TERM)
        ]
        if has_type and has_term:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: данные полные",
                explanation=(
                    "Вид и размер наказания извлечены; проверки пределов "
                    "санкции работают по полным данным."
                ),
                facts_used=facts_used,
            )
        if not has_type and not has_term:
            return self._evaluation(
                status=RuleStatus.PASS,
                headline=f"{self.title}: не применимо",
                explanation=(
                    "Данных о назначенном наказании в извлечённых фактах нет "
                    "(например, процессуальный документ); проверять нечего."
                ),
                facts_used=facts_used,
            )
        missing = (
            "вид назначенного наказания"
            if not has_type
            else "размер назначенного наказания"
        )
        return self._evaluation(
            status=RuleStatus.WARNING,
            headline=f"{self.title}: неполные данные",
            explanation=(
                f"Извлечён только один элемент пары ({missing} отсутствует): "
                "проверки пределов санкции по такому делу недостоверны. "
                "Проверьте резолютивную часть акта вручную."
            ),
            facts_used=facts_used,
            missing=[missing],
        )
