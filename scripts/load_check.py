"""Нагрузочная проверка: параллельные анализы через HTTP API.

Загружает документ один раз и запускает ``--requests`` анализов в
``--concurrency`` потоков. Печатает пропускную способность, задержки
(медиана, 95-й процентиль, максимум) и число ошибок по кодам ответа.

Сервер запускается отдельно; ограничение частоты анализа на время замера
снимается переменной ``SO_ANALYZE_RATE_LIMIT=0``, иначе замер упрётся в лимит,
а не в производительность:

  SO_ANALYZE_RATE_LIMIT=0 SO_DATA_DIR=/tmp/so-load second-opinion serve --port 8765
  python scripts/load_check.py --url http://127.0.0.1:8765 \\
      --file data/fixtures/sample_documents/sample_158_special_clean.txt --requests 200 --concurrency 8
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--file", required=True)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--token", default="", help="Bearer-токен, если сервер требует входа")
    args = parser.parse_args(argv)

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    path = Path(args.file)
    with httpx.Client(base_url=args.url, headers=headers, timeout=120) as client:
        upload = client.post("/api/v1/documents/upload", files={"file": (path.name, path.read_bytes())})
        upload.raise_for_status()
        document_id = upload.json()["document_id"]

        def one(_: int) -> tuple[int, float]:
            started = time.perf_counter()
            response = client.post(f"/api/v1/documents/{document_id}/analyze", json={})
            return response.status_code, time.perf_counter() - started

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            results = list(pool.map(one, range(args.requests)))
        elapsed = time.perf_counter() - started

    codes = Counter(code for code, _ in results)
    latencies = [seconds for code, seconds in results if code == 200]
    print(f"документ: {path.name}, {len(path.read_text(encoding='utf-8'))} символов")
    print(f"запросов: {args.requests}, потоков: {args.concurrency}, время: {elapsed:.1f} с")
    print(f"пропускная способность: {len(latencies) / elapsed:.1f} анализов/с")
    if latencies:
        print(
            f"задержка: медиана {statistics.median(latencies) * 1000:.0f} мс, "
            f"95% {percentile(latencies, 0.95) * 1000:.0f} мс, максимум {max(latencies) * 1000:.0f} мс"
        )
    print("коды ответов:", dict(codes))
    return 0 if codes.get(200, 0) == args.requests else 1


if __name__ == "__main__":
    sys.exit(main())
