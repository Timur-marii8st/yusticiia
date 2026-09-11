"""Выбор источника редакционной истории по конфигурации."""

from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from .redaction_source import RedactionSource, RedactionSourceError


def build_redaction_source(config: AppConfig) -> RedactionSource:
    """Собрать источник по ``SO_REDACTION_SOURCE``.

    По умолчанию источник выключен: внешний контур включается только
    явным решением оператора, как и внешний LLM.
    """
    mode = config.redaction_source

    if mode == "garant":
        from .garant import GarantSource

        return GarantSource(config.garant_base_url, config.garant_token)

    if mode == "stub":
        directory = Path(
            config.garant_stub_dir or (config.fixtures_dir / "garant_stub")
        )
        if not directory.exists():
            raise RedactionSourceError(f"каталог заглушки не найден: {directory}")
        from .stub_source import StubRedactionSource

        return StubRedactionSource(directory)

    raise RedactionSourceError(
        "источник редакций выключен: задайте SO_REDACTION_SOURCE=garant "
        "(нужен токен) либо =stub (ответы из подготовленных файлов)"
    )
