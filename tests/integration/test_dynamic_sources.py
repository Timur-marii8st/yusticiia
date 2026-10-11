"""Реальный API-путь и HTTP-клиент ИПС с воспроизводимым тестовым сервером.

Разметка ниже — подготовленные тестовые ответы, не доказательство живого доступа.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from second_opinion.api.app import create_app
from second_opinion.api.deps import build_pipeline
from second_opinion.config import load_config
from second_opinion.domain.analysis import AnalysisReport
from second_opinion.domain.norms import LegalNorm, NormVersion, SanctionSpec
from second_opinion.domain.source_fragments import SourceFragment
from second_opinion.legal_sources.acquisition import NormAcquisition, SourceCache
from second_opinion.legal_sources.store import NormStore


@pytest.fixture()
def ips(monkeypatch):
    state = {
        "revision": 2,
        "pending": False,
        "status": 200,
        "complete": True,
        "malformed": False,
        "text": "Новый текст",
        "calls": [],
    }

    def handle(request):
        state["calls"].append(dict(request.url.params))
        if state["status"] != 200:
            return httpx.Response(state["status"], headers={"Retry-After": "120"})
        if "docbody" in request.url.params:
            payload = "<select><option value='1'>1 - от 01.01.2020 № 1-ФЗ</option>"
            payload += f"<option value='{state['revision']}'>{state['revision']} - от 01.01.2024 № 2-ФЗ</option>"
            if state["pending"]:
                payload += "<option value='n'>9 - от 01.01.2025 № 9-ФЗ (не готова)</option>"
            payload += "</select>"
        else:
            text = "Старый текст" if request.url.params["rdk"] == "1" else state["text"]
            payload = f"<script>textCompleted = '{str(state['complete']).lower()}'</script>"
            for number in ("15", "272", "228 1"):
                payload += f"<p>Статья {number}. Заголовок статьи</p><p>1. {text}.</p>"
        if state["malformed"]:
            payload = "<html>Страница обслуживания</html>"
        return httpx.Response(200, content=payload.encode("windows-1251"))

    http = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: http.get(url, **kw))
    monkeypatch.setattr("second_opinion.legal_sources.pravo_gov.time.sleep", lambda _: None)
    yield state
    http.close()


@pytest.fixture()
def live_pipeline(tmp_path):
    config = replace(load_config(), data_dir=tmp_path, dynamic_norm_source="pravo")
    return build_pipeline(config)


def search(client, article=272, when="2022-05-10", **kwargs):
    response = client.get(
        "/api/v1/search",
        params={
            "code": "УК РФ",
            "article": article,
            "part": 1,
            "applicable_at": when,
            **kwargs,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def analyze(client, article=272):
    doc = client.post(
        "/api/v1/documents",
        json={
            "filename": "case.txt",
            "text": (
                "УСТАНОВИЛ: Подсудимый совершил преступление. ПРИГОВОРИЛ: "
                f"Признать виновным по ч. 1 ст. {article} УК РФ и назначить наказание "
                "в виде лишения свободы на срок 48 месяцев."
            ),
        },
    ).json()
    response = client.post(
        f"/api/v1/documents/{doc['document_id']}/analyze", json={"applicable_at": "2022-05-10"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_absent_article_api_downloads_and_caches_provenance(live_pipeline, ips):
    client = TestClient(create_app(live_pipeline))
    assert live_pipeline.norm_store.find_by_article("УК РФ", 272, 1) is None
    result = search(client)
    source = result["source_state"]
    candidate = SourceFragment.model_validate(source["candidate"])
    assert source["acquisition"] == "fetched"
    assert source["applicability"] == "unknown"
    assert candidate.edition_id == "2" and "Новый текст" in candidate.text
    assert candidate.act_date == date(2024, 1, 1)
    assert candidate.entry_into_force is None and candidate.publication_date is None
    assert candidate.effective_from is None and candidate.applicability == "unknown"
    assert candidate.provider == "pravo" and candidate.document_id == "102041891"
    assert "fulltext=1" in candidate.source_url and "rdk=2" in candidate.source_url
    assert candidate.verification_status == "candidate" and candidate.scope == "article"
    assert source["structured_data"] == "missing" and result["results"] == []
    assert live_pipeline.norm_store.find_by_article("УК РФ", 272, 1) is None
    assert len(ips["calls"]) == 2
    repeated = search(client)["source_state"]
    assert repeated["acquisition"] == "cache"
    assert repeated["candidate"] == source["candidate"] and len(ips["calls"]) == 2
    # Восстановление кэша после перезапуска, в том числе другой статьи документа.
    another = build_pipeline(
        replace(
            load_config(),
            data_dir=live_pipeline.norm_acquisition.cache.directory.parents[2],
            dynamic_norm_source="pravo",
        )
    )
    assert search(TestClient(create_app(another)), 15)["source_state"]["acquisition"] == "cache"
    assert len(ips["calls"]) == 2


def install_periods(store, *, pending=False):
    norm = LegalNorm(
        norm_id="test:272",
        code="УК РФ",
        article=272,
        part=1,
        title="Тест",
        amendments_checked_through=date(2026, 10, 10),
    )
    versions = []
    for edition, start, end, text in (
        (1, date(2020, 1, 1), date(2023, 12, 31), "Подтверждённый старый текст"),
        (2, date(2024, 1, 1), None, "Подтверждённый новый текст"),
    ):
        versions.append(
            NormVersion(
                norm_id=norm.norm_id,
                version_id=f"v{edition}",
                text=text,
                sha256=NormVersion.compute_sha256(text),
                effective_from=start,
                effective_to=end,
                retrieved_at=date(2026, 1, 1),
                verification_status="verified",
                source_document="УК РФ",
                source_url="https://pravo.gov.ru/",
                source_edition=f"pravo:102041891:{edition}",
                sanctions=[
                    SanctionSpec(
                        punishment_type="imprisonment", max_months=60, pending_review=pending
                    )
                ],
            )
        )
    store.add_norm(norm, versions)


def test_two_confirmed_periods_use_local_versions_and_no_latest_substitution(live_pipeline, ips):
    install_periods(live_pipeline.norm_store)
    client = TestClient(create_app(live_pipeline))
    old = search(client)
    new = search(client, when="2025-05-10")
    assert old["results"][0]["version_id"] == "v1"
    assert new["results"][0]["version_id"] == "v2"
    assert "старый" in old["results"][0]["fragment"]
    assert old["source_state"]["candidate"] is None
    assert (
        old["source_state"]["applicability"] == new["source_state"]["applicability"] == "confirmed"
    )
    assert len(ips["calls"]) == 2
    gap = search(client, when="2019-01-01")["source_state"]
    assert gap["applicability"] == "unknown" and gap["local_fragment"] is None
    assert gap["candidate"]["effective_from"] is None


def test_unprepared_edition_and_suffix_remain_candidates(live_pipeline, ips):
    ips["pending"] = True
    source = search(TestClient(create_app(live_pipeline)), 228, part=9, article_suffix="1")[
        "source_state"
    ]
    assert source["candidate"]["article"] == "228.1"
    assert source["candidate"]["part"] == 9
    assert source["applicability"] == "unknown" and source["freshness"] == "unknown"
    assert any("неподготовленные" in r for r in source["reasons"])


def test_analysis_snapshots_candidate_and_unknown_survive_source_change(live_pipeline, ips):
    client = TestClient(create_app(live_pipeline))
    report = analyze(client)
    source = next(s for s in report["norm_sources"] if s["article"] == "272")
    evaluation = next(e for e in report["evaluations"] if e["rule_id"] == "R-001")
    assert evaluation["status"] == "UNKNOWN" and evaluation["missing"]
    assert (
        source["structured_data"] == "missing"
        and source["candidate"]["verification_status"] == "candidate"
    )
    assert report["applicable_at_source"] == "explicit" and report["applicable_at"] == "2022-05-10"
    assert any("Нормативный источник" in c["title"] for c in report["concerns"])
    assert len(ips["calls"]) == 2  # одна история, один кодекс для всех оснований
    ips["revision"] = 3
    ips["text"] = "Изменённый текст"
    live_pipeline.norm_acquisition.ttl = timedelta(0)
    latest = search(client)["source_state"]
    assert latest["candidate"]["edition_id"] == "3"
    stored = client.get(f"/api/v1/analyses/{report['analysis_id']}").json()
    restored = next(s for s in stored["norm_sources"] if s["article"] == "272")
    assert restored["candidate"] == source["candidate"]
    assert restored["candidate"]["sha256"] != latest["candidate"]["sha256"]
    assert AnalysisReport.model_validate(stored).norm_sources
    html = client.get(f"/api/v1/analyses/{report['analysis_id']}/print").text
    assert source["candidate"]["sha256"] in html and "Новый текст" in html


def test_pending_structured_sanction_is_unknown_even_with_confirmed_local_text(live_pipeline, ips):
    install_periods(live_pipeline.norm_store, pending=True)
    report = analyze(TestClient(create_app(live_pipeline)))
    assert next(e for e in report["evaluations"] if e["rule_id"] == "R-001")["status"] == "UNKNOWN"
    assert (
        next(s for s in report["norm_sources"] if s["article"] == "272")["structured_data"]
        == "pending"
    )


def test_successful_local_calculation_becomes_unknown_when_freshness_unconfirmed(
    live_pipeline, ips, sample_clean_text
):
    client = TestClient(create_app(live_pipeline))
    doc = client.post(
        "/api/v1/documents", json={"filename": "case.txt", "text": sample_clean_text}
    ).json()
    result = client.post(
        f"/api/v1/documents/{doc['document_id']}/analyze", json={"applicable_at": "2025-05-10"}
    ).json()
    check = next(e for e in result["evaluations"] if e["rule_id"] == "R-001")
    assert check["status"] == "UNKNOWN" and check["numbers"] == {}
    assert "Нормативное основание не подтверждено" in check["explanation"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"status": 503}, "unavailable"),
        ({"status": 429}, "rate_limited"),
        ({"complete": False}, "incomplete"),
        ({"malformed": True}, "invalid_response"),
    ],
)
def test_errors_preserve_corpus_and_cache(live_pipeline, ips, change, expected):
    client = TestClient(create_app(live_pipeline))
    before = [
        v.model_dump_json()
        for n in live_pipeline.norm_store.list_norms()
        for v in live_pipeline.norm_store.versions(n.norm_id)
    ]
    cached = search(client)["source_state"]["candidate"]
    files = {
        p.name: p.read_bytes()
        for p in live_pipeline.norm_acquisition.cache.directory.glob("*.json")
    }
    live_pipeline.norm_acquisition.ttl = timedelta(0)
    ips.update(change)
    failed = search(client)["source_state"]
    assert failed["acquisition"] == expected and failed["reasons"]
    assert failed["candidate"]["text"] == cached["text"]
    count = len(ips["calls"])
    assert search(client)["source_state"]["acquisition"] == expected
    assert len(ips["calls"]) == count  # cooldown, в том числе Retry-After
    if expected != "incomplete":  # полноценная история могла успешно перепровериться
        assert {
            p.name: p.read_bytes()
            for p in live_pipeline.norm_acquisition.cache.directory.glob("*.json")
        } == files
    after = [
        v.model_dump_json()
        for n in live_pipeline.norm_store.list_norms()
        for v in live_pipeline.norm_store.versions(n.norm_id)
    ]
    assert before == after


def test_article_absence_is_distinct_from_provider_failure(live_pipeline, ips):
    state = search(TestClient(create_app(live_pipeline)), 400)["source_state"]
    assert state["acquisition"] == "not_found" and state["candidate"] is None
    assert state["applicability"] == "unknown"


def test_same_revision_change_keeps_immutable_snapshot(live_pipeline, ips):
    client = TestClient(create_app(live_pipeline))
    original = search(client)["source_state"]["candidate"]
    live_pipeline.norm_acquisition.ttl = timedelta(0)
    ips["text"] = "Исправленный текст"
    new = search(client)["source_state"]["candidate"]
    assert new["edition_id"] == original["edition_id"] and new["sha256"] != original["sha256"]
    snapshots = list(live_pipeline.norm_acquisition.cache.directory.glob("snapshot-*.json"))
    assert any(
        original["text"] in json.loads(p.read_text(encoding="utf-8"))["articles"].values()
        for p in snapshots
    )


def test_bad_cache_is_detected_not_used(live_pipeline, ips):
    client = TestClient(create_app(live_pipeline))
    search(client)
    path = live_pipeline.norm_acquisition.cache.directory / "edition-2.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["articles"]["272"] = "Подменённый текст"
    path.write_text(json.dumps(payload), encoding="utf-8")
    state = search(client)["source_state"]
    assert state["acquisition"] == "cache_error" and state["candidate"] is None


def test_offline_explicit_query_and_legacy_report(pipeline, ips):
    client = TestClient(create_app(pipeline))
    assert search(client)["source_state"]["acquisition"] == "offline"
    assert not ips["calls"]
    response = client.get("/api/v1/search", params={"code": "УК РФ", "article": 272})
    assert response.status_code == 400 and "applicable_at" in response.text
    assert AnalysisReport(analysis_id="old", document_id="old").norm_sources == []


def test_one_document_per_analysis_even_with_zero_ttl(live_pipeline, ips):
    live_pipeline.norm_acquisition.ttl = timedelta(0)
    analyze(TestClient(create_app(live_pipeline)))
    assert len(ips["calls"]) == 2
    assert all(
        set(call) <= {"nd", "docbody", "doc_itself", "page", "rdk", "fulltext"}
        for call in ips["calls"]
    )


def test_source_snapshot_rejects_fabricated_confirmed_interval(tmp_path):
    service = NormAcquisition(NormStore(), SourceCache(tmp_path))
    assert service.resolve("УК РФ", "15", None, date(2022, 1, 1)).acquisition == "offline"
    with pytest.raises(ValueError, match="TTL"):
        NormAcquisition(NormStore(), SourceCache(tmp_path), ttl_seconds=-1)


def test_missing_rule_basis_is_unknown_and_candidate_is_saved(tmp_path, ips):
    config = replace(
        load_config(),
        data_dir=tmp_path,
        fixtures_dir=tmp_path / "empty",
        dynamic_norm_source="pravo",
    )
    pipeline = build_pipeline(config)
    install_periods(pipeline.norm_store)
    report = analyze(TestClient(create_app(pipeline)))
    result = next(e for e in report["evaluations"] if e["rule_id"] == "R-001")
    assert result["status"] == "UNKNOWN" and result["numbers"] == {}
    basis = next(s for s in report["norm_sources"] if s["norm_id"] == "uk-rf:art-60")
    assert basis["local_fragment"] is None and basis["applicability"] == "unknown"
    assert any("нет нормы" in reason for reason in basis["reasons"])


def test_snapshot_checksum_and_confirmed_period_are_validated(live_pipeline, ips):
    candidate = search(TestClient(create_app(live_pipeline)))["source_state"]["candidate"]
    with pytest.raises(ValueError, match="контрольная сумма"):
        SourceFragment.model_validate({**candidate, "text": "Другой текст"})
    with pytest.raises(ValueError, match="даты начала"):
        SourceFragment.model_validate({**candidate, "applicability": "confirmed"})
