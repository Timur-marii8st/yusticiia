"""Позиция в распределении лишения свободы не относится к другим видам наказания."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("phrase", "kind", "has_position"),
    [
        ("исправительных работ", "correctional_labor", False),
        ("ограничения свободы", "restriction_of_liberty", False),
        ("лишения свободы", "imprisonment", True),
    ],
)
def test_sentence_position_only_compares_imprisonment(pipeline, phrase, kind, has_position):
    text = (
        "УСТАНОВИЛ: Подсудимый совершил преступление 15 марта 2025 года. "
        "ПРИГОВОРИЛ: Признать виновным в совершении преступления, "
        "предусмотренного ч. 1 ст. 158 УК РФ, и назначить наказание "
        f"в виде {phrase} на срок 2 месяца."
    )
    document = pipeline.ingest("sentence.txt", text.encode())

    report = pipeline.analyze(document.document_id)

    assert report.case_facts.sentence.punishment_type == kind
    assert report.case_facts.sentence.term_months == 2
    assert report.analytics.n_with_term > 0, "В выборке есть сроки лишения свободы"
    assert (report.analytics.sentence_position is not None) is has_position
    assert any(c.severity == "statistics" for c in report.concerns) is has_position
