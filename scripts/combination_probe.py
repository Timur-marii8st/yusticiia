"""Проба черновика ФТ-12: совокупность по ст. 69, 70 УК РФ в тексте приговора.

Разбирает резолютивную часть, показывает назначенные наказания, окончательное
наказание и допустимые варианты. Пределы санкций берутся из каталога норм
(по умолчанию ``data/fixtures/norms``); редакция выбирается на дату, по
умолчанию сегодняшнюю. Ничего не записывает::

    python scripts/combination_probe.py data/drafts/cases/real-007.source.txt
    python scripts/combination_probe.py --date 2023-05-01 приговор.txt

Расчёт не зарегистрирован в движке правил (инвариант И-10): текст ст. 69 и 70
лежит в ``data/drafts/norms`` и ждёт сверки юристом.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from second_opinion.fact_extraction.combination import extract_combination  # noqa: E402
from second_opinion.legal_sources.store import (  # noqa: E402
    NoApplicableVersionError,
    NormNotFoundError,
    NormStore,
)
from second_opinion.rule_engine.combination import review_combination  # noqa: E402

IMPRISONMENT = "imprisonment"


def make_sanction_max(store: NormStore, applicable_at: date):
    def sanction_max(article: int, part: int | None) -> float | None:
        norm = store.find_by_article("УК РФ", article, part)
        if norm is None:
            return None
        try:
            version = store.get_norm(norm.norm_id, applicable_at)
        except (NormNotFoundError, NoApplicableVersionError):
            return None
        sanction = next(
            (s for s in version.sanctions if s.punishment_type.value == IMPRISONMENT), None
        )
        if sanction is None or sanction.max_months is None:
            return None
        return float(sanction.max_months)

    return sanction_max


def describe(path: Path, store: NormStore, applicable_at: date) -> int:
    text = path.read_text(encoding="utf-8")
    combination = extract_combination(text)
    print(f"== {path}")
    if combination is None:
        print("совокупность в резолютивной части не найдена")
        return 1
    unit = {"months": "мес.", "rubles": "руб.", "mixed": "разные виды"}[combination.unit]
    part = f" ч. {combination.part}" if combination.part else ""
    print(f"ст. {combination.article}{part} УК РФ, единица: {unit}")
    for offense in combination.offenses:
        suffix = f".{offense.article_suffix}" if offense.article_suffix else ""
        print(
            f"  ч. {offense.part} ст. {offense.article}{suffix}: {offense.value:g} ({offense.quote})"
        )
    if combination.unserved_additional_only:
        print("  присоединена неотбытая часть дополнительного наказания")
    elif combination.unserved_value is not None:
        print(f"  присоединена неотбытая часть: {combination.unserved_value:g} мес.")
    print(
        f"  окончательно: {combination.final_value if combination.final_value is not None else 'не найдено'}"
    )

    review = review_combination(combination, make_sanction_max(store, applicable_at))
    print(
        f"итог: {review.status}"
        + (f", совпало с вариантом: {review.matched}" if review.matched else "")
    )
    for variant in review.variants:
        verdict = {True: "допустим", False: "не допускается", None: "не определено"}[
            variant.allowed
        ]
        print(
            f"  вариант «{variant.name}»: {variant.low:g} - {variant.high:g} ({verdict}; {variant.basis})"
        )
    for violation in review.violations:
        print(f"  НАРУШЕНИЕ: {violation}")
    for gap in review.gaps:
        print(f"  не проверено: {gap}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="текстовые файлы приговоров")
    parser.add_argument("--norms", type=Path, default=REPO_ROOT / "data" / "fixtures" / "norms")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)
    store = NormStore.from_directory(args.norms)
    return max(describe(path, store, args.date) for path in args.paths)


if __name__ == "__main__":
    sys.exit(main())
