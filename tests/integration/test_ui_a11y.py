"""UI a11y/адаптивность: регрессия разметки и скриптов (без браузера)."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from second_opinion.api.routes import create_app


def test_index_has_a11y_landmarks(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    assert 'class="skip-link"' in html
    assert 'for="auth-token"' in html
    assert 'for="doc-text"' in html
    assert 'for="search-query"' in html
    assert 'for="new-fact-quote"' in html
    assert 'role="alert"' in html
    assert 'aria-live="assertive"' in html
    assert 'aria-live="polite"' in html
    assert 'lang="ru"' in html


def test_app_js_disclosure_is_keyboard_accessible(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text
    assert "wireDisclosure" in js
    assert 'setAttribute("role", "button")' in js
    assert 'setAttribute("tabindex", "0")' in js
    assert "aria-expanded" in js
    assert '"Enter"' in js or "'Enter'" in js
    # бейджи озвучиваются статусом, а не символом
    assert 'aria-label' in js


def test_file_picker_is_a_visible_control(pipeline) -> None:
    """Выбор файла — видимая кнопка, доступная с клавиатуры.

    Прежде это была строка 13px цветом акцента, а само поле пряталось через
    `display: none` — такое поле выпадает из обхода клавиатурой целиком.
    """
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    css = client.get("/static/style.css").text

    assert 'id="file-label"' in html
    assert "Выбрать файл" in html

    input_rule = re.search(r"\.file-label input \{(.*?)\}", css, re.DOTALL)
    assert input_rule is not None, "правило .file-label input исчезло"
    assert "display: none" not in input_rule.group(1), (
        "display: none возвращает поле обратно в недоступность для клавиатуры"
    )
    # Фокус внутри скрытого поля должен быть виден на обёртке.
    assert ".file-label:focus-within" in css


def test_report_download_goes_through_api_fetch(pipeline) -> None:
    """Выгрузка отчёта не должна быть обычной ссылкой на эндпойнт.

    Браузер идёт по `href` обычной навигацией, без заголовка Authorization,
    поэтому в развёртывании с авторизацией судья получал 401 вместо файла
    (проверено: GET /api/analyses/{id} без заголовка → 401, с ним → 200).
    """
    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text

    assert "href = `/api/" not in js, "ссылка снова ведёт мимо авторизации"
    assert "createObjectURL" in js
    assert 'download = "second-opinion-report.json"' in js


def test_add_fact_error_is_shown_next_to_the_form(pipeline) -> None:
    """Ошибка добавления обстоятельства — у формы, а не в панели вверху."""
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert 'id="add-fact-error"' in html
    submit = js.split("async function submitNewFact")[1].split("async function")[0]
    assert "showFactError" in submit
    assert "showError(" not in submit, "ошибка формы снова уходит в панель «Документ»"


def test_fact_origin_is_visible_in_the_list(pipeline) -> None:
    """Внесённое судьёй обстоятельство отличимо от извлечённого машиной."""
    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text
    css = client.get("/static/style.css").text

    assert "внесено судьёй" in js
    assert "исключено из проверок" in js
    assert ".fact-origin" in css


def test_search_score_is_explained(pipeline) -> None:
    """Число у результата поиска подписано: это релевантность, не применимость."""
    client = TestClient(create_app(pipeline))
    html = client.get("/").text
    js = client.get("/static/app.js").text

    assert "лексическая релевантность" in html
    assert "лексическая релевантность" in js


def test_document_has_one_heading_of_the_first_level(pipeline) -> None:
    """Двенадцать h1 читаются скринридером как двенадцать документов.

    Заголовок первого уровня — название системы; разделы идут вторым
    уровнем, иначе навигация по структуре страницы бессмысленна.
    """
    client = TestClient(create_app(pipeline))
    html = client.get("/").text

    assert html.count("<h1") == 1, "заголовков первого уровня должно быть ровно один"
    assert html.count("<h2") >= 10


def test_every_section_is_a_named_landmark(pipeline) -> None:
    """Секция без доступного имени не объявляется ориентиром вовсе."""
    client = TestClient(create_app(pipeline))
    html = client.get("/").text

    sections = re.findall(r"<section[^>]*>", html)
    assert sections
    unnamed = [s for s in sections if "aria-label" not in s]
    assert not unnamed, f"секции без имени: {unnamed}"


def test_dynamic_fields_carry_labels(pipeline) -> None:
    """Поля, которые рисует скрипт, тоже должны иметь подпись.

    Плейсхолдер подписью не является: он исчезает при вводе и читается
    не всеми скринридерами.
    """
    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text

    assert 'for="nf-article"' in js
    assert 'for="nf-part"' in js
    assert 'setAttribute("aria-label", "Значение обстоятельства")' in js


def test_css_has_focus_and_responsive(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    css = client.get("/static/style.css").text
    assert "skip-link" in css
    assert "focus-visible" in css
    assert "@media (max-width:" in css
    assert "@media print" in css
