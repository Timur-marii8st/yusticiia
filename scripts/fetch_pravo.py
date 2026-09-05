"""Офлайн-инжест с publication.pravo.gov.ru в draft-фикстуры норм.

НЕ вызывается конвейером в runtime. Оператор вручную:
  1. скачивает карточку документа: ``fetch-card --eo-number ...``
  2. копирует текст из официального PDF в ``--text-file``
  3. собирает draft-фикстуру: ``build-fixture ...``

Фикстура всегда ``verification_status=draft`` — перевод в ``verified``
только после юридической сверки (см. docs/DATA_SOURCES.md).
Текст никогда не генерируется скриптом: без ``--text-file`` фикстура
не создаётся, чтобы не нарушать инвариант источника (docs/LEGAL_RAG.md).

Примеры:
  python scripts/fetch_pravo.py fetch-card --eo-number 2600202104190001
  python scripts/fetch_pravo.py build-fixture --eo-number 2600202104190001 \\
      --raw data/raw_pravo/2600202104190001.json \\
      --text-file /tmp/official_text.txt --code "УК РФ" --article 158 \\
      --title "Кража" --effective-from 2024-01-01 \\
      --out data/fixtures/norms/pravo_158_amendment.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "https://publication.pravo.gov.ru"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw_pravo"


def api_documents_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/Documents"


def api_document_url(base_url: str, eo_number: str) -> str:
    return base_url.rstrip("/") + f"/api/Document?eoNumber={eo_number}"


def build_source_document(card: dict) -> str:
    """Собрать реквизиты документа из карточки API для поля source_document."""
    name = str(card.get("name") or card.get("title") or "").strip()
    number = str(card.get("number") or "").strip()
    doc_date = str(card.get("documentDate") or card.get("signDate") or "").strip()
    eo = str(card.get("eoNumber") or "").strip()
    publish = str(
        card.get("publishDate") or card.get("publishDateShort") or ""
    ).strip()
    parts = [p for p in (name, f"№ {number}" if number else "", f"от {doc_date}" if doc_date else "") if p]
    base = " ".join(parts) if parts else "Документ с pravo.gov.ru"
    suffix = " ".join(
        p
        for p in (
            f"(электронное опубликование № {eo}" if eo else "",
            f"от {publish})" if publish and eo else (f"({publish})" if publish else ""),
        )
        if p
    )
    return f"{base} {suffix}".strip() if suffix else base


def build_fixture_payload(
    *,
    norm_id: str,
    code: str,
    article: int,
    part: int | None,
    title: str,
    text: str,
    effective_from: str,
    effective_to: str | None,
    source_document: str,
    source_url: str,
    retrieved_at: str,
    version_id: str = "v-pravo-draft",
) -> dict:
    """Собрать payload фикстуры норм. Чистая функция, без сети и файлов."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("текст нормы пуст: скопируйте текст из официального PDF в --text-file")
    sha256 = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    version: dict = {
        "version_id": version_id,
        "norm_id": norm_id,
        "text": cleaned,
        "sanctions": [],
        "effective_from": effective_from,
        "effective_to": effective_to,
        "source_url": source_url,
        "retrieved_at": retrieved_at,
        "sha256": sha256,
        "verification_status": "draft",
        "synthetic": False,
    }
    return {
        "code": code,
        "source_document": source_document,
        "norms": [
            {
                "norm_id": norm_id,
                "code": code,
                "article": article,
                "part": part,
                "title": title,
                "versions": [version],
            }
        ],
    }


def _require_httpx() -> None:
    if httpx is None:
        raise RuntimeError("нужен пакет httpx: pip install -e '.[dev]' (httpx — базовая зависимость)")


def _get_json(url: str, params: dict | None, timeout: float) -> object:
    """GET JSON с понятной ошибкой сети (прокси/VPN/502 на стороне портала)."""
    _require_httpx()
    assert httpx is not None
    try:
        resp = httpx.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"запрос к {url} не удался ({exc}). Проверьте сеть/VPN/прокси "
            "и повторите позже; сырой JSON можно сохранить вручную."
        ) from exc
    return resp.json()


