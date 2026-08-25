"""Фабрика приложения."""

from __future__ import annotations

from fastapi import FastAPI

from ..config import AppConfig
from .deps import build_pipeline
from .routes import create_app


def make_app(config: AppConfig | None = None) -> FastAPI:
    return create_app(build_pipeline(config))
