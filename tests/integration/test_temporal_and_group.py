from __future__ import annotations

from tests.conftest import FIXTURES_DIR


def _analysis(pipeline, path, applicable_at=None):
    document = pipeline.ingest(path.name, path.read_bytes())
    report = pipeline.analyze(document.document_id, applicable_at=applicable_at)
    return report


# -- групповое деяние -------------------------------------------------------------


def test_group_offense_extracted_from_fraud_sample(pipeline) -> None:
    """«по предварительному сговору … группой лиц» → факт group_offense."""
    path = FIXTURES_DIR / "sample_documents" / "sample_159_group_restitution.txt"
    report = _analysis(pipeline, path)
    fact = next(f for f in report.facts if f.type == "group_offense")
    assert fact.value == "group_with_conspiracy"
    assert fact.evidence
    assert report.case_facts.offense.complicity_role == "group_with_conspiracy"


def test_group_criterion_used_in_case_retrieval(pipeline) -> None:
    path = FIXTURES_DIR / "sample_documents" / "sample_159_group_restitution.txt"
    report = _analysis(pipeline, path)
    matches = [m for m in report.comparable_cases if m.case.group]
    assert matches, "групповые дела должны попадать в выдачу"
    assert any("групп" in r for m in matches for r in m.reasons)
    # распределение признаков в аналитике содержит группу
    assert "group" in report.analytics.feature_spread


# -- временные редакции: исход проверки зависит от применимой редакции -------------


def test_same_document_different_editions_change_rule_outcome(pipeline) -> None:
    """Один документ по синтетической ст. 999 (48 мес.):
    - на дату 2022 действует редакция v2020 (максимум 36) → R-001 FAIL;
    - на дату 2025 действует v2024 (максимум 60) → R-001 PASS.
    Редакция не выбирается «молча» — она видна в norms_applied."""
    path = FIXTURES_DIR / "sample_documents" / "sample_synth_temporal_999.txt"

    old_report = _analysis(pipeline, path, applicable_at="2022-06-01")
    new_report = _analysis(pipeline, path, applicable_at="2025-06-01")

    def rule1(report):
        return next(e for e in report.evaluations if e.rule_id == "R-001")

    old_eval, new_eval = rule1(old_report), rule1(new_report)
    assert old_eval.status.value == "FAIL"
    assert old_eval.numbers["max_months"] == 36
    assert new_eval.status.value == "PASS"
    assert new_eval.numbers["max_months"] == 60

    versions_old = {n.version_id for n in old_report.norms_applied}
    versions_new = {n.version_id for n in new_report.norms_applied}
    assert "v2020" in versions_old
    assert "v2024" in versions_new

    # дата из документа (10.05.2022) сама выбирает старую редакцию
    default_report = _analysis(pipeline, path)
    assert default_report.applicable_at == "2022-05-10"
    assert rule1(default_report).status.value == "FAIL"
