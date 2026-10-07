from __future__ import annotations

from second_opinion.legal_sources.history_trace import trace_article_history
from second_opinion.legal_sources.pravo_gov import Edition


class FakeSource:
    def __init__(self, texts_by_edition: dict[int, dict[str, str]], unprepared: int = 0) -> None:
        self._texts = texts_by_edition
        self._unprepared = unprepared
        self.fetched: list[int] = []

    def editions(self, topic: str) -> list[Edition]:
        prepared = [Edition(number=n, label=f"{n} - акт {n}") for n in sorted(self._texts)]
        return prepared + [Edition(number=None, label="не готова")] * self._unprepared

    def article_texts(self, topic: str, rdk: int) -> dict[str, str]:
        self.fetched.append(rdk)
        return self._texts[rdk]


def _editions(count: int, changes: dict[str, dict[int, str]]) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for number in range(count):
        result[number] = {}
        for article, by_edition in changes.items():
            current = by_edition[0]
            for at, text in by_edition.items():
                if number >= at:
                    current = text
            result[number][article] = current
    return result


def test_change_points_are_found_for_each_article() -> None:
    source = FakeSource(
        _editions(
            16,
            {
                "10": {0: "v1", 5: "v2", 12: "v3"},
                "11": {0: "same"},
                "12": {0: "a", 7: "b"},
            },
        )
    )
    history = trace_article_history(source, "1", ["10", "11", "12"])  # type: ignore[arg-type]
    assert [(v.edition, v.text) for v in history["10"]] == [(0, "v1"), (5, "v2"), (12, "v3")]
    assert [(v.edition, v.text) for v in history["11"]] == [(0, "same")]
    assert [(v.edition, v.text) for v in history["12"]] == [(0, "a"), (7, "b")]


def test_every_edition_is_fetched_at_most_once_and_not_all_of_them() -> None:
    source = FakeSource(_editions(64, {"10": {0: "v1", 40: "v2"}}))
    trace_article_history(source, "1", ["10"])  # type: ignore[arg-type]
    assert len(source.fetched) == len(set(source.fetched))
    assert len(source.fetched) < 64


def test_unprepared_editions_are_ignored_and_label_is_kept() -> None:
    source = FakeSource(_editions(4, {"10": {0: "v1", 2: "v2"}}), unprepared=3)
    history = trace_article_history(source, "1", ["10"])  # type: ignore[arg-type]
    assert [v.label for v in history["10"]] == ["0 - акт 0", "2 - акт 2"]


def test_article_added_later_starts_with_empty_text() -> None:
    texts = {0: {}, 1: {}, 2: {"76.2": "новая статья"}, 3: {"76.2": "новая статья"}}
    history = trace_article_history(FakeSource(texts), "1", ["76.2"])  # type: ignore[arg-type]
    assert [(v.edition, v.text) for v in history["76.2"]] == [(0, ""), (2, "новая статья")]


def test_cache_lets_a_rerun_skip_fetched_editions(tmp_path) -> None:
    texts = _editions(16, {"10": {0: "v1", 9: "v2"}})
    cache = tmp_path / "trace.cache.json"
    first = FakeSource(texts)
    trace_article_history(first, "1", ["10"], cache_path=cache)  # type: ignore[arg-type]
    second = FakeSource(texts)
    history = trace_article_history(second, "1", ["10"], cache_path=cache)  # type: ignore[arg-type]
    assert second.fetched == []
    assert [(v.edition, v.text) for v in history["10"]] == [(0, "v1"), (9, "v2")]


def test_cache_with_missing_article_is_refetched(tmp_path) -> None:
    texts = _editions(4, {"10": {0: "v1", 2: "v2"}, "11": {0: "a", 3: "b"}})
    cache = tmp_path / "trace.cache.json"
    trace_article_history(FakeSource(texts), "1", ["10"], cache_path=cache)  # type: ignore[arg-type]
    second = FakeSource(texts)
    history = trace_article_history(second, "1", ["10", "11"], cache_path=cache)  # type: ignore[arg-type]
    assert second.fetched
    assert [(v.edition, v.text) for v in history["11"]] == [(0, "a"), (3, "b")]
