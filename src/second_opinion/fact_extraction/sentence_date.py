"""Дата приговора из шапки документа.

Дата стоит между словами «Именем Российской Федерации» и «в составе»: «г. Райчихинск
10 марта 2026 года», ««24» ноября 2025 года», «31 марта 2026 г.». Если суд обезличил дату
(«ДД.ММ.ГГГГ»), её нет: значение не выдумывается.
"""

from __future__ import annotations

import re
from datetime import date

HEAD_CHARS = 1200

_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_DATE = re.compile(
    r"(?:«\s*(\d{1,2})\s*»|(?<![\d.])(\d{1,2}))\s+("
    + "|".join(_MONTHS)
    + r")\s+(\d{4})\s*(?:года|г\b)",
    re.IGNORECASE,
)
_NUMERIC = re.compile(r"(?<![\d.])(\d{2})\.(\d{2})\.(\d{4})(?![\d.])")
_COMPOSITION = re.compile(r"в\s+составе", re.IGNORECASE)


def extract_sentence_date(text: str) -> tuple[date, str] | None:
    """Дата приговора и цитата, по которой она найдена, либо ``None``."""
    head = text[:HEAD_CHARS]
    cut = _COMPOSITION.search(head)
    if cut is not None:
        head = head[: cut.start()]
    candidates: list[tuple[int, date, str]] = []
    for match in _DATE.finditer(head):
        day = int(match.group(1) or match.group(2))
        month = _MONTHS[match.group(3).lower()]
        parsed = _safe_date(int(match.group(4)), month, day)
        if parsed is not None:
            candidates.append((match.start(), parsed, match.group(0)))
    for match in _NUMERIC.finditer(head):
        parsed = _safe_date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        if parsed is not None:
            candidates.append((match.start(), parsed, match.group(0)))
    if not candidates:
        return None
    _position, parsed, quote = min(candidates, key=lambda item: item[0])
    return parsed, " ".join(quote.split())


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
