"""Реальные приговоры как проверка данных норм и правил.

Приговор, вынесенный судом, по умолчанию не нарушает пределов санкции.
Если правило движка выдаёт на нём FAIL, ошибка либо в данных нормы (предел
внесён неверно), либо в самом правиле, либо в разметке дела: все три
случая требуют разбора, а не оправдания. Чем больше реальных дел в корпусе,
тем сильнее проверка.
"""

from __future__ import annotations

import pytest

from second_opinion.api.deps import load_cases
from second_opinion.domain.cases import ComparableCase
from second_opinion.legal_sources.store import NormStore
from second_opinion.rule_engine.engine import RuleEngine
from tests.conftest import FIXTURES_DIR
from tests.unit.test_rule_engine import make_case

REAL_CASES = [case for case in load_cases(FIXTURES_DIR) if not case.synthetic]


@pytest.mark.parametrize("case", REAL_CASES, ids=lambda case: case.case_id)
def test_real_sentence_does_not_fail_any_rule(case: ComparableCase) -> None:
    facts = make_case(
        article=case.article,
        part=case.part,
        term=case.term_months,
        stage=case.stage,
        special=case.special_procedure,
        plea=case.guilty_plea,
        mitigating=tuple(case.mitigating_codes),
        aggravating=tuple(case.aggravating_codes),
        suspended=case.suspended,
        punishment_type=case.punishment_type,
    )
    store = NormStore.from_directory(FIXTURES_DIR / "norms")
    evaluations = RuleEngine().evaluate(facts, store, case.date)
    failed = [(e.rule_id, e.explanation) for e in evaluations if e.status.value == "FAIL"]
    assert not failed, f"{case.case_id} ({case.source_url}): {failed}"


def test_corpus_has_real_cases_to_check() -> None:
    assert len(REAL_CASES) >= 5


def test_the_check_can_fail_on_an_excessive_term() -> None:
    case = next(c for c in REAL_CASES if c.punishment_type == "imprisonment")
    facts = make_case(article=case.article, part=case.part, term=999, special=False)
    store = NormStore.from_directory(FIXTURES_DIR / "norms")
    evaluations = RuleEngine().evaluate(facts, store, case.date)
    assert any(e.status.value == "FAIL" for e in evaluations)
