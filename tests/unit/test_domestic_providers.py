"""Отечественные поставщики моделей: Yandex AI Studio и GigaChat.

НТ-02 требует приоритета отечественных решений, критерий технической
реализации конкурса прямо называет алгоритмы машинного обучения. Оба
поставщика подключаются за тем же интерфейсом ``LLMProvider``, поэтому
юридические выводы по-прежнему делает детерминированный движок.

Сеть здесь не трогается: транспорт подменяется, проверяется то, что
уходило бы наружу.
"""

from __future__ import annotations

import json

import httpx
import pytest

from second_opinion.fact_extraction.schemas import LLMExtractionResult
from second_opinion.llm import gigachat as gigachat_module
from second_opinion.llm import openai_compatible
from second_opinion.llm.gigachat import (
    GigaChatAuthError,
    GigaChatProvider,
    GigaChatTokenSource,
)
from second_opinion.llm.openai_compatible import OpenAICompatibleProvider

ANSWER = json.dumps(
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
    def __init__(self, payload: dict | str, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise httpx.HTTPStatusError("ошибка", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        if isinstance(self._payload, str):
            return {"choices": [{"message": {"content": self._payload}}]}
        return self._payload


def _extract(provider, **kwargs):
    return provider.generate_structured(
        prompt_name="fact_extraction",
        prompt_version="v1",
        prompt_template="Извлеки обстоятельства.",
        document_text="Смирнов А.В., возраст 34 года.",
        schema=LLMExtractionResult,
        **kwargs,
    )



def test_yandex_auth_scheme_is_api_key(monkeypatch) -> None:
    """Yandex AI Studio ждёт «Api-Key», а не «Bearer».

    Схема вынесена в настройку именно поэтому: с зашитым «Bearer»
    отечественный контур отвечал бы отказом авторизации, и выглядело бы
    это как неработающая модель.
    """
    sent: list[dict] = []
    monkeypatch.setattr(
        openai_compatible.httpx,
        "post",
        lambda url, **kw: (sent.append(kw["headers"]), _Response(ANSWER))[1],
    )
    provider = OpenAICompatibleProvider(
        "https://llm.api.cloud.yandex.net/v1",
        "test-key",
        "gpt://folder/yandexgpt/latest",
        auth_scheme="Api-Key",
    )
    payload, _ = _extract(provider)

    assert payload is not None
    assert sent[0]["Authorization"] == "Api-Key test-key"


def test_bearer_stays_the_default(monkeypatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr(
        openai_compatible.httpx,
        "post",
        lambda url, **kw: (sent.append(kw["headers"]), _Response(ANSWER))[1],
    )
    _extract(OpenAICompatibleProvider("https://llm.invalid/v1", "k", "m"))

    assert sent[0]["Authorization"] == "Bearer k"



def test_authorization_key_is_exchanged_for_a_token(monkeypatch) -> None:
    """Ключ авторизации наружу не уходит — уходит временный токен."""
    calls: list[tuple[str, dict]] = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        if "oauth" in url:
            return _Response({"access_token": "tok-1", "expires_at": 9_999_999_999_000})
        return _Response(ANSWER)

    monkeypatch.setattr(gigachat_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post)

    payload, result = _extract(GigaChatProvider("base64-ключ"))

    assert payload is not None
    assert result.provider == "gigachat"

    oauth_url, oauth_kw = calls[0]
    assert "oauth" in oauth_url
    assert oauth_kw["headers"]["Authorization"] == "Basic base64-ключ"
    assert oauth_kw["headers"]["RqUID"], "идентификатор запроса обязателен"
    assert oauth_kw["data"] == {"scope": "GIGACHAT_API_PERS"}

    _chat_url, chat_kw = calls[1]
    assert chat_kw["headers"]["Authorization"] == "Bearer tok-1"
    assert "base64-ключ" not in json.dumps(chat_kw, default=str), (
        "ключ авторизации попал в запрос к модели"
    )


def test_token_is_reused_until_it_nears_expiry(monkeypatch) -> None:
    """Второй разбор не ходит за токеном заново: он живёт тридцать минут."""
    oauth_calls = 0

    def fake_post(url, **kw):
        nonlocal oauth_calls
        if "oauth" in url:
            oauth_calls += 1
            return _Response({"access_token": "tok", "expires_at": 9_999_999_999_000})
        return _Response(ANSWER)

    monkeypatch.setattr(gigachat_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post)

    provider = GigaChatProvider("ключ")
    _extract(provider)
    _extract(provider)

    assert oauth_calls == 1


def test_expired_token_is_refreshed(monkeypatch) -> None:
    """Истекающий токен обновляется заранее, а не в полёте запроса."""
    issued: list[str] = []

    def fake_post(url, **kw):
        if "oauth" in url:
            issued.append(f"tok-{len(issued) + 1}")
            return _Response({"access_token": issued[-1], "expires_at": 1_000})
        return _Response(ANSWER)

    monkeypatch.setattr(gigachat_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_compatible.httpx, "post", fake_post)

    provider = GigaChatProvider("ключ")
    _extract(provider)
    _extract(provider)

    assert issued == ["tok-1", "tok-2"]


def test_auth_failure_does_not_break_the_analysis(monkeypatch) -> None:
    """Недоступная авторизация не роняет разбор: он идёт на правилах.

    Система задумана так, что модель — дополнение, а не условие работы.
    Отказ GigaChat обязан выглядеть как «модель ничего не добавила», а не
    как прерванный анализ документа.
    """

    def fake_post(url, **kw):
        if "oauth" in url:
            raise httpx.ConnectError("нет связи")
        raise AssertionError("запрос к модели без токена недопустим")

    monkeypatch.setattr(gigachat_module.httpx, "post", fake_post)

    payload, result = _extract(GigaChatProvider("ключ"))

    assert payload is None
    assert result.provider == "gigachat"


def test_missing_key_is_named_plainly() -> None:
    with pytest.raises(ValueError, match="SO_GIGACHAT_AUTH_KEY"):
        GigaChatTokenSource("")


def test_service_without_token_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(
        gigachat_module.httpx, "post", lambda url, **kw: _Response({"нет": "токена"})
    )
    with pytest.raises(GigaChatAuthError, match="access_token"):
        GigaChatTokenSource("ключ").token()
