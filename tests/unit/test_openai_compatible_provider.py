"""Адаптер внешнего LLM-контура: повтор попытки и обращение с текстом документа.

Модуль единственный в системе ходит наружу, и до сих пор он не был покрыт
ничем. Повторную попытку при негодном ответе обещает докстринга протокола
(`LLMProvider.generate_structured`), а моделировал её мок, который на деле
возвращал окончательное «ничего не нашлось» — то есть проверял не то.
Здесь повтор проверяется у того кода, который его выполняет.
"""

from __future__ import annotations

import json

import httpx
import pytest

from second_opinion.fact_extraction.schemas import LLMExtractionResult
from second_opinion.llm import openai_compatible
from second_opinion.llm.openai_compatible import OpenAICompatibleProvider

VALID = json.dumps(
    {
        "facts": [
            {
                "type": "defendant_age",
                "value": 34,
                "confidence": 0.9,
                "quote": "возраст 34 года",
            }
        ]
    }
)


class _Response:
    """Минимальный ответ httpx: провайдеру нужны только эти три вещи."""

    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider("http://llm.invalid/v1", "test-key", "model-1")


def _call(provider: OpenAICompatibleProvider, **kwargs):
    return provider.generate_structured(
        prompt_name="fact_extraction",
        prompt_version="v1",
        prompt_template="Извлеки обстоятельства.",
        document_text="Смирнов А.В., возраст 34 года.",
        schema=LLMExtractionResult,
        **kwargs,
    )


def test_key_is_required(monkeypatch) -> None:
    """Внешний контур не включается молча: без ключа адаптер не создаётся."""
    with pytest.raises(ValueError, match="SO_OPENAI_API_KEY"):
        OpenAICompatibleProvider("http://llm.invalid/v1", "", "model-1")


def test_invalid_answer_is_retried(monkeypatch) -> None:
    """Негодный ответ — ещё одна попытка, и число попыток попадает в журнал."""
    answers = ["{не json", VALID]
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        return _Response(answers[len(calls) - 1])

    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post)
    payload, result = _call(_provider())

    assert isinstance(payload, LLMExtractionResult)
    assert payload.facts[0].value == 34
    assert result.attempts == 2, "вторая попытка не отражена в метаданных вызова"
    assert len(calls) == 2


def test_exhausted_attempts_return_nothing_not_a_guess(monkeypatch) -> None:
    """Исчерпав попытки, адаптер отдаёт пустоту, а не придуманный разбор."""
    monkeypatch.setattr(
        openai_compatible.httpx, "post", lambda url, **kwargs: _Response("{не json")
    )
    payload, result = _call(_provider(), max_attempts=3)

    assert payload is None
    assert result.attempts == 3


def test_network_failure_is_survived(monkeypatch) -> None:
    """Недоступный контур не роняет разбор: система работает на паттернах."""

    def fail(url, **kwargs):
        raise httpx.ConnectError("нет связи")

    monkeypatch.setattr(openai_compatible.httpx, "post", fail)
    payload, _ = _call(_provider())

    assert payload is None


def test_document_goes_as_data_between_markers(monkeypatch) -> None:
    """Текст документа передаётся как данные, а не как указания модели.

    Ограждение метками — единственное, что отделяет содержимое документа от
    инструкций: без него фраза внутри приговора становится командой.
    """
    sent: list[dict] = []
    monkeypatch.setattr(
        openai_compatible.httpx,
        "post",
        lambda url, **kwargs: (sent.append(kwargs["json"]), _Response(VALID))[1],
    )
    _call(_provider())

    messages = sent[0]["messages"]
    system = next(m["content"] for m in messages if m["role"] == "system")
    user = next(m["content"] for m in messages if m["role"] == "user")

    assert "НЕДОВЕРЕННЫМИ ДАННЫМИ" in system
    start = user.index("=== НАЧАЛО НЕДОВЕРЕННОГО ТЕКСТА ДОКУМЕНТА ===")
    end = user.index("=== КОНЕЦ НЕДОВЕРЕННОГО ТЕКСТА ДОКУМЕНТА ===")
    assert start < user.index("Смирнов А.В.") < end
    assert sent[0]["temperature"] == 0, "разбор обязан быть воспроизводимым"
