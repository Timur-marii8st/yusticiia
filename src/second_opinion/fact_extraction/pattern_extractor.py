from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..domain.documents import Document
from ..domain.enums import FactStatus, FactType, OffenseStage, PunishmentType
from ..domain.evidence import Evidence
from ..domain.facts import LegalFact

PATTERN_CONFIDENCE = 0.95

_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}


def _months_from_tokens(tokens: list[tuple[int, str]]) -> float | None:
    total = 0.0
    for value, unit in tokens:
        if unit.startswith("мес"):
            total += value
        else:
            total += value * 12
    return total if total > 0 else None


_TERM_TOKEN = re.compile(
    r"(\d+)\s+(лет|год(?:а|ов)?|мес(?:яцев|яца|ец)?\w*)", re.IGNORECASE
)


def _extract_term_months(window: str) -> tuple[float, int, int] | None:
    """Найти срок в месяцах внутри окна текста; вернуть (месяцы, старт, конец)."""
    tokens: list[tuple[int, str]] = []
    first_start: int | None = None
    last_end = -1
    for match in _TERM_TOKEN.finditer(window):
        if last_end != -1 and match.start() - last_end > 10:
            break
        if first_start is None:
            first_start = match.start()
        tokens.append((int(match.group(1)), match.group(2).lower()))
        last_end = match.end()
    months = _months_from_tokens(tokens) if tokens else None
    if not months or not tokens or first_start is None:
        return None
    return months, first_start, last_end


