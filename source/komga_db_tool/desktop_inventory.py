"""Desktop book paging, isolated from the enrichment and write workflows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QItemSelectionModel, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QAbstractItemView

from .integrations import CachedKomgaService
from .inventory import inventory_page
from .runtime import MemoryCache


class DesktopInventoryMixin:
    def _init_book_pagination(self, layout):
        self._book_page_api = None
        self._book_page_result = None
        self._book_paged_selection = {}
        self._book_page_rendering = False
        self._book_page_options = {}
        self._book_page_timer = QTimer(self)
        self._book_page_timer.setSingleShot(True)
        self._book_page_timer.setInterval(300)
        self._book_page_timer.timeout.connect(lambda: self._request_book_page(0))
        bar = QHBoxLayout()
        self.book_page_previous = QPushButton("Page précédente")
        self.book_page_next = QPushButton("Page suivante")
        self.book_page_label = QLabel("Page 1 / 1")
        self.book_page_size = QComboBox()
        for size in (50, 100, 200, 500):
            self.book_page_size.addItem(str(size), size)
        self.book_page_size.setCurrentIndex(1)
        clear = QPushButton("Vider la sélection")
        clear.setToolTip("Retirer tous les tomes sélectionnés, sur toutes les pages.")
        bar.addWidget(self.book_page_previous)
        bar.addWidget(self.book_page_label)
        bar.addWidget(self.book_page_next)
        bar.addStretch(1)
        bar.addWidget(QLabel("Par page"))
        bar.addWidget(self.book_page_size)
        bar.addWidget(clear)
        layout.addLayout(bar)
        self.book_page_previous.setEnabled(False)
        self.book_page_next.setEnabled(False)
        self.book_page_previous.clicked.connect(lambda: self._request_book_page(self._book_page_result["page"] - 1))
        self.book_page_next.clicked.connect(lambda: self._request_book_page(self._book_page_result["page"] + 1))
        self.book_page_size.currentIndexChanged.connect(lambda *_: self.apply_book_explorer_filters())
        clear.clicked.connect(self.clear_book_page_selection)

    def _book_filter_options(self):
        days = int(self.book_explorer_added_filter.currentData() or 0)
        return dict(
            query=self.book_explorer_search.text(), added_days=days,
            language=str(self.book_explorer_language_filter.currentData() or ""),
            series_status=str(self.book_explorer_status_filter.currentData() or "ALL"),
            source_filter=str(self.book_explorer_source_filter.currentData() or "all"),
            missing_field=str(self.book_explorer_missing_filter.currentData() or ""),
            empty_summary=self.book_explorer_empty_summary_filter.isChecked(),
            hide_chapter_series=self.book_explorer_hide_chapters_filter.isChecked(),
            sort_field=str(self.book_explorer_sort.currentData() or "added_at"),
            descending=bool(self.book_explorer_order.currentData()),
            page_size=int(self.book_page_size.currentData()),
        )

    def clear_book_page_selection(self):
        self._book_paged_selection.clear()
        self.book_explorer_table.clearSelection()
        self.on_book_explorer_selected()

    def _clear_book_page(self, *, clear_selection=False):
        self._book_page_rendering = True
        try:
            if clear_selection:
                self._book_paged_selection.clear()
            self.book_explorer_rows = []
            self.book_explorer_visible_rows = []
            self._set_table(self.book_explorer_table, self._book_explorer_headers(), [], row_data=[])
            self.book_page_previous.setEnabled(False)
            self.book_page_next.setEnabled(False)
            self.book_explorer_analyze_button.setEnabled(False)
        finally:
            self._book_page_rendering = False
        self.on_book_explorer_selected()
        # No enrichment while a different query is being loaded.
        self.book_explorer_analyze_button.setEnabled(False)

    def load_book_explorer(self):
        self._book_page_timer.stop()
        self._next_series_load_generation("book_explorer")
        self._book_page_api = None
        self._book_page_result = None
        self._clear_book_page(clear_selection=True)
        library_id = self._library_id("book_explorer")
        if not library_id:
            self.book_explorer_count_label.setText("Choisissez une bibliothèque pour charger les tomes.")
            return
        # Separate bounded cache per explicit refresh / connection / library.
        # Capture the API on the UI thread; workers never read Qt credentials.
        self._book_page_api = CachedKomgaService(self.komga_api(), MemoryCache(), content_ttl_seconds=20)
        self._book_page_library = library_id
        self._book_page_options = self._book_filter_options()
        self._book_filter_time = datetime.now(timezone.utc)
        self._request_book_page(0)

    def _schedule_book_page(self):
        self._next_series_load_generation("book_explorer")
        self._book_page_options = self._book_filter_options()
        self._book_filter_time = datetime.now(timezone.utc)
        self._clear_book_page(clear_selection=True)
        self.book_explorer_count_label.setText("Filtres modifiés — chargement de la première page…")
        self._book_page_timer.start()

    def _request_book_page(self, page):
        api = self._book_page_api
        if api is None:
            return
        self._book_page_timer.stop()
        library_id = self._book_page_library
        if library_id != self._library_id("book_explorer"):
            return
        options = dict(self._book_page_options)
        days = options.pop("added_days", 0)
        options["added_since"] = self._book_filter_time - timedelta(days=days) if days else None
        generation = self._next_series_load_generation("book_explorer")
        self._clear_book_page()
        self.book_explorer_count_label.setText(f"Chargement de la page {page + 1}…")
        completed = False

        def current():
            return (self._is_current_series_load_generation("book_explorer", generation)
                    and self._book_page_api is api and self._library_id("book_explorer") == library_id)

        def done(result):
            nonlocal completed
            if not current():
                return
            completed = True
            self._render_book_page(result)

        def finished():
            if current() and not completed:
                self.book_explorer_count_label.setText("Chargement échoué — utilisez « Charger les tomes » pour réessayer.")

        self.run_worker("Chargement explorateur de tomes — page", lambda: inventory_page(api, library_id, page=page, **options), done, finished)

    def _render_book_page(self, result):
        self._book_page_result = result
        self.book_explorer_rows = result["rows"]
        self.book_explorer_visible_rows = result["rows"]
        self._book_page_rendering = True
        try:
            self._set_table(self.book_explorer_table, self._book_explorer_headers(),
                            [self._book_explorer_table_row(row) for row in result["rows"]],
                            stretch_from=2, selection_mode=QAbstractItemView.ExtendedSelection,
                            row_data=result["rows"])
            selection = self.book_explorer_table.selectionModel()
            for index, row in enumerate(result["rows"]):
                if row["book_id"] in self._book_paged_selection:
                    self._book_paged_selection[row["book_id"]] = row
                    selection.select(self.book_explorer_table.model().index(index, 0),
                                     QItemSelectionModel.Select | QItemSelectionModel.Rows)
        finally:
            self._book_page_rendering = False
        self.book_page_label.setText(f"Page {result['page'] + 1} / {result['total_pages']}")
        self.book_page_previous.setEnabled(not result["first"])
        self.book_page_next.setEnabled(not result["last"])
        mode = "pagination Komga" if result["load_mode"] == "komga_page" else "filtres sur l’inventaire complet"
        self.book_explorer_count_label.setText(
            f"{result['total']} au total — {result['filtered_total']} correspondent — "
            f"{len(result['rows'])} sur cette page — {result['hidden']} masqué(s) — {mode}")
        self.on_book_explorer_selected()
        self.log(f"✅ Explorateur de tomes : page {result['page'] + 1}/{result['total_pages']} — {len(result['rows'])} ligne(s), {mode}.")

    def _sync_book_page_selection(self):
        if self._book_page_rendering:
            return
        selected_indexes = set(self._selected_row_indexes(self.book_explorer_table))
        for index, row in enumerate(self.book_explorer_visible_rows):
            if index in selected_indexes:
                self._book_paged_selection[row["book_id"]] = row
            else:
                self._book_paged_selection.pop(row["book_id"], None)

