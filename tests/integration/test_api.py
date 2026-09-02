from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


@pytest.fixture()
def client(pipeline):
    return TestClient(create_app(pipeline))


def _upload(client: TestClient, text: str, filename: str = "case.txt") -> str:
    response = client.post(
        "/api/documents", json={"filename": filename, "text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()["document_id"]


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_metrics_disabled_by_default(client) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body == {"enabled": False}


def test_metrics_enabled_after_increments(pipeline, monkeypatch) -> None:
    from second_opinion.metrics import (
        ANALYSES_TOTAL,
        reset_metrics_for_tests,
    )

    monkeypatch.setenv("SO_METRICS_ENABLED", "1")
    reset_metrics_for_tests()
    try:
        from second_opinion.api.routes import create_app

        client = TestClient(create_app(pipeline))
        # Прямой инкремент счётчика — снимок должен показать ненулевое значение.
        from second_opinion.metrics import get_metrics

        get_metrics().counter(ANALYSES_TOTAL, "test").inc()
        response = client.get("/metrics")
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        counters = body["metrics"]["counters"]
        assert ANALYSES_TOTAL in counters
        assert counters[ANALYSES_TOTAL] >= 1
    finally:
        reset_metrics_for_tests()


def test_index_served(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Второе мнение" in response.text


def test_upload_analyze_roundtrip(client, sample_clean_text) -> None:
    document_id = _upload(client, sample_clean_text)

    response = client.get(f"/api/documents/{document_id}")
    assert response.status_code == 200
    assert response.json()["text"] == sample_clean_text

    response = client.post(f"/api/documents/{document_id}/analyze", json={})
    assert response.status_code == 200
    report = response.json()
    assert report["facts"]
    assert report["evaluations"]
    assert report["comparable_cases"]
    assert report["disclaimers"]

    analysis_id = report["analysis_id"]
    response = client.get(f"/api/analyses/{analysis_id}")
    assert response.status_code == 200
    assert response.json()["analysis_id"] == analysis_id


def test_analyze_with_explicit_applicable_at(client, sample_clean_text) -> None:
    document_id = _upload(client, sample_clean_text)
    response = client.post(
        f"/api/documents/{document_id}/analyze", json={"applicable_at": "2024-06-01"}
    )
    assert response.status_code == 200
    assert response.json()["applicable_at"] == "2024-06-01"


def test_norm_temporal_endpoint(client) -> None:
    response = client.get(
        "/api/norms/uk-rf:art-999-synthetic", params={"applicable_at": "2022-06-01"}
    )
    assert response.status_code == 200
    assert response.json()["version"]["version_id"] == "v2020"

    response = client.get(
        "/api/norms/uk-rf:art-999-synthetic", params={"applicable_at": "2025-01-01"}
    )
    assert response.json()["version"]["version_id"] == "v2024"


def test_norm_not_applicable_date_404(client) -> None:
    response = client.get(
        "/api/norms/uk-rf:art-999-synthetic", params={"applicable_at": "2019-01-01"}
    )
    assert response.status_code == 404


def test_norms_list(client) -> None:
    response = client.get("/api/norms")
    assert response.status_code == 200
    norm_ids = {n["norm_id"] for n in response.json()["norms"]}
    assert "uk-rf:art-158-part-2" in norm_ids


def test_cases_filter(client) -> None:
    response = client.get("/api/cases", params={"article": 158, "part": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert all(c["article"] == 158 and c["part"] == 2 for c in body["cases"])
    assert all(c["synthetic"] for c in body["cases"])


def test_empty_text_rejected(client) -> None:
    response = client.post("/api/documents", json={"filename": "x.txt", "text": ""})
    assert response.status_code == 422  # min_length=1


def test_unsupported_format_rejected(client) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("malware.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
    )
    assert response.status_code == 400


def test_unknown_document_404(client) -> None:
    assert client.get("/api/documents/missing").status_code == 404
    assert client.post("/api/documents/missing/analyze", json={}).status_code == 404


def test_patch_fact_recalculates_report(client, sample_clean_text) -> None:
    document_id = _upload(client, sample_clean_text)
    analysis = client.post(f"/api/documents/{document_id}/analyze", json={}).json()
    restitution = next(
        f for f in analysis["facts"] if f["type"] == "restitution"
    )

    response = client.patch(
        f"/api/analyses/{analysis['analysis_id']}/facts/{restitution['id']}",
        json={"status": "NOT_FOUND"},
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    fact = next(f for f in updated["facts"] if f["id"] == restitution["id"])
    assert fact["status"] == "NOT_FOUND"
    r004 = next(e for e in updated["evaluations"] if e["rule_id"] == "R-004")
    assert "не применимо" in r004["headline"]


def test_patch_fact_invalid_status_400(client, sample_clean_text) -> None:
    document_id = _upload(client, sample_clean_text)
    analysis = client.post(f"/api/documents/{document_id}/analyze", json={}).json()
    fact = analysis["facts"][0]
    response = client.patch(
        f"/api/analyses/{analysis['analysis_id']}/facts/{fact['id']}",
        json={"status": "CONFLICT"},
    )
    assert response.status_code == 400


def test_patch_unknown_fact_404(client, sample_clean_text) -> None:
    document_id = _upload(client, sample_clean_text)
    analysis = client.post(f"/api/documents/{document_id}/analyze", json={}).json()
    response = client.patch(
        f"/api/analyses/{analysis['analysis_id']}/facts/missing",
        json={"status": "VERIFIED"},
    )
    assert response.status_code == 404
    response = client.patch(
        "/api/analyses/missing/facts/missing", json={"status": "VERIFIED"}
    )
    assert response.status_code == 404


def test_search_sources(client) -> None:
    response = client.get("/api/search", params={"q": "предел при покушении"})
    assert response.status_code == 200
    body = response.json()
    assert body["results"]
    assert body["results"][0]["norm_id"] == "uk-rf:art-66"
    for hit in body["results"]:
        assert hit["fragment"]
        assert hit["sha256"]
        assert hit["matched_terms"]


def test_search_sources_temporal(client) -> None:
    response = client.get(
        "/api/search",
        params={"q": "синтетическая фикстура", "applicable_at": "2022-06-01"},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    synthetic = next(r for r in results if r["norm_id"] == "uk-rf:art-999-synthetic")
    assert synthetic["version_id"] == "v2020"


def test_search_sources_bad_date_400(client) -> None:
    response = client.get(
        "/api/search", params={"q": "наказание", "applicable_at": "не дата"}
    )
    assert response.status_code == 400


def test_search_sources_requires_query(client) -> None:
    assert client.get("/api/search").status_code == 422


def test_search_sources_empty_for_nonsense(client) -> None:
    response = client.get("/api/search", params={"q": "квантовая хромодинамика"})
    assert response.status_code == 200
    assert response.json()["results"] == []
