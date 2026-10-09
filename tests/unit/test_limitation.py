"""Сроки давности (ст. 78 УК РФ) и дата приговора: черновик, в реестре правил не числится."""

from __future__ import annotations

from datetime import date

import pytest

from second_opinion.fact_extraction.sentence_date import extract_sentence_date
from second_opinion.rule_engine.limitation import (
    add_years,
    crime_category,
    limitation_not_applied,
    review_limitation,
)


@pytest.mark.parametrize(
    ("max_months", "offense", "expected"),
    [
        (24, date(2010, 1, 1), "небольшой тяжести"),
        (36, date(2010, 1, 1), "средней тяжести"),
        (36, date(2011, 12, 8), "небольшой тяжести"),
        (36, date(2011, 12, 7), "средней тяжести"),
        (60, date(2020, 1, 1), "средней тяжести"),
        (61, date(2020, 1, 1), "тяжкое"),
        (120, date(2020, 1, 1), "тяжкое"),
        (121, date(2020, 1, 1), "особо тяжкое"),
    ],
)
def test_category_follows_the_maximum_term_and_the_date(max_months, offense, expected) -> None:
    assert crime_category(max_months, offense).name == expected


@pytest.mark.parametrize(
    ("name_months", "years"),
    [(24, 2), (60, 6), (120, 10), (180, 15)],
)
def test_limitation_years_per_category(name_months: int, years: int) -> None:
    assert crime_category(name_months, date(2020, 1, 1)).years == years


def test_add_years_handles_a_leap_day() -> None:
    assert add_years(date(2024, 2, 29), 2) == date(2026, 3, 1)
    assert add_years(date(2020, 5, 17), 6) == date(2026, 5, 17)


def _review(max_months=36, offense=date(2020, 1, 10), sentence=date(2026, 3, 10), **kwargs):
    return review_limitation(
        kwargs.get("article", 158), None, kwargs.get("part", 1), max_months, offense, sentence
    )


def test_expired_period_is_a_warning_phrased_as_a_prompt_to_look() -> None:
    review = _review(max_months=24, offense=date(2020, 1, 10), sentence=date(2026, 3, 10))

    assert review.status == "WARNING"
    assert review.category.name == "небольшой тяжести"
    assert review.deadline == date(2022, 1, 10)
    assert "проверьте, не применима ли ст. 78" in review.explanation
    assert "освободить" not in review.explanation and "прекратить" not in review.explanation


def test_period_ending_on_the_sentence_date_counts_as_expired() -> None:
    review = _review(max_months=60, offense=date(2020, 3, 10), sentence=date(2026, 3, 10))

    assert review.status == "WARNING"


def test_period_ending_right_after_the_sentence_warns_about_the_appeal_window() -> None:
    sentence = date(2026, 3, 10)
    offense = date(2020, 3, 30)
    review = _review(max_months=60, offense=offense, sentence=sentence)

    assert review.status == "WARNING"
    assert "вступит в силу позже" in review.explanation


def test_period_far_from_expiry_passes_and_still_lists_what_is_unchecked() -> None:
    review = _review(max_months=60, offense=date(2024, 6, 1), sentence=date(2026, 3, 10))

    assert review.status == "PASS"
    assert review.deadline == date(2030, 6, 1)
    assert any("ч. 3 ст. 78" in gap for gap in review.gaps)


def test_margin_boundary_is_exact() -> None:
    sentence = date(2026, 3, 10)
    deadline_inside = date(2026, 4, 9)
    deadline_outside = date(2026, 4, 10)

    inside = _review(max_months=60, offense=add_years(deadline_inside, -6), sentence=sentence)
    outside = _review(max_months=60, offense=add_years(deadline_outside, -6), sentence=sentence)

    assert inside.status == "WARNING"
    assert outside.status == "PASS"


@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [
        ({"max_months": None}, "предел"),
        ({"offense": None}, "даты совершения"),
        ({"sentence": None}, "даты приговора"),
    ],
)
def test_missing_inputs_give_unknown_not_a_guess(kwargs, missing) -> None:
    review = review_limitation(
        158,
        None,
        1,
        kwargs.get("max_months", 36),
        kwargs["offense"] if "offense" in kwargs else date(2020, 1, 1),
        kwargs["sentence"] if "sentence" in kwargs else date(2026, 3, 10),
    )

    assert review.status == "UNKNOWN"
    assert missing in review.explanation


def test_especially_grave_crimes_mention_the_court_decision_for_life_sentences() -> None:
    review = _review(
        max_months=240, offense=date(2020, 1, 1), sentence=date(2026, 3, 10), article=105
    )

    assert review.status == "PASS"
    assert any("ч. 4 ст. 78" in gap for gap in review.gaps)


@pytest.mark.parametrize(
    ("article", "suffix", "part", "expected"),
    [
        (205, None, 1, True),
        (205, "1", 2, True),
        (206, None, 3, True),
        (206, None, 1, False),
        (211, None, 4, True),
        (211, None, 2, False),
        (281, "2", None, True),
        (361, None, 1, True),
        (158, None, 1, False),
    ],
)
def test_exempt_articles_follow_part_five(article, suffix, part, expected) -> None:
    assert limitation_not_applied(article, suffix, part) is expected


def test_exempt_article_is_not_applicable() -> None:
    review = review_limitation(205, "1", 1, 240, date(2000, 1, 1), date(2026, 3, 10))

    assert review.status == "NOT_APPLICABLE"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (
            "Производство № 1-48/2026 П Р И Г О В О Р Именем Российской Федерации г. Райчихинск "
            "10 марта 2026 года Райчихинский городской суд в составе: судьи",
            date(2026, 3, 10),
        ),
        (
            "Дело № 1-66/2025 г. ПРИГОВОР ИМЕНЕМ РОССИЙСКОЙ ФЕДЕРАЦИИ г. Приволжск «24» ноября "
            "2025 года Приволжский районный суд в составе: судьи",
            date(2025, 11, 24),
        ),
        (
            "ПРИГОВОР Именем Российской Федерации г. Омск 31 марта 2026 г. Кировский районный суд "
            "г. Омска в составе: судьи",
            date(2026, 3, 31),
        ),
        (
            "ПРИГОВОР Именем Российской Федерации 01.04.2026 г. Омск Суд в составе: судьи",
            date(2026, 4, 1),
        ),
    ],
)
def test_sentence_date_is_read_from_the_header(header: str, expected: date) -> None:
    found = extract_sentence_date(header)

    assert found is not None
    assert found[0] == expected
    assert found[1] in header


def test_masked_date_is_not_invented() -> None:
    text = "П Р И Г О В О Р ИМЕНЕМ РОССИЙСКОЙ ФЕДЕРАЦИИ ДД.ММ.ГГГГ г. Одинцово суд в составе: судьи"

    assert extract_sentence_date(text) is None


def test_dates_after_the_composition_clause_are_ignored() -> None:
    text = "ПРИГОВОР суд в составе: судьи Иванова, подсудимого, ранее судимого 12 мая 2019 года"

    assert extract_sentence_date(text) is None


def test_impossible_dates_are_skipped() -> None:
    assert extract_sentence_date("ПРИГОВОР 31 февраля 2026 года суд в составе: судьи") is None
