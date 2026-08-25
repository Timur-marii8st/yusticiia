from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PromptRef:
    """Версионируемый промпт: имя, версия, текст."""

    def __init__(self, name: str, version: str, text: str) -> None:
        self.name = name
        self.version = version
        self.text = text

    @property
    def full_name(self) -> str:
        return f"{self.name}@{self.version}"


def load_prompt(name: str, version: str = "v1") -> PromptRef:
    path = PROMPTS_DIR / f"{name}_{version}.md"
    if not path.exists():
        raise FileNotFoundError(f"промпт не найден: {path}")
    return PromptRef(name=name, version=version, text=path.read_text(encoding="utf-8"))
