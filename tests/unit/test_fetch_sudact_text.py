from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from fetch_sudact_text import page_to_text  # noqa: E402

PAGE = (
    "<html><body><h1>Приговор № 1</h1><script>var x = 1;</script>"
    '<hr class="hr-h1"><br ><b ><center >ПРИГОВОР</b></center><br >'
    "Именем Российской Федерации<br ><br >Суд &laquo;рассмотрел&raquo; дело.<br >"
    "<!-- реклама -->П Р И Г О В О Р И Л: признать виновным.<br >"
    "<br>Суд:<br>Районный суд<br>Судьи дела:</body></html>"
)


def test_page_is_reduced_to_the_decision_text() -> None:
    text = page_to_text(PAGE)
    assert text.startswith("ПРИГОВОР")
    assert "Именем Российской Федерации" in text
    assert "Суд «рассмотрел» дело." in text
    assert "П Р И Г О В О Р И Л: признать виновным." in text
    assert "var x" not in text and "реклама" not in text
    assert "Судьи дела" not in text and "Районный суд" not in text
