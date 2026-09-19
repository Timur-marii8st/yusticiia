from __future__ import annotations

from pydantic import BaseModel

from .provider import LLMResult, validate_payload


class MockLLMProvider:
    """Детерминированный провайдер для тестов и полностью офлайн-запуска.

    Ответы задаются заранее (``canned``: prompt_name → JSON-строка).
    Пустой/отсутствующий ответ трактуется как «модель ничего не нашла» —
    это честный дефолт: система продолжает работать на паттерновом
    экстракторе без выдуманных фактов.
    """

    name = "mock"

    def __init__(self, canned: dict[str, str] | None = None, model: str = "mock-1") -> None:
        self._canned = canned or {}
        self._model = model

    def set_canned(self, prompt_name: str, payload: str) -> None:
        self._canned[prompt_name] = payload

    def generate_structured(
        self,
        *,
        prompt_name: str,
        prompt_version: str,
        prompt_template: str,
        document_text: str,
        schema: type[BaseModel],
        max_attempts: int = 2,
    ) -> tuple[BaseModel | None, LLMResult]:
        result = LLMResult(
            provider=self.name,
            model=self._model,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )
        raw = self._canned.get(prompt_name)
        if raw is None or not raw.strip():
            return None, result
        try:
            return validate_payload(schema, raw), result
        except ValueError:
            return None, result
