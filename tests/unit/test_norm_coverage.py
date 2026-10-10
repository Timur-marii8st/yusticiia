"""Аудит редакционного покрытия норм."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from second_opinion.domain.enums import PunishmentType
from second_opinion.domain.norms import (
    PLENUM_CODE,
    LegalNorm,
    NormVersion,
    SanctionSpec,
)
from second_opinion.legal_sources.coverage import (
    analyze_norm,
    analyze_store,
    format_report,
    history_caveats,
    needs_legal_review,
)
from second_opinion.legal_sources.store import NormStore
from second_opinion.rule_engine.rules import REFERENCED_NORM_IDS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "data" / "fixtures"
TODAY = date(2026, 9, 8)


def _norm(norm_id: str = "uk-rf:art-1", article: int = 1) -> LegalNorm:
    return LegalNorm(
        norm_id=norm_id, code="УК РФ", article=article, part=1, title="Тест"
    )


def _version(
    norm_id: str,
    version_id: str,
    effective_from: date,
    effective_to: date | None = None,
    *,
    sanctions: bool = False,
) -> NormVersion:
    text = f"текст {version_id}"
    return NormVersion(
        norm_id=norm_id,
        version_id=version_id,
        text=text,
        sanctions=(
            [SanctionSpec(punishment_type=PunishmentType.IMPRISONMENT, max_months=36)]
            if sanctions
            else []
        ),
        effective_from=effective_from,
        effective_to=effective_to,
        source_document="Тестовый источник",
        retrieved_at=date(2026, 9, 1),
        sha256=NormVersion.compute_sha256(text),
    )



def test_contiguous_versions_have_no_gaps() -> None:
    norm = _norm()
    coverage = analyze_norm(
        norm,
        [
            _version(norm.norm_id, "v1", date(1996, 7, 1), date(2003, 12, 15)),
            _version(norm.norm_id, "v2", date(2003, 12, 16)),
        ],
    )
    assert coverage.gaps == ()
    assert coverage.version_count == 2
    assert coverage.covered_from == date(1996, 7, 1)
    assert coverage.covered_to is None


def test_gap_between_versions_is_detected() -> None:
    """NormStore ловит пересечения, но не дыры — их видно только здесь."""
    norm = _norm()
    coverage = analyze_norm(
        norm,
        [
            _version(norm.norm_id, "v1", date(1996, 7, 1), date(2003, 12, 15)),
            _version(norm.norm_id, "v2", date(2010, 1, 1)),
        ],
    )
    assert coverage.gaps == ((date(2003, 12, 16), date(2009, 12, 31)),)
    assert needs_legal_review([coverage], TODAY) == [coverage]



def test_single_open_version_over_threshold_is_flagged() -> None:
    norm = _norm()
    coverage = analyze_norm(
        norm, [_version(norm.norm_id, "v1", date(1996, 7, 1), sanctions=True)]
    )
    assert coverage.history_unrecorded(TODAY) is True
    assert coverage.rule_relevant is True


def test_recent_single_version_is_not_flagged() -> None:
    norm = _norm()
    coverage = analyze_norm(
        norm, [_version(norm.norm_id, "v1", date(2024, 1, 1), sanctions=True)]
    )
    assert coverage.history_unrecorded(TODAY) is False


def test_multiple_versions_are_not_flagged() -> None:
    norm = _norm()
    coverage = analyze_norm(
        norm,
        [
            _version(norm.norm_id, "v1", date(1996, 7, 1), date(2003, 12, 15), sanctions=True),
            _version(norm.norm_id, "v2", date(2003, 12, 16), sanctions=True),
        ],
    )
    assert coverage.history_unrecorded(TODAY) is False
    assert needs_legal_review([coverage], TODAY) == []


def test_closed_interval_is_not_flagged() -> None:
    """Закрытый интервал не утверждает «норма не менялась до сих пор»."""
    norm = _norm()
    coverage = analyze_norm(
        norm,
        [_version(norm.norm_id, "v1", date(1996, 7, 1), date(2003, 12, 15), sanctions=True)],
    )
    assert coverage.history_unrecorded(TODAY) is False


def test_reference_norm_without_sanctions_is_not_flagged() -> None:
    """Справочная норма вне движка правил сверки не требует."""
    norm = _norm(norm_id="uk-rf:art-unused", article=999)
    coverage = analyze_norm(norm, [_version(norm.norm_id, "v1", date(1996, 7, 1))])
    assert coverage.rule_relevant is False
    assert needs_legal_review([coverage], TODAY) == []



def test_referenced_norm_ids_match_rule_engine_source() -> None:
    """Список не должен разъезжаться со ссылками в коде движка."""
    found: set[str] = set()
    for path in (PROJECT_ROOT / "src/second_opinion/rule_engine").rglob("*.py"):
        found |= set(
            re.findall(r'"((?:uk|upk|ppvs)-rf:[a-z0-9:-]+)"', path.read_text(encoding="utf-8"))
        )
    assert found == set(REFERENCED_NORM_IDS)


def test_every_referenced_norm_exists_in_fixtures() -> None:
    """Правило не должно ссылаться на отсутствующую норму."""
    store = NormStore.from_directory(FIXTURES / "norms")
    known = {n.norm_id for n in store.list_norms()}
    assert known >= REFERENCED_NORM_IDS


def test_norms_used_by_rules_are_marked_relevant() -> None:
    store = NormStore.from_directory(FIXTURES / "norms")
    by_id = {c.norm_id: c for c in analyze_store(store)}
    for norm_id in REFERENCED_NORM_IDS:
        assert by_id[norm_id].rule_relevant is True, norm_id


def test_draft_only_norms_are_not_reported_as_active() -> None:
    """Норма, упомянутая только в незарегистрированном правиле, не
    называется в отчёте «используется движком правил».

    Регрессия на опережение: у главы 11 УК РФ (``uk-rf:art-75`` и
    остальные) есть код в ``rule_engine/rules/release.py``, но самого
    правила нет в ``DEFAULT_RULES`` — оно ждёт юридической сверки норм.
    Отчёт о покрытии обязан различать «код существует» и «анализ это
    читает»: смешать их значило бы сказать юристу, что черновик уже
    работает в выводе для судьи.
    """
    from second_opinion.rule_engine.rules import DRAFT_ONLY_NORM_IDS

    assert DRAFT_ONLY_NORM_IDS, "нечего проверять — черновичных норм не осталось"

    store = NormStore.from_directory(FIXTURES / "norms")
    by_id = {c.norm_id: c for c in analyze_store(store)}
    report = format_report(analyze_store(store))

    flagged = 0
    for norm_id in DRAFT_ONLY_NORM_IDS:
        coverage = by_id[norm_id]
        assert coverage.draft_rule_only is True, norm_id
        lines = [line for line in report.splitlines() if line.startswith(f"  {coverage.ref} —")]
        flagged += len(lines)
        for line in lines:
            assert "упомянута в черновике правила, не активна" in line
            assert "используется движком правил" not in line
    assert flagged, "ни одна черновичная норма не попала в отчёт — проверка формулировки не выполнена"



def test_report_declines_years_correctly() -> None:
    norm = _norm()
    report = format_report(
        [analyze_norm(norm, [_version(norm.norm_id, "v1", date(2002, 7, 1), sanctions=True)])],
        TODAY,
    )
    assert "24 года" in report
    assert "24 лет" not in report


def test_report_states_when_nothing_needs_review() -> None:
    norm = _norm()
    coverage = analyze_norm(
        norm, [_version(norm.norm_id, "v1", date(2025, 1, 1), sanctions=True)]
    )
    assert "не обнаружено" in format_report([coverage], TODAY)


def test_plenum_points_name_their_number_in_the_record() -> None:
    """Пункт постановления, названный в идентификаторе, назван и в поле.

    Регрессия: у `ppvs-rf:n58-p1` поле `part` осталось пустым, и ссылка
    печаталась как «ППВС № 58» — то есть как отсылка ко всему
    постановлению сразу, тогда как разъяснение взято из одного пункта.
    Проверка сравнивает запись саму с собой и ничего не утверждает о
    праве: номер пункта берётся из идентификатора, который юрист сверял
    вместе с текстом.
    """
    store = NormStore.from_directory(FIXTURES / "norms")
    point_in_id = re.compile(r"-p(\d+)$")

    mismatched = []
    plenum = [n for n in store.list_norms() if n.code.startswith(PLENUM_CODE)]
    assert plenum, "в корпусе не нашлось ни одного постановления Пленума"

    for norm in plenum:
        match = point_in_id.search(norm.norm_id)
        if not match:
            continue
        if norm.part != int(match.group(1)):
            mismatched.append(f"{norm.norm_id}: part={norm.part}, ссылка «{norm.ref}»")

    assert not mismatched, "пункт назван в идентификаторе, но не в записи: " + "; ".join(
        mismatched
    )


def test_single_old_edition_gives_unrecorded_caveat_on_case_date() -> None:
    norm = _norm()
    versions = [_version(norm.norm_id, "v1", date(1996, 7, 1))]
    caveats = history_caveats(norm, versions, "v1", date(2025, 3, 15))
    assert [(c.kind, c.since) for c in caveats] == [("unrecorded", date(1996, 7, 1))]


def test_confirmed_history_removes_unrecorded_caveat() -> None:
    norm = _norm().model_copy(update={"amendments_checked_through": date(2026, 9, 3)})
    versions = [_version(norm.norm_id, "v1", date(1996, 7, 1))]
    assert history_caveats(norm, versions, "v1", date(2025, 3, 15)) == []


def test_case_date_close_after_edition_start_gives_boundary_caveat() -> None:
    norm = _norm()
    versions = [
        _version(norm.norm_id, "v1", date(1996, 7, 1), date(2024, 12, 31)),
        _version(norm.norm_id, "v2", date(2025, 1, 1)),
    ]
    caveats = history_caveats(norm, versions, "v2", date(2025, 2, 1))
    assert [(c.kind, c.days_since) for c in caveats] == [("near_boundary", 31)]


def test_case_date_far_from_boundary_and_first_edition_give_no_boundary_caveat() -> None:
    norm = _norm()
    versions = [
        _version(norm.norm_id, "v1", date(1996, 7, 1), date(2024, 12, 31)),
        _version(norm.norm_id, "v2", date(2025, 1, 1)),
    ]
    assert history_caveats(norm, versions, "v2", date(2025, 9, 1)) == []
    assert history_caveats(norm, versions, "v1", date(1996, 7, 20)) == []
