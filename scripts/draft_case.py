"""Черновая выгрузка ComparableCase из текста реального приговора (ФТ-24/25).

Реальное дело для базы практики требует не только текста, но юридической
квалификации: какие именно пункты ст. 61/63 УК РФ смягчают/отягчают,
краткое юридическое изложение дела, реквизиты суда. Этого движок не
извлекает и придумывать не должен — те же PATTERN_CONFIDENCE-факты, что
разбирают загружаемый документ (``PatternFactExtractor``), находят только
статью/часть, стадию, групповой характер, признание вины, особый порядок,
суд присяжных, вид/срок наказания (см. ниже, какие виды) и условность.
Остальное — обязательно вручную. Если в резолютивной части есть
окончательное наказание по совокупности (ст. 69, 70), в ``term_months``
попадает оно, как и в корпусе, а срок за само преступление остаётся в
``evidence``.

Важная оговорка про качество автодетекта: паттерны ищут слово, не смысл.
Простое отрицание они распознают («не возместил ущерб», «без группы
лиц», «рецидива преступлений судом не усмотрено», «явку с повинной не
заявлял»), сложное — нет: «рецидив в действиях подсудимого суд не
усматривает» совпадает со словом «рецидив» так же, как «установлен
рецидив». Каждое булево поле в черновике сопровождается цитатой
(``evidence``) ровно для того, чтобы это увидеть и поправить, а не
поверить на слово.

Известное ограничение самого экстрактора: вид наказания распознаётся для
лишения свободы, исправительных и обязательных работ (``_extract_sentence``
в ``fact_extraction/pattern_extractor.py``, расширено 28.09.2026) — но не
для штрафа (сумма в рублях, не в месяцах — отдельное поле в модели не
заведено) и не для ограничения свободы (на практике чаще дополнительное
наказание к лишению свободы, чем основное; то же совпадение по слову без
понимания смысла путало бы одно с другим). Для этих видов
``punishment_type``/``punishment_term`` в черновике будут пустыми, это не
баг этого скрипта. Если резолютивная часть называет штраф, ограничение
свободы, арест или принудительные работы, экстрактор вид не заполняет
вовсе и не берёт лишение свободы из судимости в описательной части
(06.10.2026).

НЕ пишет в ``data/fixtures/cases`` — тот каталог подключён в конвейер
(``load_cases`` грузит всё без разбора, черновик там значил бы, что судья
уже сегодня увидит непроверенную запись как настоящую практику). Пишет в
``data/drafts/cases/<case_id>.json``; оттуда забирает ``promote``, и
только после того, как все поля из ``needs_human`` заполнены не пустыми
значениями.

Примеры:
  python scripts/draft_case.py draft --text-file приговор.txt \\
      --case-id real-006 --source-url https://bsr.sudrf.ru/... \\
      --court "Ленинский районный суд г. Кирова" --date 2026-05-14 \\
      --out data/drafts/cases/real-006.json

  python scripts/draft_case.py promote --draft data/drafts/cases/real-006.json \\
      --fixture data/fixtures/cases/real_cases_v2.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

ALWAYS_HUMAN = ["title", "summary", "mitigating_codes", "aggravating_codes"]


def _build_document(text: str, case_id: str):
    from second_opinion.domain.documents import Document

    return Document(
        document_id=f"draft-{case_id}",
        filename=f"{case_id}.txt",
        content_type="text/plain",
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _draft_from_facts(facts, case_id: str, court: str, when: str, source_url: str) -> dict:
    fields: dict = {
        "case_id": case_id,
        "title": None,
        "court": court or None,
        "date": when or None,
        "article": None,
        "article_suffix": None,
        "part": None,
        "stage": "completed",
        "recidivism": False,
        "special_procedure": False,
        "guilty_plea": False,
        "jury_trial": False,
        "group": False,
        "mitigating_codes": [],
        "aggravating_codes": [],
        "punishment_type": None,
        "term_months": None,
        "suspended": False,
        "summary": None,
        "synthetic": False,
        "source_url": source_url,
    }
    evidence: dict[str, list[str]] = {}
    final_term = None
    qualifications: list[tuple[int, str | None, int | None, str]] = []

    def note(field: str, quote: str) -> None:
        evidence.setdefault(field, []).append(quote)

    for fact in facts:
        quote = fact.evidence[0].quote if fact.evidence else ""
        if fact.type.value == "qualification":
            triple = (
                fact.value["article"],
                fact.value.get("article_suffix"),
                fact.value.get("part"),
            )
            if triple not in {(a, s, p) for a, s, p, _ in qualifications}:
                qualifications.append((*triple, quote))
            note("article/part", quote)
        elif fact.type.value == "offense_stage":
            fields["stage"] = fact.value
            note("stage", quote)
        elif fact.type.value == "group_offense":
            fields["group"] = True
            note("group", quote)
        elif fact.type.value == "guilty_plea":
            fields["guilty_plea"] = True
            note("guilty_plea", quote)
        elif fact.type.value == "special_procedure":
            fields["special_procedure"] = True
            note("special_procedure", quote)
        elif fact.type.value == "jury_trial":
            fields["jury_trial"] = True
            note("jury_trial", quote)
        elif fact.type.value == "aggravating_recidivism":
            fields["recidivism"] = True
            note("recidivism", quote)
        elif fact.type.value == "punishment_type":
            fields["punishment_type"] = fact.value
            note("punishment_type", quote)
        elif fact.type.value == "punishment_term":
            fields["term_months"] = fact.value
            note("term_months", quote)
        elif fact.type.value == "final_punishment_term":
            final_term = fact.value
            note("term_months — окончательное наказание", quote)
        elif fact.type.value == "suspended_sentence":
            fields["suspended"] = True
            note("suspended", quote)

    if final_term is not None:
        if fields["term_months"] is not None:
            note("term_months — срок за преступление", f"{fields['term_months']:g} мес.")
        fields["term_months"] = final_term

    needs_human = list(ALWAYS_HUMAN)

    if len(qualifications) == 1:
        fields["article"], fields["article_suffix"], fields["part"], _ = qualifications[0]
    elif len(qualifications) > 1:
        needs_human.append("article")
        evidence["article — НЕСКОЛЬКО КАНДИДАТОВ, выбрать вручную"] = [
            f"ст. {a}" + (f".{s}" if s else "") + (f" ч. {p}" if p else "") + f" — «{q}»"
            for a, s, p, q in qualifications
        ]
        first_article, first_suffix, first_part, first_quote = qualifications[0]
        evidence["article — ПРЕДЛОЖЕНИЕ, проверить"] = [
            "первая из найденных (резолютивная часть просматривается первой): "
            + f"ст. {first_article}"
            + (f".{first_suffix}" if first_suffix else "")
            + (f" ч. {first_part}" if first_part else "")
            + f" — «{first_quote}»"
        ]

    for field in ("court", "date", "article", "punishment_type"):
        if fields.get(field) in (None, ""):
            needs_human.append(field)

    return {
        "fields": fields,
        "evidence": evidence,
        "needs_human": sorted(set(needs_human)),
        "warning": (
            "Автодетект понимает только простое отрицание («не возместил», "
            "«без группы лиц», «рецидива не усмотрено»), сложные обороты "
            "читает как утверждение. Вводную часть приговора "
            "(до «У С Т А Н О В И Л») он при поиске статьи, даты и "
            "процедурных фактов пропускает, но судимости подсудимого "
            "пересказываются и в описательной части (например, при "
            "назначении по ст. 70) — оттуда прежние статьи по-прежнему "
            "попадают в кандидаты. «Рецидив в действиях подсудимого суд не "
            "усматривает» совпадёт так же, как «рецидив установлен»; «не "
            "усматривает оснований для ст. 73» — так же, как реальное "
            "условное осуждение. Каждое "
            "поле — включая уже заполненные — сверить с полным текстом "
            "приговора, не только с evidence."
        ),
    }


def cmd_draft(args: argparse.Namespace) -> int:
    from second_opinion.fact_extraction.pattern_extractor import PatternFactExtractor

    text = Path(args.text_file).read_text(encoding="utf-8")
    document = _build_document(text, args.case_id)
    facts = PatternFactExtractor().extract(document)

    draft = _draft_from_facts(facts, args.case_id, args.court, args.date, args.source_url)
    draft["source_sha256"] = document.sha256
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    source_path = out_path.with_suffix(".source.txt")
    source_path.write_text(text, encoding="utf-8")
    out_path.write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Черновик записан: {out_path}")
    print(f"Исходный текст сохранён: {source_path}")
    print(f"Автоматически определено фактов: {len(facts)}")
    print(f"Обязательно заполнить руками: {', '.join(draft['needs_human'])}")
    print(draft["warning"])
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from second_opinion.domain.cases import ComparableCase

    draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    fields = draft["fields"]

    empty = [
        f
        for f in draft["needs_human"]
        if not isinstance(fields.get(f), list) and not fields.get(f)
    ]
    if empty:
        print(
            f"ОШИБКА: не заполнено — {', '.join(empty)}. "
            "Откройте черновик и впишите значения перед promote.",
            file=sys.stderr,
        )
        return 2

    try:
        case = ComparableCase.model_validate(fields)
    except ValidationError as exc:
        print(f"ОШИБКА: черновик не собирается в ComparableCase:\n{exc}", file=sys.stderr)
        return 2

    fixture_path = Path(args.fixture)
    if fixture_path.exists():
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    else:
        payload = {
            "note": (
                "РЕАЛЬНАЯ судебная практика, добавленная через "
                "scripts/draft_case.py (черновик подтверждён человеком "
                "перед добавлением сюда)."
            ),
            "cases": [],
        }
    if any(c["case_id"] == case.case_id for c in payload["cases"]):
        print(f"ОШИБКА: {case.case_id} уже есть в {fixture_path}", file=sys.stderr)
        return 2

    payload["cases"].append(json.loads(case.model_dump_json()))
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Дело {case.case_id} добавлено в {fixture_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_draft = sub.add_parser("draft", help="извлечь черновые факты из текста приговора")
    p_draft.add_argument("--text-file", required=True)
    p_draft.add_argument("--case-id", required=True)
    p_draft.add_argument("--source-url", required=True)
    p_draft.add_argument("--court", default="")
    p_draft.add_argument("--date", default="", help="YYYY-MM-DD, если известна")
    p_draft.add_argument("--out", required=True)
    p_draft.set_defaults(func=cmd_draft)

    p_promote = sub.add_parser("promote", help="перенести проверенный черновик в корпус")
    p_promote.add_argument("--draft", required=True)
    p_promote.add_argument("--fixture", required=True)
    p_promote.set_defaults(func=cmd_promote)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
