from __future__ import annotations

from datetime import date

from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.domain.evidence import Evidence
from second_opinion.domain.facts import (
    AggravatingFactor,
    CaseFacts,
    LegalFact,
    MitigatingFactor,
    Qualification,
)
from second_opinion.domain.rules import RuleEvaluation
from second_opinion.legal_sources.store import NormStore
from second_opinion.rule_engine.engine import RuleEngine

APPLICABLE_AT = date(2025, 1, 1)


def _register_fact(case: CaseFacts, fact_type: FactType, value: object) -> None:
    case.facts.append(
        LegalFact(
            id=f"fact-{fact_type.value}-{len(case.facts) + 1}",
            type=fact_type,
            value=value,
            confidence=0.95,
            evidence=[
                Evidence(document_id="unit", quote="q", start_offset=0, end_offset=1)
            ],
            extraction_method="pattern",
            status=FactStatus.VERIFIED,
        )
    )


def make_case(
    *,
    article: int = 158,
    part: int | None = 2,
    term: float | None = None,
    stage: str = "completed",
    special: bool | None = None,
    plea: bool | None = None,
    mitigating: tuple[str, ...] = (),
    aggravating: tuple[str, ...] = (),
    suspended: bool | None = None,
    punishment_type: str | None = "imprisonment",
) -> CaseFacts:
    case = CaseFacts()
    case.offense.qualifications.append(Qualification(article=article, part=part))
    _register_fact(case, FactType.QUALIFICATION, {"code": "УК РФ", "article": article, "part": part})
    case.offense.stage = stage
    _register_fact(case, FactType.OFFENSE_STAGE, stage)
    case.sentence.punishment_type = punishment_type
    if punishment_type is not None:
        _register_fact(case, FactType.PUNISHMENT_TYPE, punishment_type)
    case.sentence.term_months = term
    if term is not None:
        _register_fact(case, FactType.PUNISHMENT_TERM, term)
    case.sentence.suspended = suspended
    if suspended is not None:
        _register_fact(case, FactType.SUSPENDED_SENTENCE, suspended)
    case.procedural.special_procedure = special
    if special is not None:
        _register_fact(case, FactType.SPECIAL_PROCEDURE, special)
    case.procedural.guilty_plea = plea
    for code in mitigating:
        case.mitigating.append(
            MitigatingFactor(code=code, title=code, fact_ids=[f"fact-{code}"])
        )
    for code in aggravating:
        case.aggravating.append(
            AggravatingFactor(code=code, title=code, fact_ids=[f"fact-{code}"])
        )
    return case


def run(norm_store: NormStore, case: CaseFacts) -> dict[str, RuleEvaluation]:
    engine = RuleEngine()
    evaluations = engine.evaluate(case, norm_store, APPLICABLE_AT)
    assert len(evaluations) == 6
    for evaluation in evaluations:
        assert evaluation.rule_version
    return {e.rule_id: e for e in evaluations}


# -- R-001: пределы санкции --------------------------------------------------


def test_r001_fail_when_term_exceeds_sanction_max(norm_store: NormStore) -> None:
    result = run(norm_store, make_case(term=66))
    evaluation = result["R-001"]
    assert evaluation.status.value == "FAIL"
    assert evaluation.numbers["term_months"] == 66
    assert evaluation.numbers["max_months"] == 60
    assert evaluation.facts_used or evaluation.missing


def test_r001_pass_within_sanction(norm_store: NormStore) -> None:
    assert run(norm_store, make_case(term=36))["R-001"].status.value == "PASS"


def test_r001_warning_below_sanction_min(norm_store: NormStore) -> None:
    # ч. 2 ст. 228: минимум 36 мес.
    evaluation = run(norm_store, make_case(article=228, part=2, term=24))["R-001"]
    assert evaluation.status.value == "WARNING"


def test_r001_unknown_without_term(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=None))["R-001"]
    assert evaluation.status.value == "UNKNOWN"
    assert evaluation.missing


def test_r001_unknown_when_norm_absent(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(article=500, term=12))["R-001"]
    assert evaluation.status.value == "UNKNOWN"
    assert evaluation.missing


# -- R-002: неоконченное преступление ----------------------------------------


def test_r002_attempt_fail_over_three_quarters(norm_store: NormStore) -> None:
    # ст. 228 ч. 1: макс 36; покушение ≤ 27
    evaluation = run(norm_store, make_case(article=228, part=1, stage="attempt", term=30))["R-002"]
    assert evaluation.status.value == "FAIL"
    assert evaluation.numbers["limit_months"] == 27


