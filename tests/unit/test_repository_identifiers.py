"""Файловое хранилище не выпускает идентификатор за пределы каталога.

`get` и `delete` строят путь из идентификатора объекта, и до правки
`../secret` читал и удалял файл рядом с хранилищем. Через HTTP это
недостижимо — маршрут не совпадёт, — но слой не должен рассчитывать на
предупредительность вызывающего: тот же метод зовут из CLI и из тестов, а
завтра позовут из эндпойнта с параметром запроса.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from second_opinion.domain.documents import Document
from second_opinion.storage.repositories import (
    JsonFileRepository,
    UnsafeIdentifierError,
)


def _repo(tmp_path) -> JsonFileRepository[Document]:
    return JsonFileRepository[Document](tmp_path / "documents", Document, "document_id")


def _outsider(tmp_path) -> Path:
    victim = tmp_path / "secret.json"
    victim.write_text(
        json.dumps(
            {
                "document_id": "secret",
                "filename": "s.txt",
                "content_type": "text/plain",
                "text": "чужие данные",
                "sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    return victim


def test_traversal_cannot_read_outside_the_store(tmp_path) -> None:
    repo = _repo(tmp_path)
    _outsider(tmp_path)
    assert repo.get("../secret") is None


def test_traversal_cannot_delete_outside_the_store(tmp_path) -> None:
    repo = _repo(tmp_path)
    victim = _outsider(tmp_path)

    assert repo.delete("../secret") is False
    assert victim.exists(), "файл за пределами хранилища был удалён"


@pytest.mark.parametrize(
    "bad",
    ["..", ".", "../secret", "a/b", "a\\b", "", "x" * 129, "пробел и ещё"],
)
def test_bad_identifiers_are_refused(tmp_path, bad: str) -> None:
    repo = _repo(tmp_path)
    assert repo.get(bad) is None
    assert repo.delete(bad) is False


def test_saving_with_a_bad_identifier_is_an_error(tmp_path) -> None:
    """Сохранение — наш код, и молчать о негодном идентификаторе нельзя."""
    repo = _repo(tmp_path)
    document = Document(
        document_id="../escape",
        filename="d.txt",
        content_type="text/plain",
        text="текст",
        sha256="b" * 64,
    )
    with pytest.raises(UnsafeIdentifierError):
        repo.save(document)


def test_normal_identifiers_keep_working(tmp_path) -> None:
    repo = _repo(tmp_path)
    document = Document(
        document_id="0f1e2d3c4b5a6978",
        filename="d.txt",
        content_type="text/plain",
        text="текст",
        sha256="c" * 64,
    )
    repo.save(document)

    assert repo.get(document.document_id) is not None
    assert repo.delete(document.document_id) is True
    assert repo.get(document.document_id) is None
