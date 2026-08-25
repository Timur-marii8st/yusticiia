from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .cases import CaseMatch
from .facts import CaseFacts, LegalFact
from .norms import NormRef
from .rules import RuleEvaluation


class AnalyticsSummary(BaseModel):
    """Описательная (НЕ предписывающая) статистика по выборке дел."""

    n_cases: int
    n_with_term: int = 0
    median_months: float | None = None
    mean_months: float | None = None
    p25_months: float | None = None
    p75_months: float | None = None
    min_months: float | None = None
    max_months: float | None = None
    punishment_type_distribution: dict[str, int] = Field(default_factory=dict)
    suspended_share: float | None = None


class AnalysisReport(BaseModel):
    """Итог анализа: второе мнение с полной прослеживаемостью."""

    analysis_id: str
    document_id: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(microsecond=0)
    )
    applicable_at: str | None = None
    applicable_at_assumed: bool = False
    facts: list[LegalFact] = Field(default_factory=list)
    case_facts: CaseFacts = Field(default_factory=CaseFacts)
    evaluations: list[RuleEvaluation] = Field(default_factory=list)
    norms_applied: list[NormRef] = Field(default_factory=list)
    comparable_cases: list[CaseMatch] = Field(default_factory=list)
    analytics: AnalyticsSummary | None = None
    disclaimers: list[str] = Field(default_factory=list)


DEFAULT_DISCLAIMERS = [
    "Система является вспомогательным инструментом проверки и не заменяет судью.",
    "Ни один вывод системы не является обязательным для суда.",
    "Статистика по сопоставимым делам носит исключительно описательный характер "
    "и не является основанием для назначения наказания.",
    "Извлечённые обстоятельства требуют проверки судьёй по исходному тексту документа.",
]
