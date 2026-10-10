"""Черновики корпуса из официальных PDF Верховного Суда (vsrf.ru).

НЕ вызывается конвейером в runtime. Скрипт скачивает PDF постановления
Пленума или Обзора судебной практики, режет текст на пункты и пишет
draft-фикстуру: ``verification_status=draft``, перевод в ``verified`` только
после юридической сверки (docs/DATA_SOURCES.md). Текст не генерируется и не
пересказывается: в фикстуру попадает извлечённый из PDF текст пункта, а у
Обзора — тезис позиции и реквизиты определения.

Ограничения извлечения, которые сверяет юрист:

- надстрочные индексы PDF передаются цифрой подряд: «401» означает 40¹,
  «2281» — 228¹; номера пунктов с индексом (28¹) распознаются отдельно;
- заголовки разделов отрезаются по форме (короткая строка без точки в конце);
- пометки «Абзац исключен ...» и «Пункт исключен ...» сохраняются: это часть
  официального текста.

Примеры:
  python scripts/fetch_vsrf_document.py plenum \\
      --url https://www.vsrf.ru/media-proxy/upload/iblock/aca/tozewdcjp1nkjyu6dvx0cgq710g21f7m.pdf \\
      --points 27,28,28.1,29 --out data/fixtures/norms/plenum_58_2015_draft.json
  python scripts/fetch_vsrf_document.py review \\
      --url https://www.vsrf.ru/media-proxy/upload/iblock/5cf/sjjrtcmqewhepaahib7g4spn0cult30e.pdf \\
      --points 1,19 --out data/fixtures/norms/review_sentencing_2024_draft.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

PLENUM_58 = {
    "code": "ППВС РФ",
    "number": 58,
    "adopted": "2015-12-22",
    "edition": "2025-12-23",
    "source_document": (
        "Постановление Пленума Верховного Суда РФ от 22.12.2015 № 58 «О практике "
        "назначения судами Российской Федерации уголовного наказания» (с изменениями "
        "от 29.11.2016 № 56, от 18.12.2018 № 43, от 23.12.2025 № 40)"
    ),
}
REVIEW_2024 = {
    "code": "Обзор практики ВС РФ о назначении наказания (утв. 18.12.2024)",
    "year": 2024,
    "approved": "2024-12-18",
    "source_document": (
        "Обзор практики Верховного Суда Российской Федерации по применению норм "
        "уголовного закона о назначении наказания и иных мер уголовно-правового "
        "характера (по материалам уголовных дел, рассмотренных в кассационном "
        "порядке), утвержден Президиумом Верховного Суда РФ 18.12.2024"
    ),
}

_POINT = re.compile(r"^(\d{1,3})\.\s")
_PAGE_NUMBER = re.compile(r"\s*\d{1,3}\s*")
_INDENT = re.compile(r"^\s{4,}\S")
_DECISION = re.compile(r"^(?:Кассационное\s+)?(?:Определение|Постановление)\s+№\s*\S+")
_KEEP_TAILS = ("Абзац исключен", "Пункт исключен")


def download(url: str, target: Path, timeout: float = 120.0) -> Path:
    import httpx

    response = httpx.get(
        url, timeout=timeout, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"по адресу {url} не PDF")
    target.write_bytes(response.content)
    return target


def paragraphs(pdf_path: Path) -> list[str]:
    """Абзацы текста PDF: новая строка с отступом начинает новый абзац."""
    from pypdf import PdfReader

    result: list[str] = []
    current: list[str] = []
    for page in PdfReader(str(pdf_path)).pages:
        for line in page.extract_text(extraction_mode="layout").split("\n"):
            if not line.strip() or _PAGE_NUMBER.fullmatch(line):
                continue
            if _INDENT.match(line) and current:
                result.append(" ".join(current))
                current = []
            current.append(re.sub(r"\s+", " ", line.strip()))
    if current:
        result.append(" ".join(current))
    return result


def split_points(paras: list[str]) -> dict[str, list[str]]:
    """Пункты по номерам; «281.» после п. 28 — подпункт 28¹, пропуски номеров допустимы."""
    points: dict[str, list[str]] = {}
    current: str | None = None
    last = 0
    for para in paras:
        match = _POINT.match(para)
        key = None
        if match:
            number = match.group(1)
            if last and number.startswith(str(last)) and len(number) == len(str(last)) + 1:
                key = f"{last}.{number[len(str(last)):]}"
            elif last < int(number) <= last + 5:
                last = int(number)
                key = number
        if key is not None:
            current = key
            points[key] = [para]
        elif current is not None:
            points[current].append(para)
    return points


def strip_heading(text: str) -> str:
    """Отрезать заголовок следующего раздела, прилипший к концу пункта."""
    stripped = text.rstrip()
    if re.search(r"[.;:»)]$", stripped):
        return stripped
    cut = max(stripped.rfind(". "), stripped.rfind("; "), stripped.rfind(": "))
    tail = stripped[cut + 2:] if cut != -1 else ""
    if tail and len(tail) <= 120 and tail[0].isupper() and not tail.startswith(_KEEP_TAILS):
        return stripped[: cut + 1]
    return stripped


def point_text(key: str, paras: list[str]) -> str:
    text = strip_heading(" ".join(paras))
    number = key.replace(".", "")
    return re.sub(rf"^{re.escape(number)}\.\s", f"{key}. ", text, count=1)


def _title(prefix: str, text: str) -> str:
    body = re.sub(r"^\d+(?:\.\d+)?\.\s", "", text)
    sentence = re.split(r"(?<=[.;])\s", body, maxsplit=1)[0]
    if len(sentence) > 110:
        sentence = sentence[:110].rsplit(" ", 1)[0] + "…"
    return f"{prefix}: {sentence}"


def _ru_date(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _norm_id_suffix(key: str) -> tuple[int, str | None, str]:
    main, _, sub = key.partition(".")
    return int(main), (sub or None), key.replace(".", "-")


def _version(norm_id: str, version_id: str, text: str, effective_from: str, url: str,
             edition: str, retrieved_at: str) -> dict:
    return {
        "version_id": version_id,
        "norm_id": norm_id,
        "text": text,
        "sanctions": [],
        "effective_from": effective_from,
        "effective_to": None,
        "source_url": url,
        "source_edition": edition,
        "retrieved_at": retrieved_at,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "verification_status": "draft",
        "synthetic": False,
    }


def plenum_payload(points: dict[str, list[str]], selected: list[str], url: str,
                   applies_to: dict[str, list[str]], retrieved_at: str) -> dict:
    meta = PLENUM_58
    norms = []
    for key in selected:
        text = point_text(key, points[key])
        part, suffix, slug = _norm_id_suffix(key)
        norm_id = f"ppvs-rf:n{meta['number']}-p{slug}"
        norms.append(
            {
                "norm_id": norm_id,
                "article": meta["number"],
                "part": part,
                "part_suffix": suffix,
                "title": _title(f"Пленум ВС РФ № {meta['number']}, п. {key}", text),
                "applies_to": applies_to.get(key, []),
                "versions": [
                    _version(norm_id, f"v-{meta['edition']}", text, meta["adopted"], url,
                             f"vsrf.ru, ред. от {meta['edition']}", retrieved_at)
                ],
            }
        )
    return {
        "code": meta["code"],
        "source_document": meta["source_document"],
        "note": (
            "Черновик: текст пунктов извлечён автоматически из официального PDF на "
            "vsrf.ru (scripts/fetch_vsrf_document.py), не сверен юристом. Текст в "
            f"редакции от {meta['edition']}, дата начала — дата постановления: история "
            "изменений отдельных пунктов не прослежена. Надстрочные индексы переданы "
            "цифрой подряд («401» = 40¹). Поле applies_to — к каким проверкам относится "
            "пункт; хранилище норм его не читает."
        ),
        "norms": norms,
    }


def review_positions(paras: list[str]) -> dict[str, tuple[str, str]]:
    """Позиции Обзора: тезис (первый абзац позиции) и реквизиты определения."""
    result: dict[str, tuple[str, str]] = {}
    for key, block in split_points(paras).items():
        decision = next((p for p in reversed(block) if _DECISION.match(p)), "")
        result[key] = (block[0], _DECISION.match(decision).group(0) if decision else "")
    return result


def review_payload(positions: dict[str, tuple[str, str]], selected: list[str], url: str,
                   applies_to: dict[str, list[str]], retrieved_at: str) -> dict:
    meta = REVIEW_2024
    norms = []
    for key in selected:
        thesis, decision = positions[key]
        text = strip_heading(thesis) + (f" ({decision})" if decision else "")
        part, suffix, slug = _norm_id_suffix(key)
        norm_id = f"vsrf-review:{meta['approved']}-p{slug}"
        norms.append(
            {
                "norm_id": norm_id,
                "article": meta["year"],
                "part": part,
                "part_suffix": suffix,
                "title": _title(f"Обзор ВС РФ от {_ru_date(meta['approved'])}, п. {key}", thesis),
                "applies_to": applies_to.get(key, []),
                "versions": [
                    _version(norm_id, f"v-{meta['approved']}", text, meta["approved"], url,
                             f"vsrf.ru, утв. {meta['approved']}", retrieved_at)
                ],
            }
        )
    return {
        "code": meta["code"],
        "source_document": meta["source_document"],
        "note": (
            "Черновик: тезисы позиций Обзора извлечены автоматически из официального "
            "PDF на vsrf.ru (scripts/fetch_vsrf_document.py), не сверены юристом. В "
            "корпус взяты тезис и номер определения; описание дела остаётся в "
            "источнике. Надстрочные индексы переданы цифрой подряд («2281» = 228¹). "
            "Поле applies_to — к каким проверкам относится позиция; хранилище норм его "
            "не читает."
        ),
        "norms": norms,
    }


def _applies(raw: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in filter(None, (chunk.strip() for chunk in raw.split(";"))):
        key, _, rules = item.partition("=")
        result[key.strip()] = [r.strip() for r in rules.split(",") if r.strip()]
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="kind", required=True)
    for kind in ("plenum", "review"):
        command = sub.add_parser(kind)
        command.add_argument("--url", required=True, help="адрес PDF на vsrf.ru")
        command.add_argument("--pdf", help="уже скачанный PDF вместо загрузки")
        command.add_argument("--points", required=True, help="номера пунктов через запятую: 27,28.1")
        command.add_argument("--applies-to", default="", help="«27=R-001;47=R-D04,R-002»")
        command.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected = [p.strip() for p in args.points.split(",") if p.strip()]
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(args.pdf) if args.pdf else download(args.url, Path(tmp) / "source.pdf")
        paras = paragraphs(pdf)
    today = date.today().isoformat()
    applies = _applies(args.applies_to)
    if args.kind == "plenum":
        points = split_points(paras)
        missing = [p for p in selected if p not in points]
        payload = plenum_payload(points, selected, args.url, applies, today)
    else:
        positions = review_positions(paras)
        missing = [p for p in selected if p not in positions]
        payload = review_payload(positions, selected, args.url, applies, today)
    if missing:
        print(f"ОШИБКА: в документе нет пунктов {missing}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"записано {len(payload['norms'])} пунктов в {args.out} (draft)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
