"""Page the view, never the authoritative ordered membership in the form."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from .inventory_pager import InventoryPager


def install_resource_members(window, table, layout, kind, ids_from_text):
    collection = kind == "collection"
    editor = window.collection_series_ids if collection else window.readlist_book_ids
    records_attr = "collection_member_rows" if collection else "readlist_book_rows"
    panel = QWidget()
    box = QVBoxLayout(panel)
    box.setContentsMargins(0, 0, 0, 0)
    table.setMinimumHeight(300)
    table.setProperty("komgaMinimumVisibleRows", 8)
    box.addWidget(table, 1)

    def render(rows):
        headers = window._series_table_headers() if collection else window._book_table_headers(include_series=True)
        values = [window._series_table_row(row) if collection else window._book_table_row(row, include_series=True) for row in rows]
        window._set_table(table, headers, values, row_data=rows)

    def label():
        size = int(pager.size.currentData())
        pages = max(1, (len(pager.rows) + size - 1) // size)
        pager.label.setText(f"{len(pager.rows)} membres affichables — {len(pager.visible_rows)} sur cette page — page {pager.page + 1} / {pages} — {len(ids_from_text(editor.toPlainText()))} IDs dans la liste complète")

    pager = InventoryPager(table, box, render, label, row_key=window._record_id, compact=True, multiple=False)

    def refresh(*, clear_selection=False, follow_id=None):
        records = {window._record_id(row): row for row in getattr(window, records_attr)}
        excluded = window.local_exclusions.ids() if collection else set()
        rows = [records.get(key, {"id": key, "name": "Membre ajouté — détail non chargé"})
                for key in ids_from_text(editor.toPlainText()) if key not in excluded]
        pager.set_rows(rows, clear_selection=clear_selection)
        if follow_id:
            index = next((i for i, row in enumerate(rows) if window._record_id(row) == follow_id), -1)
            if index >= 0:
                pager.show_page(index // int(pager.size.currentData()))
                table.setCurrentCell(index % int(pager.size.currentData()), 0)
                table.selectRow(index % int(pager.size.currentData()))

    pager.refresh = refresh
    editor.textChanged.connect(lambda: refresh())
    note = QLabel("Monter/Descendre agit dans la liste complète et suit le membre entre pages. La pagination ne modifie pas l'ordre enregistré.")
    note.setWordWrap(True)
    box.addWidget(note)
    layout.addWidget(panel, 1)
    return pager
