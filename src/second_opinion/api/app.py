"""Фабрика приложения."""

from __future__ import annotations

from fastapi import FastAPI

from ..config import AppConfig, load_config
from .deps import build_pipeline
from .routes import create_app


def make_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    pipeline = build_pipeline(config)
    return create_app(pipeline, auth_token=load_config().auth_token)
