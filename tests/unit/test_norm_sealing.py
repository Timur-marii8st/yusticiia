"""Опечатывание нормативного корпуса."""

from __future__ import annotations

import json
from datetime import date

import pytest

from second_opinion.legal_sources.sealing import (
    MANIFEST_NAME,
    UNSEALED,
    UnsealedNormError,
    check,
    seal,
    text_digest,
    version_digest,
)
from second_opinion.legal_sources.store import NormStore

TEXT = "Ч. 1 ст. 158 УК РФ: лишение свободы на срок до двух лет"


def _version(**overrides) -> dict:
    version = {
        "norm_id": "uk-rf:art-158-part-1",
        "version_id": "v-2003",
        "text": TEXT,
        "sanctions": [
            {"punishment_type": "imprisonment", "min_months": None, "max_months": 24}
        ],
        "effective_from": "2003-12-16",
        "effective_to": None,
        "source_url": "https://example.invalid/158",
        "retrieved_at": "2026-09-03",
        "sha256": UNSEALED,
        "verification_status": "verified",
    }
    version.update(overrides)
    return version


def _corpus(tmp_path, *versions):
    payload = {
        "code": "УК РФ",
        "source_document": "Уголовный кодекс Российской Федерации",
        "norms": [
            {
                "norm_id": "uk-rf:art-158-part-1",
                "article": 158,
                "part": 1,
                "title": "Кража",
                "versions": list(versions) or [_version()],
            }
        ],
    }
    (tmp_path / "uk.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return tmp_path



def test_record_digest_covers_sanctions_not_only_text() -> None:
    """Главный пробел старой схемы: предел наказания вне контрольной суммы."""
    original = _version(sha256=text_digest(TEXT))
    tampered = json.loads(json.dumps(original))
    tampered["sanctions"][0]["max_months"] = 120

    assert text_digest(original["text"]) == text_digest(tampered["text"])
    assert version_digest(original) != version_digest(tampered)


def test_record_digest_covers_effective_dates() -> None:
    original = _version(sha256=text_digest(TEXT))
    moved = _version(sha256=text_digest(TEXT), effective_from="1996-07-01")
    assert version_digest(original) != version_digest(moved)


def test_record_digest_ignores_own_checksum() -> None:
    """Запись не должна зависеть от собственного хэша."""
    assert version_digest(_version(sha256=UNSEALED)) == version_digest(
        _version(sha256=text_digest(TEXT))
    )



def test_unsealed_corpus_is_reported(tmp_path) -> None:
    report = check(_corpus(tmp_path))
    assert report.ok is False
    assert report.unsealed == ["uk-rf:art-158-part-1@v-2003"]


def test_seal_then_check_is_clean(tmp_path) -> None:
    report = seal(_corpus(tmp_path), sealed_at=date(2026, 9, 8))
    assert report.ok is True
    assert report.checked == 1
    assert (tmp_path / MANIFEST_NAME).exists()


def test_seal_writes_text_checksum_into_fixture(tmp_path) -> None:
    seal(_corpus(tmp_path))
    payload = json.loads((tmp_path / "uk.json").read_text(encoding="utf-8"))
    assert payload["norms"][0]["versions"][0]["sha256"] == text_digest(TEXT)


def test_edited_sanction_is_detected(tmp_path) -> None:
    """То, что старая схема пропускала: текст цел, предел подменён."""
    directory = _corpus(tmp_path)
    seal(directory)

    path = tmp_path / "uk.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["norms"][0]["versions"][0]["sanctions"][0]["max_months"] = 120
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = check(directory)
    assert report.ok is False
    assert report.record_mismatch == ["uk-rf:art-158-part-1@v-2003"]
    assert report.text_mismatch == []


def test_edited_text_is_detected(tmp_path) -> None:
    directory = _corpus(tmp_path)
    seal(directory)

    path = tmp_path / "uk.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["norms"][0]["versions"][0]["text"] = "подменённый текст"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = check(directory)
    assert report.text_mismatch == ["uk-rf:art-158-part-1@v-2003"]


def test_added_version_is_detected(tmp_path) -> None:
    """Пер-версионные суммы не видят добавления — манифест видит."""
    directory = _corpus(tmp_path)
    seal(directory)

    path = tmp_path / "uk.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    extra = _version(version_id="v-подделка", effective_from="1996-07-01",
                     effective_to="2003-12-15", sha256=text_digest(TEXT))
    payload["norms"][0]["versions"].append(extra)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = check(directory)
    assert report.missing_from_manifest == ["uk-rf:art-158-part-1@v-подделка"]


