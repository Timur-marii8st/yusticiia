from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app
from second_opinion.pipeline import DocumentNotFound
from tests.conftest import SAMPLE_CLEAN


@pytest.fixture()
def client(pipeline):
    return TestClient(create_app(pipeline))


def _upload_and_analyze(client) -> tuple[str, str]:
    with SAMPLE_CLEAN.open("rb") as fh:
        response = client.post(
            "/api/documents/upload",
            files={"file": (SAMPLE_CLEAN.name, fh, "text/plain")},
        )
    document_id = response.json()["document_id"]
    analysis_id = client.post(f"/api/documents/{document_id}/analyze", json={}).json()[
        "analysis_id"
    ]
    return document_id, analysis_id


def test_delete_analysis(client, pipeline) -> None:
    _, analysis_id = _upload_and_analyze(client)
    assert pipeline.get_analysis(analysis_id) is not None

    response = client.delete(f"/api/analyses/{analysis_id}")
    assert response.status_code == 200
    assert pipeline.get_analysis(analysis_id) is None

    # повторное удаление — 404
    assert client.delete(f"/api/analyses/{analysis_id}").status_code == 404


def test_delete_document_cascades_analyses(client, pipeline) -> None:
    document_id, analysis_id = _upload_and_analyze(client)

    response = client.delete(f"/api/documents/{document_id}")
    assert response.status_code == 200
    assert pipeline.get_analysis(analysis_id) is None
    with pytest.raises(DocumentNotFound):
        pipeline.get_document(document_id)
    # анализ недоступен и через API
    assert client.get(f"/api/analyses/{analysis_id}").status_code == 404


def test_delete_missing_document_404(client) -> None:
    assert client.delete("/api/documents/nonexistent").status_code == 404


def test_deletion_logged_to_audit(pipeline, client) -> None:
    document_id, _ = _upload_and_analyze(client)
    client.delete(f"/api/documents/{document_id}")
    operations = [event.operation for event in pipeline._audit.recent]
    assert "document_delete" in operations
