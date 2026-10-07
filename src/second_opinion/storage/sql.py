"""Идентификаторы SQL, которые подставляются в запрос текстом."""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def safe_sql_identifier(value: str) -> str:
    """Вернуть имя таблицы или префикс, если оно безопасно для подстановки.

    Значения параметров идут отдельно от запроса, а имя таблицы так передать
    нельзя, поэтому оно проверяется шаблоном: латиница, цифры и подчёркивание.
    """
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"недопустимое имя в SQL: {value!r} (латиница в нижнем регистре, цифры и _)"
        )
    return value
