"""Чтение загружаемого файла с соблюдением лимита."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile

#: Размер порции при чтении загружаемого файла.
UPLOAD_CHUNK_BYTES = 64 * 1024


async def read_within_limit(file: UploadFile, limit: int) -> bytes:
    """Прочитать файл, оборвав чтение при превышении лимита.

    ``await file.read()`` без аргумента затягивает в память весь запрос
    целиком, и лимит проверялся бы уже после этого: запрос на гигабайты
    буферизовался, чтобы затем быть отклонённым.
    """
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code=400, detail=f"файл больше лимита {limit} байт")
        chunks.append(chunk)
    return b"".join(chunks)
