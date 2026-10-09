from __future__ import annotations

from second_opinion.domain.documents import Document
from second_opinion.domain.enums import FactStatus, FactType
from second_opinion.fact_extraction.pattern_extractor import PatternFactExtractor


def _document(text: str) -> Document:
    return Document(
        document_id="doc-test",
        filename="t.txt",
        content_type="text/plain",
        text=text,
        sha256="0" * 64,
    )


def _facts(text: str) -> list:
    return PatternFactExtractor().extract(_document(text))


def _by_type(facts: list, fact_type: FactType) -> list:
    return [f for f in facts if f.type is fact_type]


def test_all_evidence_quotes_match_text_by_offsets(sample_clean_text: str) -> None:
    document = _document(sample_clean_text)
    facts = PatternFactExtractor().extract(document)
    assert facts
    for fact in facts:
        for evidence in fact.evidence:
            fragment = document.text[evidence.start_offset : evidence.end_offset]
            assert fragment == evidence.quote, (fact.type, fact.id)


def test_clean_sample_extracts_expected_facts(sample_clean_text: str) -> None:
    facts = _facts(sample_clean_text)
    types = {f.type for f in facts}
    assert FactType.QUALIFICATION in types
    assert FactType.DEFENDANT_AGE in types
    assert FactType.MINOR_DEPENDENTS in types
    assert FactType.GUILTY_PLEA in types
    assert FactType.RESTITUTION in types
    assert FactType.SPECIAL_PROCEDURE in types
    assert FactType.PUNISHMENT_TYPE in types
    assert FactType.PUNISHMENT_TERM in types
    assert FactType.SUSPENDED_SENTENCE in types
    assert FactType.DATE_OF_OFFENSE in types


def test_qualification_value(sample_clean_text: str) -> None:
    qualification = _by_type(_facts(sample_clean_text), FactType.QUALIFICATION)[0]
    assert qualification.value == {
        "code": "УК РФ",
        "article": 158,
        "article_suffix": None,
        "part": 2,
    }


def test_qualification_with_decimal_article_suffix() -> None:
    text = "Подсудимый обвиняется в совершении преступления, предусмотренного ч. 2 ст. 264.1 УК РФ."
    qualification = _by_type(_facts(text), FactType.QUALIFICATION)[0]
    assert qualification.value == {
        "code": "УК РФ",
        "article": 264,
        "article_suffix": "1",
        "part": 2,
    }


def test_primary_qualification_is_not_taken_from_prior_conviction_biography() -> None:
    """Самая серьёзная находка на реальном деле (СудАкт.ру): статья из
    строки о ПРЕЖНЕЙ судимости («...по ч.1 ст.264.1 УК РФ к 1 году...» во
    вводной части приговора) становилась ``primary_qualification`` — тем
    самым, что ``R-001`` использует для проверки предела санкции, а
    ``CaseRetriever`` — для подбора сопоставимой практики. Реальный срок в
    рамках санкции ст.111 ч.4 при этом проверялся бы против санкции
    ст.264.1 — и выглядел бы как превышение максимума, которого нет.
    """
    from second_opinion.fact_extraction.builder import build_case_facts

    text = (
        "ФИО1, ранее судимый по приговору от 2020 года по ч.1 ст.264.1 УК "
        "РФ к 1 году лишения свободы условно, "
        "обвиняемого в совершении преступления, предусмотренного ч.4 "
        "ст.111 УК РФ, "
        "У С Т А Н О В И Л: "
        "ФИО1 совершил умышленное причинение тяжкого вреда здоровью, "
        "повлекшее по неосторожности смерть потерпевшего. "
        "ПРИГОВОРИЛ: признать виновным по ч.4 ст.111 УК РФ и назначить "
        "наказание в виде лишения свободы на срок 8 лет."
    )
    case = build_case_facts(_facts(text))
    primary = case.primary_qualification
    assert primary is not None
    assert (primary.article, primary.part) == (111, 4)


