from PyQt6.QtWidgets import QWidget

from magscope.ui.search import (
    PanelControlTarget,
    SearchHighlighter,
    SearchMatch,
    SearchRegistry,
    SearchTarget,
    normalize_search_text,
)


def test_normalize_search_text_replaces_separators_and_folds_case():
    assert normalize_search_text("Bead-Lock_Panel") == "bead lock panel"
    assert normalize_search_text("  Foo  Bar  ") == "foo bar"
    assert normalize_search_text("CAMERA") == "camera"


class TestSearchRegistry:
    def test_clear_empties_all_targets(self):
        reg = SearchRegistry([SearchTarget(label="Test")])
        assert len(reg.targets) == 1

        reg.clear()
        assert reg.targets == []

    def test_register_ignores_duplicate_display_label(self):
        reg = SearchRegistry()
        target_a = SearchTarget(label="Camera", context="Settings")
        target_b = SearchTarget(label="Camera", context="Settings")

        reg.register(target_a)
        reg.register(target_b)

        assert len(reg.targets) == 1

    def test_register_accepts_same_label_different_context(self):
        reg = SearchRegistry()
        reg.register(SearchTarget(label="Camera", context="Panel"))
        reg.register(SearchTarget(label="Camera", context="Settings"))

        assert len(reg.targets) == 2

    def test_register_many_adds_all_non_duplicates(self):
        reg = SearchRegistry([
            SearchTarget(label="A"),
            SearchTarget(label="A"),
            SearchTarget(label="B"),
        ])
        assert len(reg.targets) == 3

    def test_matches_exact_display_label(self):
        reg = SearchRegistry([
            SearchTarget(label="Foo", context="Bar Panel"),
        ])
        results = reg.matches("foo - bar panel")
        assert len(results) == 1
        assert results[0].rank == SearchRegistry.RANK_EXACT_DISPLAY

    def test_matches_exact_label(self):
        reg = SearchRegistry([
            SearchTarget(label="BeadLock", context="XY"),
        ])
        results = reg.matches("beadlock")
        assert len(results) == 1
        assert results[0].rank == SearchRegistry.RANK_EXACT_LABEL

    def test_matches_exact_alias(self):
        reg = SearchRegistry([
            SearchTarget(label="Z-Lock", aliases=("focus",)),
        ])
        results = reg.matches("focus")
        assert len(results) == 1
        assert results[0].rank == SearchRegistry.RANK_EXACT_ALIAS

    def test_matches_prefix(self):
        reg = SearchRegistry([
            SearchTarget(label="BeadLockManager"),
        ])
        results = reg.matches("bead")
        assert len(results) == 1
        assert results[0].rank == SearchRegistry.RANK_PREFIX

    def test_matches_contains(self):
        reg = SearchRegistry([
            SearchTarget(label="XYLockPanel"),
        ])
        results = reg.matches("lock")
        assert len(results) == 1
        assert results[0].rank == SearchRegistry.RANK_CONTAINS

    def test_matches_fuzzy_when_no_exact_match(self):
        reg = SearchRegistry([
            SearchTarget(label="FocusMotor"),
        ])
        results = reg.matches("FocusMoter")
        assert len(results) == 1
        assert results[0].rank == SearchRegistry.RANK_FUZZY

    def test_matches_empty_query_returns_all_with_rank_10(self):
        reg = SearchRegistry([
            SearchTarget(label="Alpha"),
            SearchTarget(label="Beta"),
        ])
        results = reg.matches("")
        assert len(results) == 2
        for match in results:
            assert match.rank == SearchRegistry.RANK_EMPTY_QUERY

    def test_matches_sorts_by_rank_and_score(self):
        reg = SearchRegistry([
            SearchTarget(label="Camera Panel", keywords=("cam",)),
            SearchTarget(label="Cam"),
        ])
        results = reg.matches("cam")
        assert len(results) >= 2
        assert results[0].rank <= results[-1].rank

    def test_labels_respects_limit(self):
        reg = SearchRegistry([
            SearchTarget(label="Item 1"),
            SearchTarget(label="Item 2"),
            SearchTarget(label="Item 3"),
        ])
        labels = reg.labels("", limit=2)
        assert len(labels) == 2

    def test_best_returns_none_for_empty_query(self):
        reg = SearchRegistry([
            SearchTarget(label="Something"),
        ])
        assert reg.best("") is None
        assert reg.best("   ") is None

    def test_best_returns_top_match_for_non_empty_query(self):
        reg = SearchRegistry([
            SearchTarget(label="Zoom"),
            SearchTarget(label="Boom"),
        ])
        match = reg.best("zoom")
        assert match is not None
        assert match.label == "Zoom"

    def test_panel_control_target_includes_widget_path(self):
        target = PanelControlTarget(
            label="Test",
            panel_id="camera",
            widget_path=("group", "exposure"),
        )
        assert target.panel_id == "camera"
        assert target.widget_path == ("group", "exposure")


class TestSearchHighlighter:
    def test_clear_handles_runtime_error_from_deleted_widget(self, qtbot):
        widget = QWidget()
        highlighter = SearchHighlighter()
        highlighter.highlight(widget)

        widget.deleteLater()
        qtbot.wait(50)

        highlighter.clear()

    def test_clear_widget_ignores_none_style(self):
        highlighter = SearchHighlighter()
        highlighter.clear_widget(QWidget())
