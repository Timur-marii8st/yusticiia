from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

from second_opinion.legal_sources.pravo_gov import Edition

ROOT = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("build_sanction_corpus", ROOT / "scripts" / "build_sanction_corpus.py")
builder = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = builder
_spec.loader.exec_module(builder)

OLD = (
    "1. Кража - наказывается лишением свободы на срок до двух лет. "
    "(В редакции федеральных законов от 27.12.2009 г. N 377-ФЗ ; от 06.05.2010 г. N 81-ФЗ ) "
    "2. Кража, совершенная группой лиц, - наказывается лишением свободы на срок до пяти лет."
)
NEW = (
    "1. Кража - наказывается лишением свободы на срок до двух лет (в ред. Федерального закона "
    "от 7 декабря 2011 г. N 420-ФЗ - Собрание законодательства Российской Федерации, 2011, N 50, "
    "ст. 7362). 2. Кража, совершенная группой лиц, - наказывается лишением свободы на срок до "
    "шести лет (в ред. Федерального закона от 7 декабря 2011 г. N 420-ФЗ - Собрание "
    "законодательства Российской Федерации, 2011, N 50, ст. 7362)."
)


def _timeline(entry: dict[date, date] | None = None):
    editions = [
        Edition(number=0, label="Исходная редакция"),
        Edition(number=1, act_date=date(2005, 3, 1), act_name="10-ФЗ", label="1"),
        Edition(number=None, act_date=date(2010, 5, 19), act_name="87-ФЗ", label="не готова"),
        Edition(number=None, act_date=date(2011, 12, 7), act_name="420-ФЗ", label="не готова"),
        Edition(number=None, act_date=date(2012, 3, 1), act_name="18-ФЗ", label="не готова"),
        Edition(number=2, act_date=date(2018, 12, 27), act_name="533-ФЗ", label="2"),
    ]
    return builder.Timeline(editions, entry)


def test_old_and_new_annotation_styles_do_not_count_as_a_change() -> None:
    old_part = "Кража - наказывается лишением свободы на срок до двух лет. (В редакции Федерального закона от 27.12.2009 г. N 377-ФЗ )"
    new_part = (
        "Кража - наказывается лишением свободы на срок до двух лет (в ред. Федерального закона от "
        "7 декабря 2011 г. N 420-ФЗ - Собрание законодательства Российской Федерации, 2011, N 50, ст. 7362)."
    )
    assert builder.normalize(old_part) == builder.normalize(new_part)


def test_amending_acts_are_read_from_both_annotation_styles() -> None:
    assert builder.amending_acts(OLD) == {(date(2009, 12, 27), "377-ФЗ"), (date(2010, 5, 6), "81-ФЗ")}
    assert builder.amending_acts(NEW) == {(date(2011, 12, 7), "420-ФЗ")}


def test_change_window_uses_acts_named_in_annotations() -> None:
    timeline = _timeline()
    assert timeline.change_window(2, {(date(2011, 12, 7), "420-ФЗ")}) == (date(2011, 12, 7), date(2011, 12, 7))
    assert timeline.change_window(2, {(date(2010, 5, 19), "87-ФЗ"), (date(2012, 3, 1), "18-ФЗ")}) == (
        date(2010, 5, 19),
        date(2012, 3, 1),
    )
    assert timeline.change_window(2, set()) == (date(2010, 5, 19), date(2018, 12, 27))
    assert timeline.change_window(1, set()) == (date(2005, 3, 1), date(2005, 3, 1))


def test_periods_follow_the_named_act_and_leave_unknown_gaps_empty() -> None:
    versions = [
        {"edition": 1, "label": "1", "text": OLD},
        {"edition": 2, "label": "2", "text": NEW},
    ]
    part_two = builder.periods(versions, 2, _timeline())
    assert [(p.start, p.end, p.edition) for p in part_two] == [
        (date(2005, 3, 1), date(2011, 12, 6), 1),
        (date(2011, 12, 7), None, 2),
    ]
    part_one = builder.periods(versions, 1, _timeline())
    assert [(p.start, p.end, p.edition) for p in part_one] == [(date(2005, 3, 1), None, 1)]

    unnamed = [dict(v, text=v["text"].replace("420-ФЗ", "999-ФЗ")) for v in versions]
    gapped = builder.periods(unnamed, 2, _timeline())
    assert [(p.start, p.end) for p in gapped] == [
        (date(2005, 3, 1), date(2010, 5, 18)),
        (date(2018, 12, 27), None),
    ]


def test_verified_entry_date_replaces_the_act_date() -> None:
    versions = [
        {"edition": 1, "label": "1", "text": OLD},
        {"edition": 2, "label": "2", "text": NEW},
    ]
    timeline = _timeline({date(2011, 12, 7): date(2011, 12, 9)})
    assert [(p.start, p.end) for p in builder.periods(versions, 2, timeline)] == [
        (date(2005, 3, 1), date(2011, 12, 8)),
        (date(2011, 12, 9), None),
    ]