def test_sentence_term_is_18_months(sample_clean_text: str) -> None:
    term = _by_type(_facts(sample_clean_text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 18.0


def test_correctional_labor_sentence_is_recognized() -> None:
    text = "Суд решил назначить Иванову исправительные работы на срок 8 месяцев."
    facts = _facts(text)
    punishment_type = _by_type(facts, FactType.PUNISHMENT_TYPE)[0]
    term = _by_type(facts, FactType.PUNISHMENT_TERM)[0]
    assert punishment_type.value == "correctional_labor"
    assert term.value == 8.0


def test_compulsory_labor_sentence_is_recognized() -> None:
    text = "Суд решил назначить Петрову обязательные работы на срок 200 часов."
    punishment_type = _by_type(_facts(text), FactType.PUNISHMENT_TYPE)[0]
    assert punishment_type.value == "compulsory_labor"


def test_imprisonment_still_preferred_after_sentencing_word() -> None:
    """Регресс: расширение на другие виды не должно сломать выбор резолютивной части."""
    text = (
        "Наказывается исправительными работами либо лишением свободы. "
        "Суд решил назначить Сидорову лишение свободы на срок 24 месяца."
    )
    punishment_type = _by_type(_facts(text), FactType.PUNISHMENT_TYPE)[0]
    term = _by_type(_facts(text), FactType.PUNISHMENT_TERM)[0]
    assert punishment_type.value == "imprisonment"
    assert term.value == 24.0


def test_suspended_sentence_not_taken_from_prior_conviction_biography() -> None:
    """Регресс: ст.73 в биографии подсудимого — не условность ТЕКУЩЕГО приговора.

    Найдено на реальном деле (СудАкт.ру): «судимого... на основании ст.73
    УК РФ... условно» в описании прежней судимости, за которым следует
    реальный (не условный) срок по текущему обвинению — раньше
    ``_SUSPENDED_ART73`` искал по всему документу и ловил чужое условное
    осуждение.
    """
    text = (
        "Иванов, ранее судимый 05.05.2020 по ч.1 ст.158 УК РФ к 1 году "
        "лишения свободы, на основании ст.73 УК РФ условно с испытательным "
        "сроком 1 год, обвиняемого в совершении преступления, "
        "предусмотренного ч.4 ст.111 УК РФ, суд установил: "
        "приговорил: признать виновным и назначить наказание в виде "
        "лишения свободы на срок 8 лет с отбыванием в колонии строгого режима."
    )
    assert _by_type(_facts(text), FactType.SUSPENDED_SENTENCE) == []


def test_suspended_sentence_still_detected_near_the_actual_sentence() -> None:
    """Регресс-страховка: сужение окна не должно потерять настоящую условность."""
    text = (
        "Суд решил назначить Петрову наказание в виде лишения свободы на "
        "срок 2 года. На основании ст. 73 УК РФ назначенное наказание "
        "считать условным с испытательным сроком 1 год."
    )
    fact = _by_type(_facts(text), FactType.SUSPENDED_SENTENCE)[0]
    assert fact.value is True


def test_special_procedure_detected_by_article_316_citation() -> None:
    """Найдено на реальном деле: «особый порядок» текстом не назван, только
    ссылкой на ст.316 УПК РФ (основание главы 40 УПК — согласие обвиняемого
    с обвинением)."""
    text = "На основании изложенного, руководствуясь ст.316 УПК РФ, суд приговорил..."
    assert _by_type(_facts(text), FactType.SPECIAL_PROCEDURE)


def test_date_of_offense(sample_clean_text: str) -> None:
    date_fact = _by_type(_facts(sample_clean_text), FactType.DATE_OF_OFFENSE)[0]
    assert date_fact.value == "2025-03-15"


def test_procedural_and_mitigating_facts_not_taken_from_prior_conviction_biography() -> None:
    """Особый порядок, присяжные и возмещение ущерба во вводной части —
    про прежнее дело подсудимого, а не про текущее."""
    text = (
        "Петров, ранее судимый приговором, постановленным в особом порядке "
        "с участием присяжных заседателей, возместил ущерб по тому делу, "
        "обвиняемого в совершении преступления, предусмотренного ч.1 "
        "ст.158 УК РФ, "
        "У С Т А Н О В И Л: "
        "дело рассмотрено в общем порядке, ущерб не возмещён. "
        "ПРИГОВОРИЛ: назначить наказание в виде лишения свободы на срок "
        "12 месяцев."
    )
    facts = _facts(text)
    assert _by_type(facts, FactType.SPECIAL_PROCEDURE) == []
    assert _by_type(facts, FactType.JURY_TRIAL) == []
    assert _by_type(facts, FactType.RESTITUTION) == []


def test_jury_and_special_procedure_in_court_composition_are_found() -> None:
    """Состав суда и порядок рассмотрения названы до слов «в отношении»:
    это текущее дело, а не биография."""
    text = (
        "Областной суд в составе судьи, с участием присяжных заседателей, "
        "рассмотрев в особом порядке уголовное дело в отношении Власова, "
        "возраст 41 год, ранее не судимого, "
        "У С Т А Н О В И Л: "
        "Власов совершил приготовление. "
        "ПРИГОВОРИЛ: назначить лишение свободы на срок 12 месяцев."
    )
    facts = _facts(text)
    assert _by_type(facts, FactType.JURY_TRIAL)
    assert _by_type(facts, FactType.SPECIAL_PROCEDURE)


def test_prior_case_procedure_after_subject_is_still_ignored() -> None:
    text = (
        "Суд в составе судьи, рассмотрев уголовное дело в отношении Петрова, "
        "ранее судимого приговором, постановленным в особом порядке с "
        "участием присяжных заседателей, "
        "У С Т А Н О В И Л: "
        "дело рассмотрено в общем порядке. "
        "ПРИГОВОРИЛ: назначить лишение свободы на срок 12 месяцев."
    )
    facts = _facts(text)
    assert _by_type(facts, FactType.JURY_TRIAL) == []
    assert _by_type(facts, FactType.SPECIAL_PROCEDURE) == []


def test_date_of_offense_not_taken_from_prior_conviction_biography() -> None:
    """Дата совершения текущего преступления — юридически значимая дата
    (И-03, выбор редакции нормы). Найдено тем же способом, что и находка
    про квалификацию: вводная часть с прежней судимостью («...ранее
    судимый за то, что совершил... 03 марта 2019 года...») удовлетворяет
    контекстному фильтру («соверш…») так же, как текст по существу дела.
    """
    text = (
        "Петров, ранее судимый за то, что совершил кражу 03 марта 2019 "
        "года, осужден 05 мая 2019 года к 1 году лишения свободы условно, "
        "обвиняемого в совершении преступления, предусмотренного ч.1 "
        "ст.158 УК РФ, "
        "У С Т А Н О В И Л: "
        "подсудимый Петров совершил кражу 10 января 2026 года. "
        "ПРИГОВОРИЛ: назначить наказание в виде лишения свободы на срок "
        "12 месяцев."
    )
    date_fact = _by_type(_facts(text), FactType.DATE_OF_OFFENSE)[0]
    assert date_fact.value == "2026-01-10"


def test_date_of_offense_from_accusation_formula_when_narrative_has_none() -> None:
    """Постановление о прекращении дела: дата называется только в формуле
    обвинения вводной части. Прежняя судимость до слова «обвиняемого»
    в выбор не попадает."""
    text = (
        "Петров, ранее судимый за то, что совершил кражу 03 марта 2019 "
        "года, обвиняемого в совершении преступления 14 мая 2025 года "
        "(ч. 1 ст. 158 УК РФ), "
        "У С Т А Н О В И Л: "
        "подсудимый вину признал. "
        "ПОСТАНОВИЛ: дело прекратить."
    )
    date_fact = _by_type(_facts(text), FactType.DATE_OF_OFFENSE)[0]
    assert date_fact.value == "2025-05-14"


def test_prior_conviction_date_is_not_taken_when_formula_has_no_date() -> None:
    text = (
        "Петров, ранее судимый за то, что совершил кражу 03 марта 2019 "
        "года, обвиняемого в совершении преступления, предусмотренного "
        "ч. 1 ст. 158 УК РФ, "
        "У С Т А Н О В И Л: "
        "подсудимый вину признал. "
        "ПРИГОВОРИЛ: назначить лишение свободы на срок 12 месяцев."
    )
    assert _by_type(_facts(text), FactType.DATE_OF_OFFENSE) == []


def test_date_of_a_document_is_not_the_date_of_the_offense() -> None:
    """Реальный приговор: после «совершил покушение на преступление» идёт приказ
    о назначении должностного лица «от 02 ноября 2009 года», и дата приказа читалась
    как дата деяния (и по ней выбиралась бы редакция нормы)."""
    text = (
        "обвиняемого в совершении преступления, предусмотренного ч. 2 ст. 159 УК РФ, "
        "У С Т А Н О В И Л: "
        "ФИО1 совершил покушение на умышленное преступление небольшой тяжести при "
        "следующих обстоятельствах. Приказом № от 02 ноября 2009 года начальника "
        "станции назначена на должность начальника филиала. "
        "ПРИГОВОРИЛ: назначить наказание в виде штрафа."
    )

    assert _by_type(_facts(text), FactType.DATE_OF_OFFENSE) == []


def test_date_of_offense_after_a_document_date_is_still_found() -> None:
    text = (
        "обвиняемого в совершении преступления, предусмотренного ч. 2 ст. 159 УК РФ, "
        "У С Т А Н О В И Л: "
        "Приказом № от 02 ноября 2009 года Иванова назначена на должность. "
        "Подсудимый совершил мошенничество 14 января 2026 года. "
        "ПРИГОВОРИЛ: назначить наказание в виде штрафа."
    )

    assert _by_type(_facts(text), FactType.DATE_OF_OFFENSE)[0].value == "2026-01-14"


def test_date_of_offense_after_the_phrase_under_the_following_circumstances() -> None:
    """Реальный приговор: глагол «совершил» стоит далеко от даты, а перед ней фраза
    «при следующих обстоятельствах»."""
    text = (
        "обвиняемого в совершении преступления, предусмотренного ч. 1 ст. 161 УК РФ, "
        "У С Т А Н О В И Л: "
        "ФИО2 грабёж, то есть открытое хищение чужого имущества потерпевшей Потерпевший №1, "
        "при следующих обстоятельствах: ФИО2, в период времени с 11 часов до 12 часов "
        "30 минут 2 сентября 2025 года, находясь в состоянии алкогольного опьянения, "
        "ПРИГОВОРИЛ: назначить наказание в виде лишения свободы на срок 12 месяцев."
    )

    assert _by_type(_facts(text), FactType.DATE_OF_OFFENSE)[0].value == "2025-09-02"


def test_attempt_stage_detected(sample_violation_text: str) -> None:
    stage = _by_type(_facts(sample_violation_text), FactType.OFFENSE_STAGE)[0]
    assert stage.value == "attempt"


def test_violation_sample_term_30_months(sample_violation_text: str) -> None:
    term = _by_type(_facts(sample_violation_text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 30.0


def test_negative_prior_conviction_not_extracted(sample_clean_text: str) -> None:
    assert not _by_type(_facts(sample_clean_text), FactType.PRIOR_CONVICTIONS)


def test_general_part_article_is_not_qualification() -> None:
    text = "На основании ст. 73 УК РФ наказание считать условным."
    assert not _by_type(_facts(text), FactType.QUALIFICATION)


def test_pattern_facts_are_verified(sample_clean_text: str) -> None:
    facts = _facts(sample_clean_text)
    assert all(f.status is FactStatus.VERIFIED for f in facts)
    assert all(f.evidence for f in facts)


def test_sentence_taken_from_resolutive_part() -> None:
    text = (
        "Санкция предусматривает лишение свободы на срок до пяти лет. "
        "Суд решил назначить наказание в виде лишения свободы на срок 2 года."
    )
    term = _by_type(_facts(text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 24.0


def test_restitution_with_participle_between_verb_and_noun() -> None:
    facts = _facts("Подсудимый добровольно возместил причинённый ущерб потерпевшему.")
    assert FactType.RESTITUTION in {f.type for f in facts}


def test_negative_document_extracts_nothing(sample_negative_text: str) -> None:
    """Негативный контроль: процессуальный документ без обстоятельств
    дела не должен порождать юридически значимых фактов."""
    assert _facts(sample_negative_text) == []


def test_article_64_citation_detected() -> None:
    text = (
        "Суд, руководствуясь ст. 64 УК РФ, назначил наказание ниже низшего "
        "предела, предусмотренного санкцией статьи."
    )
    facts = _by_type(_facts(text), FactType.EXCEPTIONAL_CIRCUMSTANCES)
    assert len(facts) == 1
    assert facts[0].value is True


def test_no_article_64_citation_no_fact() -> None:
    text = "Суд назначил наказание в пределах санкции статьи."
    assert _by_type(_facts(text), FactType.EXCEPTIONAL_CIRCUMSTANCES) == []


def test_article_64_not_taken_from_prior_conviction_biography() -> None:
    """Регресс: ст.64 в биографии подсудимого — не мотивировка ТЕКУЩЕГО дела.

    Тот же класс ошибки, что и у условного осуждения (см. тест про ст. 73):
    вводная часть приговора с судимостями подсудимого стоит до «УСТАНОВИЛ»,
    и упоминание статьи там не должно читаться как рассуждение суда по
    текущему делу.
    """
    text = (
        "Иванов, ранее судимый по приговору от 2020 года по ч.1 ст.158 УК "
        "РФ, с применением ст.64 УК РФ, к 6 месяцам лишения свободы, "
        "обвиняемого в совершении преступления, предусмотренного ч.4 "
        "ст.111 УК РФ, "
        "У С Т А Н О В И Л: "
        "суд приговорил: признать виновным и назначить наказание в виде "
        "лишения свободы на срок 8 лет."
    )
    assert _by_type(_facts(text), FactType.EXCEPTIONAL_CIRCUMSTANCES) == []


def test_group_of_persons_plain() -> None:
    facts = _facts("Преступление совершено группой лиц по предварительному сговору.")
    group = [f for f in facts if f.type is FactType.GROUP_OFFENSE]
    assert {f.value for f in group} == {"group_with_conspiracy", "group_of_persons"}


def test_organized_group_value() -> None:
    facts = _facts("Деяние совершено организованной группой.")
    group = [f for f in facts if f.type is FactType.GROUP_OFFENSE]
    assert len(group) == 1
    assert group[0].value == "organized_group"


def test_no_group_words_no_fact() -> None:
    assert FactType.GROUP_OFFENSE not in {
        f.type for f in _facts("Он единолично совершил кражу из квартиры.")
    }


def test_term_with_parenthesized_number_word() -> None:
    text = "Суд решил назначить наказание в виде лишения свободы на срок 2 (два) года."
    term = _by_type(_facts(text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 24.0


def test_term_one_year_with_parenthesized_word() -> None:
    text = "назначить наказание в виде лишения свободы на срок 1 (один) год."
    term = _by_type(_facts(text), FactType.PUNISHMENT_TERM)[0]
    assert term.value == 12.0


def test_calendar_year_is_not_a_term() -> None:
    """Регрессия: «2025 года» в окне приговора не должно давать 24300 мес."""
    text = (
        "Признать виновным и назначить наказание в виде лишения свободы "
        "на срок 1 (один) год. Срок наказания исчислять с 4 марта 2025 года."
    )
    terms = _by_type(_facts(text), FactType.PUNISHMENT_TERM)
    assert len(terms) == 1
    assert terms[0].value == 12.0


def test_year_alone_gives_no_term() -> None:
    text = (
        "назначить наказание в виде лишения свободы. "
        "Срок наказания исчислять с 20 октября 2025 года."
    )
    assert not _by_type(_facts(text), FactType.PUNISHMENT_TERM)


def test_victim_age_is_not_defendant_age() -> None:
    """«В возрасте 15 лет» в тексте по существу дела — про потерпевшего."""
    text = (
        "ФИО1, 1990 года рождения, обвиняемого в совершении преступления, "
        "предусмотренного ч.1 ст.111 УК РФ, "
        "У С Т А Н О В И Л: ФИО1 причинил тяжкий вред здоровью потерпевшего "
        "ФИО2 в возрасте 15 лет."
    )
    assert _by_type(_facts(text), FactType.DEFENDANT_AGE) == []


def test_victim_child_is_not_defendant_dependent() -> None:
    text = (
        "ФИО1, возраст 34 года, обвиняемого в совершении преступления, "
        "предусмотренного ч.1 ст.158 УК РФ, "
        "У С Т А Н О В И Л: потерпевшая ФИО2 имеет несовершеннолетнего ребёнка."
    )
    assert _by_type(_facts(text), FactType.MINOR_DEPENDENTS) == []


def test_defendant_own_child_with_yo_is_detected() -> None:
    text = (
        "ФИО1, возраст 34 года, ч.1 ст.158 УК РФ, У С Т А Н О В И Л: суд "
        "учитывает наличие на иждивении ФИО1 несовершеннолетнего ребёнка."
    )
    assert _by_type(_facts(text), FactType.MINOR_DEPENDENTS)


def test_punishment_not_taken_from_prior_conviction_or_trailing_appointment_word() -> None:
    """Наказание из вводной части («к 1 году исправительных работ») и слово
    «назначенного» в конце приговора («адвоката, назначенного судом») не
    должны сдвигать выбор: иначе тип наказания читается как исправительные
    работы, и R-001 молча считает проверку лишения свободы неприменимой."""
    base = (
        "ФИО1, ранее судимый к 1 году исправительных работ, обвиняемого по "
        "ч.1 ст.158 УК РФ, У С Т А Н О В И Л: кража. "
        "{marker}назначить наказание в виде лишения свободы на срок 3 года. "
        "Издержки по оплате труда адвоката, назначенного судом, возместить "
        "за счёт федерального бюджета."
    )
    for marker in ("ПРИГОВОРИЛ: ", ""):
        facts = _facts(base.format(marker=marker))
        assert _by_type(facts, FactType.PUNISHMENT_TYPE)[0].value == "imprisonment"
        assert _by_type(facts, FactType.PUNISHMENT_TERM)[0].value == 36.0


def test_qualification_named_only_in_the_accusation_formula_is_still_found() -> None:
    """Короткий приговор без статьи в описательной части: текущее обвинение
    стоит только во вводной формуле «обвиняемого в совершении…», а не в
    перечне судимостей перед ней."""
    from second_opinion.fact_extraction.builder import build_case_facts

    text = (
        "ФИО1, ранее судимого по ч.1 ст.158 УК РФ, обвиняемого в совершении "
        "преступления, предусмотренного ч.2 ст.159 УК РФ, "
        "У С Т А Н О В И Л: дело рассмотрено в особом порядке. "
        "ПРИГОВОРИЛ: назначить наказание в виде штрафа."
    )
    primary = build_case_facts(_facts(text)).primary_qualification
    assert primary is not None
    assert (primary.article, primary.part) == (159, 2)


def test_primary_qualification_is_the_one_in_the_resolutive_part() -> None:
    """Суд переквалифицировал ч. 2 на ч. 1 ст. 159 по ходу рассуждения: первой
    в описательной части названа первоначальная, а осуждён подсудимый по
    резолютивной. Предел санкции проверяется по той, по которой осуждён
    (у ч. 2 санкция строже — иначе завышение срока по ч. 1 осталось бы
    незамеченным)."""
    from second_opinion.fact_extraction.builder import build_case_facts

    text = (
        "ФИО1, не судимого, обвиняемого в совершении преступления, "
        "предусмотренного ч. 2 ст. 159 УК РФ, "
        "У С Т А Н О В И Л: суд переквалифицирует действия с ч. 2 ст. 159 "
        "УК РФ на ч. 1 ст. 159 УК РФ. "
        "ПРИГОВОРИЛ: признать виновным в совершении преступления, "
        "предусмотренного ч. 3 ст. 30, ч. 1 ст. 159 УК РФ, и назначить "
        "наказание в виде обязательных работ на срок 300 часов."
    )
    primary = build_case_facts(_facts(text)).primary_qualification
    assert primary is not None
    assert (primary.article, primary.part) == (159, 1)


_HEAD = (
    "ФИО1, обвиняемого в совершении преступления, предусмотренного ч.1 "
    "ст.158 УК РФ, У С Т А Н О В И Л: "
)
_TAIL = " ПРИГОВОРИЛ: назначить наказание в виде лишения свободы на срок 30 месяцев."


def _kinds(middle: str, *kinds: FactType) -> set[FactType]:
    return {f.type for f in _facts(_HEAD + middle + _TAIL)} & set(kinds)


def test_negated_restitution_is_not_a_mitigating_fact(norm_store) -> None:
    """«Не возместил ущерб» не должно читаться как возмещение: ложный пункт
    «к» ч. 1 ст. 62 включал правило двух третей, и срок в пределах санкции
    получал ложное нарушение (FAIL) в R-004."""
    from datetime import date

    from second_opinion.fact_extraction.builder import build_case_facts
    from second_opinion.rule_engine.engine import RuleEngine

    text = _HEAD + "Подсудимый не возместил причинённый ущерб, вину не признал." + _TAIL
    facts = _facts(text)
    assert not {f.type for f in facts} & {FactType.RESTITUTION, FactType.GUILTY_PLEA}
    evaluations = RuleEngine().evaluate(build_case_facts(facts), norm_store, date(2025, 3, 15))
    assert {e.rule_id: e.status.value for e in evaluations}["R-004"] != "FAIL"


def test_negation_does_not_swallow_a_real_fact_in_the_same_sentence() -> None:
    """«Ранее не судим» стоит в той же фразе, но к признанию вины не относится."""
    middle = "Подсудимый ранее не судим вину признал полностью, возместил ущерб."
    found = _kinds(middle, FactType.GUILTY_PLEA, FactType.RESTITUTION)
    assert found == {FactType.GUILTY_PLEA, FactType.RESTITUTION}


def test_negated_surrender_and_group_and_recidivism() -> None:
    assert not _kinds("Явку с повинной подсудимый не заявлял.", FactType.SURRENDER_OR_CONFESSION)
    assert not _kinds("Суд не усматривает явки с повинной.", FactType.SURRENDER_OR_CONFESSION)
    assert not _kinds("Действовал без группы лиц.", FactType.GROUP_OFFENSE)
    assert not _kinds("Рецидив отсутствует.", FactType.AGGRAVATING_RECIDIVISM)
    assert not _kinds("При отсутствии признания вины суд учитывает.", FactType.GUILTY_PLEA)


_RESOLUTIVE = " ПРИГОВОРИЛ: признать виновным в совершении преступления, предусмотренного "


def _verdict(narrative: str, resolutive: str) -> list:
    return _facts(
        "Суд, рассмотрев дело в отношении Иванова И.И., "
        f"У С Т А Н О В И Л: {narrative}{_RESOLUTIVE}{resolutive}"
    )


def test_qualification_from_fully_spelled_citation() -> None:
    facts = _verdict(
        "Иванов совершил кражу.",
        "частью 1 статьи 158 Уголовного кодекса Российской Федерации, и назначить наказание.",
    )
    values = [
        (f.value["article"], f.value["part"]) for f in _by_type(facts, FactType.QUALIFICATION)
    ]
    assert values == [(158, 1)]


def test_qualification_from_spelled_citation_with_point_and_article_first() -> None:
    facts = _verdict(
        "Иванов совершил кражу.",
        "пунктами «б» и «в» части 2 статьи 159 Уголовного кодекса РФ, а также статьёй 161 частью 1 Уголовного кодекса.",
    )
    values = {
        (f.value["article"], f.value["part"]) for f in _by_type(facts, FactType.QUALIFICATION)
    }
    assert (159, 2) in values


def test_procedural_code_citation_is_not_a_qualification() -> None:
    facts = _verdict(
        "Дело рассмотрено в особом порядке.",
        "частью 1 статьи 228 Уголовного кодекса, процессуальные издержки на основании "
        "части 10 статьи 316 Уголовно-процессуального кодекса Российской Федерации.",
    )
    values = [
        (f.value["article"], f.value["part"]) for f in _by_type(facts, FactType.QUALIFICATION)
    ]
    assert values == [(228, 1)]
    assert _by_type(facts, FactType.SPECIAL_PROCEDURE)


def test_fine_in_resolutive_part_is_not_replaced_by_earlier_imprisonment() -> None:
    facts = _verdict(
        "Ранее Иванов осуждён к наказанию в виде лишения свободы сроком на 1 (один) год "
        "и 6 месяцев условно с испытательным сроком на 1 (один) год.",
        "частью 1 статьи 228 Уголовного кодекса Российской Федерации, и назначить ему "
        "наказание в виде штрафа в размере 20 000 (двадцать тысяч) рублей.",
    )
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TYPE)] == ["fine"]
    assert _by_type(facts, FactType.PUNISHMENT_TERM) == []
    assert _by_type(facts, FactType.SUSPENDED_SENTENCE) == []


def test_other_termless_punishments_in_resolutive_part() -> None:
    restriction = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 111 Уголовного кодекса, и назначить наказание в виде ограничения свободы на срок 1 год.",
    )
    arrest = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 111 Уголовного кодекса, и назначить наказание в виде ареста на срок 3 месяца.",
    )
    assert [f.value for f in _by_type(restriction, FactType.PUNISHMENT_TYPE)] == [
        "restriction_of_liberty"
    ]
    assert [f.value for f in _by_type(arrest, FactType.PUNISHMENT_TYPE)] == ["other"]
    assert _by_type(restriction, FactType.PUNISHMENT_TERM) == []


def test_imprisonment_in_resolutive_part_still_wins_over_narrative() -> None:
    facts = _verdict(
        "Ранее Иванов осуждён к штрафу.",
        "частью 1 статьи 111 Уголовного кодекса, и назначить наказание в виде лишения свободы сроком 3 года.",
    )
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TYPE)] == ["imprisonment"]
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TERM)] == [36.0]


