"""Модель только в закрытом контуре: облачный адрес останавливает сборку."""

from __future__ import annotations

import dataclasses

import pytest

from second_opinion.api.deps import build_pipeline
from second_opinion.config import load_config
from second_opinion.llm.contour import OutsideContourError, is_inside_contour


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8001/v1",
        "http://[::1]:8000/v1",
        "http://10.20.0.5:8000/v1",
        "http://192.168.1.10/v1",
        "http://172.16.4.2:8000/v1",
        "http://vllm:8000/v1",
        "http://llm.court.internal/v1",
        "http://gpu01.local:8000/v1",
    ],
)
def test_addresses_inside_the_contour_are_accepted(url: str) -> None:
    assert is_inside_contour(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://llm.api.cloud.yandex.net/v1",
        "https://gigachat.devices.sberbank.ru/api/v1",
        "https://api.giga.chat/v1",
        "http://8.8.8.8/v1",
        "",
        "not a url",
    ],
)
def test_public_and_broken_addresses_are_refused(url: str) -> None:
    assert not is_inside_contour(url)


def test_operator_can_name_an_internal_server_with_a_regular_domain() -> None:
    url = "https://llm.sud.example.ru/v1"
    assert not is_inside_contour(url)
    assert is_inside_contour(url, ["LLM.sud.example.ru"])


def _config(tmp_path, **changes):
    base = dataclasses.replace(
        load_config(), data_dir=tmp_path, storage_backend="file", rag_mode="lexical"
    )
    return dataclasses.replace(base, **changes)


def test_pipeline_refuses_a_cloud_model(tmp_path) -> None:
    config = _config(
        tmp_path,
        llm_provider="openai_compatible",
        openai_base_url="https://api.openai.com/v1",
        openai_api_key="k",
        llm_model="m",
    )
    with pytest.raises(OutsideContourError, match="вне закрытого контура"):
        build_pipeline(config)


def test_pipeline_accepts_a_model_in_the_docker_network(tmp_path) -> None:
    config = _config(
        tmp_path,
        llm_provider="openai_compatible",
        openai_base_url="http://vllm:8000/v1",
        openai_api_key="k",
        llm_model="m",
    )
    assert build_pipeline(config).llm_provider.name == "openai_compatible"


@pytest.mark.parametrize("provider", ["gigachat", "yandex", "openai"])
def test_unknown_or_removed_provider_is_an_error_not_a_silent_mock(tmp_path, provider: str) -> None:
    with pytest.raises(ValueError, match="допустимы mock"):
        build_pipeline(_config(tmp_path, llm_provider=provider))
