from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from draft_case import cmd_draft, cmd_promote  # noqa: E402

SAMPLE_TEXT = (
    "Подсудимый обвиняется в совершении преступления, предусмотренного "
    "ч. 1 ст. 158 УК РФ.\n"
    "Подсудимый вину признал полностью, в содеянном раскаялся.\n"
    "Уголовное дело рассмотрено в особом порядке принятия судебного решения.\n"
    "Суд, назначая наказание, учитывает, что рецидива преступлений судом "
    "не усмотрено.\n"
    "Назначить наказание в виде лишения свободы на срок 1 (один) год "
    "6 (шесть) месяцев, на основании ст. 73 УК РФ наказание считать "
    "условным.\n"
)


class _Args:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _draft(tmp_path: Path, *, case_id: str = "real-test-001") -> Path:
    text_file = tmp_path / "sentence.txt"
    text_file.write_text(SAMPLE_TEXT, encoding="utf-8")
    out = tmp_path / "draft.json"
    code = cmd_draft(
        _Args(
            text_file=str(text_file),
            case_id=case_id,
            source_url="https://example.org/case",
            court="Тестовый суд",
            date="2026-01-01",
            out=str(out),
        )
    )
    assert code == 0
    return out


def test_draft_extracts_known_facts(tmp_path: Path) -> None:
    draft_path = _draft(tmp_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    fields = draft["fields"]

    assert fields["article"] == 158
    assert fields["part"] == 1
    assert fields["guilty_plea"] is True
    assert fields["special_procedure"] is True
    assert fields["punishment_type"] == "imprisonment"
    assert fields["term_months"] == 18.0
    assert fields["suspended"] is True
    assert fields["source_url"] == "https://example.org/case"


def test_multiple_qualifications_are_not_silently_resolved(tmp_path: Path) -> None:
    """Найдено на настоящем деле со СудАкт.ру (Приговор № 1-48/2026,

    Райчихинский городской суд): текст называл прежние судимости
    подсудимого (ч. 1 ст. 158, ч. 1 ст. 166 УК РФ) раньше, чем текущее
    обвинение, и extractor молча взял первую попавшуюся статью — из чужой
    строки судимости, а не из приговора по существу. Одна статья —
    заполняем; больше одной — не гадаем.
    """
    text = (
        "Ранее судим 19.11.2021 по ч. 1 ст. 158 УК РФ.\n"
        "Также судим по ч. 1 ст. 166 УК РФ.\n"
        "Подсудимый обвиняется в совершении преступления, "
        "предусмотренного ч. 2 ст. 159 УК РФ.\n"
    )
    text_file = tmp_path / "sentence.txt"
    text_file.write_text(text, encoding="utf-8")
    out = tmp_path / "draft.json"
    cmd_draft(
        _Args(
            text_file=str(text_file),
            case_id="real-ambiguous",
            source_url="https://example.org/case",
            court="",
            date="",
            out=str(out),
        )
    )
    draft = json.loads(out.read_text(encoding="utf-8"))

    assert draft["fields"]["article"] is None
    assert draft["fields"]["part"] is None
    assert "article" in draft["needs_human"]
    candidates = draft["evidence"]["article — НЕСКОЛЬКО КАНДИДАТОВ, выбрать вручную"]
    assert len(candidates) == 3
    assert any("158" in c for c in candidates)
    assert any("166" in c for c in candidates)
    assert any("159" in c for c in candidates)
    suggestion = draft["evidence"]["article — ПРЕДЛОЖЕНИЕ, проверить"]
    assert len(suggestion) == 1
    assert suggestion[0].startswith("первая из найденных")


def test_simple_negation_is_understood_complex_is_not(tmp_path: Path) -> None:
    """«Рецидива преступлений судом не усмотрено» распознаётся как отрицание,
    а оборот с длинной вставкой («рецидив в действиях подсудимого суд не
    усматривает») — нет: ограничение остаётся задокументированным, поэтому
    обязательны warning и evidence на каждое булево поле.
    """
    draft = json.loads(_draft(tmp_path).read_text(encoding="utf-8"))
    assert draft["fields"]["recidivism"] is False
    assert "отрицание" in draft["warning"]

    text_file = tmp_path / "complex.txt"
    text_file.write_text(
        "Подсудимый обвиняется по ч. 1 ст. 158 УК РФ.\n"
        "У С Т А Н О В И Л:\n"
        "Рецидив в действиях подсудимого суд не усматривает.\n"
        "Назначить наказание в виде лишения свободы на срок 1 год.\n",
        encoding="utf-8",
    )
    out = tmp_path / "complex.json"
    code = cmd_draft(
        _Args(
            text_file=str(text_file),
            case_id="real-test-neg",
            source_url="https://example.org/neg",
            court="Тестовый суд",
            date="2026-01-01",
            out=str(out),
        )
    )
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["fields"]["recidivism"] is True


def test_draft_lists_fields_that_need_a_human(tmp_path: Path) -> None:
    draft_path = _draft(tmp_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))

    for field in ("title", "summary", "mitigating_codes", "aggravating_codes"):
        assert field in draft["needs_human"]


