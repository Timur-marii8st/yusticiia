"""Проверка контракта OpenAI-совместимого сервера (vLLM, Ollama) — НТ-02.

Решение по закрытому контуру (docs/TZ.md, раздел 2.10) держится на том, что
vLLM говорит тем же протоколом, что и ``OpenAICompatibleProvider``. До
живого прогона это совпадение было сверено только по документации. Скрипт
проверяет его на запущенном сервере:

1. ``GET {base}/models`` — сервер отвечает и называет нужную модель;
2. ``POST {base}/chat/completions`` — тот же вид запроса, что шлёт адаптер,
   ответ содержит текст в ``choices[0].message.content``;
3. ``OpenAICompatibleProvider.generate_structured`` — настоящий адаптер на
   том же сервере (результат может быть пустым: малая модель не обязана
   вернуть годный JSON, проверяется транспорт, а не качество).

Параметры — из окружения, как у самой системы::

    SO_OPENAI_BASE_URL=http://127.0.0.1:8001/v1 SO_LLM_MODEL=Qwen/Qwen2.5-0.5B-Instruct \\
        python scripts/check_llm_contract.py

Код возврата 0 — шаги 1 и 2 прошли; шаг 3 только сообщается.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from second_opinion.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402


class _Answer(BaseModel):
    answer: str


def check(base_url: str, api_key: str, model: str, timeout: float = 600.0) -> int:
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    ok = True

    models = httpx.get(f"{base}/models", headers=headers, timeout=timeout)
    ids = [m.get("id") for m in models.json().get("data", [])] if models.is_success else []
    found = model in ids
    print(f"1. GET /models: HTTP {models.status_code}, модели: {ids or '—'}")
    if not models.is_success or not found:
        print(f"   ОШИБКА: модель {model!r} не названа в ответе")
        ok = False

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "Отвечай одним словом."},
            {"role": "user", "content": "Столица России?"},
        ],
    }
    response = httpx.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout)
    content = None
    if response.is_success:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError):
            content = None
    print(f"2. POST /chat/completions: HTTP {response.status_code}, choices[0].message.content: {content!r}")
    if not isinstance(content, str):
        print("   ОШИБКА: текст ответа не найден по пути choices[0].message.content")
        ok = False

    provider = OpenAICompatibleProvider(base, api_key, model, timeout=timeout)
    result, meta = provider.generate_structured(
        prompt_name="contract_check",
        prompt_version="1",
        prompt_template='Верни JSON {"answer": "<одно слово>"}: столица России.',
        document_text="Москва — столица России.",
        schema=_Answer,
        max_attempts=2,
    )
    print(f"3. OpenAICompatibleProvider: попыток {meta.attempts}, структурированный ответ: {result!r}")
    if result is None:
        print("   (пусто — для малой модели нормально: проверялся транспорт, не качество)")
    return 0 if ok else 1


def main() -> int:
    base = os.environ.get("SO_OPENAI_BASE_URL", "")
    model = os.environ.get("SO_LLM_MODEL", "")
    if not base or not model:
        print("Нужны SO_OPENAI_BASE_URL и SO_LLM_MODEL.", file=sys.stderr)
        return 2
    return check(base, os.environ.get("SO_OPENAI_API_KEY", "local"), model)


if __name__ == "__main__":
    raise SystemExit(main())
