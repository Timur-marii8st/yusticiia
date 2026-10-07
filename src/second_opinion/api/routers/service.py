"""Служебные эндпойнты: состояние и метрики. Вне ``/api``, авторизации не требуют."""

from __future__ import annotations

from fastapi import APIRouter

from ... import __version__
from ..schemas import HealthResponse, MetricsResponse


def build_router(auth_mode: str) -> APIRouter:
    router = APIRouter(tags=["служебные"])

    @router.get("/health")
    def health() -> HealthResponse:
        return {"status": "ok", "version": __version__, "auth_mode": auth_mode}

    @router.get("/metrics", response_model_exclude_none=True)
    def metrics() -> MetricsResponse:
        """Снимок внутренних метрик конвейера.

        Включается через ``SO_METRICS_ENABLED=1``. Если выключено,
        возвращается ``{"enabled": false}`` без накопления значений.
        """
        from ...metrics import get_metrics

        registry = get_metrics()
        if not registry.enabled:
            return {"enabled": False}
        return {"enabled": True, "metrics": registry.snapshot()}

    return router
