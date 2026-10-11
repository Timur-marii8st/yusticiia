"""Снимки источников: текст, применимость и санкции проверяются отдельно."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .norms import NormVersion


class SourceFragment(BaseModel):
    provider: str
    document_id: str
    edition_id: str
    edition_label: str = ""
    amending_act: str = ""
    norm_id: str = ""
    version_id: str = ""
    article: str
    part: int | None = None
    # Если часть нельзя надёжно выделить, сохраняется вся статья.
    scope: Literal["article", "part", "local_norm"] = "article"
    source_url: str
    text: str = Field(min_length=1)
    sha256: str
    retrieved_at: datetime | date
    last_checked_at: datetime | None = None
    act_date: date | None = None
    publication_date: date | None = None
    entry_into_force: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    applicability: Literal["confirmed", "unknown"] = "unknown"
    verification_status: str = "candidate"
    synthetic: bool = False

    @model_validator(mode="after")
    def validate_snapshot(self) -> SourceFragment:
        if NormVersion.compute_sha256(self.text) != self.sha256:
            raise ValueError("контрольная сумма фрагмента не совпадает с текстом")
        if self.applicability == "confirmed" and self.effective_from is None:
            raise ValueError("подтверждённый период требует даты начала")
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValueError("конец периода раньше начала")
        return self


class NormSourceState(BaseModel):
    code: str
    article: str
    part: int | None = None
    norm_id: str = ""
    ref: str
    applicable_at: date
    applicability: Literal["confirmed", "unknown"] = "unknown"
    structured_data: Literal["verified", "pending", "missing"] = "missing"
    verified_punishment_types: list[str] = Field(default_factory=list)
    pending_punishment_types: list[str] = Field(default_factory=list)
    freshness: Literal["confirmed", "unknown", "offline"] = "unknown"
    acquisition: Literal[
        "local",
        "cache",
        "fetched",
        "offline",
        "not_found",
        "unavailable",
        "rate_limited",
        "invalid_response",
        "incomplete",
        "edition_uncertain",
        "cache_error",
    ] = "local"
    reasons: list[str] = Field(default_factory=list)
    local_fragment: SourceFragment | None = None
    candidate: SourceFragment | None = None
