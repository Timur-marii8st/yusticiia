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
        except (ValueError, Exception):
            return None, result


class FlakyMockLLMProvider(MockLLMProvider):
    """Мок, который первые ``fail_times`` попыток возвращает невалидный JSON.

    Используется в тестах деградированного вывода модели.
    """

    def __init__(self, canned: dict[str, str], fail_times: int = 1) -> None:
        super().__init__(canned)
        self._fail_left = fail_times

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
        if self._fail_left > 0:
            self._fail_left -= 1
            return None, LLMResult(
                provider=self.name,
                model=self._model,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
            )
        return super().generate_structured(
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            prompt_template=prompt_template,
            document_text=document_text,
            schema=schema,
            max_attempts=max_attempts,
        )