def test_r002_attempt_pass_on_boundary(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(article=228, part=1, stage="attempt", term=27))["R-002"]
    assert evaluation.status.value == "PASS"


def test_r002_preparation_half_max(norm_store: NormStore) -> None:
    ok = run(norm_store, make_case(article=228, part=1, stage="preparation", term=18))["R-002"]
    assert ok.status.value == "PASS"
    bad = run(norm_store, make_case(article=228, part=1, stage="preparation", term=19))["R-002"]
    assert bad.status.value == "FAIL"


def test_r002_not_applicable_for_completed(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=36))["R-002"]
    assert evaluation.status.value == "PASS"
    assert "не применимо" in evaluation.headline


# -- R-003: особый порядок ----------------------------------------------------


def test_r003_special_procedure_fail(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=45, special=True))["R-003"]
    assert evaluation.status.value == "FAIL"
    assert evaluation.numbers["limit_months"] == 40


def test_r003_special_procedure_pass_boundary(norm_store: NormStore) -> None:
    assert (
        run(norm_store, make_case(term=40, special=True))["R-003"].status.value == "PASS"
    )


def test_r003_not_applicable(norm_store: NormStore) -> None:
    assert (
        run(norm_store, make_case(term=45, special=False))["R-003"].status.value == "PASS"
    )


def test_r003_unknown_when_procedure_not_extracted(norm_store: NormStore) -> None:
    assert (
        run(norm_store, make_case(term=45, special=None))["R-003"].status.value == "UNKNOWN"
    )


# -- R-004: смягчающие пп. «и»/«к» ---------------------------------------------


def test_r004_mitigating_two_thirds_fail(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=45, mitigating=("61.1.и",)))["R-004"]
    assert evaluation.status.value == "FAIL"


def test_r004_mitigating_two_thirds_pass(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=40, mitigating=("61.1.к",)))["R-004"]
    assert evaluation.status.value == "PASS"


def test_r004_not_applicable_with_aggravating(norm_store: NormStore) -> None:
    evaluation = run(
        norm_store,
        make_case(term=45, mitigating=("61.1.и",), aggravating=("63.1.а",)),
    )["R-004"]
    assert evaluation.status.value == "PASS"
    assert "отягчающ" in evaluation.explanation.lower()


def test_r004_not_applicable_without_ik(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=45))["R-004"]
    assert evaluation.status.value == "PASS"


# -- R-005: совокупный предел ч. 3 ст. 62 --------------------------------------


def test_r005_combined_limit_fail(norm_store: NormStore) -> None:
    evaluation = run(
        norm_store, make_case(term=25, special=True, mitigating=("61.1.и",))
    )["R-005"]
    assert evaluation.status.value == "FAIL"
    assert evaluation.numbers["limit_months"] == 20


def test_r005_combined_limit_pass_boundary(norm_store: NormStore) -> None:
    evaluation = run(
        norm_store, make_case(term=20, special=True, mitigating=("61.1.и",))
    )["R-005"]
    assert evaluation.status.value == "PASS"


def test_r005_not_applicable_without_both_conditions(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=25, special=True))["R-005"]
    assert evaluation.status.value == "PASS"
    assert "не применимо" in evaluation.headline


# -- R-006: условное осуждение --------------------------------------------------


def test_r006_suspended_over_8_years_fail(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=100, suspended=True))["R-006"]
    assert evaluation.status.value == "FAIL"


def test_r006_suspended_pass(norm_store: NormStore) -> None:
    assert (
        run(norm_store, make_case(term=96, suspended=True))["R-006"].status.value
        == "PASS"
    )


def test_r006_not_applicable(norm_store: NormStore) -> None:
    assert (
        run(norm_store, make_case(term=100, suspended=False))["R-006"].status.value
        == "PASS"
    )


def test_r006_unknown_without_suspended_fact(norm_store: NormStore) -> None:
    assert (
        run(norm_store, make_case(term=36, suspended=None))["R-006"].status.value
        == "UNKNOWN"
    )


# -- provenance в оценках -------------------------------------------------------


def test_conclusive_evaluation_cites_norm_version(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=66))["R-001"]
    refs = {(n.norm_id, n.version_id) for n in evaluation.norms_used}
    assert ("uk-rf:art-158-part-2", "v-current") in refs


def test_evaluation_records_used_fact_ids(norm_store: NormStore) -> None:
    case = make_case(term=66)
    case.offense.qualifications.clear()
    case.offense.qualifications.append(Qualification(article=158, part=2))
    evaluation = run(norm_store, case)["R-001"]
    assert evaluation.facts_used or evaluation.missing
