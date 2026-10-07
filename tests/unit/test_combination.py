"""Совокупность преступлений и приговоров (ст. 69, 70 УК РФ): разбор текста и расчёт вариантов.

Фрагменты резолютивных частей взяты из реальных приговоров (сайт судов общей
юрисдикции), персональные данные в них уже заменены на ФИО1 и аналоги.
Расчёт не зарегистрирован в движке (инвариант И-10), поэтому здесь проверяется
сам модуль, а не вывод правила.
"""

from __future__ import annotations

import pytest

from second_opinion.fact_extraction.combination import Combination, Offense, extract_combination
from second_opinion.rule_engine.combination import (
    CAP_FACTOR,
    IMPRISONMENT_LIMIT_70_MONTHS,
    review_combination,
)

FINE_PRIOR_SENTENCE = (
    "Суд, руководствуясь ст.316 УПК РФ, П Р И Г О В О Р И Л: Признать ФИО1 виновным "
    "в совершении преступлений, предусмотренных ч.2 ст.159, ч.2 ст.159 УК РФ и назначить "
    "ему наказание: -по ч.2 ст.159 УК РФ (по преступлению, совершенному в отношении "
    "потерпевшей ФИО3) в виде штрафа в размере 80000 (восемьдесят тысяч) рублей; "
    "-по ч.2 ст.159 УК РФ (по преступлению, совершенному в отношении потерпевшей ФИО8) "
    "в виде штрафа в размере 70000 (семьдесят тысяч) рублей, в соответствии с ч.2 ст.69 "
    "УК РФ, по совокупности преступлений, путем частичного сложения назначенных наказаний, "
    "окончательно назначить ФИО1 наказание в виде штрафа в размере 100000 (сто тысяч) рублей. "
    "Меру пресечения отменить."
)

ADDITIONAL_ONLY_70 = (
    "Суд П Р И Г О В О Р И Л: признать ФИО3 виновным в совершении преступления, "
    "предусмотренного ч.4 ст.111 УК РФ, и назначить ему наказание в виде лишения свободы "
    "на срок 8 (восемь) лет. На основании ст. 70 УК РФ, по совокупности приговоров, к "
    "назначенному наказанию присоединить неотбытое дополнительное наказание, назначенное "
    "приговором районного суда от 14 марта 2023 года, и назначить ФИО5 окончательное "
    "наказание в виде лишения свободы на срок 8 (восемь) лет с отбыванием наказания в "
    "исправительной колонии строгого режима, с лишением права заниматься деятельностью, "
    "связанной с управлением транспортными средствами, на срок 5 месяцев 11 дней."
)

TWO_IMPRISONMENTS_69 = (
    "Суд П Р И Г О В О Р И Л: Признать ФИО1 виновным в совершении преступлений, "
    "предусмотренных ч.2 ст.158, ч.2 ст.159 УК РФ, и назначить ему наказание: по ч.2 ст.158 "
    "УК РФ в виде лишения свободы на срок {first}; по ч.2 ст.159 УК РФ в виде лишения "
    "свободы на срок {second}. В соответствии с ч.{part} ст.69 УК РФ по совокупности "
    "преступлений путем частичного сложения наказаний окончательно назначить ФИО1 наказание "
    "в виде лишения свободы на срок {final}."
)

UNSERVED_70 = (
    "Суд П Р И Г О В О Р И Л: Признать ФИО1 виновным в совершении преступления, "
    "предусмотренного ч.1 ст.158 УК РФ, и назначить ему наказание в виде лишения свободы "
    "на срок 1 год. В соответствии со ст.70 УК РФ по совокупности приговоров к назначенному "
    "наказанию частично присоединить неотбытую часть наказания по приговору районного суда "
    "от 10 мая 2022 года в виде лишения свободы на срок 6 месяцев и окончательно назначить "
    "ФИО1 наказание в виде лишения свободы на срок {final}."
)


PARTIAL_WORDING_70 = (
    "Руководствуясь ст.ст.302-304, 307-309, 316 УПК РФ, суд ПРИГОВОРИЛ: Признать ФИО2 "
    "виновным в совершении преступления, предусмотренного ч. 1 ст. 161 УК РФ и назначить "
    "ему наказание в виде лишения свободы сроком на 1 год 6 месяцев. В соответствии со "
    "ст. 70 УК РФ, по совокупности приговоров, к наказанию, назначенному по данному "
    "приговору, частично присоединить не отбытую часть наказания по приговору от ДАТА, "
    "с учётом ст.71 УК РФ, в виде 6 месяцев лишения свободы, и назначить ФИО2 "
    "окончательное наказание в виде лишения свободы сроком на 2 года, с отбыванием "
    "наказания в исправительной колонии строгого режима."
)


