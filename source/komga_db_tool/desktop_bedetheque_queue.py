"""Bounded rendering of the Bedetheque validation queue; no network or writes."""
from .inventory_pager import InventoryPager


def install_queue_pager(window, table, layout):
    table.setProperty("komgaMinimumVisibleRows", 8)
    table.setMinimumHeight(300)
    window._register_table(table, "bedetheque.queue", default_hidden=["ID"])

    def render(rows):
        positions = {series.id: i + 1 for i, series in enumerate(window.bdt_queue)}
        libraries = {lib.id: lib.name for lib in window.libraries}
        window._set_table(table, ["Position", "Titre", "Bibliothèque", "ID"],
                          [[positions[row.id], row.title, libraries.get(row.library_id, ""), row.id] for row in rows],
                          stretch_from=1, row_data=rows)

    def selection_changed():
        selected = window.bdt_queue_pager.selected_rows()
        if selected:
            window.bdt_queue_index = next((i for i, row in enumerate(window.bdt_queue) if row.id == selected[0].id), -1)
        update_status(window)

    window.bdt_queue_pager = InventoryPager(table, layout, render, selection_changed, compact=True, multiple=False)


def update_status(window):
    total = len(window.bdt_queue)
    current = window.bdt_queue_index + 1 if 0 <= window.bdt_queue_index < total else 0
    window.bdt_queue_status.setText(
        f"File : {current}/{total}. Ouvrir la série, vérifier le candidat et la prévisualisation, "
        "appliquer séparément, puis passer à la suivante. Retirer de la file ne supprime rien dans Komga."
    )


def refresh_queue(window):
    pager = getattr(window, "bdt_queue_pager", None)
    if pager is None:
        return
    intended_index = window.bdt_queue_index
    pager.set_rows(window.bdt_queue, clear_selection=False)
    window.bdt_queue_index = intended_index
    if 0 <= window.bdt_queue_index < len(window.bdt_queue):
        series = window.bdt_queue[window.bdt_queue_index]
        pager.selected = {series.id: series}
        pager.show_page(window.bdt_queue_index // int(pager.size.currentData()))
    update_status(window)
