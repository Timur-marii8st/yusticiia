from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..domain.documents import Document
from ..domain.enums import ExtractionMethod, FactStatus, FactType, OffenseStage, PunishmentType
from ..domain.evidence import Evidence
from ..domain.facts import LegalFact

PATTERN_CONFIDENCE = 0.95

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}


_ST = r"(?:ст\.?|стат\w+)"
_CH = r"(?:ч\.?|част\w+)"

_NUMBER_WORDS = {
    "один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4, "пять": 5, "пяти": 5, "шесть": 6,
    "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9,
    "девяти": 9, "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11,
    "двенадцать": 12, "двенадцати": 12, "тринадцать": 13, "тринадцати": 13,
    "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17,
    "восемнадцать": 18, "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
}
_TENS_WORDS = {"двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30}
_SMALL_NUMBER = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_TENS_NUMBER = "|".join(_TENS_WORDS)
_WORD_NUMBER = rf"(?:(?:{_TENS_NUMBER})(?:\s+(?:{_SMALL_NUMBER}))?|{_SMALL_NUMBER})"


def _number_value(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    return sum(_TENS_WORDS.get(w) or _NUMBER_WORDS[w] for w in raw.lower().split())


def _months_from_tokens(tokens: list[tuple[int, str]]) -> float | None:
    total = 0.0
    for value, unit in tokens:
        if unit.startswith("мес"):
            total += value
        else:
            total += value * 12
    return total if total > 0 else None


_TERM_TOKEN = re.compile(
    rf"(?<!\w)(?P<num>\d+|{_WORD_NUMBER}(?!\w))\s+(?:\([^)]*\)\s*)?"
    r"(?P<unit>лет|год(?:а|ов)?|мес(?:яцев|яца|ец)?\w*)",
    re.IGNORECASE,
)


def _extract_term_months(window: str) -> tuple[float, int, int] | None:
    """Найти срок в месяцах внутри окна текста; вернуть (месяцы, старт, конец)."""
    tokens: list[tuple[int, str]] = []
    first_start: int | None = None
    last_end = -1
    for match in _TERM_TOKEN.finditer(window):
        value, unit = _number_value(match.group("num")), match.group("unit").lower()
        if 1900 <= value <= 2100 and unit.startswith(("год", "лет")):
            continue
        if last_end != -1 and match.start() - last_end > 10:
            break
        if first_start is None:
            first_start = match.start()
        tokens.append((value, unit))
        last_end = match.end()
    months = _months_from_tokens(tokens) if tokens else None
    if not months or not tokens or first_start is None:
        return None
    return months, first_start, last_end


_CODE_SUFFIX = re.compile(r"\s{1,3}РФ\b")


def _complete_span(text: str, start: int, end: int) -> int:
    """Конец цитаты, доведённый до конца слова и до «РФ» после «УК» или «УПК».

    Шаблоны останавливаются на основе слова («возраст 34 год», «ст. 73 УК»), и в
    заключении цитата выглядела оборванной. Начало и смысл цитаты не меняются,
    она только дочитывается до границы слова.
    """
    while 0 < end < len(text) and text[end - 1].isalpha() and text[end].isalpha():
        end += 1
    if text[start:end].endswith(("УК", "УПК")):
        tail = _CODE_SUFFIX.match(text, end)
        if tail:
            end = tail.end()
    return end


class PatternFactExtractor:
    """Детерминированное извлечение фактов по паттернам.

    Каждый факт получает evidence с точными оффсетами совпадения; статус
    детерминированного извлечения — VERIFIED.
    """

    def extract(self, document: Document) -> list[LegalFact]:
        text = document.text
        found: dict[tuple[str, str], LegalFact] = {}
        order: list[tuple[str, str]] = []

        def emit(
            fact_type: FactType,
            value: Any,
            start: int,
            end: int,
            page: int | None = None,
        ) -> None:
            key = (fact_type.value, _value_key(value))
            end = _complete_span(text, start, end)
            evidence = Evidence(
                document_id=document.document_id,
                quote=text[start:end],
                start_offset=start,
                end_offset=end,
                page=page,
            )
            if key in found:
                found[key].evidence.append(evidence)
                return
            fact = LegalFact(
                id=f"fact-{fact_type.value}-{len(order) + 1}",
                type=fact_type,
                value=value,
                confidence=PATTERN_CONFIDENCE,
                evidence=[evidence],
                extraction_method=ExtractionMethod.PATTERN,
                status=FactStatus.VERIFIED,
            )
            found[key] = fact
            order.append(key)

        self._extract_qualifications(text, emit)
        self._extract_dates(text, emit)
        self._extract_defendant(text, emit)
        self._extract_mitigating(text, emit)
        self._extract_aggravating(text, emit)
        self._extract_procedural(text, emit)
        self._extract_exceptional_circumstances(text, emit)
        self._extract_sentence(document, emit)
        self._extract_final_term(text, emit)
        return [found[key] for key in order]

    _VERDICT_NARRATIVE_MARKER = re.compile(r"У\s*С\s*Т\s*А\s*Н\s*О\s*В\s*И\s*Л", re.IGNORECASE)

    _QUAL_PART_FIRST = re.compile(
        rf"{_CH}\s*(\d)\s*{_ST}\s*(\d{{1,3}}(?:\.\d+)?)\s*(?:УК|Уголовного)", re.IGNORECASE
    )
    _QUAL_ARTICLE_FIRST = re.compile(
        rf"{_ST}\s*(\d{{1,3}}(?:\.\d+)?)\s*{_CH}\s*(\d)\s*(?:УК|Уголовного)", re.IGNORECASE
    )

    _NEGATION_BEFORE = re.compile(
        r"(?:\b(?:не|без|нет|отсутств\w+)\s+"
        r"|\bне\s+(?:усматрива\w+|усмотр\w+|установ\w+|нашл\w+|нашёл|нашел"
        r"|имеет\w*|заявл\w+)\s+)$",
        re.IGNORECASE,
    )
    _NEGATION_AFTER = re.compile(
        r"^\s*(?:\w+\s+){0,3}?(?:не\s+(?:усмотр\w+|установ\w+|имеет\w*|заявл\w+|было)|отсутств\w+|нет\b)",
        re.IGNORECASE,
    )
    _CLAUSE_BREAK = re.compile(r"[,;:.()\n]")

    def _negated_before(self, text: str, start: int) -> bool:
        chunk = text[max(0, start - 40):start]
        breaks = list(self._CLAUSE_BREAK.finditer(chunk))
        clause = chunk[breaks[-1].end():] if breaks else chunk
        return bool(self._NEGATION_BEFORE.search(clause))

    def _negated_after(self, text: str, end: int) -> bool:
        chunk = text[end:end + 60]
        cut = self._CLAUSE_BREAK.search(chunk)
        return bool(self._NEGATION_AFTER.match(chunk[:cut.start()] if cut else chunk))

    def _narrative_start(self, text: str) -> int:
        marker = self._VERDICT_NARRATIVE_MARKER.search(text)
        return marker.end() if marker else 0

    _ACCUSED = re.compile(r"обвиняем\w*", re.IGNORECASE)

    def _extract_qualifications(self, text: str, emit: Callable[..., None]) -> None:
        search_from = self._narrative_start(text)
        resolutive = self._RESOLUTIVE_MARKER.search(text, search_from)
        split = resolutive.end() if resolutive else len(text)
        found = self._emit_qualifications(text, split, len(text), emit)
        if self._emit_qualifications(text, search_from, split, emit) or found:
            return
        accused = list(self._ACCUSED.finditer(text, 0, search_from))
        if accused:
            self._emit_qualifications(text, accused[-1].start(), search_from, emit)

    def _emit_qualifications(
        self, text: str, pos: int, endpos: int, emit: Callable[..., None]
    ) -> bool:
        found = False
        for match in self._QUAL_PART_FIRST.finditer(text, pos, endpos):
            parsed = _article_number(match.group(2))
            if parsed is None:
                continue
            article, suffix = parsed
            emit(
                FactType.QUALIFICATION,
                {
                    "code": "УК РФ",
                    "article": article,
                    "article_suffix": suffix,
                    "part": int(match.group(1)),
                },
                match.start(),
                match.end(),
            )
            found = True
        for match in self._QUAL_ARTICLE_FIRST.finditer(text, pos, endpos):
            parsed = _article_number(match.group(1))
            if parsed is None:
                continue
            article, suffix = parsed
            emit(
                FactType.QUALIFICATION,
                {
                    "code": "УК РФ",
                    "article": article,
                    "article_suffix": suffix,
                    "part": int(match.group(2)),
                },
                match.start(),
                match.end(),
            )
            found = True
        return found

    _DATE = re.compile(
        r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\s+(?:года|г\.)",
        re.IGNORECASE,
    )

    _DOCUMENT_DATE = re.compile(r"\bот\s*$")
    _OFFENSE_ANCHORS = ("соверш", "преступлен", "при следующих обстоятельствах")

    def _extract_dates(self, text: str, emit: Callable[..., None]) -> None:
        search_from = self._narrative_start(text)
        if self._emit_offense_date(text, search_from, len(text), search_from, emit):
            return
        accused = list(self._ACCUSED.finditer(text, 0, search_from))
        if accused:
            self._emit_offense_date(text, accused[-1].start(), search_from, accused[-1].start(), emit)

    def _emit_offense_date(
        self, text: str, pos: int, endpos: int, floor: int, emit: Callable[..., None]
    ) -> bool:
        for match in self._DATE.finditer(text, pos, endpos):
            window_start = max(floor, match.start() - 160)
            context = text[window_start:match.start()].lower()
            if not any(anchor in context for anchor in self._OFFENSE_ANCHORS):
                continue
            if self._DOCUMENT_DATE.search(context):
                continue
            day, month, year = int(match.group(1)), _MONTHS[match.group(2).lower()], int(match.group(3))
            try:
                value = f"{year:04d}-{month:02d}-{day:02d}"
            except ValueError:
                continue
            emit(FactType.DATE_OF_OFFENSE, value, match.start(), match.end())
            return True
        return False

    _AGE = re.compile(r"возраст\w*[^0-9]{0,20}(\d{2})\s*(?:лет|год)", re.IGNORECASE)
    _MINOR_DEPENDENTS = re.compile(
        r"(?:несовершеннолетн\w{0,4}\s+(?:реб[её]н[её]?о?к?\w*|дет\w+|сын\w*|доч\w*))"
        r"|(?:малолет\w+\s+(?:реб[её]н[её]?о?к?\w*|дет\w+|сын\w*|доч\w*))"
        r"|(?:имеет(?:ся)?[^.;]{0,60}?несовершеннолетн\w+)",
        re.IGNORECASE,
    )
    _HEALTH = re.compile(
        r"состояни\w+\s+здоровья|инвалид\w+|хроническ\w+\s+заболеван\w+"
        r"|заболеван\w+|ВИЧ-инфекци\w+|туберкулез\w*",
        re.IGNORECASE,
    )
    _PRIOR = re.compile(
        r"ранее\s+(?:дважды|трижды|четырежды|многократно)?\s*судим\w+"
        r"|имеет(?:ся)?\s+(?:непогашенн\w+|несняты\w+|неснят\w+)?\s*судимост\w+",
        re.IGNORECASE,
    )

    _OTHER_PERSON = re.compile(r"потерпев\w+|свидетел\w+|представител\w+", re.IGNORECASE)
    _SENTENCE_BREAK = re.compile(r"[.;!?](?:\s|$)|\n")

    def _about_other_person(self, text: str, pos: int) -> bool:
        """Есть ли в том же предложении до ``pos`` слово про постороннего."""
        before = text[max(0, pos - 200):pos]
        breaks = list(self._SENTENCE_BREAK.finditer(before))
        sentence = before[breaks[-1].end():] if breaks else before
        return bool(self._OTHER_PERSON.search(sentence))

    def _extract_defendant(self, text: str, emit: Callable[..., None]) -> None:
        marker = self._VERDICT_NARRATIVE_MARKER.search(text)
        preamble_end = marker.start() if marker else len(text)
        for age in self._AGE.finditer(text, 0, preamble_end):
            if self._about_other_person(text, age.start()):
                continue
            emit(FactType.DEFENDANT_AGE, int(age.group(1)), age.start(), age.end())
            break
        for match in self._MINOR_DEPENDENTS.finditer(text):
            if self._about_other_person(text, match.start()):
                continue
            emit(FactType.MINOR_DEPENDENTS, True, match.start(), match.end())
        for match in self._HEALTH.finditer(text):
            if self._about_other_person(text, match.start()):
                continue
            emit(FactType.HEALTH_FACTOR, match.group(0).strip(), match.start(), match.end())
        for match in self._PRIOR.finditer(text):
            emit(FactType.PRIOR_CONVICTIONS, True, match.start(), match.end())

    _GUILTY_PLEA = re.compile(
        r"вину\s+(?:\w+\s+){0,8}?(?:призна(?:вал|л)\w*|призна[её]т)"
        r"|призна(?:вал|л)\w*\s+(?:свою\s+)?вину"
        r"|призна(?:вал|л)\w*\s+себя\s+виновн\w+"
        r"|признани[ея]\s+(?:(?:им|ею|подсудим\w+|своей)\s+){0,3}вины",
        re.IGNORECASE,
    )
    _NEGATION_INSIDE = re.compile(r"(?<!\w)не(?!\w)", re.IGNORECASE)
    _PARTIAL_AFTER = re.compile(r"^\s*(?:\w+\s+){0,2}?частичн", re.IGNORECASE)
    _PARTIAL_BEFORE = re.compile(r"частичн\w*\s+(?:\w+\s+)?$", re.IGNORECASE)
    _SURRENDER = re.compile(
        r"явк\w+\s+с\s+повинн\w+|активн\w+\s+способствов\w+\s+(?:раскрытию|расследованию)",
        re.IGNORECASE,
    )
    _RESTITUTION = re.compile(
        r"(?:добровольн\w+\s+)?возмест\w+\s+(?:причин[её]нн\w+\s+)?(?:ущерб|вред|материальн\w+\s+ущерб)"
        r"|заглад\w+\s+(?:причиненн\w+\s+)?(?:вред|ущерб)",
        re.IGNORECASE,
    )

    def _extract_mitigating(self, text: str, emit: Callable[..., None]) -> None:
        search_from = self._narrative_start(text)
        for match in self._GUILTY_PLEA.finditer(text, search_from):
            if not (
                self._negated_before(text, match.start())
                or self._NEGATION_INSIDE.search(match.group(0))
                or self._PARTIAL_AFTER.match(text[match.end():match.end() + 40])
                or self._PARTIAL_BEFORE.search(text[max(0, match.start() - 30):match.start()])
            ):
                emit(FactType.GUILTY_PLEA, True, match.start(), match.end())
        for match in self._SURRENDER.finditer(text, search_from):
            if not (
                self._negated_before(text, match.start())
                or self._negated_after(text, match.end())
            ):
                emit(FactType.SURRENDER_OR_CONFESSION, True, match.start(), match.end())
        for match in self._RESTITUTION.finditer(text, search_from):
            if not self._negated_before(text, match.start()):
                emit(FactType.RESTITUTION, True, match.start(), match.end())

    _RECIDIVISM = re.compile(r"рецидив\w*", re.IGNORECASE)

    def _extract_aggravating(self, text: str, emit: Callable[..., None]) -> None:
        for match in self._RECIDIVISM.finditer(text):
            if self._negated_before(text, match.start()) or self._negated_after(text, match.end()):
                continue
            emit(FactType.AGGRAVATING_RECIDIVISM, True, match.start(), match.end())

    _SPECIAL_PROCEDURE = re.compile(
        rf"особ\w+\s+поряд\w+|{_ST}\s*316\s*(?:УПК|Уголовно-процессуального)",
        re.IGNORECASE,
    )
    _JURY = re.compile(r"суд\w*\s+присяжн\w+|с участием присяжных", re.IGNORECASE)
    _ATTEMPT = re.compile(r"покушени[ея]\s+на", re.IGNORECASE)
    _PREPARATION = re.compile(r"приготовлени[ея]\s+к", re.IGNORECASE)

    _GROUP = re.compile(
        r"организованн\w+\s+групп\w+"
        r"|групп\w+\s+лиц"
        r"|по\s+предварительному\s+сговору",
        re.IGNORECASE,
    )

    _CASE_SUBJECT = re.compile(r"в\s+отношении", re.IGNORECASE)

    def _extract_procedural(self, text: str, emit: Callable[..., None]) -> None:
        search_from = self._narrative_start(text)
        subject = self._CASE_SUBJECT.search(text, 0, search_from) if search_from else None
        spans = [(search_from, len(text))]
        if subject:
            spans.insert(0, (0, subject.start()))
        for pos, endpos in spans:
            for match in self._SPECIAL_PROCEDURE.finditer(text, pos, endpos):
                if not self._negated_before(text, match.start()):
                    emit(FactType.SPECIAL_PROCEDURE, True, match.start(), match.end())
            for match in self._JURY.finditer(text, pos, endpos):
                if not self._negated_before(text, match.start()):
                    emit(FactType.JURY_TRIAL, True, match.start(), match.end())
        for match in self._GROUP.finditer(text, search_from):
            if self._negated_before(text, match.start()):
                continue
            lowered = match.group(0).lower()
            role = (
                "organized_group"
                if "организованн" in lowered
                else "group_with_conspiracy"
                if "сговор" in lowered
                else "group_of_persons"
            )
            emit(FactType.GROUP_OFFENSE, role, match.start(), match.end())
        for match in self._ATTEMPT.finditer(text, search_from):
            emit(FactType.OFFENSE_STAGE, OffenseStage.ATTEMPT.value, match.start(), match.end())
            return
        for match in self._PREPARATION.finditer(text, search_from):
            emit(
                FactType.OFFENSE_STAGE,
                OffenseStage.PREPARATION.value,
                match.start(),
                match.end(),
            )
            return

    _ARTICLE_64 = re.compile(rf"{_ST}\s*64\s*(?:УК|Уголовного)", re.IGNORECASE)

    def _extract_exceptional_circumstances(
        self, text: str, emit: Callable[..., None]
    ) -> None:
        search_from = self._narrative_start(text)
        for match in self._ARTICLE_64.finditer(text, search_from):
            emit(FactType.EXCEPTIONAL_CIRCUMSTANCES, True, match.start(), match.end())

    _PUNISHMENT_PATTERNS: tuple[tuple[PunishmentType, re.Pattern[str]], ...] = (
        (PunishmentType.IMPRISONMENT, re.compile(r"лишени[ея]\s+свободы", re.IGNORECASE)),
        (
            PunishmentType.CORRECTIONAL_LABOR,
            re.compile(r"исправительны[ех]\s+работ\w*", re.IGNORECASE),
        ),
        (
            PunishmentType.COMPULSORY_LABOR,
            re.compile(r"обязательны[ех]\s+работ\w*", re.IGNORECASE),
        ),
    )
    _SUSPENDED_ART73 = re.compile(rf"{_ST}\s*73\s*(?:УК|Уголовного)", re.IGNORECASE)
    _SUSPENDED_WORD = re.compile(r"условн\w+", re.IGNORECASE)
    _SENTENCING = re.compile(r"назнач\w+", re.IGNORECASE)
    _FINAL_PUNISHMENT = re.compile(
        r"окончательн\w+(?:\s+\w+){0,3}?\s+(?:наказани\w+|срок\w*)", re.IGNORECASE
    )
    _TERMLESS_PUNISHMENTS: tuple[tuple[PunishmentType, re.Pattern[str]], ...] = (
        (
            PunishmentType.FINE,
            re.compile(r"наказани[ея]\s+в\s+виде\s+штраф\w*", re.IGNORECASE),
        ),
        (
            PunishmentType.RESTRICTION_OF_LIBERTY,
            re.compile(r"наказани[ея]\s+в\s+виде\s+ограничени\w+\s+свободы", re.IGNORECASE),
        ),
        (
            PunishmentType.OTHER,
            re.compile(
                r"наказани[ея]\s+в\s+виде\s+(?:арест\w*|принудительн\w+\s+работ\w*)",
                re.IGNORECASE,
            ),
        ),
    )
    _RESOLUTIVE_MARKER = re.compile(r"П\s*Р\s*И\s*Г\s*О\s*В\s*О\s*Р\s*И\s*Л", re.IGNORECASE)

    def _extract_final_term(self, text: str, emit: Callable[..., None]) -> None:
        resolutive = self._RESOLUTIVE_MARKER.search(text, self._narrative_start(text))
        if resolutive is None:
            return
        for match in self._FINAL_PUNISHMENT.finditer(text, resolutive.end()):
            window = text[match.start():match.start() + 250]
            term = _extract_term_months(window)
            if term is not None:
                months, rel_start, rel_end = term
                emit(
                    FactType.FINAL_PUNISHMENT_TERM,
                    months,
                    match.start() + rel_start,
                    match.start() + rel_end,
                )
                return

    def _extract_sentence(self, document: Document, emit: Callable[..., None]) -> None:
        text = document.text
        narrative_start = self._narrative_start(text)
        matches = [
            (m.start(), m.end(), punishment_type)
            for punishment_type, pattern in self._PUNISHMENT_PATTERNS
            for m in pattern.finditer(text, narrative_start)
        ]
        matches.sort(key=lambda item: item[0])
        resolutive = self._RESOLUTIVE_MARKER.search(text, narrative_start)
        after = [item for item in matches if resolutive and item[0] > resolutive.end()]
        if resolutive and not after:
            stated = [
                (m.start(), m.end(), kind)
                for kind, pattern in self._TERMLESS_PUNISHMENTS
                for m in pattern.finditer(text, resolutive.end())
            ]
            if stated:
                first_start, first_end, kind = min(stated, key=lambda item: item[0])
                emit(FactType.PUNISHMENT_TYPE, kind.value, first_start, first_end)
                return
        if not matches:
            return
        start, end, punishment_type = matches[0]
        if not after:
            for sentencing in reversed(list(self._SENTENCING.finditer(text, narrative_start))):
                after = [item for item in matches if item[0] > sentencing.start()]
                if after:
                    break
        if after:
            start, end, punishment_type = after[0]
        emit(FactType.PUNISHMENT_TYPE, punishment_type.value, start, end)
        window = text[start:start + 300]
        lead_start = max(0, start - 45)
        lead_term = _extract_term_months(text[lead_start:start])
        if lead_term is not None and start - lead_start - lead_term[2] <= 10:
            months, rel_start, rel_end = lead_term
            emit(
                FactType.PUNISHMENT_TERM,
                months,
                lead_start + rel_start,
                lead_start + rel_end,
            )
        else:
            term = _extract_term_months(window)
            if term is not None:
                months, rel_start, rel_end = term
                emit(
                    FactType.PUNISHMENT_TERM,
                    months,
                    start + rel_start,
                    start + rel_end,
                )
        art73 = self._SUSPENDED_ART73.search(window)
        if art73:
            abs_start = start + art73.start()
            emit(FactType.SUSPENDED_SENTENCE, True, abs_start, abs_start + len(art73.group(0)))
            return
        suspended = self._SUSPENDED_WORD.search(window)
        if suspended:
            abs_start = start + suspended.start()
            emit(
                FactType.SUSPENDED_SENTENCE,
                True,
                abs_start,
                abs_start + len(suspended.group(0)),
            )


def _article_number(raw: str) -> tuple[int, str | None] | None:
    """«158» -> (158, None); «264.1» -> (264, "1")."""
    number, _, suffix = raw.partition(".")
    try:
        return int(number), (suffix or None)
    except ValueError:
        return None


def _value_key(value: Any) -> str:
    if isinstance(value, dict):
        return "|".join(f"{k}={value[k]}" for k in sorted(value))
    return str(value)
