from __future__ import annotations

import pytest
from pydantic import ValidationError

from second_opinion.domain import (
    Evidence,
    LegalFact,
    NormVersion,
)
from second_opinion.domain.enums import FactStatus, FactType


def test_fact_without_evidence_cannot_be_verified() -> None:
    with pytest.raises(ValidationError):
        LegalFact(
            id="f1",
            type=FactType.GUILTY_PLEA,
            value=True,
            confidence=1.0,
            evidence=[],
            extraction_method="pattern",
            status=FactStatus.VERIFIED,
        )


def test_fact_without_evidence_can_be_not_found() -> None:
    fact = LegalFact(
        id="f1",
        type=FactType.GUILTY_PLEA,
        value=None,
        confidence=1.0,
        evidence=[],
        extraction_method="pattern",
        status=FactStatus.NOT_FOUND,
    )
    assert fact.status is FactStatus.NOT_FOUND


def test_fact_with_evidence_can_be_verified() -> None:
    fact = LegalFact(
        id="f1",
        type=FactType.GUILTY_PLEA,
        value=True,
        confidence=0.95,
        evidence=[
            Evidence(
                document_id="d1", quote="вину признал", start_offset=10, end_offset=22
            )
        ],
        extraction_method="pattern",
        status=FactStatus.VERIFIED,
    )
    assert fact.status is FactStatus.VERIFIED


def test_evidence_offsets_must_be_coherent() -> None:
    with pytest.raises(ValidationError):
        Evidence(document_id="d1", quote="abc", start_offset=10, end_offset=10)


def test_norm_version_checksum_must_match_text() -> None:
    with pytest.raises(ValidationError):
        NormVersion(
            norm_id="uk-rf:art-158-part-2",
            version_id="v1",
            text="текст нормы",
            effective_from="2020-01-01",
            effective_to=None,
            source_document="тест",
            source_url="",
            retrieved_at="2026-01-01",
            sha256="0" * 64,
        )


def test_norm_version_valid_checksum() -> None:
    text = "текст нормы"
    version = NormVersion(
        norm_id="uk-rf:art-158-part-2",
        version_id="v1",
        text=text,
        effective_from="2020-01-01",
        effective_to=None,
        source_document="тест",
        source_url="",
        retrieved_at="2026-01-01",
        sha256=NormVersion.compute_sha256(text),
    )
    assert version.sha256 == NormVersion.compute_sha256(text)


def test_norm_version_rejects_reversed_dates() -> None:
    text = "текст"
    with pytest.raises(ValidationError):
        NormVersion(
            norm_id="uk-rf:art-158-part-2",
            version_id="v1",
            text=text,
            effective_from="2022-01-01",
            effective_to="2020-01-01",
            source_document="тест",
            source_url="",
            retrieved_at="2026-01-01",
            sha256=NormVersion.compute_sha256(text),
        )


# -- сборка CaseFacts: приоритет форм группового деяния


def _group_fact(value: str) -> LegalFact:
    from second_opinion.domain.enums import ExtractionMethod
    from second_opinion.domain.evidence import Evidence

    return LegalFact(
        id=f"fact-group-{abs(hash(value)) % 10000}",
        type=FactType.GROUP_OFFENSE,
        value=value,
        confidence=0.95,
        evidence=[Evidence(document_id="t", quote="q", start_offset=0, end_offset=1)],
        extraction_method=ExtractionMethod.PATTERN,
        status=FactStatus.VERIFIED,
    )


def test_builder_keeps_strongest_group_role() -> None:
    from second_opinion.fact_extraction.builder import build_case_facts

    case = build_case_facts(
        [
            _group_fact("group_with_conspiracy"),
            _group_fact("group_of_persons"),
            _group_fact("organized_group"),
        ]
    )
    assert case.offense.complicity_role == "organized_group"


def test_plenum_ref_is_a_point_of_a_ruling_not_an_article() -> None:
    """Пункт постановления Пленума не печатается как статья с частью.

    Номер постановления лежит в `article`, номер пункта — в `part`, поэтому
    общий шаблон давал «ППВС РФ ст. 60 ч. 14» — для юриста это бессмыслица.
    """
    from second_opinion.domain.norms import LegalNorm

    point = LegalNorm(
        norm_id="ppvs-rf:n60-p14",
        code="ППВС РФ",
        article=60,
        part=14,
        title="Пленум ВС РФ № 60: неоконченное в особом порядке",
    )
    assert point.ref == "п. 14 ППВС № 60"

    # Ссылка на постановление целиком — случай законный, поэтому пустой
    # `part` печатается без пункта. Идентификатор здесь намеренно без
    # `-pN`: записи корпуса, у которых пункт назван в идентификаторе,
    # обязаны называть его и в поле — это проверяется отдельно.
    whole = LegalNorm(
        norm_id="ppvs-rf:n58",
        code="ППВС РФ",
        article=58,
        part=None,
        title="Пленум ВС РФ № 58",
    )
    assert whole.ref == "ППВС № 58"


def test_article_suffix_is_a_separate_article_not_a_part() -> None:
    """Ст. 76² печатается как «ст. 76.2», а не как «ст. 76 ч. 2».

    Статья, введённая позднее между двумя существующими (76¹, 76², 159¹…),
    — самостоятельный состав, а не часть ст. 76: у неё своя санкция и свои
    условия применения. `part` для неё был бы враньём — «часть 2 статьи 76»
    читается как ч. 2 обычной статьи, которой в ст. 76 нет вовсе.
    """
    from second_opinion.domain.norms import LegalNorm

    point = LegalNorm(
        norm_id="uk-rf:art-76-2",
        code="УК РФ",
        article=76,
        article_suffix="2",
        title="Освобождение от уголовной ответственности с назначением судебного штрафа",
    )
    assert point.ref == "УК РФ ст. 76.2"

    plain = LegalNorm(
        norm_id="uk-rf:art-76",
        code="УК РФ",
        article=76,
        title="Освобождение от уголовной ответственности в связи с примирением с потерпевшим",
    )
    assert plain.ref == "УК РФ ст. 76"


def test_article_suffix_survives_loading_from_fixtures(norm_store) -> None:
    """Регрессия: `store.load_file` конструирует `LegalNorm` по явному
    списку полей, а не распаковкой словаря. Новое поле в JSON-фикстуре,
    не добавленное в этот список, тихо теряется при загрузке — ref
    печатается без суффикса, и ошибку не увидит никто, кроме того, кто
    сверяет цитату вручную. Тот же класс ошибки, что уже ловился на
    `article_suffix`: значение есть в файле, но не доезжает до модели.
    """
    from datetime import date

    meta = norm_store.get_norm_meta("uk-rf:art-76-2")
    assert meta.article_suffix == "2"
    assert meta.ref == "УК РФ ст. 76.2"

    version = norm_store.get_norm("uk-rf:art-76-2", date(2026, 9, 20))
    assert version.verification_status == "draft"


def test_code_articles_keep_their_reference_format() -> None:
    from second_opinion.domain.norms import LegalNorm

    article = LegalNorm(
        norm_id="uk-rf:art-158-part-2",
        code="УК РФ",
        article=158,
        part=2,
        title="Кража",
    )
    assert article.ref == "УК РФ ст. 158 ч. 2"
