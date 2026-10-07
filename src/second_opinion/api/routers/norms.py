"""Нормы: список, состояние корпуса, отдельная норма с редакциями."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from ...config import AppConfig, load_config
from ...legal_sources.store import NoApplicableVersionError, NormNotFoundError
from ...pipeline import AnalysisPipeline
from ..errors import human_message
from ..request_context import actor
from ..schemas import (
    AddVersionPayload,
    ConfirmHistoryPayload,
    NormCoverageItem,
    NormCoverageResponse,
    NormDetailResponse,
    NormEditResponse,
    NormListResponse,
)


def _require_norm_editor(request: Request) -> None:
    """Правка корпуса норм — только администратор, вне зависимости от режима.

    В отличие от разбора документа, здесь нет демонстрационного сценария
    без входа: правка меняет закон, на который опираются все проверки для
    всех судей, использующих это развёртывание, — не своё дело, а общее
    основание.
    """
    from ...auth.service import get_auth_service
    from ...domain.users import UserRole

    user = get_auth_service().get_user(actor(request))
    if user is None or user.role is not UserRole.ADMIN:
        raise HTTPException(
            status_code=403,
            detail="правка корпуса норм доступна только администратору",
        )


def _require_file_backend(config: AppConfig) -> None:
    if config.norms_backend != "file":
        raise HTTPException(
            status_code=409,
            detail=(
                f"правка недоступна: носитель норм — {config.norms_backend!r}, "
                "а не файловый корпус"
            ),
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

    @router.post("/{norm_id}/confirm-history")
    def confirm_history(
        norm_id: str, payload: ConfirmHistoryPayload, request: Request
    ) -> NormEditResponse:
        """Юрист подтверждает: история нормы сверена, статья не менялась.

        Без точки отсчёта у источника (``source_document`` в CLI): она
        требует запроса к ИПС, а медленному внешнему серверу не место в
        обработке HTTP-запроса, как и в ``/coverage`` выше. Без неё
        обнаружение устаревания продолжит честно отвечать «сравнивать не с
        чем» — это не проигрыш, а явная неопределённость вместо догадки.
        """
        config = load_config()
        _require_file_backend(config)
        _require_norm_editor(request)

        from ...audit.trail import AuditTrail
        from ...legal_sources.editing import NormEditError
        from ...legal_sources.editing import confirm_history as do_confirm
        from ...legal_sources.sealing import seal

        directory = config.fixtures_dir / "norms"
        try:
            do_confirm(
                directory,
                norm_id,
                checked_through=payload.checked_through,
                source=payload.source,
            )
        except NormEditError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc

        seal(directory)
        pipeline.norm_store.reload_from_directory(directory)
        AuditTrail(config.audit_path).log(
            operation="norm_confirm_history",
            component="norms_router",
            request_id=norm_id,
            actor=actor(request),
            input_hash=norm_id,
            details={
                "checked_through": payload.checked_through.isoformat(),
                "source": payload.source,
            },
        )
        return {
            "norm_id": norm_id,
            "message": (
                "История сверена и записана. Отслеживание устаревания "
                "(norms-check-updates) появится после привязки точки "
                "отсчёта из терминала: second-opinion norms-confirm-history "
                f"--norm-id {norm_id} --checked-through "
                f"{payload.checked_through.isoformat()} --source "
                f'"{payload.source}" --source-document <id документа в ИПС>'
            ),
            "sealed": True,
        }

    @router.post("/{norm_id}/add-version")
    def add_version(
        norm_id: str, payload: AddVersionPayload, request: Request
    ) -> NormEditResponse:
        """Юрист добавляет редакцию с фактическими границами действия.

        Текст вносит человек, вставляя формулировку из официального
        источника, — не результат OCR или модели. Пределы наказания
        (``sanctions``) редакция получает пустыми: они не выводятся из
        текста и вносятся отдельной юридической сверкой.
        """
        config = load_config()
        _require_file_backend(config)
        _require_norm_editor(request)

        from ...audit.trail import AuditTrail
        from ...legal_sources.editing import NormEditError
        from ...legal_sources.editing import add_version as do_add_version
        from ...legal_sources.sealing import seal

        directory = config.fixtures_dir / "norms"
        version_id = payload.version_id.strip() or f"v-{payload.effective_from.year}"
        try:
            do_add_version(
                directory,
                norm_id,
                version_id=version_id,
                text=payload.text,
                effective_from=payload.effective_from,
                effective_to=payload.effective_to,
                source_url=payload.source_url,
                source_edition=payload.source_edition,
            )
        except NormEditError as exc:
            raise HTTPException(status_code=400, detail=human_message(exc)) from exc

        seal(directory)
        pipeline.norm_store.reload_from_directory(directory)
        AuditTrail(config.audit_path).log(
            operation="norm_add_version",
            component="norms_router",
            request_id=norm_id,
            actor=actor(request),
            input_hash=norm_id,
            output_hash=version_id,
            details={
                "effective_from": payload.effective_from.isoformat(),
                "effective_to": (
                    payload.effective_to.isoformat() if payload.effective_to else None
                ),
                "source_url": payload.source_url,
            },
        )
        return {
            "norm_id": norm_id,
            "message": (
                f"Редакция «{version_id}» добавлена статусом draft. Пределы "
                "наказания внесите отдельной юридической сверкой, затем "
                "снова подтвердите норму."
            ),
            "sealed": True,
        }

    return router
