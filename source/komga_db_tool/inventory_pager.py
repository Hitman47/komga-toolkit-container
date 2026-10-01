"""Bounded rendering of an already filtered inventory, with selection by ID.

The complete inventory stays available to callers for explicit all-items actions.
This component does not fetch data or change the scope of those actions.
"""
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton


class InventoryPager:
    def __init__(self, table, layout, render_rows, selection_changed, row_key=lambda row: row.id,
                 *, compact=False, multiple=True, page_sizes=(50, 100, 200)):
        self.table = table
        self.render_rows = render_rows
        self.selection_changed = selection_changed
        self.row_key = row_key
        self.multiple = multiple
        self.rows = []
        self.visible_rows = []
        self.selected = {}
        self.page = 0
        self.rendering = False
        bar = QHBoxLayout()
        self.previous = QPushButton("Page précédente")
        self.next = QPushButton("Page suivante")
        self.label = QLabel("0 série — page 1 / 1")
        self.size = QComboBox()
        for size in page_sizes:
            self.size.addItem(str(size), size)
        self.size.setCurrentIndex(1)
        self.clear_button = QPushButton("Vider la sélection")
        self.clear_button.setToolTip("Effacer la sélection sur toutes les pages ; Maj sélectionne dans la page visible.")
        if compact:
            self.label.setWordWrap(True)
            layout.addWidget(self.label)
            self.previous.setText("Précédente")
            self.next.setText("Suivante")
            self.clear_button.setText("Désélectionner")
        widgets = (self.previous, self.next, self.size, self.clear_button) if compact else (self.previous, self.label, self.next, self.size, self.clear_button)
        for widget in widgets:
            bar.addWidget(widget)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.previous.clicked.connect(lambda: self.show_page(self.page - 1))
        self.next.clicked.connect(lambda: self.show_page(self.page + 1))
        self.size.currentIndexChanged.connect(lambda *_: self.show_page(0))
        self.clear_button.clicked.connect(self.clear_selection)
        table.itemSelectionChanged.connect(self.sync_selection)

    def set_rows(self, rows, *, clear_selection=True):
        self.rows = list(rows)
        valid = {self.row_key(row): row for row in self.rows}
        self.selected = {} if clear_selection else {key: valid[key] for key in self.selected if key in valid}
        self.show_page(0 if clear_selection else self.page)

    def show_page(self, page):
        size = int(self.size.currentData())
        pages = max(1, (len(self.rows) + size - 1) // size)
        self.page = max(0, min(int(page), pages - 1))
        self.visible_rows = self.rows[self.page * size:(self.page + 1) * size]
        if not self.multiple:
            visible_ids = {self.row_key(row) for row in self.visible_rows}
            self.selected = {key: row for key, row in self.selected.items() if key in visible_ids}
        self.rendering = True
        blocked = self.table.blockSignals(True)
        try:
            self.render_rows(self.visible_rows)
            selection = self.table.selectionModel()
            # QTableWidget can retain positional selections when the new page has
            # the same row count. Restore by ID only, never by old row number.
            selection.clear()
            for index, row in enumerate(self.visible_rows):
                if self.row_key(row) in self.selected:
                    selection.select(self.table.model().index(index, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        finally:
            self.table.blockSignals(blocked)
            self.rendering = False
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page < pages - 1)
        self.label.setText(f"{len(self.rows)} séries filtrées — {len(self.visible_rows)} sur cette page — page {self.page + 1} / {pages}")
        self.selection_changed()

    def sync_selection(self):
        if self.rendering:
            return
        indexes = {index.row() for index in self.table.selectionModel().selectedRows()}
        if not self.multiple:
            self.selected.clear()
        for index, row in enumerate(self.visible_rows):
            key = self.row_key(row)
            if index in indexes:
                self.selected[key] = row
            else:
                self.selected.pop(key, None)
        self.selection_changed()

    def selected_rows(self):
        return list(self.selected.values())

    def clear_selection(self):
        self.selected.clear()
        self.table.clearSelection()
        self.selection_changed()
