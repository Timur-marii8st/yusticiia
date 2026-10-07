"""Ограничение размера тела запроса до его разбора."""

from __future__ import annotations

import contextlib
import json

BODY_METHODS = {b"POST", b"PUT", b"PATCH"}


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Отклонить запрос, тело которого больше ``max_bytes``, с ``413``.

    Без этого сервер читает в память тело любого размера, а предел
    ``SO_MAX_UPLOAD_BYTES`` срабатывает уже после буферизации. Размер
    проверяется и по ``Content-Length``, и по фактически полученным байтам:
    запрос без заголовка (chunked) иначе обошёл бы лимит.
    """

    def __init__(self, app, max_bytes: int) -> None:  # noqa: ANN001
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http" or scope["method"].encode() not in BODY_METHODS:
            await self._app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_bytes:
            await self._refuse(send)
            return

        received = 0
        exceeded = False
        refused = False

        async def limited_receive():  # noqa: ANN202
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    exceeded = True
                    raise _TooLarge
            return message

        async def guarded_send(message) -> None:  # noqa: ANN001
            nonlocal refused
            if not exceeded:
                await send(message)
                return
            if not refused:
                refused = True
                await self._refuse(send)

        with contextlib.suppress(_TooLarge):
            await self._app(scope, limited_receive, guarded_send)
        if exceeded and not refused:
            await self._refuse(send)

    async def _refuse(self, send) -> None:  # noqa: ANN001
        body = json.dumps(
            {"detail": "тело запроса больше допустимого", "error": {"code": "payload_too_large"}},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
