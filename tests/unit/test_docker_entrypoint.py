"""Точка входа контейнера: пустой том с данными получает нормы из образа."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "docker-entrypoint.sh"


def _run(data: Path, image: Path, *command: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SO_DATA_DIR": str(data), "SO_IMAGE_FIXTURES": str(image)}
    return subprocess.run(
        ["sh", str(SCRIPT), *command], env=env, capture_output=True, text=True, check=False
    )


def _image_fixtures(root: Path) -> Path:
    image = root / "image-fixtures"
    (image / "norms").mkdir(parents=True)
    (image / "norms" / "n.json").write_text("{}", encoding="utf-8")
    (image / "cases").mkdir()
    return image


def test_empty_volume_gets_the_fixtures_from_the_image(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()

    result = _run(data, _image_fixtures(tmp_path), "echo", "started")

    assert result.returncode == 0 and "started" in result.stdout
    assert (data / "fixtures" / "norms" / "n.json").exists()
    assert (data / "fixtures" / "cases").is_dir()
    assert "копирую" in result.stderr


def test_missing_data_directory_is_created(tmp_path: Path) -> None:
    data = tmp_path / "does-not-exist-yet"

    result = _run(data, _image_fixtures(tmp_path), "true")

    assert result.returncode == 0
    assert (data / "fixtures" / "norms" / "n.json").exists()


def test_existing_fixtures_are_left_alone(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "fixtures" / "norms").mkdir(parents=True)
    own = data / "fixtures" / "norms" / "own.json"
    own.write_text('{"edited": true}', encoding="utf-8")

    result = _run(data, _image_fixtures(tmp_path), "true")

    assert result.returncode == 0 and result.stderr == ""
    assert own.read_text(encoding="utf-8") == '{"edited": true}'
    assert not (data / "fixtures" / "norms" / "n.json").exists()


def test_the_command_exit_code_is_passed_through(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "fixtures" / "norms").mkdir(parents=True)

    assert _run(data, _image_fixtures(tmp_path), "sh", "-c", "exit 7").returncode == 7


def test_a_missing_image_copy_stops_the_start(tmp_path: Path) -> None:
    result = _run(tmp_path / "data", tmp_path / "nowhere", "echo", "started")

    assert result.returncode != 0 and "started" not in result.stdout