def test_promote_refuses_incomplete_draft(tmp_path: Path, capsys) -> None:
    draft_path = _draft(tmp_path)
    fixture_path = tmp_path / "cases" / "real_cases_v2.json"

    code = cmd_promote(_Args(draft=str(draft_path), fixture=str(fixture_path)))

    assert code == 2
    assert not fixture_path.exists()
    assert "не заполнено" in capsys.readouterr().err


def test_empty_list_is_a_legitimate_answer_not_missing(tmp_path: Path) -> None:
    """aggravating_codes: [] — законный ответ «отягчающих нет», не пробел."""
    draft_path = _draft(tmp_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["fields"]["title"] = "Кража, особый порядок"
    draft["fields"]["summary"] = "Тест."
    draft["fields"]["mitigating_codes"] = ["61.1.и"]
    draft["fields"]["aggravating_codes"] = []
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    fixture_path = tmp_path / "cases" / "real_cases_v2.json"

    code = cmd_promote(_Args(draft=str(draft_path), fixture=str(fixture_path)))

    assert code == 0
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert payload["cases"][0]["aggravating_codes"] == []


def test_promote_writes_a_valid_comparable_case(tmp_path: Path) -> None:
    draft_path = _draft(tmp_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["fields"]["title"] = "Кража, особый порядок, условный срок"
    draft["fields"]["summary"] = "Тестовая запись."
    draft["fields"]["mitigating_codes"] = ["61.1.и"]
    draft["fields"]["recidivism"] = False
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    fixture_path = tmp_path / "cases" / "real_cases_v2.json"

    code = cmd_promote(_Args(draft=str(draft_path), fixture=str(fixture_path)))
    assert code == 0

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from second_opinion.api.deps import load_cases

    cases = load_cases(tmp_path)
    assert len(cases) == 1
    assert cases[0].synthetic is False
    assert cases[0].recidivism is False


def test_promote_refuses_duplicate_case_id(tmp_path: Path, capsys) -> None:
    draft_path = _draft(tmp_path)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["fields"].update(
        title="Т", summary="Т", mitigating_codes=["61.1.и"], recidivism=False
    )
    draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    fixture_path = tmp_path / "cases" / "real_cases_v2.json"

    assert cmd_promote(_Args(draft=str(draft_path), fixture=str(fixture_path))) == 0
    code = cmd_promote(_Args(draft=str(draft_path), fixture=str(fixture_path)))

    assert code == 2
    assert "уже есть" in capsys.readouterr().err


def test_draft_does_not_touch_the_live_fixtures_directory(tmp_path: Path) -> None:
    """Черновик не должен приземлиться туда, откуда его грузит CaseRetriever."""
    draft_path = _draft(tmp_path)
    assert "fixtures" not in str(draft_path)


def test_draft_keeps_the_source_text_next_to_it(tmp_path: Path) -> None:
    """Без исходного текста черновик нельзя перепроверить после правки
    экстрактора — тексты реальных дел уже пропадали из временных папок."""
    import hashlib

    draft_path = _draft(tmp_path)
    source = draft_path.with_suffix(".source.txt")
    assert source.read_text(encoding="utf-8") == SAMPLE_TEXT
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft["source_sha256"] == hashlib.sha256(SAMPLE_TEXT.encode("utf-8")).hexdigest()



def test_draft_takes_final_term_as_term_months(tmp_path) -> None:
    text = (
        "У С Т А Н О В И Л: Иванов совершил кражу 14 мая 2025 года. "
        "ПРИГОВОРИЛ: признать виновным по ч. 1 ст. 161 УК РФ и назначить наказание в виде "
        "лишения свободы сроком на 1 год 6 месяцев. По совокупности приговоров назначить "
        "окончательное наказание в виде лишения свободы сроком на 2 года."
    )
    source = tmp_path / "t.txt"
    source.write_text(text, encoding="utf-8")
    out = tmp_path / "d.json"
    args = _Args(
        text_file=str(source), case_id="real-x", source_url="https://sudact.ru/x",
        court="Суд", date="2026-01-01", out=str(out),
    )
    assert cmd_draft(args) == 0
    draft = json.loads(out.read_text(encoding="utf-8"))
    assert draft["fields"]["term_months"] == 24.0
    assert draft["evidence"]["term_months — срок за преступление"] == ["18 мес."]
    assert "term_months — окончательное наказание" in draft["evidence"]
