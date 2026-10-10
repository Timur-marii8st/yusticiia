"""Правка фикстур: сверка истории и добавление редакций."""

from __future__ import annotations

import json
from datetime import date

import pytest

from second_opinion.legal_sources.coverage import analyze_store, needs_legal_review
from second_opinion.legal_sources.editing import NormEditError, add_version, confirm_history
from second_opinion.legal_sources.sealing import UNSEALED, check, seal
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


def test_reversed_interval_leaves_corpus_and_manifest_unchanged(tmp_path) -> None:
    directory = _corpus(tmp_path)
    before = {p.name: p.read_bytes() for p in directory.glob("*.json")}

    with pytest.raises(NormEditError, match="effective_to раньше effective_from"):
        add_version(
            directory,
            "uk-rf:art-158-part-1",
            version_id="v-invalid",
            text="новый текст",
            effective_from=date(2027, 1, 1),
            effective_to=date(2026, 1, 1),
        )

    assert {p.name: p.read_bytes() for p in directory.glob("*.json")} == before
    assert check(directory).ok
    current = NormStore.from_directory(directory).get_norm(
        "uk-rf:art-158-part-1", date(2027, 1, 1)
    )
    assert current.version_id == "v-1996"
    assert current.effective_to is None


def test_overlap_with_closed_history_is_refused_before_writing(tmp_path) -> None:
    directory = _corpus(tmp_path)
    add_version(
        directory,
        "uk-rf:art-158-part-1",
        version_id="v-2003",
        text="текст 2003 года",
        effective_from=date(2003, 1, 1),
        effective_to=date(2010, 12, 31),
    )
    seal(directory)
    before = {p.name: p.read_bytes() for p in directory.glob("*.json")}

    with pytest.raises(NormEditError, match="пересекаются"):
        add_version(
            directory,
            "uk-rf:art-158-part-1",
            version_id="v-overlap",
            text="пересекающийся текст",
            effective_from=date(2009, 1, 1),
            effective_to=date(2011, 12, 31),
        )

    assert {p.name: p.read_bytes() for p in directory.glob("*.json")} == before
    assert check(directory).ok


def test_multiple_edits_can_be_validated_before_sealing(tmp_path) -> None:
    """Проверка candidate не требует преждевременно опечатывать черновики."""
    directory = _corpus(tmp_path)
    for year in (2003, 2011):
        add_version(
            directory,
            "uk-rf:art-158-part-1",
            version_id=f"v-{year}",
            text=f"текст {year} года",
            effective_from=date(year, 1, 1),
        )

    assert not check(directory).ok
    seal(directory)
    store = NormStore.from_directory(directory)
    assert store.get_norm("uk-rf:art-158-part-1", date(2004, 1, 1)).version_id == "v-2003"
    assert store.get_norm("uk-rf:art-158-part-1", date(2012, 1, 1)).version_id == "v-2011"


def test_cli_explains_missing_text_file(tmp_path, monkeypatch, capsys) -> None:
    """--text-file готовит человек; сообщение должно это объяснять."""
    from second_opinion.cli import main
    from second_opinion.config import reset_config_cache

    monkeypatch.setenv("SO_FIXTURES_DIR", str(tmp_path))
    reset_config_cache()

    code = main(
        [
            "norms-add-version",
            "--norm-id", "uk-rf:art-158-part-1",
            "--text-file", "нет-такого.txt",
            "--effective-from", "2004-01-01",
        ]
    )
    assert code == 2
    assert "готовите вы" in capsys.readouterr().err


