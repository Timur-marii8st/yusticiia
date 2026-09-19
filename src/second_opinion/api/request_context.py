"""Идентификатор запроса и актор: то, что известно про запрос до обработчика.

Эти два значения проходят через всё: структурный лог, аудит-журнал,
заголовок ответа. Заполняет их middleware в :mod:`second_opinion.api.app`,
читают — обработчики.
"""

from __future__ import annotations

import uuid

from fastapi import Request

from ..audit.trail import ANONYMOUS_ACTOR

#: Заголовок сквозного идентификатора запроса.
#:
#: Связывает то, что увидел человек, с записью в журнале: без него, наткнувшись
#: на ошибку, невозможно назвать, какую именно запись искать.
REQUEST_ID_HEADER = "X-Request-Id"

#: Предел длины идентификатора, принятого от клиента.
MAX_REQUEST_ID_LENGTH = 64


def request_id(request: Request) -> str:
    """Идентификатор запроса: принятый от клиента или свой.

    Чужое значение принимается только в безопасном виде: оно попадает в журнал
    и в заголовок ответа, а значит может стать вектором подмены строк лога.
    """
    incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if (
        incoming
        and len(incoming) <= MAX_REQUEST_ID_LENGTH
        and incoming.isascii()
        and incoming.isprintable()
    ):
        return incoming
    return uuid.uuid4().hex


def actor(request: Request) -> str:
    """Кто выполняет запрос. Опознание делает middleware."""
    return getattr(request.state, "actor", ANONYMOUS_ACTOR)
