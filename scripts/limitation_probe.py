"""Проба черновика ст. 78 УК РФ (сроки давности) на тексте приговора.

Находит дату приговора в шапке, статью и дату совершения преступления, берёт предел
лишения свободы из каталога норм на дату деяния и показывает вывод. Ничего не
записывает::

    python scripts/limitation_probe.py data/drafts/cases/real-010.source.txt

Расчёт не зарегистрирован в движке правил (инвариант И-10): текст ст. 78 лежит в корпусе
со статусом ``draft``.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from second_opinion.domain.documents import Document  # noqa: E402
from second_opinion.fact_extraction.combination import SPECIAL_PART_FIRST_ARTICLE  # noqa: E402
from second_opinion.fact_extraction.pattern_extractor import PatternFactExtractor  # noqa: E402
from second_opinion.fact_extraction.sentence_date import extract_sentence_date  # noqa: E402
from second_opinion.legal_sources.store import (  # noqa: E402
    NoApplicableVersionError,
    NormNotFoundError,
    NormStore,
)
from second_opinion.rule_engine.limitation import review_limitation  # noqa: E402

IMPRISONMENT = "imprisonment"


def sanction_max(store: NormStore, article: int, suffix: str | None, part: int | None, when: date):
    norm = store.find_by_article("УК РФ", article, part, suffix)
    if norm is None:
        return None
    try:
        version = store.get_norm(norm.norm_id, when)
    except (NormNotFoundError, NoApplicableVersionError):
        return None
    sanction = next((s for s in version.sanctions if s.punishment_type.value == IMPRISONMENT), None)
    if sanction is None or sanction.max_months is None:
        return None
    return float(sanction.max_months)


def describe(path: Path, store: NormStore) -> None:
    text = path.read_text(encoding="utf-8")
    document = Document(
        document_id=path.stem,
        filename=path.name,
        content_type="text/plain",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    facts = PatternFactExtractor().extract(document)
    print(f"== {path}")
    found = extract_sentence_date(text)
    sentence = found[0] if found else None
    print(
        f"дата приговора: {found[0].isoformat() + ' (' + found[1] + ')' if found else 'не найдена'}"
    )
    qualification = next(
        (
            f.value
            for f in facts
            if f.type.value == "qualification" and f.value["article"] >= SPECIAL_PART_FIRST_ARTICLE
        ),
        None,
    )
    offense_fact = next((f for f in facts if f.type.value == "date_of_offense"), None)
    offense = date.fromisoformat(offense_fact.value) if offense_fact else None
    if qualification is None:
        print("квалификация не найдена")
        return
    article, suffix, part = (
        qualification["article"],
        qualification.get("article_suffix"),
        qualification.get("part"),
    )
    ref = f"ч. {part} ст. {article}{'.' + suffix if suffix else ''}"
    print(
        f"квалификация: {ref}; дата совершения: {offense.isoformat() if offense else 'не найдена'}"
    )
    maximum = sanction_max(store, article, suffix, part, offense) if offense else None
    print(
        f"предел лишения свободы: {maximum:g} мес."
        if maximum
        else "предел лишения свободы: нет в корпусе"
    )
    review = review_limitation(article, suffix, part, maximum, offense, sentence)
    print(f"итог: {review.status}. {review.explanation}")
    for gap in review.gaps:
        print(f"  не проверено: {gap}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--norms", type=Path, default=REPO_ROOT / "data" / "fixtures" / "norms")
    args = parser.parse_args(argv)
    store = NormStore.from_directory(args.norms)
    for path in args.paths:
        describe(path, store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