def _term(resolutive: str) -> list:
    return [
        f.value
        for f in _by_type(
            _verdict("Иванов виновен.", f"частью 1 статьи 111 Уголовного кодекса, {resolutive}"),
            FactType.PUNISHMENT_TERM,
        )
    ]


def test_term_in_words_without_preposition() -> None:
    assert _term("и назначить наказание в виде лишения свободы сроком три года.") == [36.0]


def test_term_in_words_with_years_and_months() -> None:
    assert _term(
        "и назначить наказание в виде лишения свободы сроком на один год шесть месяцев."
    ) == [18.0]


def test_term_in_words_months_only_and_compound_number() -> None:
    assert _term("наказание в виде лишения свободы сроком шесть месяцев.") == [6.0]
    assert _term("наказание в виде лишения свободы на срок двадцать пять лет.") == [300.0]


def test_probation_term_in_words_is_not_taken_as_sentence_term() -> None:
    facts = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 111 Уголовного кодекса, и назначить наказание в виде лишения свободы "
        "сроком три года. На основании статьи 73 Уголовного кодекса наказание считать условным "
        "с испытательным сроком два года.",
    )
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TERM)] == [36.0]
    assert _by_type(facts, FactType.SUSPENDED_SENTENCE)


def test_guilty_plea_with_words_between_noun_and_verb() -> None:
    assert _kinds(
        "Подсудимый вину в совершении описанного преступления признал.", FactType.GUILTY_PLEA
    )


