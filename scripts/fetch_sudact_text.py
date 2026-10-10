"""Текст судебного акта со страницы СудАкта в файл для ``draft_case.py``.

Страница акта (``https://sudact.ru/regular/doc/...``) содержит навигацию и
рекламу; скрипт оставляет текст от заголовка «ПРИГОВОР» до блока «Суд:»
и записывает его в UTF-8::

    python scripts/fetch_sudact_text.py https://sudact.ru/regular/doc/XXXX/ приговор.txt

Акты опубликованы судом и обезличены при публикации (ФЗ-262); скрипт не
обходит ограничения сайта и делает один запрос.
"""

from __future__ import annotations

import html as html_module
import re
import sys
from pathlib import Path

import httpx

START_MARK = 'class="hr-h1"'
END_MARK = "\nСуд:\n"


def page_to_text(raw: str) -> str:
    start = raw.find(START_MARK)
    segment = raw[start + len(START_MARK) :] if start >= 0 else raw
    segment = re.sub(r"^\s*/?>", "", segment)
    segment = re.sub(r"<script.*?</script>", "", segment, flags=re.S)
    segment = re.sub(r"<!--.*?-->", "", segment, flags=re.S)
    segment = re.sub(r"<br\s*/?>", "\n", segment, flags=re.I)
    segment = re.sub(r"</(p|div|tr|center|h\d)>", "\n", segment, flags=re.I)
    segment = re.sub(r"<[^>]+>", "", segment)
    text = html_module.unescape(segment).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    end = text.find(END_MARK)
    return (text[:end] if end > 0 else text).strip()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Использование: fetch_sudact_text.py URL ФАЙЛ.txt", file=sys.stderr)
        return 2
    url, out = args
    response = httpx.get(
        url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60.0, follow_redirects=True
    )
    if response.status_code != 200:
        print(f"ОШИБКА: HTTP {response.status_code} для {url}", file=sys.stderr)
        return 2
    text = page_to_text(response.text)
    if not text:
        print("ОШИБКА: текст акта на странице не найден", file=sys.stderr)
        return 2
    Path(out).write_text(text + "\n", encoding="utf-8")
    print(f"Записано {len(text)} символов в {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
