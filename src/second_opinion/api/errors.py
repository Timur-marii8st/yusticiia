"""Единый формат ошибки API.

Рядом с ``detail`` (его читает интерфейс, поэтому поле всегда строка) едет
``error`` с кодом, сообщением, полями и идентификатором запроса — тем же,
что в заголовке ответа и в аудит-журнале.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..metrics import get_metrics, http_status_metric
from .request_context import REQUEST_ID_HEADER

logger = logging.getLogger(__name__)

ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "too_many_requests",
    500: "internal_error",
    503: "service_unavailable",
}


def human_message(exc: Exception) -> str:
    """Сообщение об ошибке без служебного обрамления.

    ``KeyError`` — основа половины наших исключений («норма не найдена»,
    «документ не найден»), а ``str()`` у него возвращает **repr** аргумента:
    наружу уходило ``"'норма не найдена: uk-rf:art-1'"`` вместе с кавычками.
    Для судьи это выглядит как сбой, а не как объяснение.
    """
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def error_body(
    status_code: int,
    message: str,
    request: Request,
    fields: list[dict] | None = None,
) -> dict:
    body: dict[str, object] = {
        "detail": message,
        "error": {
            "code": ERROR_CODES.get(status_code, "error"),
            "message": message,
            "request_id": getattr(request.state, "request_id", ""),
        },
    }
    if fields:
        body["error"]["fields"] = fields  # type: ignore[index]
    return body


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else human_message(exc)
        headers = dict(exc.headers or {})
        headers[REQUEST_ID_HEADER] = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.status_code, message, request),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", ""),
            }
            for error in exc.errors()
        ]
        first = fields[0] if fields else {"location": "", "message": "неверный запрос"}
        message = (
            f"{first['location']}: {first['message']}"
            if first["location"]
            else first["message"]
        )
        return JSONResponse(
            status_code=422,
            content=error_body(422, message, request, fields),
            headers={REQUEST_ID_HEADER: getattr(request.state, "request_id", "")},
        )

    @app.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        rid = getattr(request.state, "request_id", "")
        logger.error("необработанная ошибка, запрос %s", rid, exc_info=exc)
        metrics = get_metrics()
        if metrics.enabled:
            metrics.counter(http_status_metric(500) or "").inc()
        message = f"Внутренняя ошибка сервера. Назовите администратору идентификатор запроса: {rid}"
        return JSONResponse(
            status_code=500,
            content=error_body(500, message, request),
            headers={REQUEST_ID_HEADER: rid},
        )