def test_cli_confirm_history_writes_an_audit_entry(tmp_path, monkeypatch) -> None:
    """confirm-history/add-version раньше не писали в аудит-журнал вообще —

    правка боевого корпуса норм не оставляла следа, кто и когда её
    выполнил. Пишем в изолированный SO_DATA_DIR, чтобы не задеть
    настоящий data/store/audit.jsonl репозитория.
    """
    from second_opinion.audit.trail import CLI_ACTOR
    from second_opinion.cli import main
    from second_opinion.config import load_config, reset_config_cache

    norms_dir = tmp_path / "fixtures" / "norms"
    norms_dir.mkdir(parents=True)
    _corpus(norms_dir)
    monkeypatch.setenv("SO_FIXTURES_DIR", str(tmp_path / "fixtures"))
    monkeypatch.setenv("SO_DATA_DIR", str(tmp_path / "store"))
    reset_config_cache()

    code = main(
        [
            "norms-confirm-history",
            "--norm-id", "uk-rf:art-158-part-1",
            "--checked-through", "2026-09-11",
            "--source", "КонсультантПлюс",
        ]
    )
    assert code == 0

    events = [
        json.loads(line)
        for line in load_config().audit_path.read_text(encoding="utf-8").splitlines()
    ]
    entry = next(e for e in events if e["operation"] == "norm_confirm_history")
    assert entry["actor"] == CLI_ACTOR
    assert entry["input_hash"] == "uk-rf:art-158-part-1"
    assert entry["details"]["source"] == "КонсультантПлюс"


def test_cli_add_version_writes_an_audit_entry(tmp_path, monkeypatch) -> None:
    from second_opinion.audit.trail import CLI_ACTOR
    from second_opinion.cli import main
    from second_opinion.config import load_config, reset_config_cache

    norms_dir = tmp_path / "fixtures" / "norms"
    norms_dir.mkdir(parents=True)
    _corpus(norms_dir)
    monkeypatch.setenv("SO_FIXTURES_DIR", str(tmp_path / "fixtures"))
    monkeypatch.setenv("SO_DATA_DIR", str(tmp_path / "store"))
    reset_config_cache()

    text_file = tmp_path / "ред-2004.txt"
    text_file.write_text("текст 2004 года", encoding="utf-8")

    code = main(
        [
            "norms-add-version",
            "--norm-id", "uk-rf:art-158-part-1",
            "--text-file", str(text_file),
            "--effective-from", "2004-01-01",
        ]
    )
    assert code == 0

    events = [
        json.loads(line)
        for line in load_config().audit_path.read_text(encoding="utf-8").splitlines()
    ]
    entry = next(e for e in events if e["operation"] == "norm_add_version")
    assert entry["actor"] == CLI_ACTOR
    assert entry["input_hash"] == "uk-rf:art-158-part-1"
    assert entry["output_hash"] == "v-2004"
    assert entry["details"]["effective_from"] == "2004-01-01"


def test_confirm_history_records_source_edition_on_open_version(tmp_path) -> None:
    """Сверка ставит точку отсчёта: с чем сравнивать при проверке обновлений."""
    directory = _corpus(tmp_path)

    confirm_history(
        directory,
        "uk-rf:art-158-part-1",
        checked_through=TODAY,
        source="КонсультантПлюс",
        source_edition="pravo:102041891:365",
    )

    payload = json.loads(next(directory.glob("*.json")).read_text(encoding="utf-8"))
    norm = next(n for n in payload["norms"] if n["norm_id"] == "uk-rf:art-158-part-1")
    open_version = next(v for v in norm["versions"] if v["effective_to"] in (None, ""))
    assert open_version["source_edition"] == "pravo:102041891:365"


def test_confirm_history_without_stamp_leaves_versions_untouched(tmp_path) -> None:
    """Отметка необязательна: сверка истории и точка отсчёта — разные факты."""
    directory = _corpus(tmp_path)

    confirm_history(
        directory,
        "uk-rf:art-158-part-1",
        checked_through=TODAY,
        source="КонсультантПлюс",
    )

    payload = json.loads(next(directory.glob("*.json")).read_text(encoding="utf-8"))
    norm = next(n for n in payload["norms"] if n["norm_id"] == "uk-rf:art-158-part-1")
    assert all(not v.get("source_edition") for v in norm["versions"])
