from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Evidence(BaseModel):
    """Ссылка на фрагмент исходного документа, подтверждающий факт.

    Инвариант: ``text[start_offset:end_offset]`` нормализованного текста
    документа соответствует ``quote``.
    """

    document_id: str
    quote: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    page: int | None = None

    @model_validator(mode="after")
    def _offsets_coherent(self) -> Evidence:
        if self.end_offset <= self.start_offset:
            raise ValueError(
                f"end_offset ({self.end_offset}) должен быть больше "
                f"start_offset ({self.start_offset})"
            )
        if self.end_offset - self.start_offset > 5000:
            raise ValueError("evidence ссылается на подозрительно большой фрагмент")
        return self
