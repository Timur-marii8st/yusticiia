"""Документация обязана совпадать с репозиторием.

Две вещи гниют тише всего: ссылка на переехавший файл и карта модулей,
списанная с раскладки полугодичной давности. Обе выглядят достоверно и
обе врут читателю, который только пришёл в проект. Проверяются здесь,
потому что глазами их никто не сверяет.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "src" / "second_opinion"

IGNORED = {".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".claude"}

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _markdown_files() -> list[Path]:
    return [
        p
        for p in sorted(PROJECT_ROOT.rglob("*.md"))
        if not any(part in IGNORED for part in p.relative_to(PROJECT_ROOT).parts)
    ]


def test_markdown_links_resolve() -> None:
    """Относительная ссылка обязана открываться.

    Регрессия: после переноса рабочих материалов из репозитория часть
    ссылок вела в никуда. Ничего не ломалось — просто читатель получал
    пустоту там, где ему обещали объяснение.
    """
    broken: list[str] = []
    for path in _markdown_files():
        for match in LINK.finditer(path.read_text(encoding="utf-8")):
            target = match.group(1).split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (path.parent / target).resolve().exists():
                broken.append(f"{path.relative_to(PROJECT_ROOT)} -> {target}")

    assert not broken, "ссылки ведут в никуда:\n  " + "\n  ".join(broken)


def test_architecture_describes_the_real_layout() -> None:
    """Карта модулей в docs/ARCHITECTURE.md и каталоги на диске — одно и то же.

    Прежняя карта в README отстала настолько, что называла `cli.py` файлом
    (давно пакет), не знала про `export/` и `api/routers/`, а про учётные
    записи писала «in-memory, MVP» при живом хранилище на PostgreSQL.
    """
    text = (PROJECT_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    start = text.index("## Раскладка пакета")
    block = text[text.index("src/second_opinion/", start) : text.index("evaluation/", start)]

    described = {
        line.strip().rstrip("/").split()[0].rstrip("/")
        for line in block.splitlines()
        if line.startswith("  ") and line.strip() and not line.strip().startswith("#")
    }
    described_packages = {name for name in described if not name.endswith(".py")}
    described_modules = {name for name in described if name.endswith(".py")}

    real_packages = {
        p.name for p in PACKAGE.iterdir() if p.is_dir() and p.name != "__pycache__"
    }
    real_modules = {p.name for p in PACKAGE.glob("*.py") if p.name != "__init__.py"}

    assert described_packages == real_packages, (
        f"в карте лишние: {sorted(described_packages - real_packages)}; "
        f"не описаны: {sorted(real_packages - described_packages)}"
    )
    assert described_modules == real_modules, (
        f"в карте лишние: {sorted(described_modules - real_modules)}; "
        f"не описаны: {sorted(real_modules - described_modules)}"
    )


def test_docs_index_lists_every_document() -> None:
    """Указатель не умалчивает ни об одном документе в docs/.

    Документ, которого нет в указателе, не существует для читателя: его
    находят только поиском по репозиторию, а значит — случайно.
    """
    index = (PROJECT_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    listed = {m.group(1).split("#", 1)[0] for m in LINK.finditer(index)}

    docs = {
        p.name
        for p in (PROJECT_ROOT / "docs").glob("*.md")
        if p.name != "README.md"
    }
    missing = sorted(name for name in docs if name not in listed)

    assert not missing, f"не названы в docs/README.md: {missing}"


def test_working_notes_stay_out_of_the_repository() -> None:
    """Заметки о ведении разработки в поставляемый репозиторий не попадают.

    Проект отдаётся жюри конкурса. Материалы о том, как над ним работает
    ассистент, — не часть продукта, и читателя они только путают.
    """
    assert not (PROJECT_ROOT / ".claude").exists(), (
        "каталог .claude снова в репозитории: рабочие материалы хранятся вне него"
    )

    ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/" in ignore, "правило .claude/ пропало из .gitignore"


def test_markdown_follows_the_house_style() -> None:
    """Оформление документов единообразно.

    Проверяются правила, которые можно проверить машиной: один заголовок первого уровня, иерархия без
    пропусков, у блока кода назван язык. Без этого подсветка выбирается
    наугад, а оглавление по заголовкам собирается неправильно.
    """
    problems: list[str] = []
    for path in _markdown_files():
        name = path.relative_to(PROJECT_ROOT)
        in_fence = False
        first_levels = 0
        previous = 0
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                if not in_fence and not line.strip()[3:].strip():
                    problems.append(f"{name}:{number} блок кода без языка")
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            heading = re.match(r"^(#{1,6})\s", line)
            if not heading:
                continue
            level = len(heading.group(1))
            if level == 1:
                first_levels += 1
            if previous and level > previous + 1:
                problems.append(f"{name}:{number} пропущен уровень заголовка")
            previous = level
        if first_levels != 1:
            problems.append(f"{name} заголовков первого уровня: {first_levels}, нужен один")

    assert not problems, "оформление разошлось с правилами:\n  " + "\n  ".join(problems)


def test_documented_module_entry_point_runs() -> None:
    """`python -m second_opinion.cli` работает: README обещает этот путь.

    Регрессия: пока команды жили в одном модуле `cli.py`, запуск через
    `-m` получался сам собой. После разбиения на пакет он отвалился —
    у пакета точка входа объявляется отдельным `__main__.py`. Тесты
    молчали: они зовут `main()` напрямую, а README отсылал читателя к
    команде, которая падала с «is a package and cannot be directly
    executed».
    """
    import subprocess
    import sys

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "python -m second_opinion.cli" in readme, (
        "README больше не описывает этот путь — проверка потеряла смысл"
    )

    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "second_opinion.cli", "--help"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert "serve" in result.stdout


def test_license_file_matches_the_declared_one() -> None:
    """Файл лицензии есть, и он тот же, что объявлен в пакете.

    Регрессия: `pyproject.toml` объявлял MIT, а файла лицензии в
    репозитории не было вовсе. Формально это значит «все права
    защищены» — то есть ни жюри, ни кто-либо другой не вправе даже
    запустить код, тогда как метаданные пакета обещали обратное.
    """
    import tomllib

    license_file = PROJECT_ROOT / "LICENSE"
    assert license_file.exists(), "нет файла LICENSE"

    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = metadata["project"]["license"]
    assert isinstance(declared, str), (
        "лицензия объявлена устаревшей табличной формой; нужна строка SPDX"
    )

    text = license_file.read_text(encoding="utf-8")
    assert declared.lower() in text.lower().split("\n", 1)[0], (
        f"в pyproject.toml объявлено {declared!r}, а LICENSE начинается иначе: "
        f"{text.splitlines()[0]!r}"
    )
    assert "Copyright (c)" in text, "в LICENSE нет строки об авторском праве"
    assert metadata["project"]["license-files"] == ["LICENSE"], (
        "файл лицензии не попадёт в сборку пакета"
    )


def test_markdown_tables_are_well_formed() -> None:
    """Разделитель таблицы совпадает по числу столбцов с заголовком.

    Регрессия: правка, добавлявшая столбец «Источник» в требования,
    прошлась по всем разделителям подряд — включая таблицы плана работ,
    где столбцов три. Заголовок остался трёхстолбцовым, разделитель стал
    четырёхстолбцовым, и таблица перестала быть таблицей. Прогон при этом
    был зелёным: разметку документов до сих пор никто не проверял.
    """
    broken: list[str] = []
    for path in _markdown_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if not line.startswith("|---"):
                continue
            header = lines[number - 2] if number >= 2 else ""
            if not header.startswith("|"):
                broken.append(f"{path.relative_to(PROJECT_ROOT)}:{number} разделитель без заголовка")
                continue
            if header.count("|") != line.count("|"):
                broken.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{number} "
                    f"столбцов в заголовке {header.count('|') - 1}, "
                    f"в разделителе {line.count('|') - 1}"
                )

    assert not broken, "таблицы разъехались:\n  " + "\n  ".join(broken)


def test_domain_model_lists_every_fact_type() -> None:
    from second_opinion.domain.enums import FactType

    text = (PROJECT_ROOT / "docs" / "DOMAIN_MODEL.md").read_text(encoding="utf-8")
    missing = [t.value for t in FactType if f"`{t.value}`" not in text]
    assert not missing, f"в docs/DOMAIN_MODEL.md не названы типы обстоятельств: {missing}"


def test_package_version_is_declared_consistently() -> None:
    import tomllib

    import second_opinion

    declared = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared["project"]["version"] == second_opinion.__version__


def test_lock_pins_every_runtime_dependency() -> None:
    import tomllib

    declared = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = {
        re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0].lower().replace("_", "-")
        for requirement in [
            *declared["dependencies"],
            *declared["optional-dependencies"]["pg"],
            *declared["optional-dependencies"]["ocr"],
        ]
    }
    lock = (PROJECT_ROOT / "requirements.lock").read_text(encoding="utf-8")
    pinned = {
        match.group(1).lower().replace("_", "-"): match.group(0)
        for match in re.finditer(r"^([A-Za-z0-9_.-]+)==[^\s\\]+", lock, flags=re.MULTILINE)
    }
    missing = sorted(name for name in names if name not in pinned)
    assert not missing, f"в requirements.lock нет закреплённых версий: {missing}"
    assert lock.count("--hash=sha256:") >= len(pinned), "у части пакетов нет хэшей"
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes -r requirements.lock" in dockerfile
