from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .constants import KORA_GENRES, MAX_KORA_GENRES
from .models import PendingChange
from .suggestions import SeriesGenreSuggestion
from .tag_logic import genre_label, readable_genres, validate_genres


class KoraSuggestionDialog(QDialog):
    """Révision en lot des suggestions, sans écriture avant validation explicite."""

    def __init__(self, suggestions: list[SeriesGenreSuggestion], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Suggestions de genres Kora")
        self.resize(1450, 820)
        self.action: Literal["queue", "apply"] | None = None
        self.drafts = [
            {
                "suggestion": suggestion,
                "genres": list(suggestion.suggested_genres),
                "apply": suggestion.changed,
            }
            for suggestion in suggestions
        ]
        self.genre_checks: dict[str, QCheckBox] = {}
        self._loading_editor = False

        root = QVBoxLayout(self)
        intro = QLabel(
            "Les genres Komga sont prioritaires ; les tags Komga servent de fallback. "
            f"Les genres Kora existants sont conservés, avec un maximum de {MAX_KORA_GENRES}."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Appliquer",
                "Bibliothèque",
                "Série",
                "Genres Komga",
                "Genres Kora actuels",
                "Genres Kora proposés",
                "État",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._load_selected_editor)
        splitter.addWidget(self.table)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        self.editor_title = QLabel("Sélectionne une série")
        self.editor_title.setWordWrap(True)
        self.editor_title.setStyleSheet("font-weight: 700;")
        editor_layout.addWidget(self.editor_title)

        genre_box = QGroupBox(f"Genres Kora proposés — maximum {MAX_KORA_GENRES}")
        genre_layout = QVBoxLayout(genre_box)
        for slug in KORA_GENRES:
            checkbox = QCheckBox(genre_label(slug))
            checkbox.stateChanged.connect(
                lambda _state=0, selected_slug=slug: self._on_genre_changed(selected_slug)
            )
            self.genre_checks[slug] = checkbox
            genre_layout.addWidget(checkbox)
        editor_layout.addWidget(genre_box)

        self.evidence_label = QLabel("")
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        editor_layout.addWidget(self.evidence_label)
        editor_layout.addStretch(1)
        splitter.addWidget(editor)
        splitter.setSizes([1000, 420])
        root.addWidget(splitter, 1)

        self.summary_label = QLabel("")
        root.addWidget(self.summary_label)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("Annuler")
        cancel_button.clicked.connect(self.reject)
        queue_button = QPushButton("Mettre en attente")
        queue_button.clicked.connect(lambda: self._accept_action("queue"))
        apply_button = QPushButton("Valider et appliquer")
        apply_button.clicked.connect(lambda: self._accept_action("apply"))
        actions.addWidget(cancel_button)
        actions.addWidget(queue_button)
        actions.addWidget(apply_button)
        root.addLayout(actions)

        self._populate_table()

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self.drafts))
        for row, draft in enumerate(self.drafts):
            suggestion = draft["suggestion"]
            assert isinstance(suggestion, SeriesGenreSuggestion)
            apply_item = QTableWidgetItem("")
            apply_item.setFlags((apply_item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            apply_item.setCheckState(Qt.Checked if draft["apply"] else Qt.Unchecked)
            self.table.setItem(row, 0, apply_item)
            values = [
                suggestion.library_name,
                suggestion.title,
                " | ".join(suggestion.komga_genres) or "—",
                readable_genres(suggestion.current_genres) or "Aucun",
                readable_genres(draft["genres"]) or "Aucun",
                self._status_text(row),
            ]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row, column, item)
        self.table.setColumnWidth(0, 75)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 290)
        self.table.setColumnWidth(3, 220)
        self.table.setColumnWidth(4, 230)
        self.table.setColumnWidth(5, 260)
        self.table.setColumnWidth(6, 180)
        if self.table.rowCount():
            self.table.selectRow(0)
        self._refresh_summary()

    def _status_text(self, row: int) -> str:
        draft = self.drafts[row]
        suggestion = draft["suggestion"]
        assert isinstance(suggestion, SeriesGenreSuggestion)
        genres = draft["genres"]
        if len(genres) > MAX_KORA_GENRES:
            return "À corriger : limite dépassée"
        statuses: list[str] = []
        if suggestion.has_pending:
            statuses.append("pending existant")
        if suggestion.overflow_genres:
            statuses.append(f"{len(suggestion.overflow_genres)} secondaire(s)")
        if tuple(genres) == suggestion.current_genres:
            statuses.append("inchangé")
        elif not statuses:
            statuses.append("prêt")
        return " · ".join(statuses)

    def _selected_row(self) -> int:
        return self.table.currentRow()

    def _load_selected_editor(self) -> None:
        row = self._selected_row()
        enabled = row >= 0
        for checkbox in self.genre_checks.values():
            checkbox.setEnabled(enabled)
        if not enabled:
            return
        draft = self.drafts[row]
        suggestion = draft["suggestion"]
        assert isinstance(suggestion, SeriesGenreSuggestion)
        genres = set(draft["genres"])
        self._loading_editor = True
        try:
            for slug, checkbox in self.genre_checks.items():
                checkbox.setChecked(slug in genres)
        finally:
            self._loading_editor = False
        pending = " — une modification en attente sert de point de départ" if suggestion.has_pending else ""
        self.editor_title.setText(f"{suggestion.library_name} — {suggestion.title}{pending}")
        candidate_lines: list[str] = []
        for candidate in suggestion.candidates:
            sources = ", ".join(
                f"{item.source} « {item.value} »"
                for item in candidate.evidence
            )
            state = "retenu" if candidate.slug in genres else "secondaire"
            candidate_lines.append(
                f"• {genre_label(candidate.slug)} — {state}, score {candidate.score} — {sources}"
            )
        if candidate_lines:
            self.evidence_label.setText("Origine des propositions :\n" + "\n".join(candidate_lines))
        else:
            self.evidence_label.setText("Aucune correspondance fiable trouvée.")

    def _on_genre_changed(self, slug: str) -> None:
        if self._loading_editor:
            return
        row = self._selected_row()
        if row < 0:
            return
        draft = self.drafts[row]
        genres = list(draft["genres"])
        checkbox = self.genre_checks[slug]
        if checkbox.isChecked() and slug not in genres:
            if len(genres) >= MAX_KORA_GENRES:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
                QMessageBox.information(
                    self,
                    "Genres Kora",
                    f"Maximum {MAX_KORA_GENRES} genres. Retire d’abord un genre pour en choisir un autre.",
                )
                return
            genres.append(slug)
        elif not checkbox.isChecked() and slug in genres:
            genres.remove(slug)
        draft["genres"] = genres
        suggestion = draft["suggestion"]
        assert isinstance(suggestion, SeriesGenreSuggestion)
        changed = tuple(genres) != suggestion.current_genres and len(genres) <= MAX_KORA_GENRES
        draft["apply"] = changed
        self.table.item(row, 0).setCheckState(Qt.Checked if changed else Qt.Unchecked)
        self.table.item(row, 5).setText(readable_genres(genres) or "Aucun")
        self.table.item(row, 6).setText(self._status_text(row))
        self._refresh_summary()
        self._load_selected_editor()

    def _refresh_summary(self) -> None:
        selected = 0
        unchanged = 0
        overflow = 0
        conflicts = 0
        for row, draft in enumerate(self.drafts):
            suggestion = draft["suggestion"]
            assert isinstance(suggestion, SeriesGenreSuggestion)
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                selected += 1
            if tuple(draft["genres"]) == suggestion.current_genres:
                unchanged += 1
            if suggestion.overflow_genres:
                overflow += 1
            if suggestion.has_pending:
                conflicts += 1
        self.summary_label.setText(
            f"{len(self.drafts)} série(s) · {selected} modification(s) cochée(s) · "
            f"{unchanged} inchangée(s) · {overflow} avec suggestion(s) secondaire(s) · "
            f"{conflicts} pending existant(s)"
        )

    def selected_changes(self) -> list[PendingChange]:
        changes: list[PendingChange] = []
        for row, draft in enumerate(self.drafts):
            item = self.table.item(row, 0)
            if item is None or item.checkState() != Qt.Checked:
                continue
            suggestion = draft["suggestion"]
            assert isinstance(suggestion, SeriesGenreSuggestion)
            genres = validate_genres(draft["genres"])
            if tuple(genres) == suggestion.current_genres:
                continue
            changes.append(
                PendingChange(
                    suggestion.series_id,
                    suggestion.library_name,
                    suggestion.title,
                    genres,
                    source="genre-suggestion",
                    note="genres Komga puis fallback tags",
                )
            )
        return changes

    def _accept_action(self, action: Literal["queue", "apply"]) -> None:
        try:
            changes = self.selected_changes()
        except ValueError as exc:
            QMessageBox.warning(self, "Genres Kora", str(exc))
            return
        if not changes:
            QMessageBox.information(self, "Genres Kora", "Aucune modification cochée.")
            return
        self.action = action
        self.accept()
