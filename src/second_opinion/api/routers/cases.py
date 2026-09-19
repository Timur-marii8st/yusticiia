"""Сопоставимая практика: справочный материал, а не основание вывода."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...domain.cases import ComparableCase
from ...pipeline import AnalysisPipeline
from ..schemas import CaseListResponse


def build_router(pipeline: AnalysisPipeline) -> APIRouter:
    router = APIRouter(prefix="/cases", tags=["практика"])

    @router.get("")
    def list_cases(
        article: int | None = Query(default=None),
        part: int | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> CaseListResponse:
        selected: list[ComparableCase] = []
        for case in pipeline.retriever.cases:
            if article is not None and case.article != article:
                continue
            if part is not None and case.part != part:
                continue
            selected.append(case)
        window = selected[offset : offset + limit]
        return {
            "total": len(selected),
            "cases": window,
            "page": {
                "total": len(selected),
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(window) < len(selected),
            },
        }

    return router
