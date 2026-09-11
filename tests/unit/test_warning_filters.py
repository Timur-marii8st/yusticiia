"""Фильтры предупреждений: глушим чужое, своё оставляем ошибкой.

`pyproject.toml` превращает `DeprecationWarning` в ошибку — сознательная
строгость проекта. Одно исключение сделано для псевдонимов `anyio.abc.*`,
которые импортирует `starlette.testclient`: без него `pytest` не мог даже
собрать тесты, и CI был красным.

Исключение привязано к модулю-источнику. Эти тесты удерживают его узким:
то же предупреждение, пришедшее откуда-то ещё, обязано ронять прогон.
"""

from __future__ import annotations

import warnings

import pytest


def test_starlette_testclient_imports_cleanly() -> None:
    """Реальный случай: импорт TestClient не должен ронять сборку тестов."""
    from fastapi.testclient import TestClient

    assert TestClient is not None


def test_same_warning_from_our_code_still_raises() -> None:
    """Исключение действует только для starlette.testclient.

    Если бы фильтр был задан одним текстом сообщения, без привязки к
    модулю, этот вызов прошёл бы молча — и любое такое устаревание в
    нашем коде осталось бы незамеченным.
    """
    with pytest.raises(DeprecationWarning):
        warnings.warn(
            "The anyio.abc.BlockingPortal alias is deprecated, "
            "use anyio.from_thread.BlockingPortal instead.",
            DeprecationWarning,
            stacklevel=1,
        )


def test_our_own_deprecation_still_raises() -> None:
    with pytest.raises(DeprecationWarning):
        warnings.warn("устаревший вызов в second_opinion", DeprecationWarning, stacklevel=1)
