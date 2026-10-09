from __future__ import annotations

import re

from ..audit.trail import AuditTrail
from ..domain.documents import Document
from ..domain.enums import ExtractionMethod, FactStatus, FactType
from ..domain.evidence import Evidence
from ..domain.facts import LegalFact
from ..fact_values import BOOLEAN_FACT_TYPES, FactValidationError, validate_fact_value
from ..llm.provider import LLMProvider
from ..privacy import MaskStyle, mask_text
from ..prompts import load_prompt
from .pattern_extractor import _value_key
from .schemas import LLMExtractionResult

MAX_DOCUMENT_CHARS = 20000
RESOLUTIVE_TAIL_CHARS = 8000
OMISSION_MARK = "\n[... часть текста пропущена ...]\n"
PROMPT_VERSION = "v3"

_STAGE_ANCHORS = {
    "attempt": re.compile(r"покуш", re.IGNORECASE),
    "preparation": re.compile(r"приготов", re.IGNORECASE),
}

_RESOLUTIVE = re.compile(r"П\s*Р\s*И\s*Г\s*О\s*В\s*О\s*Р\s*И\s*Л", re.IGNORECASE)


def fit_document(text: str, limit: int = MAX_DOCUMENT_CHARS) -> str:
    """Уложить документ в ``limit`` символов, сохранив резолютивную часть.

    Длинный приговор обрезался по первым ``limit`` символам, и резолютивная
    часть (статья, наказание, срок) оставалась за пределом. Теперь берутся
    начало и фрагмент от слова «ПРИГОВОРИЛ» (без маркера - конец текста);
    пропуск обозначен меткой, а цитата, захватывающая метку, в документе не
    найдётся и будет отброшена. Оба куска дословные подстроки исходного текста.
    """
    if len(text) <= limit:
        return text
    marker = _RESOLUTIVE.search(text)
    tail_start = marker.start() if marker else max(0, len(text) - RESOLUTIVE_TAIL_CHARS)
    tail = text[tail_start : tail_start + RESOLUTIVE_TAIL_CHARS]
    head_limit = limit - len(tail) - len(OMISSION_MARK)
    if head_limit >= tail_start:
        return text[:limit]
    return text[:head_limit] + OMISSION_MARK + tail

SCALAR_FACT_TYPES = frozenset(
    {
        FactType.DEFENDANT_AGE,
        FactType.PRIOR_CONVICTIONS,
        FactType.MINOR_DEPENDENTS,
        FactType.OFFENSE_STAGE,
        FactType.GUILTY_PLEA,
        FactType.SPECIAL_PROCEDURE,
        FactType.JURY_TRIAL,
        FactType.PUNISHMENT_TYPE,
        FactType.PUNISHMENT_TERM,
        FactType.FINAL_PUNISHMENT_TERM,
        FactType.PUNISHMENT_HOURS,
        FactType.FINE_AMOUNT,
        FactType.SUSPENDED_SENTENCE,
        FactType.DATE_OF_OFFENSE,
    }
)


_SCALAR_VALUES = {t.value for t in SCALAR_FACT_TYPES}


class LLMFactExtractor:
    """Извлечение фактов через LLM с валидацией и привязкой цитат.

    Цитата, которую модель вернула, обязана реально присутствовать в тексте
    документа; иначе факт отбрасывается (не выдумывать). Факты из этого
    экстрактора получают статус LIKELY до подтверждения человеком.
    """

    def __init__(
        self,
        provider: LLMProvider,
        audit: AuditTrail | None = None,
        mask_personal_data: bool = True,
    ) -> None:
        self._provider = provider
        self._audit = audit
        self._mask_personal_data = mask_personal_data

    def extract(
        self, document: Document, known_keys: set[tuple[str, str]]
    ) -> list[LegalFact]:
        prompt = load_prompt("fact_extraction", PROMPT_VERSION)
        outgoing = (
            mask_text(document.text, style=MaskStyle.PRESERVE_LENGTH)
            if self._mask_personal_data
            else document.text
        )
        payload, meta = self._provider.generate_structured(
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_template=prompt.text,
            document_text=fit_document(outgoing),
            schema=LLMExtractionResult,
        )
        if self._audit is not None:
            self._audit.log(
                operation="llm_fact_extraction",
                component="fact_extraction.llm",
                input_hash=document.sha256,
                model=meta.model,
                prompt_version=prompt.version,
                details={
                    "attempts": meta.attempts,
                    "found": payload is not None,
                    "personal_data_masked": self._mask_personal_data,
                },
            )
        if payload is None:
            return []
        if not isinstance(payload, LLMExtractionResult):
            return []

        facts: list[LegalFact] = []
        haystack = outgoing
        text = document.text
        counter = 0
        scalar_taken = {FactType(t) for t, _ in known_keys if t in _SCALAR_VALUES}
        for item in payload.facts:
            if item.type in SCALAR_FACT_TYPES and item.type in scalar_taken:
                continue
            if item.type in BOOLEAN_FACT_TYPES and item.value is not True:
                continue
            if item.type is FactType.OFFENSE_STAGE:
                anchor = _STAGE_ANCHORS.get(str(item.value))
                if anchor is None or not anchor.search(item.quote):
                    continue
            try:
                validate_fact_value(item.type, item.value)
            except FactValidationError:
                continue
            start = haystack.find(item.quote)
            if start < 0:
                continue
            quote = text[start : start + len(item.quote)]
            key = (item.type.value, _value_key(item.value))
            if key in known_keys:
                continue
            known_keys.add(key)
            if item.type in SCALAR_FACT_TYPES:
                scalar_taken.add(item.type)
            counter += 1
            facts.append(
                LegalFact(
                    id=f"fact-{item.type.value}-llm-{counter}",
                    type=item.type,
                    value=item.value,
                    confidence=item.confidence,
                    evidence=[
                        Evidence(
                            document_id=document.document_id,
                            quote=quote,
                            start_offset=start,
                            end_offset=start + len(item.quote),
                        )
                    ],
                    extraction_method=ExtractionMethod.LLM,
                    status=FactStatus.LIKELY,
                    prompt_version=meta.prompt_version,
                )
            )
        return facts
