"""Paginated tracking results; field decisions remain on the complete report."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from .inventory_pager import InventoryPager


def install_tracking_results(window, layout):
    def render(entries):
        window.rt_table.setUpdatesEnabled(False)
        try:
            window._render_release_tracking_result_page(entries)
        finally:
            window.rt_table.setUpdatesEnabled(True)

    def selection_changed():
        window.rt_detail.clear()
        window.show_release_tracking_detail()
        update_summary(window)

    window.rt_result_pager = InventoryPager(
        window.rt_table, layout, render,
        selection_changed, row_key=lambda entry: entry[0], compact=True,
        page_sizes=(50, 100, 200, 500),
    )
    window.rt_result_pager.clear_button.setText("Effacer le surlignage")
    window.rt_result_pager.clear_button.setToolTip("Ne change pas les cases statut/tomes à appliquer.")
    window.rt_result_summary = QLabel()
    window.rt_result_summary.setWordWrap(True)
    layout.addWidget(window.rt_result_summary)

    def field_changed(item):
        pager = window.rt_result_pager
        if pager.rendering or item.column() not in (0, 1):
            return
        index = item.data(Qt.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(window.release_tracking_rows):
            return
        field = "apply_status" if item.column() == 0 else "apply_totalBookCount"
        window.release_tracking_rows[index][field] = item.checkState() == Qt.Checked
        update_summary(window)

    window.rt_table.itemChanged.connect(field_changed)


def update_summary(window):
    pager = window.rt_result_pager
    if not hasattr(window, "rt_result_summary"):
        return
    size = int(pager.size.currentData())
    pages = max(1, (len(pager.rows) + size - 1) // size)
    pager.label.setText(f"{len(pager.rows)} résultats filtrés — {len(pager.visible_rows)} sur cette page — page {pager.page + 1} / {pages}")
    checked = sum(bool(row.get("apply_status") or row.get("apply_totalBookCount"))
                  for row in window.release_tracking_rows)
    visible_checked = sum(bool(row.get("apply_status") or row.get("apply_totalBookCount"))
                          for _, row in pager.rows)
    window.rt_result_summary.setText(
        f"{len(window.release_tracking_rows)} résultats reçus · {len(pager.rows)} filtrés · "
        f"{checked} séries cochées, dont {checked - visible_checked} masquées par les filtres. "
        "Les cases statut/tomes sont conservées entre pages et filtres. "
        "Appliquer, tout cocher/décocher et exporter portent sur le rapport complet."
    )


def refresh_tracking_results(window):
    pager = getattr(window, "rt_result_pager", None)
    if pager is None:
        return
    entries = [(index, row) for index, row in enumerate(window.release_tracking_rows)
               if window._release_tracking_row_matches_filter(row)]
    pager.set_rows(entries)
    window._update_release_tracking_selection_label()
