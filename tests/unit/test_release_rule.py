"""R-D01: основания освобождения от уголовной ответственности (гл. 11 УК РФ, черновик).

Правило написано для ФТ-45 (п. 2.5 Положения), но **не зарегистрировано**
в `DEFAULT_RULES` движка — нормы, на которые оно ссылается, ещё не сверены
юристом (`verification_status: draft`), а И-10 запрещает правило без
верифицированной нормы-основания. Первый тест в файле проверяет именно
это разделение: код готов, но физически не может попасть в отчёт судье.
"""

from __future__ import annotations

from datetime import date

from second_opinion.domain.enums import FactType
from second_opinion.domain.facts import CaseFacts, MitigatingFactor, Qualification
from second_opinion.domain.rules import RuleStatus
from second_opinion.legal_sources.store import NormStore
from second_opinion.rule_engine.context import RuleContext
from second_opinion.rule_engine.engine import DEFAULT_RULES, RuleEngine
from second_opinion.rule_engine.rules import ReleaseFromResponsibilityRule
from tests.unit.test_rule_engine import _register_fact

APPLICABLE_AT = date(2025, 1, 1)


def _case(
    *,
    article: int = 158,
    part: int | None = 1,
    prior_convictions: bool | None = False,
    mitigating: tuple[str, ...] = (),
) -> CaseFacts:
    case = CaseFacts()
    case.offense.qualifications.append(Qualification(article=article, part=part))
    _register_fact(
        case, FactType.QUALIFICATION, {"code": "УК РФ", "article": article, "part": part}
    )
    case.defendant.prior_convictions = prior_convictions
    if prior_convictions is not None:
        _register_fact(case, FactType.PRIOR_CONVICTIONS, prior_convictions)
    for code in mitigating:
        case.mitigating.append(
            MitigatingFactor(code=code, title=code, fact_ids=[f"fact-{code}"])
        )
    return case


def run(norm_store: NormStore, case: CaseFacts):
    context = RuleContext(case_facts=case, applicable_at=APPLICABLE_AT, norm_store=norm_store)
    return ReleaseFromResponsibilityRule().evaluate(context)


def test_rule_is_written_but_not_registered() -> None:
    """Готовый код, физически недостижимый из обычного анализа."""
    assert ReleaseFromResponsibilityRule not in DEFAULT_RULES
    ids = {rule.rule_id for rule in RuleEngine()._rules}
    assert "R-D01" not in ids


def test_serious_crime_is_not_applicable(norm_store: NormStore) -> None:
    # ст. 228 ч. 2 УК РФ: максимум санкции 120 мес. — тяжкое преступление,
    # выше границы 60 мес. (небольшой/средней тяжести), на которой
    # заканчивается действие ст. 75, 76, 76.2.
    evaluation = run(norm_store, _case(article=228, part=2))
    assert evaluation.status is RuleStatus.PASS
    assert "не применимо" in evaluation.headline


def test_prior_conviction_excludes_the_chapter(norm_store: NormStore) -> None:
    case = _case(article=158, part=1, prior_convictions=True)
    evaluation = run(norm_store, case)
    assert evaluation.status is RuleStatus.PASS
    assert "впервые" in evaluation.explanation


def test_unknown_prior_convictions_is_unknown(norm_store: NormStore) -> None:
    case = _case(article=158, part=1, prior_convictions=None)
    evaluation = run(norm_store, case)
    assert evaluation.status is RuleStatus.UNKNOWN
    assert "впервые" in evaluation.missing[0]


def test_missing_restitution_is_unknown_not_pass(norm_store: NormStore) -> None:
    """Невозмещённый вред и невзвлечённый — разные вещи.

    Отсутствие сигнала в тексте — не то же самое, что «вред не заглажен»:
    паттерновый извлекатель мог его просто не найти. Утверждать «не
    применимо» здесь значило бы делать вывод из молчания.
    """
    case = _case(article=158, part=1, prior_convictions=False)
    evaluation = run(norm_store, case)
    assert evaluation.status is RuleStatus.UNKNOWN
    assert "возмещение" in evaluation.missing[0] or "заглаживание" in evaluation.missing[0]


def test_restitution_alone_suggests_judicial_fine(norm_store: NormStore) -> None:
    case = _case(
        article=158, part=1, prior_convictions=False, mitigating=("61.1.к",)
    )
    evaluation = run(norm_store, case)
    assert evaluation.status is RuleStatus.WARNING
    assert "76.2" in evaluation.explanation
    # Без явки с повинной ст. 75 (деятельное раскаяние) не предлагается:
    # для неё нужен более сильный набор признаков.
    assert "ст. 75" not in evaluation.explanation


def test_surrender_and_restitution_suggest_both_bases(norm_store: NormStore) -> None:
    case = _case(
        article=158,
        part=1,
        prior_convictions=False,
        mitigating=("61.1.и", "61.1.к"),
    )
    evaluation = run(norm_store, case)
    assert evaluation.status is RuleStatus.WARNING
    assert "76.2" in evaluation.explanation
    assert "75" in evaluation.explanation


def test_warning_never_claims_the_basis_is_established(norm_store: NormStore) -> None:
    """Формула результата — «проверьте», а не «применимо». Инвариант из
    docs/CONCEPT.md: правило не заменяет решение суда."""
    case = _case(
        article=158, part=1, prior_convictions=False, mitigating=("61.1.к",)
    )
    evaluation = run(norm_store, case)
    assert evaluation.status is RuleStatus.WARNING
    forbidden = ("основание установлено", "подлежит освобождению", "прекратите")
    for phrase in forbidden:
        assert phrase not in evaluation.explanation.lower()


def test_missing_qualification_is_unknown(norm_store: NormStore) -> None:
    evaluation = run(norm_store, CaseFacts())
    assert evaluation.status is RuleStatus.UNKNOWN