def test_removed_version_is_detected(tmp_path) -> None:
    directory = _corpus(
        tmp_path,
        _version(version_id="v-1996", effective_from="1996-07-01", effective_to="2003-12-15"),
        _version(),
    )
    seal(directory)

    path = tmp_path / "uk.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["norms"][0]["versions"] = payload["norms"][0]["versions"][:1]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = check(directory)
    assert report.missing_from_corpus == ["uk-rf:art-158-part-1@v-2003"]



def test_store_refuses_unsealed_corpus(tmp_path) -> None:
    """Раньше сумма подставлялась при загрузке — правка подтверждала сама себя."""
    with pytest.raises(UnsealedNormError, match="norms-seal"):
        NormStore.from_directory(_corpus(tmp_path))


def test_store_loads_sealed_corpus(tmp_path) -> None:
    directory = _corpus(tmp_path)
    seal(directory)
    store = NormStore.from_directory(directory)
    assert store.get_norm_meta("uk-rf:art-158-part-1").article == 158


def test_manifest_is_not_read_as_norms(tmp_path) -> None:
    directory = _corpus(tmp_path)
    seal(directory)
    assert len(NormStore.from_directory(directory).list_norms()) == 1



def test_project_corpus_is_sealed() -> None:
    """Фикстуры репозитория должны быть опечатаны — это же проверяет CI."""
    from second_opinion.config import PROJECT_ROOT

    report = check(PROJECT_ROOT / "data" / "fixtures" / "norms")
    assert report.ok, report.describe()
    assert report.checked == 38


def test_duplicate_version_ids_are_rejected(tmp_path) -> None:
    """Дубликат идентификатора редакции подменял бы одну версию другой."""
    directory = _corpus(
        tmp_path,
        _version(effective_from="1996-07-01", effective_to="2003-12-15"),
        _version(),
    )
    seal(directory)

    with pytest.raises(ValueError, match="повторяется"):
        NormStore.from_directory(directory)


def test_changed_norm_record_is_detected(tmp_path) -> None:
    """Отметка о сверке истории — юридическое утверждение.

    Регрессия: манифест покрывал только записи редакций, и правка полей
    самой нормы проходила мимо контроля целостности. Утверждение «история
    сверена по такому-то источнику» можно было дописать незаметно.
    """
    directory = _corpus(tmp_path)
    seal(directory)

    path = tmp_path / "uk.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["norms"][0]["amendments_checked_through"] = "2026-09-11"
    payload["norms"][0]["amendments_checked_source"] = "КонсультантПлюс"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    report = check(directory)
    assert report.ok is False
    assert report.norm_mismatch == ["uk-rf:art-158-part-1"]
    assert report.record_mismatch == [], "редакции не менялись"


def test_manifest_records_norm_digests(tmp_path) -> None:
    directory = _corpus(tmp_path)
    seal(directory)
    manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "uk-rf:art-158-part-1" in manifest["norms"]


def test_version_may_override_source_document(tmp_path) -> None:
    """Редакция вправе назвать свой источник, отличный от шапки файла.

    Разные редакции статьи могут быть взяты из разных публикаций. Раньше
    значение вычислялось отдельным аргументом и тут же дублировалось из
    ``**version``, поэтому такая редакция роняла загрузку с TypeError
    вместо того, чтобы переопределить источник.
    """
    directory = _corpus(
        tmp_path, _version(source_document="Собрание законодательства РФ")
    )
    seal(directory)

    store = NormStore.from_directory(directory)

    version = store.versions("uk-rf:art-158-part-1")[0]
    assert version.source_document == "Собрание законодательства РФ"


def test_version_without_source_document_inherits_it(tmp_path) -> None:
    directory = _corpus(tmp_path)
    seal(directory)

    store = NormStore.from_directory(directory)

    version = store.versions("uk-rf:art-158-part-1")[0]
    assert version.source_document == "Уголовный кодекс Российской Федерации"
