"""Сквозной конвейер: документ -> факты -> проверки -> практика -> отчёт.

Публичный API пакета — то же, что раньше отдавал модуль ``pipeline.py``:
``AnalysisPipeline`` и три исключения "не найдено". Внутреннее устройство
(``ingestion.py``, ``derivation.py``, ``errors.py``, ``facade.py``) — детали
реализации, наружу не экспортируются.
"""

from __future__ import annotations

from .errors import AnalysisNotFound, DocumentNotFound, FactNotFound
from .facade import UNSET_VALUE, AnalysisPipeline

__all__ = [
    "UNSET_VALUE",
    "AnalysisNotFound",
    "AnalysisPipeline",
    "DocumentNotFound",
    "FactNotFound",
]
