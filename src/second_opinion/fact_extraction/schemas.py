from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.enums import FactType


class LLMExtractedFact(BaseModel):
    type: FactType
    value: object
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = Field(min_length=1)


class LLMExtractionResult(BaseModel):
    """Схема структурированного вывода LLM-экстрактора."""

    facts: list[LLMExtractedFact] = Field(default_factory=list)
