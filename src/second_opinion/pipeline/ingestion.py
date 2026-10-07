"""Загрузка документа: разбор, сохранение, журнал.

Единственная ответственность — превратить присланные байты в
сохранённый `Document` и вернуть его же обратно по идентификатору.
Ни извлечения фактов, ни проверок здесь нет: конвейер анализа
(`AnalysisDerivation`) получает уже готовый документ.
"""

from __future__ import annotations

from ..audit.trail import AuditTrail
from ..domain.documents import Document
from ..ingestion.parser import parse_document
from ..storage.repositories import Repository
from .errors import DocumentNotFound


class DocumentIngestion:
    """Разбор и хранение документов."""

    def __init__(
        self,
        *,
        documents: Repository[Document],
        audit: AuditTrail,
        max_upload_bytes: int,
    ) -> None:
        self._documents = documents
        self._audit = audit
        self._max_upload_bytes = max_upload_bytes

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    def ingest(
        self,
        filename: str,
        content: bytes,
        *,
        actor: str = "anonymous",
        request_id: str | None = None,
    ) -> Document:
        document = parse_document(filename, content, self._max_upload_bytes)
        document = document.model_copy(update={"owner": actor})
        self._documents.save(document)
        self._audit.log(
            operation="document_ingest",
            actor=actor,
            component="ingestion",
            request_id=request_id or document.document_id,
            input_hash=document.sha256,
            details={"filename": document.filename, "chars": len(document.text)},
        )
        return document

    def get(self, document_id: str) -> Document:
        document = self._documents.get(document_id)
        if document is None:
            raise DocumentNotFound(f"документ не найден: {document_id}")
        return document
