"""GigaChat: OpenAI-совместимый контур с обменом ключа на временный токен.

Отличие от прочих OpenAI-совместимых поставщиков одно, но оно требует
отдельного кода: ключ авторизации нельзя послать в запрос напрямую. Его
меняют на access-токен, который живёт тридцать минут, и обновляют до
истечения. Всё остальное — тот же ``/chat/completions``, поэтому разбор
ответа, повтор попытки и ограждение текста документа берутся у общего
адаптера, а не пишутся заново.

Почему это важно для проекта: НТ-02 требует приоритета отечественных
решений, а критерий технической реализации прямо называет алгоритмы
машинного обучения. GigaChat закрывает оба, оставаясь за интерфейсом
``LLMProvider`` — юридические выводы по-прежнему делает детерминированный
движок, модель только извлекает обстоятельства, и каждую её находку
проверяет поиск цитаты в документе.
"""

from __future__ import annotations

import logging
import time
import uuid

import httpx
from pydantic import BaseModel

from .openai_compatible import OpenAICompatibleProvider
from .provider import LLMResult

logger = logging.getLogger(__name__)

#: Обмен ключа авторизации на access-токен.
OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

#: OpenAI-совместимый адрес GigaChat.
DEFAULT_BASE_URL = "https://api.giga.chat/v1"

#: Область действия токена. Для физических лиц — PERS, для юридических —
#: CORP или B2B в зависимости от договора; задаётся настройкой, потому что
#: угадать её за оператора нельзя.
DEFAULT_SCOPE = "GIGACHAT_API_PERS"

#: Токен живёт 30 минут. Обновляем заранее: запрос, начатый за секунду до
#: истечения, успел бы получить отказ уже в полёте.
REFRESH_MARGIN_SECONDS = 120


class GigaChatAuthError(RuntimeError):
    """Ключ авторизации не удалось обменять на токен."""


class GigaChatTokenSource:
    """Выдаёт действующий access-токен, обновляя его по сроку.

    Хранит токен в памяти процесса: на диск он не попадает, и перезапуск
    просто запрашивает новый.
    """

    def __init__(
        self,
        authorization_key: str,
        *,
        scope: str = DEFAULT_SCOPE,
        oauth_url: str = OAUTH_URL,
        timeout: float = 30.0,
        verify: bool | str = True,
    ) -> None:
        if not authorization_key:
            raise ValueError(
                "SO_GIGACHAT_AUTH_KEY не задан: это ключ авторизации из личного "
                "кабинета GigaChat (строка Base64), на него меняется временный токен"
            )
        self._authorization_key = authorization_key
        self._scope = scope
        self._oauth_url = oauth_url
        self._timeout = timeout
        self._verify = verify
        self._token = ""
        self._expires_at = 0.0

    def token(self) -> str:
        """Действующий токен, при необходимости обновлённый."""
        if self._token and time.time() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token
        return self._refresh()

    def _refresh(self) -> str:
        try:
            response = httpx.post(
                self._oauth_url,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    # Идентификатор запроса требуется спецификацией и
                    # обязан быть уникальным для каждого обращения.
                    "RqUID": str(uuid.uuid4()),
                    "Authorization": f"Basic {self._authorization_key}",
                },
                data={"scope": self._scope},
                timeout=self._timeout,
                verify=self._verify,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise GigaChatAuthError(
                f"не удалось получить токен GigaChat: {exc}. Проверьте ключ "
                f"авторизации и область {self._scope!r}; если контур требует "
                "сертификаты НУЦ Минцифры, укажите их через SO_GIGACHAT_CA_BUNDLE"
            ) from exc
        except ValueError as exc:
            raise GigaChatAuthError(
                f"ответ сервиса авторизации GigaChat не разобран: {exc}"
            ) from exc

        token = payload.get("access_token", "")
        if not token:
            raise GigaChatAuthError(
                "сервис авторизации GigaChat не вернул access_token"
            )
        # `expires_at` приходит в миллисекундах эпохи.
        expires_at = payload.get("expires_at")
        self._expires_at = (
            float(expires_at) / 1000.0 if expires_at else time.time() + 30 * 60
        )
        self._token = token
        logger.info("получен токен GigaChat, действует до %.0f", self._expires_at)
        return token


class GigaChatProvider(OpenAICompatibleProvider):
    """GigaChat за тем же интерфейсом, что и прочие поставщики.

    Наследование здесь уместно: отличается ровно одно — откуда берётся
    значение заголовка авторизации. Сборка запроса, ограждение текста
    документа метками, повтор попытки и разбор ответа общие.
    """

    name = "gigachat"

    def __init__(
        self,
        authorization_key: str,
        model: str = "GigaChat",
        *,
        base_url: str = DEFAULT_BASE_URL,
        scope: str = DEFAULT_SCOPE,
        timeout: float = 60.0,
        verify: bool | str = True,
        token_source: GigaChatTokenSource | None = None,
    ) -> None:
        self._tokens = token_source or GigaChatTokenSource(
            authorization_key, scope=scope, timeout=timeout, verify=verify
        )
        # Ключ в базовый класс не передаётся: наружу уходит токен, который
        # подставляется перед каждым запросом.
        super().__init__(base_url, "подставляется-перед-запросом", model, timeout)

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
        try:
            self._api_key = self._tokens.token()
        except GigaChatAuthError as exc:
            # Отказ авторизации не должен ронять разбор: система умеет
            # работать без модели, и честнее продолжить на правилах, чем
            # прервать анализ документа.
            logger.warning("GigaChat недоступен: %s", exc)
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
