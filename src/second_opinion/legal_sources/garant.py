"""Клиент ГАРАНТ: редакционная история документа.

Написан по документации `garant.ru/mobileonline/api/documentation/`, но
**ни один ответ не наблюдался вживую**: токен выдаёт обслуживающая
организация, на момент написания его нет. Поэтому разбор ответа собран в
одной функции ``parse_redactions`` — когда токен появится, правка после
первого реального запроса будет точечной.

Проверить соответствие можно командой ``second-opinion garant-check``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .redaction_source import (
    ChangingDocument,
    DocumentRedactions,
    Redaction,
    RedactionSourceError,
    UncertainPeriod,
)

logger = logging.getLogger(__name__)

#: Документация описывает только этот MIME-тип; иное значение — ошибка формата.
CONTENT_TYPE = "application/json"

#: Коды, которые документация называет отдельно.
_ERRORS = {
    400: "синтаксис запроса или превышены лимиты параметров",
    401: "неверный или отсутствующий токен",
    403: "нет доступа — ограничения подписки",
    404: "документ или раздел не найден",
    423: "заблокировано: превышен лимит запросов",
    429: "слишком частые запросы",
}


def _as_date(value: Any) -> date | None:
    """Дату источник отдаёт строкой; формат вживую не проверялся."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning("ГАРАНТ: не удалось разобрать дату %r", value)
        return None


def _periods(entry: dict) -> list[tuple[date | None, date | None]]:
    activity = entry.get("activity") or []
    if isinstance(activity, dict):
        activity = [activity]
    return [(_as_date(p.get("from")), _as_date(p.get("to"))) for p in activity]


def parse_redactions(topic: str, payload: Any) -> DocumentRedactions:
    """Разобрать ответ ``GET /v2/redactions/$topic``.

    Единственное место, зависящее от точной формы ответа. Верхний уровень
    принимается и списком, и объектом со списком внутри — документация не
    даёт однозначного примера.
    """
    entries: list[dict]
    if isinstance(payload, list):
        entries = [e for e in payload if isinstance(e, dict)]
    elif isinstance(payload, dict):
        nested = payload.get("redactions") or payload.get("items") or payload.get("data")
        entries = [e for e in nested if isinstance(e, dict)] if isinstance(nested, list) else [payload]
    else:
        raise RedactionSourceError(
            f"ГАРАНТ вернул неожиданный тип ответа: {type(payload).__name__}"
        )

    redactions: list[Redaction] = []
    uncertain: list[UncertainPeriod] = []

    for entry in entries:
        changing = [
            ChangingDocument(
                topic=str(d.get("topic", "")),
                text=str(d.get("text", "")),
                entry=str(d.get("entry", "")),
            )
            for d in (entry.get("changingDocuments") or [])
            if isinstance(d, dict)
        ]
        for valid_from, valid_to in _periods(entry):
            if valid_from is None:
                continue
            redactions.append(
                Redaction(
                    valid_from=valid_from,
                    valid_to=valid_to,
                    status=str(entry.get("status", "")),
                    changing_documents=changing,
                )
            )
        for period in entry.get("notSure") or []:
            if isinstance(period, dict):
                uncertain.append(
                    UncertainPeriod(
                        valid_from=_as_date(period.get("from")),
                        valid_to=_as_date(period.get("to")),
                        description=str(period.get("description", "")),
                    )
                )

    return DocumentRedactions(
        topic=topic, source="garant", redactions=redactions, uncertain=uncertain, live=True
    )


class GarantSource:
    """Редакционная история из ГАРАНТ по OAuth-токену."""

    name = "garant"

    def __init__(self, base_url: str, token: str, timeout: float = 20.0) -> None:
        # Базовый URL не угадывается: в документации приведены только пути
        # вида /v2/..., хост задаёт обслуживающая организация.
        if not base_url:
            raise ValueError("SO_GARANT_BASE_URL не задан")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"SO_GARANT_BASE_URL должен начинаться с http:// или https:// "
                f"(получено: {base_url!r})"
            )
        if not token:
            raise ValueError("SO_GARANT_TOKEN не задан")
        # Значение HTTP-заголовка обязано быть ASCII. Без этой проверки
        # кириллица в токене доходит до httpx и возвращается невнятным
        # «'ascii' codec can't encode characters».
        if not token.isascii():
            raise ValueError(
                "SO_GARANT_TOKEN содержит не-ASCII символы: токен передаётся "
                "в заголовке Authorization и может состоять только из "
                "латиницы, цифр и знаков пунктуации "
                "(пример из документации: U1QtOTkwMTAyLWNud3FpdWhmbzg3M)"
            )
        self._base_url = base_url.rstrip("/")
        self._token = token
        # Документация: не более 20 секунд на обработку запроса.
        self._timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": CONTENT_TYPE,
            "Content-Type": CONTENT_TYPE,
        }

    def _get(self, path: str) -> Any:
        import httpx

        url = f"{self._base_url}{path}"
        try:
            response = httpx.get(url, headers=self.headers, timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise RedactionSourceError(f"ГАРАНТ недоступен ({url}): {exc}") from exc
        except UnicodeError as exc:
            # Кириллица в хосте или токене: наружу должно идти внятное
            # сообщение, а не внутренняя ошибка кодировки.
            raise RedactionSourceError(
                f"ГАРАНТ: адрес или токен содержит недопустимые символы ({exc}). "
                "Проверьте SO_GARANT_BASE_URL и SO_GARANT_TOKEN"
            ) from exc

        if response.status_code != 200:
            reason = _ERRORS.get(response.status_code, "неожиданный код ответа")
            raise RedactionSourceError(
                f"ГАРАНТ {path}: HTTP {response.status_code} — {reason}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise RedactionSourceError(f"ГАРАНТ {path}: ответ не является JSON") from exc

    def redactions(self, topic: str) -> DocumentRedactions:
        return parse_redactions(topic, self._get(f"/v2/redactions/{topic}"))
