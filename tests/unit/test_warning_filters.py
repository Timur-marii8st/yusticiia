"""Фильтры предупреждений: глушим чужое, своё оставляем ошибкой.

`pyproject.toml` превращает `DeprecationWarning` в ошибку — это
сознательная строгость проекта. Одно исключение сделано для псевдонимов
`anyio.abc.*`, которые импортирует `starlette.testclient`: без него
`pytest` не мог даже собрать тесты, и CI был красным.

Исключение задано по тексту сообщения, а не по категории. Эти тесты
удерживают его узким: если однажды кто-то заменит фильтр на глушение
всей категории, второй тест упадёт.
"""

from __future__ import annotations

import warnings

import pytest


def test_anyio_alias_warning_is_suppressed() -> None:
    """Предупреждение из зависимости не должно ронять сборку тестов."""
    warnings.warn(
        "The anyio.abc.BlockingPortal alias is deprecated, "
        "use anyio.from_thread.BlockingPortal instead.",
        DeprecationWarning,
        stacklevel=1,
    )


def test_our_own_deprecation_still_raises() -> None:
    """Устаревание в коде проекта обязано оставаться ошибкой."""
    with pytest.raises(DeprecationWarning):
        warnings.warn("устаревший вызов в second_opinion", DeprecationWarning, stacklevel=1)


def test_unrelated_anyio_warning_still_raises() -> None:
    """Глушится ровно один текст, а не всё, что упоминает anyio."""
    with pytest.raises(DeprecationWarning):
        warnings.warn("anyio: нечто иное устарело", DeprecationWarning, stacklevel=1)
