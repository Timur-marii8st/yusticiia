"""Поиск по базе источников с обязательным цитированием."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ..schemas import SearchResponse


def build_router(rag) -> APIRouter:  # noqa: ANN001 — LegalRag собирается в deps
    router = APIRouter(prefix="/search", tags=["поиск"])

    @router.get("")
    def search_sources(
        q: str = Query(min_length=1),
        limit: int = Query(default=10, ge=1, le=50),
        code: str | None = None,
        article: int | None = None,
        applicable_at: str | None = None,
    ) -> SearchResponse:
        """Лексический поиск по базе норм.

        Только реальные фрагменты источников; пустой результат честнее
        выдуманного.
        """
        when: date | None = None
        if applicable_at:
            try:
                when = date.fromisoformat(applicable_at)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="applicable_at: ожидается ISO-дата"
                ) from exc
        hits = rag.search(q, limit=limit, code=code, article=article, applicable_at=when)
        return {"query": q, "results": hits}

    return router