def test_guilty_plea_from_noun_forms() -> None:
    assert _kinds("Суд учитывает полное признание им своей вины.", FactType.GUILTY_PLEA)
    assert _kinds("Суд учитывает признание подсудимым вины.", FactType.GUILTY_PLEA)
    assert _kinds("Подсудимый признал свою вину.", FactType.GUILTY_PLEA)
    assert _kinds("Подсудимый признал себя виновным.", FactType.GUILTY_PLEA)


def test_guilty_plea_denied_with_words_between_is_not_a_fact() -> None:
    assert not _kinds("Подсудимый вину в совершении преступления не признал.", FactType.GUILTY_PLEA)
    assert not _kinds("Подсудимый свою вину не признал.", FactType.GUILTY_PLEA)


def test_partial_guilty_plea_is_not_reported_as_a_plea() -> None:
    assert not _kinds(
        "Вину в инкриминируемом деянии подсудимый признал частично.", FactType.GUILTY_PLEA
    )
    assert not _kinds("Подсудимый частично признал вину.", FactType.GUILTY_PLEA)
    assert _kinds(
        "Подсудимый вину в инкриминируемом деянии признал полностью.", FactType.GUILTY_PLEA
    )


_COMBINED = (
    "частью 1 статьи 161 Уголовного кодекса Российской Федерации и назначить ему наказание "
    "в виде лишения свободы сроком на 1 год 6 месяцев. В соответствии со статьёй 70 "
    "Уголовного кодекса, по совокупности приговоров, частично присоединить неотбытую часть "
    "наказания в виде 6 месяцев лишения свободы, и назначить окончательное наказание "
    "в виде лишения свободы сроком на 2 года, с отбыванием в колонии."
)


