"""Bounded analysis rendering with complete report records retained as row data."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QAbstractItemView
from .inventory_pager import InventoryPager


def install_resource_analysis(window, table, container, kind):
    collection = kind == "collection"
    panel = QWidget()
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    table.setMinimumHeight(300)
    table.setProperty("komgaMinimumVisibleRows", 8)
    layout.addWidget(table, 1)

    def render(rows):
        if collection:
            headers = ["Proposition", "Source", "Séries à ajouter", "Tomes", "Collection cible", "Déjà dedans", "À ajouter", "Action", "Series IDs"]
            values = [[row.get("name", ""), row.get("rule", ""), len(row.get("series_ids") or []),
                       row.get("book_count", 0), str((row.get("recommended_collection") or {}).get("name") or "Aucune"),
                       row.get("recommended_present", 0), row.get("recommended_missing", 0),
                       row.get("recommended_action", ""), " | ".join(row.get("series_ids") or [])] for row in rows]
        else:
            headers = ["Readlist ID", "Readlist", "Série ID", "Série", "Présents", "Total", "Manquants", "Tomes manquants"]
            values = [[row.get("readlist_id", ""), row.get("readlist_name", ""), row.get("series_id", ""),
                       row.get("series_title", ""), row.get("present_count", 0), row.get("total_count", 0),
                       row.get("missing_count", 0), " | ".join(row.get("missing_books") or [])] for row in rows]
        window._set_table(table, headers, values, row_data=rows, selection_mode=QAbstractItemView.SingleSelection)

    def changed():
        size = int(pager.size.currentData())
        pages = max(1, (len(pager.rows) + size - 1) // size)
        pager.label.setText(f"{len(pager.rows)} résultats — {len(pager.visible_rows)} sur cette page — page {pager.page + 1} / {pages}")
        if collection:
            window.update_collection_suggestion_detail()
        else:
            window.update_readlist_completeness_detail()

    # The report may have duplicate titles or IDs (one readlist, many series).
    # Object identity is unique within this immutable report, reset on new results.
    pager = InventoryPager(table, layout, render, changed, row_key=id, compact=True, multiple=False)
    note = QLabel("Le rapport complet est conservé. Sélectionne une ligne de cette page pour voir son détail.")
    note.setWordWrap(True)
    layout.addWidget(note)
    container.addWidget(panel)
    return pager
