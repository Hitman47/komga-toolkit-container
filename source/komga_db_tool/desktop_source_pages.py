"""Paged source inventories; bulk selection and filtering use the full inventory."""
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QWidget

from .inventory_pager import InventoryPager


MONO_SOURCES = {"mn": "manga_news", "cv": "comicvine", "metron": "metron"}
PAGED_SOURCES = {**MONO_SOURCES, "mbk": "mangabaka", "naut": "nautiljon", "bdt": "bedetheque"}


class DesktopSourcePagesMixin:
    def _paged_source_selection_for_table(self, table, fallback):
        for pager in getattr(self, "_source_pagers", {}).values():
            if pager.table is table:
                return pager.selected_rows()
        return fallback

    def _source_multiple_active(self, prefix):
        if prefix == "bdt":
            return True  # Existing staged selection for the one-series-at-a-time queue.
        return bool(getattr(self, {"mbk": "mbk_automatch_mode", "naut": "nautiljon_automatch_mode"}.get(prefix, ""), False))

    def _set_source_pager_mode(self, prefix):
        pager = self._source_pager(prefix)
        pager.multiple = self._source_multiple_active(prefix)
        pager.set_rows(pager.rows)

    def _source_pager(self, prefix):
        pagers = self.__dict__.setdefault("_source_pagers", {})
        if prefix not in pagers:
            table = getattr(self, f"{prefix}_komga_series_table")
            table.setProperty("komgaMinimumVisibleRows", 8)
            table.setMinimumHeight(300)

            def render(rows):
                # Existing selection handlers index this visible list, never the full one.
                setattr(self, f"{prefix}_komga_series_rows", rows)
                self._set_table(table, self._series_table_headers(include_history=True),
                                self._series_table_rows_for_source(prefix, rows), stretch_from=1,
                                selection_mode=QAbstractItemView.ExtendedSelection if self._source_multiple_active(prefix) else QAbstractItemView.SingleSelection,
                                row_data=rows)

            def selection_changed():
                if not pagers[prefix].selected_rows():
                    self._clear_paged_source_target(prefix)
                if prefix in ("mbk", "naut", "bdt"):
                    pager = pagers[prefix]
                    multiple = self._source_multiple_active(prefix)
                    pager.source_selection_bar.setVisible(multiple)
                    pager.source_selection_count.setVisible(multiple)
                    pager.source_selection_count.setText(f"{len(pager.selected)} série(s) sélectionnée(s), toutes pages confondues. Maj/Ctrl : page visible.")
                    if prefix != "bdt":
                        getattr(self, f"_refresh_{PAGED_SOURCES[prefix]}_automatch_button")()

            pagers[prefix] = InventoryPager(table, table.parentWidget().layout(), render,
                                           selection_changed, compact=True,
                                           multiple=self._source_multiple_active(prefix))
            if prefix in ("mbk", "naut", "bdt"):
                pager = pagers[prefix]
                pager.source_selection_count = QLabel()
                pager.source_selection_count.setWordWrap(True)
                pager.source_selection_bar = QWidget()
                bar = QHBoxLayout(pager.source_selection_bar)
                bar.setContentsMargins(0, 0, 0, 0)
                pager.select_page_button = QPushButton("Sélectionner cette page")
                pager.select_filtered_button = QPushButton("Toutes les séries filtrées")
                pager.select_filtered_button.setToolTip("Sélectionne toutes les séries filtrées, y compris celles des autres pages. Aucune recherche n'est lancée.")
                bar.addWidget(pager.select_page_button)
                bar.addWidget(pager.select_filtered_button)
                bar.addStretch(1)
                table.parentWidget().layout().addWidget(pager.source_selection_count)
                table.parentWidget().layout().addWidget(pager.source_selection_bar)

                def select_rows(all_filtered=False):
                    if not self._source_multiple_active(prefix):
                        return
                    rows = pager.rows if all_filtered else pager.visible_rows
                    pager.selected.update({row.id: row for row in rows})
                    pager.show_page(pager.page)

                pager.select_page_button.clicked.connect(lambda: select_rows())
                pager.select_filtered_button.clicked.connect(lambda: select_rows(True))
                pager.source_selection_bar.setVisible(self._source_multiple_active(prefix))
                pager.source_selection_count.setVisible(self._source_multiple_active(prefix))
        return pagers[prefix]

    def _clear_paged_source_target(self, prefix):
        if getattr(self, "_source_page_seeking", False):
            return
        generation = "nautiljon_context_generation" if prefix == "naut" else f"{prefix}_context_generation"
        setattr(self, generation, getattr(self, generation, 0) + 1)
        table = getattr(self, f"{prefix}_komga_series_table")
        blocked = table.blockSignals(True)
        try:
            table.setCurrentCell(-1, -1)
            table.clearSelection()
        finally:
            table.blockSignals(blocked)
        if prefix == "naut":
            self.nautiljon_query.clear()
            self.nautiljon_candidate = None
            self.nautiljon_results = []
            self.nautiljon_results_table.setRowCount(0)
            self.nautiljon_metadata_table.setRowCount(0)
            self.nautiljon_preview.setPlainText("Sélectionnez une série Komga.")
            return
        getattr(self, f"{prefix}_target_id").clear()
        getattr(self, f"{prefix}_query").clear()
        if prefix == "bdt":
            self.bdt_komga_book_rows = []
            self.bdt_komga_books_table.setRowCount(0)
            self._clear_bedetheque_comparison_views()
            return
        getattr(self, f"_clear_{PAGED_SOURCES[prefix]}_views")()

    def _display_paged_source_rows(self, prefix, rows, *, log_result=True):
        filtered, active = self._apply_source_series_filters(prefix, rows)
        self._source_pager(prefix).set_rows(filtered)
        if log_result:
            name = self._source_series_view_config(prefix)["name"]
            suffix = f" — {', '.join(active)}" if active else ""
            self.log(f"{name} : {len(rows)} reçues, {len(filtered)} séries filtrées (affichage paginé){suffix}")

    def _load_paged_source_series(self, prefix):
        source = PAGED_SOURCES[prefix]
        library_id = self._library_id(source)
        search = getattr(self, f"{prefix}_komga_search").text().strip()
        generation = self._next_series_load_generation(source)
        self._invalidate_series_cache()
        self._source_series_unfiltered_rows.pop(prefix, None)
        pager = self._source_pager(prefix)
        pager.set_rows([])
        pager.label.setText("Chargement des séries…")
        completed = False

        def current():
            return (self._is_current_series_load_generation(source, generation)
                    and self._library_id(source) == library_id)

        def done(rows):
            nonlocal completed
            if not current():
                return
            completed = True
            rows = self._filter_global_series_visibility(rows)
            self._source_series_unfiltered_rows[prefix] = rows
            self._refresh_source_link_filter_options(prefix, rows)
            self._display_paged_source_rows(prefix, rows)
            if prefix == "naut" and self.nautiljon_pending_series_id:
                target_id = self.nautiljon_pending_series_id
                self.nautiljon_pending_series_id = ""
                if self._seek_paged_source_series(prefix, target_id):
                    self.on_nautiljon_komga_series_selected()

        def finished():
            if current() and not completed:
                pager.label.setText("Chargement échoué — cliquez sur Charger séries Komga pour réessayer.")

        name = self._source_series_view_config(prefix)["name"]
        self.run_worker(f"Chargement séries Komga pour {name}",
                        lambda: self._cached_series(library_id, search=search), done,
                        finished=finished)

    def _seek_paged_source_series(self, prefix, series_id):
        pager = getattr(self, "_source_pagers", {}).get(prefix)
        if pager is None:
            return False
        for index, series in enumerate(pager.rows):
            if series.id == series_id:
                # Explorer handoff sets its target/query before selecting the source row.
                # Do not clear it or trigger a second automatic source request here.
                self._source_page_seeking = True
                try:
                    pager.selected = {series.id: series}
                    pager.show_page(index // int(pager.size.currentData()))
                    item = pager.table.item(index % int(pager.size.currentData()), 0)
                    if item is not None:
                        pager.table.scrollToItem(item)
                finally:
                    self._source_page_seeking = False
                return True
        return False
