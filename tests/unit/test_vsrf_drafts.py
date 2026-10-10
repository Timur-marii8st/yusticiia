"""Черновики Пленума № 58 и Обзора ВС РФ: разбор PDF, корпус, поиск."""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import date
from pathlib import Path

from second_opinion.domain.enums import VerificationStatus
from second_opinion.domain.norms import LegalNorm, NormVersion
from second_opinion.legal_rag.search import LegalRag
from second_opinion.legal_sources.store import NormStore

ROOT = Path(__file__).resolve().parents[2]
NORMS = ROOT / "data" / "fixtures" / "norms"
PLENUM = NORMS / "plenum_58_2015_draft.json"
REVIEW = NORMS / "review_sentencing_2024_draft.json"

_spec = importlib.util.spec_from_file_location("fetch_vsrf", ROOT / "scripts" / "fetch_vsrf_document.py")
fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch)


def test_points_with_superscript_and_excluded_numbers_are_split() -> None:
    paras = [
        "Преамбула.",
        "1. Первый пункт.",
        "2. Второй пункт.",
        "21. Подпункт 2¹ с надстрочным индексом.",
        "5. Пункты 3 и 4 исключены, нумерация продолжается.",
        "продолжение пункта 5.",
        "61. Подпункт 6¹ не может идти раньше шестого пункта.",
    ]
    points = fetch.split_points(paras)
    assert list(points) == ["1", "2", "2.1", "5"]
    assert points["5"][-1].startswith("61.")


def test_trailing_section_heading_is_cut_but_official_notes_are_kept() -> None:
    assert fetch.strip_heading("Текст пункта. Статья 68 УК РФ") == "Текст пункта."
    note = "Текст. Абзац исключен постановлением Пленума от 23 декабря 2025 г. № 40"
    assert fetch.strip_heading(note) == note


def test_plenum_and_review_drafts_load_with_their_references() -> None:
    store = NormStore.from_directory(NORMS)
    assert store.get_norm_meta("ppvs-rf:n58-p28-1").ref == "п. 28.1 ППВС № 58"
    review = store.get_norm_meta("vsrf-review:2024-12-18-p19")
    assert review.ref == "Обзор практики ВС РФ о назначении наказания (утв. 18.12.2024), п. 19"
    version = store.get_norm("vsrf-review:2024-12-18-p19", date(2025, 3, 1))
    assert version.verification_status is VerificationStatus.DRAFT
    assert "Определение № 13-УД22-17-К2" in version.text


def test_drafts_are_drafts_and_texts_start_with_their_point_number() -> None:
    for path in (PLENUM, REVIEW):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["norms"], path.name
        for norm in payload["norms"]:
            number = f"{norm['part']}" + (f".{norm['part_suffix']}" if norm["part_suffix"] else "")
            for version in norm["versions"]:
                assert version["verification_status"] == "draft", norm["norm_id"]
                assert version["text"].startswith(f"{number}. "), norm["norm_id"]
                assert not re.search(r"\b(?:Статья \d+ УК РФ|Условное осуждение)$", version["text"])
            assert norm["applies_to"], norm["norm_id"]


def test_verified_norms_already_in_corpus_are_not_duplicated_by_drafts() -> None:
    drafts = {n["norm_id"] for n in json.loads(PLENUM.read_text(encoding="utf-8"))["norms"]}
    assert not drafts & {"ppvs-rf:n58-p1", "ppvs-rf:n58-p34", "ppvs-rf:n58-p36"}


def _store(*entries: tuple[str, str, VerificationStatus]) -> NormStore:
    store = NormStore()
    for norm_id, text, status in entries:
        store.add_norm(
            LegalNorm(norm_id=norm_id, code="УК РФ", article=int(norm_id.split("-")[-1]), title=norm_id),
            [
                NormVersion(
                    norm_id=norm_id,
                    version_id="v1",
                    text=text,
                    effective_from=date(2000, 1, 1),
                    source_document="тест",
                    retrieved_at=date(2026, 10, 10),
                    sha256=NormVersion.compute_sha256(text),
                    verification_status=status,
                )
            ],
        )
    return store


def test_verified_source_ranks_above_an_equally_relevant_draft() -> None:
    text = "Наказание при рецидиве преступлений назначается не менее трети максимума."
    store = _store(
        ("uk-rf:art-101", text, VerificationStatus.DRAFT),
        ("uk-rf:art-102", text, VerificationStatus.VERIFIED),
    )
    hits = LegalRag(store).search("рецидив наказание треть")
    assert [h.norm_id for h in hits] == ["uk-rf:art-102", "uk-rf:art-101"]


def test_long_text_does_not_win_only_by_being_long() -> None:
    short = "Покушение на преступление: наказание не более трёх четвертей максимума."
    long = short + " " + " ".join(f"слово{i} наказание процедура суд приговор" for i in range(60))
    store = _store(
        ("uk-rf:art-103", long, VerificationStatus.VERIFIED),
        ("uk-rf:art-104", short, VerificationStatus.VERIFIED),
    )
    hits = LegalRag(store).search("покушение наказание три четверти")
    assert hits[0].norm_id == "uk-rf:art-104"


def test_single_approximate_word_match_is_not_a_finding() -> None:
    store = _store(("uk-rf:art-105", "Учёт государственных наград виновного.", VerificationStatus.VERIFIED))
    assert LegalRag(store).search("шпионаж государственная измена") == []


def test_letter_yo_in_the_query_matches_e_in_the_norm() -> None:
    store = _store(("uk-rf:art-161", "Грабеж, то есть открытое хищение чужого имущества.", VerificationStatus.VERIFIED))
    assert [h.norm_id for h in LegalRag(store).search("грабёж открытое хищение")] == ["uk-rf:art-161"]
