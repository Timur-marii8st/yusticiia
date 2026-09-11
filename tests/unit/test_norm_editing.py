"""Правка фикстур: сверка истории и добавление редакций."""

from __future__ import annotations

import json
from datetime import date

import pytest

from second_opinion.legal_sources.coverage import analyze_store, needs_legal_review
from second_opinion.legal_sources.editing import NormEditError, add_version, confirm_history
from second_opinion.legal_sources.sealing import UNSEALED, seal
from second_opinion.legal_sources.store import NormStore

TODAY = date(2026, 9, 11)


def _corpus(tmp_path):
    payload = {
        "code": "УК РФ",
        "source_document": "Уголовный кодекс Российской Федерации",
        "norms": [
            {
                "norm_id": "uk-rf:art-158-part-1",
                "article": 158,
                "part": 1,
                "title": "Кража",
                "versions": [
                    {
                        "norm_id": "uk-rf:art-158-part-1",
                        "version_id": "v-1996",
                        "text": "текст 1996 года",
                        "sanctions": [
                            {"punishment_type": "imprisonment", "max_months": 24}
                        ],
                        "effective_from": "1996-07-01",
                        "effective_to": None,
                        "retrieved_at": "2026-09-01",
                        "sha256": UNSEALED,
                        "verification_status": "verified",
                    }
                ],
            }
        ],
    }
    (tmp_path / "uk.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    seal(tmp_path)
    return tmp_path


# --- сверка истории ----------------------------------------------------------


def test_confirmed_history_leaves_review_list(tmp_path) -> None:
    """Подтверждённая сверка превращает допущение в факт."""
    directory = _corpus(tmp_path)
    assert needs_legal_review(analyze_store(NormStore.from_directory(directory)), TODAY)

    confirm_history(
        directory,
        "uk-rf:art-158-part-1",
        checked_through=date(2026, 9, 11),
        source="КонсультантПлюс",
    )
    seal(directory)

    coverage = analyze_store(NormStore.from_directory(directory))
    assert needs_legal_review(coverage, TODAY) == []
    assert coverage[0].history_verified is True


def test_confirm_requires_a_source(tmp_path) -> None:
    """Утверждение без источника ничем не лучше допущения."""
    with pytest.raises(NormEditError, match="источник"):
        confirm_history(
            _corpus(tmp_path), "uk-rf:art-158-part-1", checked_through=TODAY, source="   "
        )


def test_confirm_reports_unknown_norm(tmp_path) -> None:
    with pytest.raises(NormEditError, match="не найдена"):
        confirm_history(_corpus(tmp_path), "нет-такой", checked_through=TODAY, source="КП")


# --- добавление редакции -----------------------------------------------------


def test_added_version_closes_previous_interval(tmp_path) -> None:
    directory = _corpus(tmp_path)
    add_version(
        directory,
        "uk-rf:art-158-part-1",
        version_id="v-2003",
        text="текст 2003 года",
        effective_from=date(2003, 12, 16),
    )
    seal(directory)
    store = NormStore.from_directory(directory)

    assert store.get_norm("uk-rf:art-158-part-1", date(2000, 1, 1)).version_id == "v-1996"
    assert store.get_norm("uk-rf:art-158-part-1", date(2010, 1, 1)).version_id == "v-2003"
    assert store.versions("uk-rf:art-158-part-1")[0].effective_to == date(2003, 12, 15)


def test_added_version_is_draft_without_sanctions(tmp_path) -> None:
    """Пределы санкции из текста не выводятся — их вносит юрист."""
    directory = _corpus(tmp_path)
    add_version(
        directory,
        "uk-rf:art-158-part-1",
        version_id="v-2003",
        text="текст 2003 года",
        effective_from=date(2003, 12, 16),
    )
    added = json.loads((tmp_path / "uk.json").read_text(encoding="utf-8"))["norms"][0][
        "versions"
    ][-1]
    assert added["verification_status"] == "draft"
    assert added["sanctions"] == []
    assert added["sha256"] == UNSEALED, "опечатывание — отдельное действие"


def test_duplicate_version_id_is_refused(tmp_path) -> None:
    with pytest.raises(NormEditError, match="уже есть"):
        add_version(
            _corpus(tmp_path),
            "uk-rf:art-158-part-1",
            version_id="v-1996",
            text="повтор",
            effective_from=date(2003, 12, 16),
        )


def test_version_starting_before_previous_is_refused(tmp_path) -> None:
    """Интервалы не должны пересекаться — NormStore такое отклонит."""
    with pytest.raises(NormEditError, match="действует с"):
        add_version(
            _corpus(tmp_path),
            "uk-rf:art-158-part-1",
            version_id="v-1990",
            text="раньше предыдущей",
            effective_from=date(1990, 1, 1),
        )


def test_empty_text_is_refused(tmp_path) -> None:
    with pytest.raises(NormEditError, match="пуст"):
        add_version(
            _corpus(tmp_path),
            "uk-rf:art-158-part-1",
            version_id="v-2003",
            text="   ",
            effective_from=date(2003, 12, 16),
        )
