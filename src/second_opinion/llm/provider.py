from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ValidationError


class LLMError(RuntimeError):
    pass


class LLMResult(BaseModel):
    """Метаданные LLM-вызова для аудит-журнала."""

    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    attempts: int = 1


class LLMProvider(Protocol):
    """Абстракция LLM (см. docs/ADR/ADR-004).

    Бизнес-логика зависит только от этого интерфейса. Контент документа
    передаётся как ДАННЫЕ, не как инструкции модели.
    """

    name: str

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
        """Вернуть структурированный вывод, валидированный схемой.

        Невалидный JSON/схема — повторная попытка; затем ``(None, ...)``.
        Никогда не «догадываться» вместо данных.
        """
        ...


def validate_payload(
    schema: type[BaseModel], raw: str
) -> BaseModel:
    """Распарсить и провалидировать JSON-ответ модели по схеме."""
    import json

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
    payload = json.loads(text)
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"ответ модели не соответствует схеме: {exc}") from exc
