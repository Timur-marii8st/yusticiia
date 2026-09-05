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
    age: int | None = None,
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
    if age is not None:
        case.defendant.age = age
        _register_fact(case, FactType.DEFENDANT_AGE, age)
    return case


def run(norm_store: NormStore, case: CaseFacts) -> dict[str, RuleEvaluation]:
    engine = RuleEngine()
    evaluations = engine.evaluate(case, norm_store, APPLICABLE_AT)
    assert len(evaluations) == 8
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


# -- R-010: неоконченное в особом порядке (п. 14 ППВС № 60) ---------------------


def test_r010_attempt_special_fail_over_combined_limit(norm_store: NormStore) -> None:
    # ст. 158 ч. 2: макс 60; покушение в особом порядке ≤ 60 × 3/4 × 2/3 = 30
    evaluation = run(
        norm_store, make_case(stage="attempt", special=True, term=31)
    )["R-010"]
    assert evaluation.status.value == "FAIL"
    assert evaluation.numbers["limit_months"] == 30


def test_r010_attempt_special_pass_boundary(norm_store: NormStore) -> None:
    evaluation = run(
        norm_store, make_case(stage="attempt", special=True, term=30)
    )["R-010"]
    assert evaluation.status.value == "PASS"


def test_r010_preparation_special_limit(norm_store: NormStore) -> None:
    # приготовление в особом порядке ≤ 60 × 1/2 × 2/3 = 20
    ok = run(norm_store, make_case(stage="preparation", special=True, term=20))["R-010"]
    assert ok.status.value == "PASS"
    bad = run(norm_store, make_case(stage="preparation", special=True, term=21))["R-010"]
    assert bad.status.value == "FAIL"
    assert bad.numbers["limit_months"] == 20


def test_r010_not_applicable_without_special(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(stage="attempt", special=False))["R-010"]
    assert evaluation.status.value == "PASS"
    assert "не применимо" in evaluation.headline


def test_r010_not_applicable_for_completed(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(stage="completed", special=True))["R-010"]
    assert evaluation.status.value == "PASS"


def test_r010_unknown_when_procedure_missing(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(stage="attempt", special=None))["R-010"]
    assert evaluation.status.value == "UNKNOWN"
    assert evaluation.missing


def test_r010_cites_norm_versions(norm_store: NormStore) -> None:
    evaluation = run(
        norm_store, make_case(stage="attempt", special=True, term=31)
    )["R-010"]
    refs = {(n.norm_id, n.version_id) for n in evaluation.norms_used}
    assert ("uk-rf:art-66", "v-current") in refs
    assert ("uk-rf:art-62", "v-2009") in refs
    assert ("ppvs-rf:n60-p14", "v-2021") in refs


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
    assert ("uk-rf:art-158-part-2", "v-2003") in refs


def test_evaluation_records_used_fact_ids(norm_store: NormStore) -> None:
    case = make_case(term=66)
    case.offense.qualifications.clear()
    case.offense.qualifications.append(Qualification(article=158, part=2))
    evaluation = run(norm_store, case)["R-001"]
    assert evaluation.facts_used or evaluation.missing

# -- R-007: особый порядок и несовершеннолетие ---------------------------------


def test_r007_minor_with_special_procedure_fails(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(special=True, age=16))["R-007"]
    assert evaluation.status.value == "FAIL"
    assert "420" in evaluation.explanation
    assert evaluation.numbers == {"age": 16.0}


def test_r007_adult_special_procedure_passes(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(special=True, age=35))["R-007"]
    assert evaluation.status.value == "PASS"


def test_r007_no_special_procedure_not_applicable(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(special=False, age=16))["R-007"]
    assert evaluation.status.value == "PASS"
    assert "не применимо" in evaluation.headline


def test_r007_missing_age_is_unknown(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(special=True))["R-007"]
    assert evaluation.status.value == "UNKNOWN"
    assert evaluation.missing


def test_r007_cites_upk_norm_version(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(special=True, age=17))["R-007"]
    refs = {(n.norm_id, n.version_id) for n in evaluation.norms_used}
    assert ("upk-rf:art-420", "v-current") in refs
    assert ("ppvs-rf:n60-p7", "v-2021") in refs


# -- R-008 удалено (см. CHANGELOG 0.22): ч. 2 ст. 63 УК РФ не устанавливает
# специального соотношения рецидива и пп. «и»/«к»; корректное поведение
# (рецидив блокирует ч. 1 ст. 62) уже покрыто тестом R-004
# test_r004_not_applicable_with_aggravating. ------------------------------------


# -- R-009: полнота данных о наказании -------------------------------------------


def test_r009_complete_pair_passes(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=18))["R-009"]
    assert evaluation.status.value == "PASS"
    assert "полные" in evaluation.headline
    assert not evaluation.missing
    assert evaluation.norms_used == []  # структурная проверка, нормы не толкует


def test_r009_no_sentence_data_passes_as_not_applicable(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=None, punishment_type=None))["R-009"]
    assert evaluation.status.value == "PASS"
    assert "не применимо" in evaluation.headline


def test_r009_term_without_type_warns(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=18, punishment_type=None))["R-009"]
    assert evaluation.status.value == "WARNING"
    assert evaluation.missing == ["вид назначенного наказания"]
    assert evaluation.facts_used  # присутствующий факт срока указан


def test_r009_type_without_term_warns(norm_store: NormStore) -> None:
    evaluation = run(norm_store, make_case(term=None))["R-009"]
    assert evaluation.status.value == "WARNING"
    assert evaluation.missing == ["размер назначенного наказания"]
