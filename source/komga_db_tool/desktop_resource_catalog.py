"""Bounded catalog rendering; the open resource and its members are independent."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QAbstractItemView
from .inventory_pager import InventoryPager


def install_resource_catalog(window, table, splitter, kind, *, multiple=False, members=False):
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    table.setMinimumHeight(300)
    table.setProperty("komgaMinimumVisibleRows", 8)
    layout.addWidget(table, 1)
    is_collection = kind == "collection"
    member_key = "seriesIds" if is_collection else "bookIds"
    label = "collections" if is_collection else "readlists"
    selection_label = "membres sélectionnés" if members else "cibles sélectionnées"
    if members:
        label = "séries" if is_collection else "tomes"

    def render(rows):
        if members:
            headers = window._series_table_headers() if is_collection else window._book_table_headers(include_series=True)
            values = [window._series_table_row(row) if is_collection else window._book_table_row(row, include_series=True) for row in rows]
            window._set_table(table, headers, values, row_data=rows, selection_mode=QAbstractItemView.ExtendedSelection)
            return
        window._set_table(table, ["ID", "Nom", "Séries" if is_collection else "Livres"],
                          [[row.id, row.name, len(row.raw.get(member_key) or [])] for row in rows], row_data=rows,
                          selection_mode=QAbstractItemView.ExtendedSelection if multiple else QAbstractItemView.SingleSelection)

    def update_label():
        size = int(pager.size.currentData())
        pages = max(1, (len(pager.rows) + size - 1) // size)
        pager.label.setText(f"{len(pager.rows)} {label} filtrées — {len(pager.visible_rows)} sur cette page — page {pager.page + 1} / {pages}")
        if multiple:
            pager.label.setText(pager.label.text() + f" — {len(pager.selected)} {selection_label} toutes pages")

    pager = InventoryPager(table, layout, render, update_label, compact=True, multiple=multiple)
    pager.clear_button.setText("Effacer le surlignage")
    pager.clear_button.setToolTip("La ressource ouverte reste dans son formulaire.")
    note = QLabel("Changer de page ne modifie ni la ressource ouverte ni l'ordre de ses membres.")
    note.setWordWrap(True)
    if multiple:
        table._resource_target_pager = pager
        pager.clear_button.setText("Vider la sélection")
        pager.clear_button.setToolTip("Effacer la sélection sur toutes les pages.")
        def select_rows(rows):
            pager.selected.update({row.id: row for row in rows})
            pager.show_page(pager.page)
        buttons = QHBoxLayout()
        pager.select_page_button = QPushButton("Sélectionner la page")
        pager.select_all_button = QPushButton("Tous les membres filtrés" if members else "Toutes les cibles filtrées")
        pager.select_page_button.clicked.connect(lambda: select_rows(pager.visible_rows))
        pager.select_all_button.clicked.connect(lambda: select_rows(pager.rows))
        buttons.addWidget(pager.select_page_button)
        buttons.addWidget(pager.select_all_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        note.setText("Maj/Ctrl agissent dans la page. La sélection est conservée entre pages ; recharger remet la sélection à zéro.")
    layout.addWidget(note)
    splitter.addWidget(panel)
    return pager
