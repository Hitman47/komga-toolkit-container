"""Explorer series pages and ID-based cross-page selection, without write logic."""
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QPushButton

from .integrations import CachedKomgaService
from .runtime import MemoryCache
from .series_inventory import series_inventory_page
from .kora.local_exclusions import LocalExclusionsStore


class DesktopSeriesMixin:
    def _init_series_pagination(self, layout):
        self._series_page_api = None
        self._series_page_result = None
        self._series_paging_active = False
        self._series_page_rendering = False
        self._series_paged_selection = {}
        bar = QHBoxLayout()
        self.series_page_previous = QPushButton("Page précédente")
        self.series_page_next = QPushButton("Page suivante")
        self.series_page_label = QLabel("Page 1 / 1")
        self.series_page_size = QComboBox()
        for size in (50, 100, 200):
            self.series_page_size.addItem(str(size), size)
        self.series_page_size.setCurrentIndex(1)
        clear = QPushButton("Vider la sélection")
        clear.setToolTip("Vider la sélection de séries sur toutes les pages.")
        for widget in (self.series_page_previous, self.series_page_label, self.series_page_next):
            bar.addWidget(widget)
        bar.addStretch(1)
        layout.addLayout(bar)
        options = QHBoxLayout()
        options.addWidget(QLabel("Séries par page"))
        options.addWidget(self.series_page_size)
        options.addWidget(clear)
        options.addStretch(1)
        layout.addLayout(options)
        self.series_page_previous.setEnabled(False)
        self.series_page_next.setEnabled(False)
        self.series_page_previous.clicked.connect(lambda: self._request_series_page(self._series_page_result["page"]-1))
        self.series_page_next.clicked.connect(lambda: self._request_series_page(self._series_page_result["page"]+1))
        self.series_page_size.currentIndexChanged.connect(lambda *_: self.load_series(force_refresh=False))
        clear.clicked.connect(self.clear_series_page_selection)

    def _series_page_filter(self):
        from .gui import is_blank_metadata_value, metadata_language_matches, metadata_status_matches, metadata_link_label_matches
        empty = self.filter_series_empty_summary.isChecked()
        language = self.filter_series_language.currentData() or ""
        status = self.filter_series_status.currentData() or "ALL"
        link = self.filter_series_link_label.currentData() or "ALL"
        hide_paths = not self._show_chap_scan_series()
        # One immutable rules snapshot per query, not several disk reads per row.
        rules = self.local_exclusions.load()
        excluded_ids = set(rules.get("excluded_series_ids") or {})
        title_rules = [r for r in rules.get("title_rules", []) if r.get("enabled", True) and r.get("pattern")]
        names = {str(name).casefold() for name in rules.get("excluded_library_names", [])}
        excluded_libraries = {row.id for row in self.libraries if row.name.casefold() in names}
        if not any((empty, language, status != "ALL", link != "ALL", hide_paths, excluded_ids, title_rules, excluded_libraries)):
            return None
        def filtered(rows):
            return [row for row in rows
                    if row.id not in excluded_ids and row.library_id not in excluded_libraries
                    and not any(LocalExclusionsStore._title_matches_rule(row.title, rule) for rule in title_rules)
                    and not (hide_paths and self._is_chap_scan_series(row))
                    and (not empty or is_blank_metadata_value(row.metadata.get("summary")))
                    and metadata_language_matches(row.metadata.get("language"), language)
                    and metadata_status_matches(row.metadata.get("status"), status)
                    and metadata_link_label_matches(row.metadata.get("links"), link)]
        return filtered

    def load_series(self, *, force_refresh=True):
        library_id = self._library_id("explorer")
        if force_refresh:
            self._invalidate_series_cache()
            self._invalidate_book_cache()
        if force_refresh or self._series_page_api is None or library_id != getattr(self, "_series_page_library", None):
            self._series_page_api = CachedKomgaService(self.komga_api(), MemoryCache(), content_ttl_seconds=20)
        self._series_page_library = library_id
        self._series_page_search = self.search_series_text.text().strip()
        self._series_page_predicate = self._series_page_filter()
        self._series_paging_active = True
        self._series_paged_selection.clear()
        if not getattr(self, "_komga_connection_validated", False) or not self.libraries:
            self._next_series_load_generation("explorer")
            self._clear_series_page()
            self.explorer_series_count_label.setText("Connectez Komga et chargez les bibliothèques.")
            return
        self._request_series_page(0)

    def _clear_series_page(self):
        self._series_page_rendering = True
        try:
            self._series_page_result = None
            self.series_rows = []
            self._set_table(self.series_table, self._series_table_headers(include_library=True), [], row_data=[])
            self._clear_explorer_series_selection()
            self.series_page_previous.setEnabled(False)
            self.series_page_next.setEnabled(False)
        finally:
            self._series_page_rendering = False

    def _request_series_page(self, page):
        api = self._series_page_api
        library_id = self._series_page_library
        if api is None or library_id != self._library_id("explorer"):
            return
        generation = self._next_series_load_generation("explorer")
        predicate, search = self._series_page_predicate, self._series_page_search
        size = self.series_page_size.currentData()
        self._clear_series_page()
        self.explorer_series_count_label.setText(f"Chargement de la page {page+1}…")
        completed = False
        def current():
            return (self._series_page_api is api and library_id == self._library_id("explorer")
                    and self._is_current_series_load_generation("explorer", generation))
        def done(result):
            nonlocal completed
            if current():
                completed = True
                self._render_series_page(result)
        def finished():
            if current() and not completed:
                self.explorer_series_count_label.setText("Chargement échoué — rechargez les séries pour réessayer.")
        self.run_worker("Chargement séries — page",
                        lambda: series_inventory_page(api, library_id, search=search, page=page, size=size, row_filter=predicate),
                        done, finished)

    def _render_series_page(self, result):
        self._series_page_result = result
        self.series_rows = result["items"]
        self._series_page_rendering = True
        try:
            self._set_table(self.series_table, self._series_table_headers(include_library=True),
                            [self._series_table_row(row, include_library=True) for row in self.series_rows],
                            selection_mode=QAbstractItemView.ExtendedSelection, row_data=self.series_rows)
            for index, row in enumerate(self.series_rows):
                if row.id in self._series_paged_selection:
                    self._series_paged_selection[row.id] = row
                    self.series_table.selectionModel().select(self.series_table.model().index(index, 0),
                                                              QItemSelectionModel.Select | QItemSelectionModel.Rows)
        finally:
            self._series_page_rendering = False
        self.series_page_label.setText(f"Page {result['page']+1} / {result['total_pages']}")
        self.series_page_previous.setEnabled(not result["first"])
        self.series_page_next.setEnabled(not result["last"])
        mode = "pages Komga" if result["load_mode"] == "komga_page" else "filtres/exclusions sur inventaire complet"
        total_label = "au total" if result["load_mode"] == "komga_page" else "reçues"
        self.explorer_series_count_label.setText(
            f"{result['filtered_total']} correspondent / {result['total']} {total_label} — {len(self.series_rows)} sur cette page — {result['hidden']} masquées — {mode}")
        self.on_series_selected()

    def _sync_series_page_selection(self):
        selected = set(self._selected_row_indexes(self.series_table))
        for index, row in enumerate(self.series_rows):
            if index in selected:
                self._series_paged_selection[row.id] = row
            else:
                self._series_paged_selection.pop(row.id, None)

    def _update_series_selection_controls(self):
        count = len(self._series_paged_selection)
        self.explorer_selection_label.setText(f"{count} série(s) sélectionnée(s) — toutes les pages (Maj : intervalle de la page)")
        self.explorer_selection_label.setWordWrap(True)
        for button in self.explorer_action_buttons:
            button.setEnabled(count > 0)

    def clear_series_page_selection(self):
        self._series_paged_selection.clear()
        self.series_table.clearSelection()
        self._update_series_selection_controls()
