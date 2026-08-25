from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ComparableCase(BaseModel):
    """Судебный акт в базе практики (для сопоставления).

    Фикстуры обязаны иметь ``synthetic=True`` и не выдаваться за реальную
    практику (см. docs/LEGAL_SAFETY_PRINCIPLES.md).
    """

    case_id: str
    title: str
    court: str
    date: date
    article: int
    part: int | None = None
    stage: str = "completed"  # OffenseStage.value
    recidivism: bool = False
    special_procedure: bool = False
    guilty_plea: bool = False
    jury_trial: bool = False
    mitigating_codes: list[str] = Field(default_factory=list)
    aggravating_codes: list[str] = Field(default_factory=list)
    punishment_type: str = "imprisonment"  # PunishmentType.value
    term_months: float | None = None
    suspended: bool = False
    summary: str = ""
    synthetic: bool = True


class CaseMatch(BaseModel):
    """Найденное сопоставимое дело с объяснением отбора."""

    case: ComparableCase
    reasons: list[str]
    score: float
