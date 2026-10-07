"""Настройки vLLM в docker-compose.yml, которые нельзя потерять при правке.

Проверка текстовая: разбор YAML потребовал бы лишней зависимости, а нужны
только несколько строк, от которых зависит закрытость контура.
"""

from __future__ import annotations

from pathlib import Path

import pytest

COMPOSE = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")


def _service(name: str) -> str:
    start = COMPOSE.index(f"\n  {name}:\n")
    rest = COMPOSE[start + 1 :]
    following = [i for i in (rest.find("\n  vllm-cpu:\n"), rest.find("\nvolumes:\n")) if i > 0]
    return rest[: min(following)] if following else rest


GPU = _service("vllm")
CPU = _service("vllm-cpu")


@pytest.mark.parametrize("block", [GPU, CPU], ids=["vllm", "vllm-cpu"])
def test_server_is_closed_and_collects_no_statistics(block: str) -> None:
    assert '"127.0.0.1:8001:8000"' in block
    assert '"--api-key"' in block
    assert "${SO_OPENAI_API_KEY:-local}" in block
    assert 'VLLM_NO_USAGE_STATS: "1"' in block
    assert 'DO_NOT_TRACK: "1"' in block


@pytest.mark.parametrize("block", [GPU, CPU], ids=["vllm", "vllm-cpu"])
def test_image_version_is_pinned(block: str) -> None:
    image = next(line for line in block.splitlines() if line.strip().startswith("image:"))
    assert "latest" not in image
    assert ":-v" in image


@pytest.mark.parametrize("block", [GPU, CPU], ids=["vllm", "vllm-cpu"])
def test_weights_load_only_as_safetensors_without_remote_code(block: str) -> None:
    assert '"--load-format"' in block
    assert "${VLLM_LOAD_FORMAT:-safetensors}" in block
    assert '"--no-trust-remote-code"' in block


def test_gpu_service_limits_context_and_restarts() -> None:
    assert '"--max-model-len"' in GPU
    assert "${VLLM_MAX_MODEL_LEN:-16384}" in GPU
    assert '"--generation-config"' in GPU
    assert "restart: unless-stopped" in GPU
    assert "driver: nvidia" in GPU
