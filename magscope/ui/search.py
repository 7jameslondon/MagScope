from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QWidget


def normalize_search_text(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").replace("_", " ").split())


@dataclass(frozen=True)
class SearchTarget:
    label: str
    aliases: tuple[str, ...] = ()
    context: str = ""
    description: str = ""
    keywords: tuple[str, ...] = ()
    guide_only: bool = True

    @property
    def display_label(self) -> str:
        return f"{self.label} - {self.context}" if self.context else self.label

    @property
    def search_values(self) -> tuple[str, ...]:
        return (self.label, self.display_label, *self.aliases, *self.keywords)


@dataclass(frozen=True)
class PanelControlTarget(SearchTarget):
    panel_id: str = ""
    widget_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreferencesSettingTarget(SearchTarget):
    setting_key: str = ""
    tab_name: str = "MagScope"


@dataclass(frozen=True)
class PreferencesWidgetTarget(SearchTarget):
    tab_name: str = ""
    widget_attr: str = ""


@dataclass(frozen=True)
class MenuActionTarget(SearchTarget):
    menu_name: str = ""
    action_text: str = ""


@dataclass(frozen=True)
class SearchMatch:
    target: SearchTarget
    rank: int
    score: float = field(default=0.0)


class SearchRegistry:
    RANK_EXACT_LABEL = 0
    RANK_EXACT_DISPLAY = 1
    RANK_EXACT_ALIAS = 2
    RANK_PREFIX = 3
    RANK_CONTAINS = 4
    RANK_FUZZY = 5
    RANK_EMPTY_QUERY = 10

    def __init__(self, targets: list[SearchTarget] | None = None) -> None:
        self._targets: list[SearchTarget] = list(targets or [])

    @property
    def targets(self) -> list[SearchTarget]:
        return list(self._targets)

    def clear(self) -> None:
        self._targets.clear()

    def register(self, target: SearchTarget) -> None:
        if target.display_label in {existing.display_label for existing in self._targets}:
            return
        self._targets.append(target)

    def register_many(self, targets: list[SearchTarget] | tuple[SearchTarget, ...]) -> None:
        for target in targets:
            self.register(target)

    def matches(self, text: str) -> list[SearchMatch]:
        query = normalize_search_text(text)
        if not query:
            return [SearchMatch(target, self.RANK_EMPTY_QUERY, 0.0) for target in self._targets]

        query_terms = query.split()
        matches: list[SearchMatch] = []
        fuzzy_matches: list[SearchMatch] = []
        for target in self._targets:
            normalized_label = normalize_search_text(target.label)
            normalized_display = normalize_search_text(target.display_label)
            normalized_aliases = [normalize_search_text(alias) for alias in target.aliases]
            normalized_keywords = [normalize_search_text(keyword) for keyword in target.keywords]
            normalized_values = [
                normalized_label,
                normalized_display,
                *normalized_aliases,
                *normalized_keywords,
            ]

            if normalized_label == query:
                matches.append(SearchMatch(target, self.RANK_EXACT_LABEL, 1.0))
                continue
            if normalized_display == query:
                matches.append(SearchMatch(target, self.RANK_EXACT_DISPLAY, 1.0))
                continue
            if any(alias == query for alias in normalized_aliases):
                matches.append(SearchMatch(target, self.RANK_EXACT_ALIAS, 1.0))
                continue
            if any(value.startswith(query) for value in normalized_values):
                matches.append(SearchMatch(target, self.RANK_PREFIX, 0.9))
                continue
            if any(query in value or all(term in value for term in query_terms) for value in normalized_values):
                matches.append(SearchMatch(target, self.RANK_CONTAINS, 0.75))
                continue

            fuzzy_score = max(
                (SequenceMatcher(None, query, value).ratio() for value in normalized_values),
                default=0.0,
            )
            if fuzzy_score >= 0.68:
                fuzzy_matches.append(SearchMatch(target, self.RANK_FUZZY, fuzzy_score))

        matches_to_sort = matches if matches else fuzzy_matches
        return sorted(matches_to_sort, key=lambda match: (match.rank, -match.score, match.target.display_label))

    def labels(self, text: str, *, limit: int = 20) -> list[str]:
        labels: list[str] = []
        for match in self.matches(text):
            label = match.target.display_label
            if label not in labels:
                labels.append(label)
            if len(labels) >= limit:
                break
        return labels

    def best(self, text: str) -> SearchTarget | None:
        if not normalize_search_text(text):
            return None
        matches = self.matches(text)
        return matches[0].target if matches else None


class SearchHighlighter:
    def __init__(self) -> None:
        self._original_styles: dict[QWidget, str] = {}

    def clear(self) -> None:
        for widget, style in list(self._original_styles.items()):
            try:
                widget.setStyleSheet(style)
            except RuntimeError:
                pass
        self._original_styles.clear()

    def highlight(self, widget: QWidget, *, duration_ms: int = 2500) -> None:
        self.clear()
        self._original_styles[widget] = widget.styleSheet()
        highlight_color = widget.palette().color(QPalette.ColorRole.Highlight).name()
        widget.setStyleSheet(
            f"border: 2px solid {highlight_color}; border-radius: 4px; padding: 2px;"
        )
        QTimer.singleShot(duration_ms, lambda w=widget: self.clear_widget(w))

    def clear_widget(self, widget: QWidget) -> None:
        original_style = self._original_styles.pop(widget, None)
        if original_style is None:
            return
        try:
            widget.setStyleSheet(original_style)
        except RuntimeError:
            pass
