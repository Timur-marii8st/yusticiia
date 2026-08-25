from __future__ import annotations

from ..audit.trail import AuditTrail
from ..domain.documents import Document
from ..domain.enums import ExtractionMethod, FactStatus
from ..domain.evidence import Evidence
from ..domain.facts import LegalFact
from ..llm.provider import LLMProvider
from ..prompts import load_prompt
from .pattern_extractor import _value_key
from .schemas import LLMExtractionResult

MAX_DOCUMENT_CHARS = 20000


class LLMFactExtractor:
    """Извлечение фактов через LLM с валидацией и привязкой цитат.

    Цитата, которую модель вернула, обязана реально присутствовать в тексте
    документа; иначе факт отбрасывается (не выдумывать). Факты из этого
    экстрактора получают статус LIKELY до подтверждения человеком.
    """

    def __init__(self, provider: LLMProvider, audit: AuditTrail | None = None) -> None:
        self._provider = provider
        self._audit = audit

    def extract(
        self, document: Document, known_keys: set[tuple[str, str]]
    ) -> list[LegalFact]:
        prompt = load_prompt("fact_extraction", "v1")
        payload, meta = self._provider.generate_structured(
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_template=prompt.text,
            document_text=document.text[:MAX_DOCUMENT_CHARS],
            schema=LLMExtractionResult,
        )
        if self._audit is not None:
            self._audit.log(
                operation="llm_fact_extraction",
                component="fact_extraction.llm",
                input_hash=document.sha256,
                model=meta.model,
                prompt_version=prompt.version,
                details={"attempts": meta.attempts, "found": payload is not None},
            )
        if payload is None:
            return []

        facts: list[LegalFact] = []
        text = document.text
        counter = 0
        for item in payload.facts:
            start = text.find(item.quote)
            if start < 0:
                # Цитата не найдена в документе — факт не принимается.
                continue
            key = (item.type.value, _value_key(item.value))
            if key in known_keys:
                continue
            known_keys.add(key)
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
                            quote=item.quote,
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
