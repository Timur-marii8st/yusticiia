"""Исключения конвейера: искомая запись не найдена."""

from __future__ import annotations


class DocumentNotFound(KeyError):
    pass


class AnalysisNotFound(KeyError):
    pass


class FactNotFound(KeyError):
    pass