class PatternFactExtractor:
    """Детерминированное извлечение фактов по паттернам.

    Каждый факт получает evidence с точными оффсетами совпадения; статус
    детерминированного извлечения — VERIFIED (см. docs/DOMAIN_MODEL.md).
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
                extraction_method="pattern",
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
        self._extract_sentence(document, emit)
        return [found[key] for key in order]

    # -- квалификации ------------------------------------------------------

    _QUAL_PART_FIRST = re.compile(
        r"ч\.?\s*(\d)\s*ст\.?\s*(\d{1,3}(?:\.\d+)?)\s*(?:УК|Уголовного)", re.IGNORECASE
    )
    _QUAL_ARTICLE_FIRST = re.compile(
        r"ст\.?\s*(\d{1,3}(?:\.\d+)?)\s*ч\.?\s*(\d)\s*(?:УК|Уголовного)", re.IGNORECASE
    )

    def _extract_qualifications(self, text: str, emit: Callable[..., None]) -> None:
        for match in self._QUAL_PART_FIRST.finditer(text):
            article = _article_number(match.group(2))
            if article is None:
                continue
            emit(
                FactType.QUALIFICATION,
                {"code": "УК РФ", "article": article, "part": int(match.group(1))},
                match.start(),
                match.end(),
            )
        for match in self._QUAL_ARTICLE_FIRST.finditer(text):
            article = _article_number(match.group(1))
            if article is None:
                continue
            emit(
                FactType.QUALIFICATION,
                {"code": "УК РФ", "article": article, "part": int(match.group(2))},
                match.start(),
                match.end(),
            )

    # -- дата совершения ---------------------------------------------------

    _DATE = re.compile(
        r"(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\s+(?:года|г\.)",
        re.IGNORECASE,
    )

    def _extract_dates(self, text: str, emit: Callable[..., None]) -> None:
        for match in self._DATE.finditer(text):
            window_start = max(0, match.start() - 160)
            context = text[window_start:match.start()].lower()
            if "соверш" not in context and "преступлен" not in context:
                continue
            day, month, year = int(match.group(1)), _MONTHS[match.group(2).lower()], int(match.group(3))
            try:
                value = f"{year:04d}-{month:02d}-{day:02d}"
            except ValueError:
                continue
            emit(FactType.DATE_OF_OFFENSE, value, match.start(), match.end())
            return  # первая дата совершения

    # -- данные о подсудимом ----------------------------------------------

    _AGE = re.compile(r"возраст\w*[^0-9]{0,20}(\d{2})\s*(?:лет|год)", re.IGNORECASE)
    _MINOR_DEPENDENTS = re.compile(
        r"(?:несовершеннолетн\w{0,4}\s+(?:ребен[её]?о?к?\w*|дет\w+|сын\w*|доч\w*))"
        r"|(?:малолет\w+\s+(?:ребен[её]?о?к?\w*|дет\w+|сын\w*|доч\w*))"
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

    def _extract_defendant(self, text: str, emit: Callable[..., None]) -> None:
        age = self._AGE.search(text)
        if age:
            emit(FactType.DEFENDANT_AGE, int(age.group(1)), age.start(), age.end())
        for match in self._MINOR_DEPENDENTS.finditer(text):
            emit(FactType.MINOR_DEPENDENTS, True, match.start(), match.end())
        for match in self._HEALTH.finditer(text):
            emit(FactType.HEALTH_FACTOR, match.group(0).strip(), match.start(), match.end())
        for match in self._PRIOR.finditer(text):
            emit(FactType.PRIOR_CONVICTIONS, True, match.start(), match.end())

    # -- смягчающие ---------------------------------------------------------

    _GUILTY_PLEA = re.compile(
        r"вину\s+(?:призна[еа]л\w*|признает|признал\w*)"
        r"|призна[еа]л\w+\s+(?:свою\s+)?вину"
        r"|признани[ея]\s+(?:им\s+|ею\s+)?вины",
        re.IGNORECASE,
    )
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
        for match in self._GUILTY_PLEA.finditer(text):
            emit(FactType.GUILTY_PLEA, True, match.start(), match.end())
        for match in self._SURRENDER.finditer(text):
            emit(FactType.SURRENDER_OR_CONFESSION, True, match.start(), match.end())
        for match in self._RESTITUTION.finditer(text):
            emit(FactType.RESTITUTION, True, match.start(), match.end())

    # -- отягчающие ---------------------------------------------------------

    _RECIDIVISM = re.compile(r"рецидив\w*", re.IGNORECASE)

    def _extract_aggravating(self, text: str, emit: Callable[..., None]) -> None:
        for match in self._RECIDIVISM.finditer(text):
            emit(FactType.AGGRAVATING_RECIDIVISM, True, match.start(), match.end())

    # -- процедурные ---------------------------------------------------------

    _SPECIAL_PROCEDURE = re.compile(r"особ\w+\s+поряд\w+", re.IGNORECASE)
    _JURY = re.compile(r"суд\w*\s+присяжн\w+|с участием присяжных", re.IGNORECASE)
    _ATTEMPT = re.compile(r"покушени[ея]\s+на", re.IGNORECASE)
    _PREPARATION = re.compile(r"приготовлени[ея]\s+к", re.IGNORECASE)

    # Групповой характер деяния: «группой лиц», «по предварительному
    # сговору», «организованной группой» (ст. 35 УК РФ).
    _GROUP = re.compile(
        r"организованн\w+\s+групп\w+"
        r"|групп\w+\s+лиц"
        r"|по\s+предварительному\s+сговору",
        re.IGNORECASE,
    )

    def _extract_procedural(self, text: str, emit: Callable[..., None]) -> None:
        for match in self._SPECIAL_PROCEDURE.finditer(text):
            emit(FactType.SPECIAL_PROCEDURE, True, match.start(), match.end())
        for match in self._JURY.finditer(text):
            emit(FactType.JURY_TRIAL, True, match.start(), match.end())
        for match in self._GROUP.finditer(text):
            lowered = match.group(0).lower()
            role = (
                "organized_group"
                if "организованн" in lowered
                else "group_with_conspiracy"
                if "сговор" in lowered
                else "group_of_persons"
            )
            emit(FactType.GROUP_OFFENSE, role, match.start(), match.end())
        for match in self._ATTEMPT.finditer(text):
            emit(FactType.OFFENSE_STAGE, OffenseStage.ATTEMPT.value, match.start(), match.end())
            return
        for match in self._PREPARATION.finditer(text):
            emit(
                FactType.OFFENSE_STAGE,
                OffenseStage.PREPARATION.value,
                match.start(),
                match.end(),
            )
            return

    # -- наказание -----------------------------------------------------------

    _IMPRISONMENT = re.compile(r"лишени[ея]\s+свободы", re.IGNORECASE)
    _SUSPENDED_ART73 = re.compile(r"ст\.?\s*73\s*(?:УК|Уголовного)", re.IGNORECASE)
    _SUSPENDED_WORD = re.compile(r"условн\w+", re.IGNORECASE)
    _SENTENCING = re.compile(r"назнач\w+", re.IGNORECASE)

    def _extract_sentence(self, document: Document, emit: Callable[..., None]) -> None:
        text = document.text
        imprison_matches = list(self._IMPRISONMENT.finditer(text))
        if not imprison_matches:
            return
        # Резолютивная часть: предпочитаем первое «лишение свободы» после
        # последнего вхождения «назнач…» (иначе срок санкции из описательной
        # части мог бы быть принят за назначенное наказание).
        target = imprison_matches[0]
        sentencing = list(self._SENTENCING.finditer(text))
        if sentencing:
            after = [m for m in imprison_matches if m.start() > sentencing[-1].start()]
            if after:
                target = after[0]
        emit(
            FactType.PUNISHMENT_TYPE,
            PunishmentType.IMPRISONMENT.value,
            target.start(),
            target.end(),
        )
        window = text[target.start():target.start() + 300]
        term = _extract_term_months(window)
        if term is not None:
            months, rel_start, rel_end = term
            emit(
                FactType.PUNISHMENT_TERM,
                months,
                target.start() + rel_start,
                target.start() + rel_end,
            )
        for match in self._SUSPENDED_ART73.finditer(text):
            emit(FactType.SUSPENDED_SENTENCE, True, match.start(), match.end())
            return
        suspended = self._SUSPENDED_WORD.search(window)
        if suspended:
            abs_start = target.start() + suspended.start()
            emit(
                FactType.SUSPENDED_SENTENCE,
                True,
                abs_start,
                abs_start + len(suspended.group(0)),
            )


def _article_number(raw: str) -> int | None:
    """«158» → 158; составы вида «158.1» в УК не используются."""
    if "." in raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _value_key(value: Any) -> str:
    if isinstance(value, dict):
        return "|".join(f"{k}={value[k]}" for k in sorted(value))
    return str(value)
