"""UI a11y/адаптивность: регрессия разметки и скриптов (без браузера)."""

from __future__ import annotations

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


def test_css_has_focus_and_responsive(pipeline) -> None:
    client = TestClient(create_app(pipeline))
    css = client.get("/static/style.css").text
    assert "skip-link" in css
    assert "focus-visible" in css
    assert "@media (max-width:" in css
    assert "@media print" in css
