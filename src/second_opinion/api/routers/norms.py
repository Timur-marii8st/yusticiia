"""Нормы: список, состояние корпуса, отдельная норма с редакциями."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from ...config import load_config
from ...legal_sources.store import NoApplicableVersionError, NormNotFoundError
from ...pipeline import AnalysisPipeline
from ..errors import human_message
from ..schemas import (
    NormCoverageItem,
    NormCoverageResponse,
    NormDetailResponse,
    NormListResponse,
)


def build_router(pipeline: AnalysisPipeline) -> APIRouter:
    router = APIRouter(prefix="/norms", tags=["нормы"])

    @router.get("")
    def list_norms(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> NormListResponse:
        """Список норм корпуса окном.

        Значение по умолчанию покрывает нынешние 27 норм целиком, поэтому
        существующие клиенты ничего не заметят; окно появляется заранее, чтобы
        рост корпуса не стал ломающим изменением.
        """
        store = pipeline.norm_store
        norms = store.list_norms()
        window = norms[offset : offset + limit]
        return {
            "norms": [
                {"norm_id": n.norm_id, "ref": n.ref, "title": n.title} for n in window
            ],
            "page": {
                "total": len(norms),
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(window) < len(norms),
            },
        }

    @router.get("/coverage")
    def norms_coverage() -> NormCoverageResponse:
        """Состояние корпуса: что сверено, что требует юридической сверки.

        Объявлен до ``/{norm_id}``: иначе «coverage» приедет сюда как
        идентификатор нормы и вернётся 404.

        Свежесть относительно внешнего источника здесь не проверяется — это
        сетевой запрос к медленному серверу, которому не место в обработке
        HTTP-запроса. Она остаётся за ``norms-check-updates``.
        """
        from ...legal_sources.coverage import analyze_store, needs_legal_review

        store = pipeline.norm_store
        coverages = analyze_store(store)
        # Наряд считается той же функцией, что и в CLI: своя формула здесь
        # однажды разойдётся с `norms-coverage`, и два числа будут называться
        # одинаково.
        flagged = {c.norm_id for c in needs_legal_review(coverages)}

        items: list[NormCoverageItem] = []
        for coverage in coverages:
            editions = sorted(
                {
                    v.source_edition
                    for v in store.versions(coverage.norm_id)
                    if v.source_edition
                }
            )
            items.append(
                NormCoverageItem(
                    norm_id=coverage.norm_id,
                    ref=coverage.ref,
                    version_count=coverage.version_count,
                    covered_from=coverage.covered_from,
                    covered_to=coverage.covered_to,
                    rule_relevant=coverage.rule_relevant,
                    history_verified=coverage.history_verified,
                    history_unrecorded=coverage.history_unrecorded(),
                    needs_review=coverage.norm_id in flagged,
                    gaps=[list(gap) for gap in coverage.gaps],
                    source_editions=editions,
                )
            )

        config = load_config()
        sealed: bool | None = None
        if config.norms_backend == "file":
            from ...legal_sources.sealing import check as check_seal

            try:
                sealed = check_seal(config.fixtures_dir / "norms").ok
            except (OSError, ValueError):
                # Манифест нечитаем — это «неизвестно», а не «цел».
                sealed = None

        return {
            "total": len(items),
            "needs_review": len(flagged),
            "corpus_sealed": sealed,
            "norms": items,
        }

    @router.get("/{norm_id}")
    def get_norm(
        norm_id: str, applicable_at: str | None = Query(default=None)
    ) -> NormDetailResponse:
        store = pipeline.norm_store
        try:
            meta = store.get_norm_meta(norm_id)
        except NormNotFoundError as exc:
            raise HTTPException(status_code=404, detail=human_message(exc)) from exc
        if applicable_at:
            try:
                when = date.fromisoformat(applicable_at)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="applicable_at: ожидается ISO-дата"
                ) from exc
            try:
                version = store.get_norm(norm_id, when)
            except NoApplicableVersionError as exc:
                raise HTTPException(status_code=404, detail=human_message(exc)) from exc
            return {"norm": meta, "version": version}
        return {"norm": meta, "versions": store.versions(norm_id)}

    return router
