"""Загрузка не должна буферизовать в память запрос сверх лимита.

Успешная загрузка покрыта в test_api / test_delete / test_fact_add —
здесь только то, что связано с обрывом чтения.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from second_opinion.api.routes import UPLOAD_CHUNK_BYTES, create_app, read_within_limit

LIMIT = 4 * UPLOAD_CHUNK_BYTES


class FakeUpload:
    """Файл, который считает, сколько байт с него реально прочитали."""

    def __init__(self, total: int) -> None:
        self.remaining = total
        self.served = 0

    async def read(self, size: int = -1) -> bytes:
        count = self.remaining if size < 0 else min(size, self.remaining)
        self.remaining -= count
        self.served += count
        return b"x" * count


def test_oversized_upload_is_not_buffered_whole() -> None:
    """Суть правки: чтение обрывается, а не дочитывает запрос до конца."""
    upload = FakeUpload(LIMIT * 1000)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(read_within_limit(upload, LIMIT))

    assert exc.value.status_code == 400
    assert upload.served <= LIMIT + UPLOAD_CHUNK_BYTES
    assert upload.remaining > 0, "файл дочитан до конца — обрыв не сработал"


def test_content_at_limit_is_read_fully() -> None:
    """Граница: файл ровно по лимиту не должен отвергаться."""
    content = asyncio.run(read_within_limit(FakeUpload(LIMIT), LIMIT))
    assert len(content) == LIMIT


def test_api_rejects_oversized_file(pipeline) -> None:
    client = TestClient(create_app(pipeline, auth_token=""))
    oversized = b"x" * (pipeline.max_upload_bytes + 1)

    response = client.post(
        "/api/v1/documents/upload",
        files={"file": ("big.txt", oversized, "text/plain")},
    )
    assert response.status_code == 400
    assert "лимита" in response.json()["detail"]
