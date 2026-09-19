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


def test_every_status_has_its_own_badge(pipeline) -> None:
    """Статус обстоятельства виден в символе, а не только в цвете.

    Регрессия: символ знали лишь три статуса правил, остальные — включая
    все пять статусов обстоятельств — печатались как «?». Подтверждённое
    обстоятельство и обстоятельство с противоречием выглядели одинаково;
    различал их только цвет рамки, а цвет читается не всеми.
    """
    from second_opinion.domain.enums import FactStatus, RuleStatus

    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text

    table = js[js.index("const STATUS_BADGES = {") :]
    table = table[: table.index("\n};")]

    for status in (*FactStatus, *RuleStatus):
        assert f"{status.value}:" in table, f"нет бейджа для статуса {status.value}"

    # Подпись бейджа — по-русски: её зачитывает экранный диктор, а «VERIFIED»
    # он произносит по буквам.
    assert '"подтверждено"' in table
    assert '"исключено из проверок"' in table


def test_static_version_marker_matches_the_interface(pipeline) -> None:
    """Метка `?v=` поднимается вместе с правкой скрипта или стилей.

    Без этого браузер отдаёт закэшированный файл, и правка выглядит как
    неработающая кнопка — на этом здесь теряли время не раз.

    Сравнение идёт с `HEAD`, поэтому проверка работает до коммита — в
    прогоне перед ним, когда правка ещё не записана. После коммита обоих
    файлов сравнивать нечего, и в CI она ничего не ловит: это местная
    страховка, а не рубеж на въезде.
    """
    import re
    import subprocess
    from pathlib import Path

    client = TestClient(create_app(pipeline))
    html = client.get("/").text

    markers = set(re.findall(r"/static/\w+\.\w+\?v=([\d.]+)", html))
    assert markers, "в разметке нет меток версии статики"
    assert len(markers) == 1, f"метки разошлись: {sorted(markers)}"

    root = Path(__file__).resolve().parents[2]
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "src/second_opinion/static"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if changed.returncode != 0:  # не репозиторий или git недоступен
        return
    touched = {line.rsplit("/", 1)[-1] for line in changed.stdout.split()}
    if touched & {"app.js", "style.css"} and "index.html" not in touched:
        raise AssertionError(
            "скрипт или стили изменены, а метка `?v=` в index.html — нет: "
            "браузер отдаст старый файл"
        )


def test_internal_names_do_not_reach_the_reader(pipeline) -> None:
    """Судья читает по-русски: `verified`, `pattern`, `PASS` — внутренние имена.

    Различие «сверено юристом / черновик» — то самое, на котором держится
    запрет применять непроверенный текст нормы. Пока оно было написано
    словом `draft`, его понимал только тот, кто знает английский и устройство
    корпуса.
    """
    from second_opinion.domain.enums import ExtractionMethod, VerificationStatus

    client = TestClient(create_app(pipeline))
    js = client.get("/static/app.js").text

    def table(name: str) -> str:
        body = js[js.index(f"const {name} = {{") :]
        return body[: body.index("\n};")]

    verification = table("VERIFICATION_LABELS")
    for status in VerificationStatus:
        assert f"{status.value}:" in verification, f"нет подписи для {status.value}"

    methods = table("METHOD_LABELS")
    for method in ExtractionMethod:
        assert f"{method.value}:" in methods, f"нет подписи для {method.value}"

    # Значения подставляются через функции, а не печатаются напрямую.
    assert "${verificationLabel(hit.verification_status)}" in js
    assert "${methodLabel(fact.extraction_method)}" in js
    assert "${statusLabel(fact.status)}" in js