def two_imprisonments(first: str, second: str, final: str, part: int = 2) -> str:
    return TWO_IMPRISONMENTS_69.format(first=first, second=second, final=final, part=part)


def sanction_max(article: int, part: int | None) -> float | None:
    return {(158, 2): 72.0, (159, 2): 60.0, (111, 4): 156.0, (158, 1): 24.0}.get((article, part))


def test_fine_combination_from_real_sentence_is_parsed() -> None:
    combination = extract_combination(FINE_PRIOR_SENTENCE)

    assert combination is not None
    assert (combination.article, combination.part) == (69, 2)
    assert [(o.article, o.part, o.value, o.unit) for o in combination.offenses] == [
        (159, 2, 80000.0, "rubles"),
        (159, 2, 70000.0, "rubles"),
    ]
    assert (combination.final_value, combination.final_unit) == (100000.0, "rubles")
    assert combination.unit == "rubles"


def test_fine_combination_reports_partial_addition_and_leaves_the_cap_unchecked() -> None:
    combination = extract_combination(FINE_PRIOR_SENTENCE)
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert review.matched == "частичное сложение"
    assert review.violations == []
    assert review.status == "UNKNOWN"
    assert any("штраф" in gap for gap in review.gaps)
    ranges = {v.name: (v.low, v.high) for v in review.variants}
    assert ranges["поглощение"] == (80000.0, 80000.0)
    assert ranges["частичное сложение"] == (80000.0, 150000.0)
    assert ranges["полное сложение"] == (150000.0, 150000.0)


def test_additional_only_article_70_is_parsed_and_not_compared() -> None:
    combination = extract_combination(ADDITIONAL_ONLY_70)

    assert combination is not None
    assert combination.article == 70
    assert combination.unserved_additional_only is True
    assert combination.unserved_value is None
    assert [(o.article, o.part, o.value) for o in combination.offenses] == [(111, 4, 96.0)]
    assert combination.final_value == 96.0

    review = review_combination(combination, sanction_max)

    assert review.status == "UNKNOWN"
    assert review.violations == []
    assert any("дополнительного" in gap for gap in review.gaps)


@pytest.mark.parametrize(
    ("first", "second", "final", "matched"),
    [
        ("2 года", "3 года", "3 года", "поглощение"),
        ("2 года", "3 года", "4 года", "частичное сложение"),
        ("2 года", "3 года", "5 лет", "полное сложение"),
    ],
)
def test_imprisonment_variants_are_matched(
    first: str, second: str, final: str, matched: str
) -> None:
    combination = extract_combination(two_imprisonments(first, second, final))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert review.matched == matched
    assert review.violations == []
    assert review.status == "PASS"
    assert [v.name for v in review.variants][-1] == "предел"


def test_final_below_the_stricter_sentence_is_a_violation() -> None:
    combination = extract_combination(two_imprisonments("2 года", "3 года", "2 года 6 месяцев"))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert review.status == "WARNING"
    assert any("меньше самого строгого" in text for text in review.violations)


def test_final_above_the_full_sum_is_a_violation() -> None:
    combination = extract_combination(two_imprisonments("2 года", "3 года", "6 лет"))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert review.status == "WARNING"
    assert any("больше полного сложения" in text for text in review.violations)


def test_final_above_the_cap_over_the_most_serious_sanction_is_a_violation() -> None:
    combination = Combination(
        article=69,
        part=2,
        offenses=[
            Offense(159, None, 2, 48.0, "months", "4 года"),
            Offense(159, None, 2, 48.0, "months", "4 года"),
        ],
        final_value=96.0,
        final_unit="months",
        units={"months"},
    )

    review = review_combination(combination, sanction_max)

    cap = CAP_FACTOR * 60.0
    assert cap == 90.0
    assert review.status == "WARNING"
    assert any("выше предела" in text for text in review.violations)


def test_absorption_under_part_three_is_flagged() -> None:
    combination = extract_combination(two_imprisonments("2 года", "3 года", "3 года", part=3))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert combination.part == 3
    assert any("часть 3" in text for text in review.violations)


def test_missing_sanction_makes_the_cap_unknown_rather_than_passing() -> None:
    combination = extract_combination(two_imprisonments("2 года", "3 года", "4 года"))
    assert combination is not None

    review = review_combination(combination, lambda article, part: None)

    assert review.status == "UNKNOWN"
    assert any("нет предела санкции" in gap for gap in review.gaps)