def cmd_fetch_card(args: argparse.Namespace) -> int:
    url = api_document_url(args.base_url, args.eo_number)
    card = _get_json(url, params=None, timeout=args.timeout)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"{args.eo_number}.json"
    out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"карточка сохранена: {out}")
    print(f"source_document-кандидат: {build_source_document(card if isinstance(card, dict) else {})}")
    print("далее: скопируйте текст из официального PDF и вызовите build-fixture")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    params: dict = {}
    for item in args.param:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"плохой --param {item!r}, нужен формат key=value")
        params[key] = value
    params.setdefault("pageSize", str(args.page_size))
    params.setdefault("page", str(args.page))
    payload = _get_json(api_documents_url(args.base_url), params=params, timeout=args.timeout)
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out = raw_dir / f"search_p{args.page}_s{args.page_size}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    items = payload.get("items") if isinstance(payload, dict) else None
    count = len(items) if isinstance(items, list) else "?"
    print(f"поиск сохранён: {out} (записей: {count})")
    return 0


def cmd_build_fixture(args: argparse.Namespace) -> int:
    raw_path = Path(args.raw)
    card: dict = {}
    if raw_path.exists():
        loaded = json.loads(raw_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            # API может вернуть карточку напрямую или обёртку — берём словарь документа.
            card = loaded.get("document", loaded) if isinstance(loaded.get("document"), dict) else loaded
    text = Path(args.text_file).read_text(encoding="utf-8")
    source_document = args.source_document or build_source_document(card)
    source_url = args.source_url or api_document_url(args.base_url, args.eo_number)
    retrieved = args.retrieved_at or date.today().isoformat()
    norm_id = args.norm_id or (
        f"pravo:{args.eo_number}" if args.article is None else f"uk-rf:art-{args.article}"
        + (f"-part-{args.part}" if args.part else "")
        + f"-pravo-{args.eo_number}"
    )
    payload = build_fixture_payload(
        norm_id=norm_id,
        code=args.code,
        article=args.article,
        part=args.part,
        title=args.title,
        text=text,
        effective_from=args.effective_from,
        effective_to=args.effective_to,
        source_document=source_document,
        source_url=source_url,
        retrieved_at=retrieved,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Самопроверка: фикстура обязана грузиться в NormStore (SHA, даты, интервалы).
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from second_opinion.legal_sources.store import NormStore

    store = NormStore()
    store.load_file(out_path)
    print(f"draft-фикстура записана и проверена NormStore: {out_path}")
    print("статус: draft. Перевод в verified — только после юр. сверки с официальным PDF.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p_card = sub.add_parser("fetch-card", help="скачать карточку документа по eoNumber")
    p_card.add_argument("--eo-number", required=True)
    p_card.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    p_card.set_defaults(func=cmd_fetch_card)

    p_search = sub.add_parser("search", help="поиск по /api/Documents, сохранить сырой JSON")
    p_search.add_argument("--param", action="append", default=[], help="key=value, повторяется")
    p_search.add_argument("--page-size", type=int, default=30)
    p_search.add_argument("--page", type=int, default=1)
    p_search.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    p_search.set_defaults(func=cmd_search)

    p_build = sub.add_parser("build-fixture", help="собрать draft-фикстуру из raw + text-file")
    p_build.add_argument("--eo-number", required=True)
    p_build.add_argument("--raw", required=True)
    p_build.add_argument("--text-file", required=True, help="txt, скопированный из официального PDF")
    p_build.add_argument("--code", required=True)
    p_build.add_argument("--article", type=int, required=True)
    p_build.add_argument("--part", type=int, default=None)
    p_build.add_argument("--title", required=True)
    p_build.add_argument("--effective-from", required=True, help="YYYY-MM-DD")
    p_build.add_argument("--effective-to", default=None)
    p_build.add_argument("--out", required=True)
    p_build.add_argument("--norm-id", default=None)
    p_build.add_argument("--source-document", default=None)
    p_build.add_argument("--source-url", default=None)
    p_build.add_argument("--retrieved-at", default=None)
    p_build.set_defaults(func=cmd_build_fixture)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
