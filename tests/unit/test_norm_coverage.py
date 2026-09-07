"""Аудит редакционного покрытия норм."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from second_opinion.domain.enums import PunishmentType
from second_opinion.domain.norms import LegalNorm, NormVersion, SanctionSpec
from second_opinion.legal_sources.coverage import (
    analyze_norm,
    analyze_store,
    format_report,
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


# --- разрывы в покрытии -----------------------------------------------------


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


# --- незафиксированная история ----------------------------------------------


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


# --- связь с движком правил -------------------------------------------------


def test_referenced_norm_ids_match_rule_engine_source() -> None:
    """Список не должен разъезжаться со ссылками в коде движка."""
    found: set[str] = set()
    for path in (PROJECT_ROOT / "src/second_opinion/rule_engine").glob("*.py"):
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


# --- отчёт ------------------------------------------------------------------


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
