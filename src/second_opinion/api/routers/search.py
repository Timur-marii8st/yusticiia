"""Поиск по базе источников с обязательным цитированием."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query

from ...legal_sources.acquisition import NormAcquisition
from ..schemas import SearchResponse


def build_router(rag, acquisition: NormAcquisition | None = None) -> APIRouter:  # noqa: ANN001
    router = APIRouter(prefix="/search", tags=["поиск"])

    @router.get("")
    def search_sources(
        q: str = Query(default="", max_length=2000),
        limit: int = Query(default=10, ge=1, le=50),
        code: str | None = None,
        article: int | None = Query(default=None, ge=1),
        article_suffix: str | None = Query(default=None, pattern=r"^\d+$"),
        part: int | None = Query(default=None, ge=1),
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
        if not q.strip() and article is None:
            raise HTTPException(status_code=422, detail="Укажите q или article")
        if part is not None and article is None:
            raise HTTPException(status_code=400, detail="part требует article")
        state = None
        if article is not None and code == "УК РФ":
            if when is None:
                raise HTTPException(status_code=400, detail="Явный запрос УК РФ требует applicable_at")
            if acquisition is not None:
                number = str(article) + (f".{article_suffix}" if article_suffix else "")
                state = acquisition.resolve(code, number, part, when)
        hits = rag.search(q, limit=limit, code=code, article=article, applicable_at=when)
        if state is not None:
            # Явная норма выбирается по реквизитам, а не BM25 по номеру статьи.
            hits = [h for h in hits if h.norm_id == state.norm_id]
            if state.local_fragment:
                from ...legal_rag.search import SearchHit

                fragment = state.local_fragment
                assert fragment.effective_from is not None
                hits = [SearchHit(
                    norm_id=fragment.norm_id, version_id=fragment.version_id,
                    ref=state.ref, title=state.ref, fragment=fragment.text,
                    effective_from=fragment.effective_from, effective_to=fragment.effective_to,
                    source_document=fragment.document_id, source_url=fragment.source_url,
                    retrieved_at=fragment.retrieved_at.date() if isinstance(fragment.retrieved_at, datetime) else fragment.retrieved_at,
                    sha256=fragment.sha256,
                    verification_status=fragment.verification_status, synthetic=fragment.synthetic,
                    score=1.0,
                    applicability=state.applicability, freshness=state.freshness,
                )]
        return {"query": q, "results": hits, "source_state": state}

    return router
