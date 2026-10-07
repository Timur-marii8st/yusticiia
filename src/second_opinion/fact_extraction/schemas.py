from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..domain.enums import FactType


class LLMExtractedFact(BaseModel):
    type: FactType
    value: object
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = Field(min_length=1)


class LLMExtractionResult(BaseModel):
    """Схема структурированного вывода LLM-экстрактора."""

    facts: list[LLMExtractedFact] = Field(default_factory=list)

    @field_validator("facts", mode="before")
    @classmethod
    def _drop_entries_without_quote(cls, items: Any) -> Any:
        """Малые модели перечисляют все типы фактов, для ненайденных ставя
        ``quote: ""``. Факт без цитаты всё равно не принимается, поэтому такие
        записи отбрасываются, а не губят весь ответ."""
        if not isinstance(items, list):
            return items
        return [
            item
            for item in items
            if not (isinstance(item, dict) and not str(item.get("quote") or "").strip())
        ]
