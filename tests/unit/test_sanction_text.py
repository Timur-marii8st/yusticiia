from __future__ import annotations

from second_opinion.legal_sources.sanction_text import (
    ParsedSanction,
    parse_sanctions,
    words_to_number,
)


def test_numbers_in_words_and_digits() -> None:
    assert words_to_number("трехсот шестидесяти") == 360
    assert words_to_number("ста двадцати тысяч") == 120_000
    assert words_to_number("80 000") == 80_000


def test_main_punishments_with_units_and_additional_ones_cut_off() -> None:
    text = (
        "Кража, совершенная группой лиц, - наказывается штрафом в размере до двухсот тысяч рублей "
        "или в размере заработной платы или иного дохода осужденного за период до восемнадцати "
        "месяцев, либо обязательными работами на срок до четырехсот восьмидесяти часов, либо "
        "исправительными работами на срок до двух лет, либо лишением свободы на срок до пяти лет "
        "с ограничением свободы на срок до одного года или без такового."
    )
    assert parse_sanctions(text) == [
        ParsedSanction("fine", None, 200_000.0, "rubles"),
        ParsedSanction("compulsory_labor", None, 480.0, "hours"),
        ParsedSanction("correctional_labor", None, 24.0, "months"),
        ParsedSanction("imprisonment", None, 60.0, "months"),
    ]


def test_alternatives_joined_by_ili_in_the_1996_edition() -> None:
    text = (
        "Умышленное причинение средней тяжести вреда здоровью - наказывается арестом на срок от трех "
        "до шести месяцев или лишением свободы на срок до трех лет."
    )
    assert parse_sanctions(text) == [ParsedSanction("imprisonment", None, 36.0, "months")]


def test_same_term_and_fine_in_minimum_wages() -> None:
    text = (
        "Деяние - наказывается штрафом в размере от двухсот до пятисот минимальных размеров оплаты "
        "труда, либо ограничением свободы на срок до трех лет, либо лишением свободы на тот же срок."
    )
    assert parse_sanctions(text) == [
        ParsedSanction("fine", None, None, "rubles"),
        ParsedSanction("restriction_of_liberty", None, 36.0, "months"),
        ParsedSanction("imprisonment", None, 36.0, "months"),
    ]