def test_term_for_the_offense_and_final_term_are_separate_facts() -> None:
    facts = _verdict("Иванов виновен.", _COMBINED)
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TERM)] == [18.0]
    assert [f.value for f in _by_type(facts, FactType.FINAL_PUNISHMENT_TERM)] == [24.0]


def test_no_final_term_without_final_punishment_wording() -> None:
    facts = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 161 Уголовного кодекса, наказание в виде лишения свободы сроком 2 года.",
    )
    assert _by_type(facts, FactType.FINAL_PUNISHMENT_TERM) == []


def test_final_fine_has_no_final_term() -> None:
    facts = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 159 Уголовного кодекса, окончательное наказание в виде штрафа в размере 20 000 рублей.",
    )
    assert _by_type(facts, FactType.FINAL_PUNISHMENT_TERM) == []


def test_final_term_with_adverb_and_name_between_words() -> None:
    facts = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 264.1 Уголовного кодекса, наказание в виде лишения свободы сроком 8 месяцев. "
        "По совокупности приговоров окончательно назначить Иванову наказание в виде "
        "1 (одного) года 2 (двух) месяцев лишения свободы.",
    )
    assert [f.value for f in _by_type(facts, FactType.FINAL_PUNISHMENT_TERM)] == [14.0]


def test_term_before_the_punishment_name_wins_over_additional_punishment_term() -> None:
    facts = _verdict(
        "Иванов виновен.",
        "частью 2 статьи 264.1 Уголовного кодекса, и назначить ему наказание в виде "
        "1 (одного) года 2 (двух) месяцев лишения свободы с лишением права заниматься "
        "деятельностью, связанной с управлением транспортными средствами, на срок "
        "4 (четыре) года.",
    )
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TERM)] == [14.0]