def test_article_70_final_between_the_larger_part_and_the_sum() -> None:
    combination = extract_combination(UNSERVED_70.format(final="1 год 4 месяца"))
    assert combination is not None

    assert (combination.article, combination.unserved_value) == (70, 6.0)
    review = review_combination(combination, sanction_max)

    assert review.matched == "итог между большим из двух и суммой"
    assert review.status == "PASS"


def test_article_70_final_equal_to_the_sum() -> None:
    combination = extract_combination(UNSERVED_70.format(final="1 год 6 месяцев"))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert review.matched == "итог равен сумме"


def test_article_70_final_not_above_the_larger_part_is_a_violation() -> None:
    combination = extract_combination(UNSERVED_70.format(final="1 год"))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert review.status == "WARNING"
    assert any("ч. 4" in text for text in review.violations)


def test_article_70_final_above_the_full_sum_is_a_violation() -> None:
    combination = extract_combination(UNSERVED_70.format(final="2 года"))
    assert combination is not None

    review = review_combination(combination, sanction_max)

    assert any("больше суммы" in text for text in review.violations)


def test_article_70_over_thirty_years_is_a_violation() -> None:
    combination = Combination(
        article=70,
        part=None,
        offenses=[Offense(105, None, 2, 240.0, "months", "20 лет")],
        final_value=IMPRISONMENT_LIMIT_70_MONTHS + 12.0,
        final_unit="months",
        unserved_value=150.0,
        units={"months"},
    )

    review = review_combination(combination, sanction_max)

    assert any("тридцати лет" in text for text in review.violations)


def test_mixed_units_are_not_compared() -> None:
    combination = Combination(
        article=69,
        part=2,
        offenses=[
            Offense(158, None, 2, 24.0, "months", "2 года"),
            Offense(159, None, 2, 80000.0, "rubles", "штраф"),
        ],
        final_value=30.0,
        final_unit="months",
        units={"months", "rubles"},
    )

    review = review_combination(combination, sanction_max)

    assert combination.unit == "mixed"
    assert review.status == "UNKNOWN"
    assert review.variants == []


def test_single_offense_under_article_69_is_not_checked() -> None:
    combination = Combination(
        article=69,
        part=2,
        offenses=[Offense(158, None, 2, 24.0, "months", "2 года")],
        final_value=24.0,
        final_unit="months",
        units={"months"},
    )

    review = review_combination(combination, sanction_max)

    assert review.status == "UNKNOWN"
    assert any("по одному преступлению" in gap for gap in review.gaps)


def test_missing_final_punishment_is_reported() -> None:
    combination = Combination(
        article=69,
        part=2,
        offenses=[
            Offense(158, None, 2, 24.0, "months", "2 года"),
            Offense(159, None, 2, 36.0, "months", "3 года"),
        ],
        final_value=None,
        final_unit=None,
        units={"months"},
    )

    review = review_combination(combination, sanction_max)

    assert review.status == "UNKNOWN"
    assert any("окончательное наказание" in gap for gap in review.gaps)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Суд установил, что подсудимый совершил кражу.",
        "Суд П Р И Г О В О Р И Л: признать ФИО1 виновным по ч.1 ст.158 УК РФ и назначить ему "
        "наказание в виде лишения свободы на срок 2 года.",
        "Суд П Р И Г О В О Р И Л: назначить окончательное наказание по ст.69 УК РФ.",
    ],
)
def test_text_without_a_combination_gives_none(text: str) -> None:
    assert extract_combination(text) is None


def test_procedural_articles_are_not_taken_for_offenses() -> None:
    combination = extract_combination(
        two_imprisonments("2 года", "3 года", "4 года").replace(
            "Признать", "Руководствуясь ст.62 УК РФ и ч.1 ст.61 УК РФ, признать"
        )
    )

    assert combination is not None
    assert [o.article for o in combination.offenses] == [158, 159]


def test_partial_wording_names_the_joined_part_not_the_whole_unserved_part() -> None:
    combination = extract_combination(PARTIAL_WORDING_70)
    assert combination is not None

    assert combination.article == 70
    assert [(o.article, o.part, o.value) for o in combination.offenses] == [(161, 1, 18.0)]
    assert (combination.unserved_value, combination.final_value) == (6.0, 24.0)

    review = review_combination(combination, sanction_max)

    assert review.matched == "итог равен сумме"
    assert review.status == "PASS"
    assert all("полное" not in variant.name for variant in review.variants)
