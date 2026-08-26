from __future__ import annotations

from datetime import date

from ..audit.trail import AuditTrail
from ..domain.facts import CaseFacts
from ..domain.rules import RuleEvaluation
from ..legal_sources.store import NormStore
from .context import RuleContext
from .rules import (
    CombinedLimitRule,
    MinorSpecialProcedureRule,
    MitigatingTwoThirdsRule,
    RecidivismMitigatingConflictRule,
    SanctionRangeRule,
    SpecialProcedureLimitRule,
    StageLimitRule,
    SuspendedLimitRule,
)

ENGINE_VERSION = "1.1.0"

DEFAULT_RULES = (
    SanctionRangeRule,
    StageLimitRule,
    SpecialProcedureLimitRule,
    MitigatingTwoThirdsRule,
    CombinedLimitRule,
    SuspendedLimitRule,
    MinorSpecialProcedureRule,
    RecidivismMitigatingConflictRule,
)


class RuleEngine:
    """Детерминированные юридические проверки (без LLM)."""

    def __init__(self, rules: tuple | None = None) -> None:
        self._rules = [cls() for cls in (rules or DEFAULT_RULES)]

    @property
    def rule_ids(self) -> list[str]:
        return [rule.rule_id for rule in self._rules]

    def evaluate(
        self,
        case_facts: CaseFacts,
        norm_store: NormStore,
        applicable_at: date,
        audit: AuditTrail | None = None,
        request_id: str = "-",
    ) -> list[RuleEvaluation]:
        context = RuleContext(
            case_facts=case_facts, norm_store=norm_store, applicable_at=applicable_at
        )
        evaluations: list[RuleEvaluation] = []
        for rule in self._rules:
            evaluation = rule.evaluate(context)
            evaluations.append(evaluation)
            if audit is not None:
                audit.log(
                    operation="rule_evaluation",
                    component="rule_engine",
                    request_id=request_id,
                    rule_version=evaluation.rule_version,
                    details={
                        "rule_id": evaluation.rule_id,
                        "status": evaluation.status.value,
                        "engine_version": ENGINE_VERSION,
                    },
                )
        return evaluations
