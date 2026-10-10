"""Разбор санкции части статьи УК РФ на основные виды наказания и их пределы.

Результат — черновик для корпуса: пределы, извлечённые из текста, вносятся с
пометкой ``pending_review`` и проверяются юристом. Разбираются только основные
наказания: альтернативы после «наказывается» разделены словом «либо» (в
редакции 1996 года местами «или»), если за ним следует вид наказания, а
дополнительные наказания («со штрафом», «с ограничением свободы», «с лишением
права») отрезаются от основного, иначе сумма дополнительного штрафа попала бы
в пределы основного.

Чего разбор сознательно не делает:

- штраф в минимальных размерах оплаты труда (редакция 1996 года) в рубли не
  переводится — предел остаётся пустым;
- штраф в размере дохода за период и кратный штраф («в размере
  десятикратной суммы») рублями не выражены — тоже пустой предел;
- арест, принудительные работы, пожизненное лишение свободы и смертная казнь
  числом не описываются и в проверки не идут.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_UNITS = {
    "одного": 1, "одной": 1, "двух": 2, "трех": 3, "трёх": 3, "четырех": 4, "четырёх": 4,
    "пяти": 5, "шести": 6, "семи": 7, "восьми": 8, "девяти": 9, "десяти": 10,
    "одиннадцати": 11, "двенадцати": 12, "тринадцати": 13, "четырнадцати": 14,
    "пятнадцати": 15, "шестнадцати": 16, "семнадцати": 17, "восемнадцати": 18,
    "девятнадцати": 19,
}
_TENS = {
    "двадцати": 20, "тридцати": 30, "сорока": 40, "пятидесяти": 50, "шестидесяти": 60,
    "семидесяти": 70, "восьмидесяти": 80, "девяноста": 90,
}
_HUNDREDS = {
    "ста": 100, "двухсот": 200, "трехсот": 300, "трёхсот": 300, "четырехсот": 400,
    "четырёхсот": 400, "пятисот": 500, "шестисот": 600, "семисот": 700, "восьмисот": 800,
    "девятисот": 900,
}
_SCALES = {"тысяч": 1_000, "тысячи": 1_000, "тысячу": 1_000, "миллиона": 1_000_000,
           "миллионов": 1_000_000, "миллион": 1_000_000}
_WORDS = sorted({**_UNITS, **_TENS, **_HUNDREDS, **_SCALES}, key=len, reverse=True)
_NUMBER = rf"(?:(?:\d[\d ]*|{'|'.join(_WORDS)})\s*)+"
_TERM_UNITS = {"месяц": 1, "месяца": 1, "месяцев": 1, "год": 12, "года": 12, "лет": 12}
_TERM_UNIT = "месяцев|месяца|месяц|лет|года|год"

_ALTERNATIVES = re.compile(
    r",?\s+(?:либо|или)\s+(?=(?:штраф|обязательн|исправительн|ограничени|принудительн|арест|"
    r"содержани|лишени|пожизненн|смертн))",
    re.IGNORECASE,
)
_PUNISHABLE = re.compile(r"наказыва\w+\s+", re.IGNORECASE)
_ADDITIONAL = re.compile(
    r"\s+(?:со|с)\s+(?:штраф|ограничени|лишени\w*\s+права|обязательн|принудительн)\w*", re.IGNORECASE
)
_RANGE = re.compile(
    rf"(?:от\s+(?P<low>{_NUMBER})(?P<low_unit>{_TERM_UNIT})?\s*)?до\s+(?P<high>{_NUMBER})", re.IGNORECASE
)


def words_to_number(text: str) -> int | None:
    """«трехсот шестидесяти» -> 360, «ста двадцати тысяч» -> 120000, «80 000» -> 80000."""
    total = 0
    current = 0
    seen = False
    for token in re.findall(r"\d[\d ]*\d|\d|[а-яё]+", text.lower()):
        if token[0].isdigit():
            current += int(token.replace(" ", ""))
            seen = True
        elif token in _SCALES:
            total += (current or 1) * _SCALES[token]
            current = 0
            seen = True
        elif token in _HUNDREDS or token in _TENS or token in _UNITS:
            current += _HUNDREDS.get(token) or _TENS.get(token) or _UNITS[token]
            seen = True
        else:
            break
    return total + current if seen else None


@dataclass(frozen=True)
class ParsedSanction:
    """Основное наказание и его пределы в единицах вида наказания."""

    punishment_type: str
    low: float | None
    high: float | None
    unit: str


def _months(number: str, unit: str | None) -> float | None:
    value = words_to_number(number)
    if value is None or unit is None:
        return None
    return float(value * _TERM_UNITS[unit.lower()])


def _term(text: str) -> tuple[float | None, float | None]:
    match = re.search(
        rf"на\s+срок\s+(?:от\s+(?P<low>{_NUMBER})(?P<low_unit>{_TERM_UNIT})?\s*)?до\s+(?P<high>{_NUMBER})(?P<high_unit>{_TERM_UNIT})",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return None, None
    high = _months(match.group("high"), match.group("high_unit"))
    low = None
    if match.group("low"):
        low = _months(match.group("low"), match.group("low_unit") or match.group("high_unit"))
    return low, high


def _hours(text: str) -> tuple[float | None, float | None]:
    match = re.search(
        rf"на\s+срок\s+(?:от\s+(?P<low>{_NUMBER}))?(?:до\s+(?P<high>{_NUMBER}))?час", text, re.IGNORECASE
    )
    if match is None or not match.group("high"):
        return None, None
    low = words_to_number(match.group("low")) if match.group("low") else None
    high = words_to_number(match.group("high"))
    return (float(low) if low is not None else None), (float(high) if high is not None else None)


def _rubles(text: str) -> tuple[float | None, float | None]:
    if re.search(r"минимальн\w+\s+размер\w*\s+оплаты", text, re.IGNORECASE):
        return None, None
    match = re.search(
        rf"в\s+размере\s+(?:от\s+(?P<low>{_NUMBER}))?до\s+(?P<high>{_NUMBER})рубл", text, re.IGNORECASE
    )
    if match is None:
        return None, None
    high = words_to_number(match.group("high"))
    low = words_to_number(match.group("low")) if match.group("low") else None
    if high is not None and low is not None and low < 1000 <= high:
        scale = next((s for word, s in _SCALES.items() if word in match.group("high").lower()), 1)
        low *= scale
    return (float(low) if low is not None else None), (float(high) if high is not None else None)


def parse_sanctions(part_text: str) -> list[ParsedSanction]:
    """Основные наказания части статьи с пределами; пустой список — санкции нет."""
    found = _PUNISHABLE.search(part_text)
    if found is None:
        return []
    body = part_text[found.end():]
    body = re.split(r"\s+Примечани", body, maxsplit=1)[0]
    result: list[ParsedSanction] = []
    previous_term: tuple[float | None, float | None] = (None, None)
    for alternative in _ALTERNATIVES.split(body):
        main = _ADDITIONAL.split(alternative, maxsplit=1)[0]
        lowered = main.lower().lstrip()
        if lowered.startswith("штраф"):
            low, high = _rubles(main)
            result.append(ParsedSanction("fine", low, high, "rubles"))
        elif lowered.startswith("обязательн"):
            low, high = _hours(main)
            result.append(ParsedSanction("compulsory_labor", low, high, "hours"))
        elif lowered.startswith("исправительн"):
            low, high = _term(main)
            result.append(ParsedSanction("correctional_labor", low, high, "months"))
        elif lowered.startswith("ограничени") and "свобод" in lowered[:30]:
            low, high = _term(main)
            result.append(ParsedSanction("restriction_of_liberty", low, high, "months"))
        elif lowered.startswith("лишени") and "свобод" in lowered[:20]:
            if "на тот же срок" in lowered:
                low, high = previous_term
            else:
                low, high = _term(main)
            result.append(ParsedSanction("imprisonment", low, high, "months"))
        if lowered.startswith(("принудительн", "исправительн", "ограничени", "лишени")):
            term = _term(main)
            if term[1] is not None:
                previous_term = term
    return result