def test_term_before_the_punishment_name_in_years() -> None:
    facts = _verdict(
        "Иванов виновен.",
        "частью 1 статьи 158 Уголовного кодекса, наказание в виде 2 лет лишения свободы.",
    )
    assert [f.value for f in _by_type(facts, FactType.PUNISHMENT_TERM)] == [24.0]
    quote = _by_type(facts, FactType.PUNISHMENT_TERM)[0].evidence[0].quote
    assert quote == "2 лет"


def test_quotes_are_completed_to_the_end_of_the_word_and_of_the_code_name() -> None:
    """Цитата не обрывается на основе слова: «возраст 34 год» читалось как опечатка."""
    text = (
        "Подсудимый, возраст 34 года, обвиняемого в совершении преступления, предусмотренного ч. 2 ст. 158 УК РФ. "
        "У С Т А Н О В И Л: ... ПРИГОВОРИЛ: назначить наказание в виде лишения свободы на срок "
        "1 год 6 месяцев условно, в соответствии со ст. 73 УК РФ."
    )

    facts = _facts(text)
    age = _by_type(facts, FactType.DEFENDANT_AGE)[0].evidence[0]
    qualification = _by_type(facts, FactType.QUALIFICATION)[0].evidence[0]

    assert age.quote == "возраст 34 года"
    assert qualification.quote.endswith("УК РФ")
    for fact in facts:
        for evidence in fact.evidence:
            assert text[evidence.start_offset : evidence.end_offset] == evidence.quote
            assert not evidence.quote.endswith(("год", "УК"))
