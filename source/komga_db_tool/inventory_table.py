"""Read-only table for large inventories, without one Qt item per cell."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QTableView


class InventoryModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.headers: list[str] = []
        self.rows: list[list[Any]] = []
        self.records: list[Any] = []
        self._display_overrides: dict[tuple[int, int, int], Any] = {}

    def replace(self, headers, rows, records=None):
        self.beginResetModel()
        self.headers = list(headers)
        self.rows = rows
        self.records = records if records is not None else []
        self._display_overrides.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        if role == Qt.UserRole and index.column() == 0:
            return self.records[index.row()] if index.row() < len(self.records) else None
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            key = (index.row(), index.column(), int(role))
            if key in self._display_overrides:
                return self._display_overrides[key]
            row = self.rows[index.row()]
            value = row[index.column()] if index.column() < len(row) else ""
            return "" if value is None else str(value)
        return None

    def set_display_value(self, row, column, value, role):
        index = self.index(row, column)
        if index.isValid():
            self._display_overrides[(row, column, int(role))] = value
            self.dataChanged.emit(index, index, [int(role)])

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.headers[section] if 0 <= section < len(self.headers) else None
        return str(section + 1)


class _Cell:
    """Ephemeral facade; display edits never mutate underlying metadata."""
    def __init__(self, view, row, column):
        self.view, self.r, self.c = view, row, column

    def text(self):
        return self.data(Qt.DisplayRole) or ""

    def setText(self, value):
        self.view.model().set_display_value(self.r, self.c, value, Qt.DisplayRole)

    def setToolTip(self, value):
        self.view.model().set_display_value(self.r, self.c, value, Qt.ToolTipRole)

    def data(self, role):
        if self.r < 0:
            return self.view.model().headerData(self.c, Qt.Horizontal, role)
        model = self.view.model()
        return model.data(model.index(self.r, self.c), role)

    def isSelected(self):
        return self.view.selectionModel().isSelected(self.view.model().index(self.r, self.c))

    def row(self):
        return self.r

    def column(self):
        return self.c


class InventoryTable(QTableView):
    itemSelectionChanged = Signal()
    itemDoubleClicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setModel(InventoryModel(self))
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setMinimumHeight(300)
        self.selectionModel().selectionChanged.connect(lambda *_: self.itemSelectionChanged.emit())
        self.doubleClicked.connect(lambda index: self.itemDoubleClicked.emit(self.item(index.row(), index.column())))

    def replace(self, headers, rows, records=None):
        self.model().replace(headers, rows, records)

    def rowCount(self):
        return self.model().rowCount()

    def columnCount(self):
        return self.model().columnCount()

    def horizontalHeaderItem(self, column):
        return _Cell(self, -1, column) if 0 <= column < self.columnCount() else None

    def currentRow(self):
        return self.currentIndex().row()

    def item(self, row, column):
        return _Cell(self, row, column) if self.model().index(row, column).isValid() else None

    def setCurrentCell(self, row, column):
        self.setCurrentIndex(self.model().index(row, column))

    def scrollToItem(self, item, hint=QAbstractItemView.EnsureVisible):
        self.scrollTo(self.model().index(item.row(), item.column()), hint)
