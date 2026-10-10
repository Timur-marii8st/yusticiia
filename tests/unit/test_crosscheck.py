from __future__ import annotations

from datetime import date

import pytest

from second_opinion.domain.enums import PunishmentType
from second_opinion.domain.norms import LegalNorm, NormVersion, SanctionSpec
from second_opinion.legal_sources.crosscheck import (
    Term,
    crosscheck_norm,
    crosscheck_store,
    parse_imprisonment,
    split_parts,
)
from second_opinion.legal_sources.pravo_gov import Edition
from second_opinion.legal_sources.store import NormStore
from tests.conftest import FIXTURES_DIR


def _norm(article: int = 112, part: int | None = 1) -> LegalNorm:
    return LegalNorm(norm_id=f"uk-rf:art-{article}", code="УК РФ", article=article, part=part, title="Тест")


def _version(max_months: float | None, min_months: float | None = None) -> NormVersion:
    text = "текст"
    return NormVersion(
        norm_id="uk-rf:art-112",
        version_id="v1",
        text=text,
        sanctions=[
            SanctionSpec(
                punishment_type=PunishmentType.IMPRISONMENT,
                min_months=min_months,
                max_months=max_months,
            )
        ],
        effective_from=date(2003, 12, 16),
        source_document="тест",
        retrieved_at=date(2026, 10, 1),
        sha256=NormVersion.compute_sha256(text),
    )


BODY = (
    "Название 1. Деяние наказывается штрафом, либо лишением свободы на срок до двух лет. "
    "2. Те же деяния при отягчающих, наказываются лишением свободы на срок от трех до восьми лет "
    "с ограничением свободы на срок до двух лет. "
    "3. Особо тяжкое деяние наказывается лишением свободы на срок от шести месяцев до двух лет. "
    "Примечание. 1. Под деянием понимается лишением свободы на срок до одного года."
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("лишением свободы на срок до двух лет", [Term(None, 24)]),
        ("лишением свободы на срок от трех до восьми лет", [Term(36, 96)]),
        ("лишением свободы на срок от шести месяцев до двух лет", [Term(6, 24)]),
        ("лишением свободы на срок до двадцати пяти лет", [Term(None, 300)]),
        ("лишением свободы на срок до одного года", [Term(None, 12)]),
        (
            "либо ограничением свободы на срок до трех лет, либо лишением свободы на тот же срок.",
            [Term(None, 36)],
        ),
        ("пожизненным лишением свободы", []),
        ("принудительными работами на срок до двух лет", []),
    ],
)
def test_parse_imprisonment(text: str, expected: list[Term]) -> None:
    assert parse_imprisonment(text) == expected


def test_split_parts_by_numbers_and_cuts_notes() -> None:
    parts = split_parts(BODY)
    assert sorted(parts) == [1, 2, 3]
    assert parts[1].startswith("Деяние наказывается")
    assert "Примечание" not in parts[3]


def test_unnumbered_article_is_part_zero() -> None:
    assert list(split_parts("Название. Деяние наказывается.")) == [0]


def test_match_when_limits_equal() -> None:
    result = crosscheck_norm(_norm(part=1), _version(24), BODY)
    assert result is not None and result.status == "match"


def test_mismatch_when_maximum_differs() -> None:
    result = crosscheck_norm(_norm(part=1), _version(36), BODY)
    assert result is not None and result.status == "mismatch"
    assert result.corpus == "до 36 мес." and result.source == "до 24 мес."


def test_mismatch_when_minimum_differs() -> None:
    result = crosscheck_norm(_norm(part=2), _version(96, min_months=24), BODY)
    assert result is not None and result.status == "mismatch"


def test_match_with_minimum_and_maximum() -> None:
    result = crosscheck_norm(_norm(part=3), _version(24, min_months=6), BODY)
    assert result is not None and result.status == "match"


@pytest.mark.parametrize(
    ("body", "part"),
    [
        (None, 1),
        (BODY, 9),
        ("Название 1. Деяние наказывается штрафом. 2. Иное.", 1),
        ("Название 1. Лишением свободы на срок до двух лет, либо лишением свободы на срок до трех лет.", 1),
    ],
)
def test_unparsed_is_not_a_match(body: str | None, part: int) -> None:
    result = crosscheck_norm(_norm(part=part), _version(24), body)
    assert result is not None and result.status == "unparsed"


def test_version_without_imprisonment_is_skipped() -> None:
    version = _version(24).model_copy(update={"sanctions": []})
    assert crosscheck_norm(_norm(), version, BODY) is None


class FakeSource:
    def __init__(self, articles: dict[str, str]) -> None:
        self._articles = articles

    def editions(self, topic: str) -> list[Edition]:
        return [
            Edition(number=1, label="Исходная редакция"),
            Edition(number=2, label="2 - от 04.08.2026 № 300-ФЗ"),
        ]

    def article_texts(self, topic: str, rdk: int) -> dict[str, str]:
        assert rdk == 2
        return self._articles


def test_store_crosscheck_reads_latest_prepared_edition() -> None:
    store = NormStore.from_directory(FIXTURES_DIR / "norms")
    articles = {"158": "Кража 1. Деяние наказывается лишением свободы на срок до двух лет."}
    label, results = crosscheck_store(store, FakeSource(articles), "102041891")  # type: ignore[arg-type]
    assert label == "2 - от 04.08.2026 № 300-ФЗ"
    by_ref = {r.ref: r for r in results}
    assert by_ref["УК РФ ст. 158 ч. 1"].status == "match"
    assert by_ref["УК РФ ст. 158 ч. 2"].status == "unparsed"
    assert all(r.status != "mismatch" for r in results)


def test_same_term_without_preceding_term_is_unparsed() -> None:
    result = crosscheck_norm(_norm(part=1), _version(36), "Название 1. Наказывается лишением свободы на тот же срок.")
    assert result is not None and result.status == "unparsed"


def test_numbered_notes_are_not_parts() -> None:
    body = (
        "Кража 1. Кража - наказывается лишением свободы на срок до двух лет. "
        "2. Кража, совершенная группой лиц, - наказывается лишением свободы на срок до пяти лет. "
        "Примечания. 1. Под хищением понимается изъятие. 2. Значительный ущерб определяется. "
        "3. Помещение - строение. 4. Крупным размером признается стоимость."
    )
    assert sorted(split_parts(body)) == [1, 2]


def test_part_number_printed_without_a_period() -> None:
    body = (
        "Грабеж 1. Грабеж - наказывается лишением свободы на срок до четырех лет. "
        "2. Грабеж, совершенный группой лиц, - наказывается лишением свободы на срок до семи лет "
        "(в ред. Федерального закона от 7 декабря 2011 г. N 420-ФЗ). 3 Грабеж, совершенный "
        "организованной группой, - наказывается лишением свободы на срок от шести до двенадцати лет."
    )
    parts = split_parts(body)
    assert sorted(parts) == [1, 2, 3]
    assert parts[3].startswith("Грабеж, совершенный организованной")
    assert "до 3 лет" not in parts[2]
