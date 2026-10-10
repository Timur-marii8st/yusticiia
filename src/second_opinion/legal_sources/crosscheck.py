"""Сверка санкций корпуса с текстом статьи в ИПС.

Пределы санкции, которыми оперирует движок правил, внесены в корпус вручную
и сверены юристом по одному источнику. Здесь они сравниваются со вторым,
независимым: текстом статьи в последней подготовленной редакции ИПС. Сравнение
касается только лишения свободы на срок: другие виды наказания в корпусе не
хранят пределов.

Результат ``unparsed`` означает «сравнить не удалось», а не «совпало»:
формулировку статьи разобрать нельзя либо её нет в тексте источника.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..domain.norms import LegalNorm, NormVersion
from .pravo_gov import PravoGovSource, prepared_editions
from .store import NormStore

IMPRISONMENT = "imprisonment"

_UNIT_WORDS = {
    "одного": 1,
    "двух": 2,
    "трех": 3,
    "трёх": 3,
    "четырех": 4,
    "четырёх": 4,
    "пяти": 5,
    "шести": 6,
    "семи": 7,
    "восьми": 8,
    "девяти": 9,
    "десяти": 10,
    "одиннадцати": 11,
    "двенадцати": 12,
    "тринадцати": 13,
    "четырнадцати": 14,
    "пятнадцати": 15,
    "шестнадцати": 16,
    "семнадцати": 17,
    "восемнадцати": 18,
    "девятнадцати": 19,
}
_TENS_WORDS = {"двадцати": 20, "тридцати": 30}
_UNIT_MONTHS = {"месяц": 1, "месяца": 1, "месяцев": 1, "год": 12, "года": 12, "лет": 12}

_UNITS = "месяцев|месяца|месяц|лет|года|год"
_SMALL = "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))
_TENS = "|".join(_TENS_WORDS)
_NUMBER = rf"(?:(?:{_TENS})(?:\s+(?:{_SMALL}))?|{_SMALL}|\d+)"

_IMPRISONMENT = re.compile(
    rf"лишением\s+свободы\s+на\s+срок\s+"
    rf"(?:от\s+(?P<low>{_NUMBER})(?:\s+(?P<low_unit>{_UNITS}))?\s+)?"
    rf"до\s+(?P<high>{_NUMBER})\s+(?P<high_unit>{_UNITS})",
    re.IGNORECASE,
)
_SAME_TERM = re.compile(r"лишением\s+свободы\s+на\s+тот\s+же\s+срок", re.IGNORECASE)
_UP_TO = re.compile(rf"на\s+срок\s+до\s+(?P<high>{_NUMBER})\s+(?P<unit>{_UNITS})", re.IGNORECASE)
_PART_STOP = re.compile(r"\sПримечани", re.IGNORECASE)

_NOTES_HEADING = re.compile(r"\sПримечани[ея]\s*\.")


@dataclass(frozen=True)
class Term:
    """Срок лишения свободы в месяцах; ``low`` пуст, если указан только верхний предел."""

    low: float | None
    high: float

    def __str__(self) -> str:
        if self.low is None:
            return f"до {_fmt(self.high)} мес."
        return f"от {_fmt(self.low)} до {_fmt(self.high)} мес."


@dataclass(frozen=True)
class CrossCheck:
    """Итог сверки одной нормы."""

    ref: str
    status: str
    corpus: str
    source: str
    detail: str = ""


def _fmt(months: float) -> str:
    return str(int(months)) if float(months).is_integer() else f"{months:g}"


def _number(words: str) -> float:
    total = 0
    for token in words.lower().split():
        if token.isdigit():
            total += int(token)
        else:
            total += _TENS_WORDS.get(token) or _UNIT_WORDS[token]
    return float(total)


def split_parts(body: str) -> dict[int, str]:
    """Разбить текст статьи на части по номерам «1.», «2.»; без номеров - часть 0.

    Номера ищутся только до заголовка «Примечание.» / «Примечания.»: нумерованные
    примечания частями статьи не являются. Если номер напечатан без точки
    («…ст. 7362). 3 Грабеж, совершенный:» в редакциях ИПС 233–234), он
    принимается только после конца предложения и перед заглавной буквой.
    """
    marks: list[tuple[int, int, int]] = []
    position = 0
    expected = 1
    notes = _NOTES_HEADING.search(body)
    limit = notes.start() if notes else len(body)
    while True:
        found = re.compile(rf"(?:^|\s){expected}\.\s+(?=[А-ЯЁ«\"(])").search(body, position, limit)
        if found is None and expected > 1:
            found = re.compile(rf"(?<=[.;)]\s){expected}\s+(?=[А-ЯЁ])").search(body, position, limit)
        if found is None:
            break
        marks.append((expected, found.start(), found.end()))
        position = found.end()
        expected += 1
    if not marks:
        return {0: body}
    parts: dict[int, str] = {}
    for index, (number, _, end) in enumerate(marks):
        stop = marks[index + 1][1] if index + 1 < len(marks) else len(body)
        chunk = body[end:stop]
        note = _PART_STOP.search(chunk)
        parts[number] = (chunk[: note.start()] if note else chunk).strip()
    return parts


def parse_imprisonment(part_text: str) -> list[Term]:
    """Все формулировки лишения свободы на срок в тексте части.

    Формулировка «на тот же срок» берёт значение у ближайшего предшествующего
    «на срок до ...».
    """
    terms: list[Term] = []
    for match in _IMPRISONMENT.finditer(part_text):
        high_unit = _UNIT_MONTHS[match.group("high_unit").lower()]
        high = _number(match.group("high")) * high_unit
        low = None
        if match.group("low"):
            low_unit = _UNIT_MONTHS[(match.group("low_unit") or match.group("high_unit")).lower()]
            low = _number(match.group("low")) * low_unit
        terms.append(Term(low=low, high=high))
    for match in _SAME_TERM.finditer(part_text):
        earlier = list(_UP_TO.finditer(part_text, 0, match.start()))
        if earlier:
            last = earlier[-1]
            months = _number(last.group("high")) * _UNIT_MONTHS[last.group("unit").lower()]
            terms.append(Term(low=None, high=months))
    return terms


def _corpus_term(version: NormVersion) -> Term | None:
    for sanction in version.sanctions:
        if sanction.punishment_type.value == IMPRISONMENT and sanction.max_months is not None:
            return Term(low=sanction.min_months, high=float(sanction.max_months))
    return None


def crosscheck_norm(
    norm: LegalNorm, version: NormVersion, article_body: str | None
) -> CrossCheck | None:
    """Сверить пределы лишения свободы одной версии нормы с текстом статьи.

    ``None``, если у версии нет предела лишения свободы: сравнивать нечего.
    """
    corpus = _corpus_term(version)
    if corpus is None:
        return None
    ref = norm.ref
    if article_body is None:
        return CrossCheck(ref, "unparsed", str(corpus), "-", "статьи нет в тексте источника")
    parts = split_parts(article_body)
    key = norm.part if norm.part is not None else 0
    part_text = parts.get(key)
    if part_text is None:
        return CrossCheck(ref, "unparsed", str(corpus), "-", f"часть {key} не найдена в тексте")
    terms = parse_imprisonment(part_text)
    if not terms:
        return CrossCheck(
            ref, "unparsed", str(corpus), "-", "в части нет формулировки «лишением свободы на срок»"
        )
    if len(terms) > 1:
        return CrossCheck(
            ref,
            "unparsed",
            str(corpus),
            "; ".join(str(t) for t in terms),
            "в части несколько формулировок лишения свободы",
        )
    source = terms[0]
    if source == corpus:
        return CrossCheck(ref, "match", str(corpus), str(source))
    return CrossCheck(ref, "mismatch", str(corpus), str(source), "пределы различаются")


def crosscheck_store(
    store: NormStore, source: PravoGovSource, topic: str
) -> tuple[str, list[CrossCheck]]:
    """Сверить действующие версии УК РФ с последней подготовленной редакцией источника."""
    editions = prepared_editions(source.editions(topic))
    last = editions[-1]
    articles = source.article_texts(topic, last.number or 0)
    results: list[CrossCheck] = []
    for norm in store.list_norms():
        if norm.code != "УК РФ":
            continue
        article = f"{norm.article}.{norm.article_suffix}" if norm.article_suffix else str(norm.article)
        for version in store.versions(norm.norm_id):
            if version.effective_to is not None or version.synthetic:
                continue
            result = crosscheck_norm(norm, version, articles.get(article))
            if result is not None:
                results.append(result)
    return last.label, results


def format_crosscheck(edition_label: str, results: list[CrossCheck]) -> str:
    lines = [f"Сверка с редакцией ИПС: «{edition_label}»", ""]
    names = {"match": "совпало", "mismatch": "РАСХОЖДЕНИЕ", "unparsed": "не сверено"}
    for item in results:
        tail = f" ({item.detail})" if item.detail else ""
        lines.append(
            f"  {item.ref:<22} {names[item.status]:<12} корпус: {item.corpus}; источник: {item.source}{tail}"
        )
    counts = {s: sum(1 for r in results if r.status == s) for s in names}
    lines += [
        "",
        f"Совпало: {counts['match']}, расхождений: {counts['mismatch']}, не сверено: {counts['unparsed']}",
    ]
    return "\n".join(lines)
