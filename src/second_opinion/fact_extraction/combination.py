"""Совокупность преступлений и приговоров (ст. 69, 70 УК РФ) в резолютивной части.

Разбирает пары «статья - наказание» по каждому преступлению, окончательное
наказание и ссылку на статью, по которой оно назначено. Работает отдельно от
``PatternFactExtractor``: тот отдаёт один срок на документ, а совокупность
требует нескольких чисел сразу.

Поддерживаются лишение свободы (срок в месяцах) и штраф (сумма в рублях).
Другие виды наказаний и смешанные случаи отдаются как ``unit="mixed"``:
пересчёт по ст. 71 УК РФ не выполняется.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pattern_extractor import (
    _CH,
    _ST,
    PatternFactExtractor,
    _article_number,
    _extract_term_months,
)

SPECIAL_PART_FIRST_ARTICLE = 105

_FINE = re.compile(r"штраф\w*\s+в\s+размере\s+(\d[\d ]*)", re.IGNORECASE)
_FINAL = re.compile(r"окончательн\w+", re.IGNORECASE)
_COMBINATION_ARTICLE = re.compile(
    rf"(?:{_CH}\s*(\d)\s*)?{_ST}\s*(69|70)\s*(?:УК|Уголовного)", re.IGNORECASE
)
_ADDITIONAL_ONLY = re.compile(r"неотбыт\w+\s+(?:часть\s+)?дополнительн\w+", re.IGNORECASE)
_JOIN = re.compile(r"присоедин\w+", re.IGNORECASE)
_LOOKBACK = 400
_AMOUNT_WINDOW = 250


@dataclass(frozen=True)
class Offense:
    article: int
    article_suffix: str | None
    part: int | None
    value: float
    unit: str
    quote: str


@dataclass(frozen=True)
class Combination:
    article: int
    part: int | None
    offenses: list[Offense]
    final_value: float | None
    final_unit: str | None
    unserved_value: float | None = None
    unserved_additional_only: bool = False
    units: set[str] = field(default_factory=set)

    @property
    def unit(self) -> str:
        if len(self.units) == 1:
            return next(iter(self.units))
        return "mixed"


def _amount(segment: str) -> tuple[float, str, int, int] | None:
    """Первое наказание в отрезке: (значение, единица, начало, конец)."""
    found: list[tuple[int, float, str, int]] = []
    fine = _FINE.search(segment)
    if fine:
        rubles = float(fine.group(1).replace(" ", ""))
        found.append((fine.start(), rubles, "rubles", fine.end()))
    term = _extract_term_months(segment)
    if term is not None:
        months, start, end = term
        found.append((start, months, "months", end))
    if not found:
        return None
    start, value, unit, end = min(found, key=lambda item: item[0])
    return value, unit, start, end


def extract_combination(text: str) -> Combination | None:
    """Совокупность из резолютивной части или ``None``, если её нет."""
    extractor = PatternFactExtractor()
    narrative = extractor._narrative_start(text)
    marker = extractor._RESOLUTIVE_MARKER.search(text, narrative)
    if marker is None:
        return None
    final = _FINAL.search(text, marker.end())
    if final is None:
        return None
    governing = list(
        _COMBINATION_ARTICLE.finditer(
            text, max(marker.end(), final.start() - _LOOKBACK), final.start()
        )
    )
    if not governing:
        return None
    rule = governing[-1]
    rule_part = int(rule.group(1)) if rule.group(1) else None

    citations: list[tuple[int, int, str | None, int | None]] = []
    for pattern in (extractor._QUAL_PART_FIRST, extractor._QUAL_ARTICLE_FIRST):
        for match in pattern.finditer(text, marker.end(), rule.start()):
            first, second = match.group(1), match.group(2)
            part_number: int
            if pattern is extractor._QUAL_PART_FIRST:
                part_number, raw = int(first), second
            else:
                raw, part_number = first, int(second)
            parsed = _article_number(raw)
            if parsed is None or parsed[0] < SPECIAL_PART_FIRST_ARTICLE:
                continue
            citations.append((match.start(), parsed[0], parsed[1], part_number))
    citations.sort()

    offenses: list[Offense] = []
    for index, (start, article, suffix, part) in enumerate(citations):
        stop = citations[index + 1][0] if index + 1 < len(citations) else rule.start()
        segment = text[start:stop]
        amount = _amount(segment)
        if amount is None:
            continue
        context = text[max(marker.end(), start - _LOOKBACK // 2) : start + amount[2]].lower()
        if "наказани" not in context:
            continue
        value, unit, a_start, a_end = amount
        offenses.append(
            Offense(article, suffix, part, value, unit, " ".join(segment[a_start:a_end].split()))
        )

    tail = _amount(text[final.start() : final.start() + _AMOUNT_WINDOW])
    unserved = None
    additional_only = False
    if rule.group(2) == "70":
        between = text[rule.end() : final.start()]
        additional_only = bool(_ADDITIONAL_ONLY.search(between))
        join = _JOIN.search(between)
        if join and not additional_only:
            joined = _amount(between[join.end() :])
            if joined is not None and joined[1] == "months":
                unserved = joined[0]
    units = {o.unit for o in offenses}
    if tail is not None:
        units.add(tail[1])
    if not offenses:
        return None
    return Combination(
        article=int(rule.group(2)),
        part=rule_part,
        offenses=offenses,
        final_value=tail[0] if tail else None,
        final_unit=tail[1] if tail else None,
        unserved_value=unserved,
        unserved_additional_only=additional_only,
        units=units,
    )
