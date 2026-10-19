from __future__ import annotations

from datetime import date

from ..audit.trail import AuditTrail
from ..domain.enums import RuleStatus
from ..domain.facts import CaseFacts
from ..domain.rules import RuleEvaluation
from ..domain.source_fragments import NormSourceState
from ..legal_sources.store import NormStore
from .context import RuleContext
from .rules import (
    MinorSpecialProcedureRule,
    MitigatingTwoThirdsRule,
    SanctionRangeRule,
    SentenceCompletenessRule,
    SequentialLimitRule,
    SpecialProcedureLimitRule,
    StageLimitRule,
    SuspendedLimitRule,
)

ENGINE_VERSION = "2.1.0"

DEFAULT_RULES = (
    SanctionRangeRule,
    StageLimitRule,
    SpecialProcedureLimitRule,
    MitigatingTwoThirdsRule,
    SequentialLimitRule,
    SuspendedLimitRule,
    MinorSpecialProcedureRule,
    SentenceCompletenessRule,
)


class RuleEngine:
    """Детерминированные юридические проверки (без LLM)."""

    def __init__(self, rules: tuple | None = None) -> None:
        self._rules = [cls() for cls in (rules or DEFAULT_RULES)]

    @property
    def rule_ids(self) -> list[str]:
        return [rule.rule_id for rule in self._rules]

    @property
    def required_norm_ids(self) -> list[str]:
        return sorted({norm_id for rule in self._rules for norm_id in rule.norm_refs})

    def evaluate(
        self,
        case_facts: CaseFacts,
        norm_store: NormStore,
        applicable_at: date,
        audit: AuditTrail | None = None,
        request_id: str = "-",
        norm_sources: list[NormSourceState] | None = None,
    ) -> list[RuleEvaluation]:
        context = RuleContext(
            case_facts=case_facts, norm_store=norm_store, applicable_at=applicable_at
        )
        evaluations: list[RuleEvaluation] = []
        sources_by_id = {s.norm_id: s for s in norm_sources or [] if s.norm_id}
        for rule in self._rules:
            evaluation = rule.evaluate(context)
            for reference in evaluation.norms_used:
                source = sources_by_id.get(reference.norm_id)
                if not reference.version_id and source and source.local_fragment:
                    reference.version_id = source.local_fragment.version_id
            if norm_sources and evaluation.status not in (RuleStatus.UNKNOWN, RuleStatus.NOT_APPLICABLE):
                used_ids = {n.norm_id for n in evaluation.norms_used}
                blockers = [
                    source for source in norm_sources
                    if source.norm_id in used_ids
                    and not (source.local_fragment and source.local_fragment.synthetic) and (
                        # Автономные расчёты сохраняются с явными оговорками отчёта.
                        source.acquisition != "offline" and (
                            source.applicability != "confirmed"
                            or source.freshness != "confirmed"
                        )
                        or (source.local_fragment is not None
                            and source.local_fragment.verification_status != "verified")
                        or (case_facts.sentence.punishment_type in source.pending_punishment_types)
                        or source.local_fragment is None
                    )
                ]
                if blockers:
                    evaluation.status = RuleStatus.UNKNOWN
                    evaluation.headline = f"{rule.title}: нормативное основание требует сверки"
                    evaluation.missing += [f"подтверждённое нормативное основание: {s.ref}" for s in blockers]
                    evaluation.explanation = "Нормативное основание не подтверждено. " + " ".join(
                        f"{s.ref}: {' '.join(s.reasons)}" for s in blockers
                    )
                    evaluation.numbers = {}
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
