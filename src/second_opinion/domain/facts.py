from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from .enums import ExtractionMethod, FactStatus, FactType
from .evidence import Evidence


class LegalFact(BaseModel):
    """Юридически значимый факт, извлечённый из документа.

    Инвариант provenance-first: факт без доказательств не может быть
    VERIFIED (см. docs/ADR/ADR-005).
    """

    id: str
    type: FactType
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    status: FactStatus
    prompt_version: str | None = None

    @model_validator(mode="after")
    def _verified_requires_evidence(self) -> LegalFact:
        if self.status is FactStatus.VERIFIED and not self.evidence:
            raise ValueError(
                f"факт {self.id} ({self.type}) имеет статус VERIFIED без evidence"
            )
        return self


class Qualification(BaseModel):
    """Квалификация: статья и часть кодекса."""

    code: str = "УК РФ"
    article: int = Field(ge=1)
    part: int | None = Field(default=None, ge=1)

    @property
    def ref(self) -> str:
        part = f" ч. {self.part}" if self.part else ""
        return f"ст. {self.article}{part} {self.code}"


class DefendantFacts(BaseModel):
    age: int | None = Field(default=None, ge=0, le=150)
    prior_convictions: bool | None = None
    minor_dependents: bool | None = None
    health_factors: list[str] = Field(default_factory=list)


class OffenseFacts(BaseModel):
    qualifications: list[Qualification] = Field(default_factory=list)
    stage: str | None = None  # OffenseStage.value
    complicity_role: str | None = None


class MitigatingFactor(BaseModel):
    code: str  # например "61.1.и"
    title: str
    fact_ids: list[str] = Field(default_factory=list)


class AggravatingFactor(BaseModel):
    code: str  # например "63.1.а"
    title: str
    fact_ids: list[str] = Field(default_factory=list)


class ProceduralFacts(BaseModel):
    special_procedure: bool | None = None
    guilty_plea: bool | None = None
    cooperation_agreement: bool | None = None
    jury_trial: bool | None = None


class SentenceFacts(BaseModel):
    punishment_type: str | None = None  # PunishmentType.value
    term_months: float | None = Field(default=None, ge=0)
    suspended: bool | None = None


class CaseFacts(BaseModel):
    """Агрегат юридически значимых обстоятельств дела."""

    defendant: DefendantFacts = Field(default_factory=DefendantFacts)
    offense: OffenseFacts = Field(default_factory=OffenseFacts)
    mitigating: list[MitigatingFactor] = Field(default_factory=list)
    aggravating: list[AggravatingFactor] = Field(default_factory=list)
    procedural: ProceduralFacts = Field(default_factory=ProceduralFacts)
    sentence: SentenceFacts = Field(default_factory=SentenceFacts)
    facts: list[LegalFact] = Field(default_factory=list)
    applicable_at: str | None = None  # ISO-дата юридически значимого события

    def fact_by_id(self, fact_id: str) -> LegalFact | None:
        return next((f for f in self.facts if f.id == fact_id), None)

    def add_fact(self, fact: LegalFact) -> None:
        self.facts.append(fact)

    @property
    def primary_qualification(self) -> Qualification | None:
        return self.offense.qualifications[0] if self.offense.qualifications else None

    @property
    def has_aggravating(self) -> bool:
        return bool(self.aggravating)

    def mitigating_codes(self) -> set[str]:
        return {m.code for m in self.mitigating}
