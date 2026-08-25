from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Document(BaseModel):
    """Нормализованный судебный документ.

    ``text`` — канонический текст; все цитирования (Evidence) ссылаются
    на него через оффсеты. ``page_offsets`` (для PDF) — стартовые оффсеты
    страниц, чтобы показывать цитату в контексте страницы.
    """

    document_id: str
    filename: str
    content_type: str
    text: str
    sha256: str
    page_offsets: list[int] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(microsecond=0)
    )
    origin: str = "upload"

    def page_for_offset(self, offset: int) -> int | None:
        if not self.page_offsets:
            return None
        page = 1
        for start in self.page_offsets:
            if start <= offset:
                page = self.page_offsets.index(start) + 1
            else:
                break
        return page
