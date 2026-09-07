"""Фабрика приложения."""

from __future__ import annotations

from fastapi import FastAPI

from ..auth.service import get_auth_service
from ..config import AppConfig, load_config
from .deps import build_pipeline
from .routes import create_app


def make_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    pipeline = build_pipeline(config)
    # Поднимаем auth-сервис заранее: неверный SO_JWT_SECRET должен ронять
    # запуск, а не первый пришедший логин.
    get_auth_service()
    return create_app(pipeline, auth_token=config.auth_token)
