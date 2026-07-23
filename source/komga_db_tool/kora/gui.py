from __future__ import annotations

import logging
import sys
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QEvent, QObject, QThreadPool, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .api import KomgaApi
from .backup import BackupManager
from .cache import CacheStore
from .config import AppConfig
from .constants import APP_NAME, APP_VERSION, KORA_GENRES, MAX_KORA_GENRES
from .csv_import import read_csv_changes
from .local_exclusions import LocalExclusionsStore
from .models import PendingChange, SeriesRecord
from .operations import apply_pending_changes
from .tag_logic import genre_label, merge_series_tags_for_genres, normalize_slug, readable_genres, validate_genres
from ..qt_tasks import Worker
from ..runtime import SecretRedactor

MIN_TABLE_VISIBLE_ROWS = 5

TAG_TO_GENRE_HINTS: dict[str, str] = {
    "adventure": "aventure",
    "biographie": "documentaire-biographie",
    "biography": "documentaire-biographie",
    "crime": "policier-crime",
    "detective": "policier-crime",
    "fantastic": "fantastique-surnaturel",
    "fantastique": "fantastique-surnaturel",
    "historical": "historique",
    "history": "historique",
    "humour": "comedie",
    "mystery": "mystere",
    "policier": "policier-crime",
    "sci-fi": "science-fiction",
    "scifi": "science-fiction",
    "science-fiction": "science-fiction",
    "superhero": "super-heros",
    "superheroes": "super-heros",
    "super-heros": "super-heros",
    "super-héros": "super-heros",
    "suspense": "thriller-suspense",
}


class MainWindow(QMainWindow):
    def __init__(
        self,
        api_provider: Callable[[], KomgaApi] | None = None,
        connection_check: Callable[[], tuple[bool, str]] | None = None,
        exclusions_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1650, 950)

        self.api_provider = api_provider
        self.connection_check = connection_check
        self.exclusions_changed = exclusions_changed
        self.config = AppConfig.default()
        self.cache = CacheStore(self.config.cache_path)
        self.backup = BackupManager(self.config.backup_dir)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[Worker] = set()
        self.current_rows: list[SeriesRecord] = []
        self.current_record: SeriesRecord | None = None
        self._pending_genres_by_series_id: dict[str, list[str]] | None = None
        self.genre_checks: dict[str, QCheckBox] = {}
        self.local_exclusions = LocalExclusionsStore()
        self._table_viewports: dict[int, QTableWidget] = {}

        self._setup_logging()
        self._build_ui()
        if self.api_provider:
            self._configure_shared_connection_ui()
        else:
            self._config_to_ui()
        self.reload_libraries_from_cache()
        self.refresh_series_table()
        self.refresh_genre_inventory(refresh_all_series=False)
        self.refresh_pending_table()
        self.log(f"Cache : {self.config.cache_path}")
        self.log(f"Exclusions locales : {self.local_exclusions.path}")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _setup_logging(self) -> None:
        log_path = Path(self.config.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )

    def _build_ui(self) -> None:
        self._build_menu()
        root = QWidget()
        root_layout = QVBoxLayout(root)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_main_manager_panel(), "Séries")
        self.tabs.addTab(self._build_tag_suggestions_panel(), "Suggestions tags")
        self.tabs.addTab(self._build_genre_inventory_panel(), "Genres par bibliothèque")
        self.tabs.addTab(self._build_exclusions_panel(), "Exclusions")
        root_layout.addWidget(self.tabs, 1)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        root_layout.addWidget(self.log_text)
        self.setCentralWidget(root)

    def _build_exclusions_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        libraries_box = QGroupBox("Bibliothèques exclues de la synchronisation")
        libraries_layout = QVBoxLayout(libraries_box)
        self.excluded_libraries_edit = QLineEdit()
        self.excluded_libraries_edit.setPlaceholderText("Divers; Magazines")
        btn_save_libraries = QPushButton("Enregistrer les bibliothèques exclues")
        btn_save_libraries.clicked.connect(self.save_excluded_libraries)
        libraries_layout.addWidget(QLabel("Noms exacts séparés par un point-virgule"))
        libraries_layout.addWidget(self.excluded_libraries_edit)
        libraries_layout.addWidget(btn_save_libraries)
        layout.addWidget(libraries_box)

        rules_box = QGroupBox("Règles automatiques sur les titres")
        rules_layout = QVBoxLayout(rules_box)
        rule_row = QHBoxLayout()
        self.exclusion_rule_type = QComboBox()
        self.exclusion_rule_type.addItem("Finit par", "suffix")
        self.exclusion_rule_type.addItem("Exact", "exact")
        self.exclusion_rule_type.addItem("Contient", "contains")
        self.exclusion_rule_type.addItem("Expression régulière", "regex")
        self.exclusion_rule_pattern = QLineEdit()
        self.exclusion_rule_pattern.setPlaceholderText("Exemple : (Univers)")
        btn_add_rule = QPushButton("Ajouter la règle")
        btn_add_rule.clicked.connect(self.add_exclusion_rule)
        rule_row.addWidget(self.exclusion_rule_type)
        rule_row.addWidget(self.exclusion_rule_pattern, 1)
        rule_row.addWidget(btn_add_rule)
        rules_layout.addLayout(rule_row)
        self.exclusion_rules_table = QTableWidget(0, 3)
        self.exclusion_rules_table.setHorizontalHeaderLabels(["Active", "Type", "Motif"])
        self.exclusion_rules_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.exclusion_rules_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.exclusion_rules_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.exclusion_rules_table, [70, 140, 520])
        rules_layout.addWidget(self.exclusion_rules_table)
        rule_actions = QHBoxLayout()
        btn_toggle_rule = QPushButton("Activer / désactiver")
        btn_toggle_rule.clicked.connect(self.toggle_selected_exclusion_rules)
        btn_remove_rule = QPushButton("Supprimer les règles sélectionnées")
        btn_remove_rule.clicked.connect(self.remove_selected_exclusion_rules)
        rule_actions.addWidget(btn_toggle_rule)
        rule_actions.addWidget(btn_remove_rule)
        rule_actions.addStretch(1)
        rules_layout.addLayout(rule_actions)
        layout.addWidget(rules_box, 1)

        manual_box = QGroupBox("Exclusions manuelles récupérées")
        manual_layout = QVBoxLayout(manual_box)
        self.manual_exclusions_table = QTableWidget(0, 5)
        self.manual_exclusions_table.setHorizontalHeaderLabels(
            ["Bibliothèque", "Titre", "Raison", "Date", "ID"]
        )
        self.manual_exclusions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.manual_exclusions_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.manual_exclusions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.manual_exclusions_table, [150, 420, 120, 110, 180])
        manual_layout.addWidget(self.manual_exclusions_table)
        manual_actions = QHBoxLayout()
        btn_remove_manual = QPushButton("Réintégrer les exclusions sélectionnées")
        btn_remove_manual.clicked.connect(self.remove_selected_manual_exclusions)
        btn_refresh = QPushButton("Rafraîchir")
        btn_refresh.clicked.connect(self.refresh_exclusions_panel)
        self.exclusions_summary = QLabel("")
        manual_actions.addWidget(btn_remove_manual)
        manual_actions.addWidget(btn_refresh)
        manual_actions.addWidget(self.exclusions_summary, 1)
        manual_layout.addLayout(manual_actions)
        layout.addWidget(manual_box, 1)

        self.refresh_exclusions_panel()
        return panel

    def _build_tag_suggestions_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        toolbar = QHBoxLayout()
        self.tag_suggestion_library_combo = QComboBox()
        self.tag_suggestion_min_count = QLineEdit()
        self.tag_suggestion_min_count.setText("2")
        self.tag_suggestion_min_count.setMaximumWidth(70)
        self.tag_suggestion_only_missing = QCheckBox("Seulement séries sans genre Kora")
        self.tag_suggestion_only_missing.setChecked(True)
        btn_analyze = QPushButton("Analyser tags")
        btn_queue = QPushButton("Mettre les propositions cochées en attente")
        toolbar.addWidget(QLabel("Bibliothèque"))
        toolbar.addWidget(self.tag_suggestion_library_combo)
        toolbar.addWidget(QLabel("Min séries"))
        toolbar.addWidget(self.tag_suggestion_min_count)
        toolbar.addWidget(self.tag_suggestion_only_missing)
        toolbar.addStretch(1)
        toolbar.addWidget(btn_analyze)
        toolbar.addWidget(btn_queue)
        layout.addLayout(toolbar)

        self.tag_suggestion_table = QTableWidget(0, 7)
        self.tag_suggestion_table.setHorizontalHeaderLabels([
            "Appliquer", "Tag / genre source", "Genre Kora proposé", "Séries", "Déjà avec genre", "Confiance", "Exemples"
        ])
        self.tag_suggestion_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tag_suggestion_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tag_suggestion_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self._configure_series_table(self.tag_suggestion_table, [80, 260, 220, 80, 110, 90, 520])
        layout.addWidget(self.tag_suggestion_table, 1)

        self.tag_suggestion_detail = QTextEdit()
        self.tag_suggestion_detail.setReadOnly(True)
        self.tag_suggestion_detail.setMaximumHeight(170)
        layout.addWidget(self.tag_suggestion_detail)

        btn_analyze.clicked.connect(self.analyze_tag_genre_suggestions)
        btn_queue.clicked.connect(self.queue_checked_tag_suggestions)
        self.tag_suggestion_table.itemSelectionChanged.connect(self.update_tag_suggestion_detail)
        return panel

    def _build_main_manager_panel(self) -> QWidget:
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([360, 840, 450])
        return splitter

    def _build_menu(self) -> None:
        toolbar = self.addToolBar("Actions")
        toolbar.setMovable(False)
        act_sync_all = QAction("Synchroniser tout", self)
        act_sync_all.triggered.connect(self.sync_all_libraries)
        toolbar.addAction(act_sync_all)
        act_apply_dry = QAction("Prévisualiser les changements", self)
        act_apply_dry.triggered.connect(lambda: self.apply_pending(dry_run=True))
        toolbar.addAction(act_apply_dry)
        act_apply = QAction("Appliquer les changements", self)
        act_apply.triggered.connect(lambda: self.apply_pending(dry_run=False))
        toolbar.addAction(act_apply)
        act_import = QAction("Importer CSV", self)
        act_import.triggered.connect(self.import_csv_to_pending)
        toolbar.addAction(act_import)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self.connection_box = QGroupBox("Connexion Komga")
        form = QFormLayout(self.connection_box)
        self.url_edit = QLineEdit()
        self.auth_mode = QComboBox()
        self.auth_mode.addItems(["api_key", "basic", "none"])
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.username_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.timeout_edit = QLineEdit()
        form.addRow("URL", self.url_edit)
        form.addRow("Auth", self.auth_mode)
        form.addRow("API key", self.api_key_edit)
        form.addRow("User", self.username_edit)
        form.addRow("Password", self.password_edit)
        form.addRow("Timeout", self.timeout_edit)
        conn_buttons = QHBoxLayout()
        self.btn_save_config = QPushButton("Appliquer pour la session")
        self.btn_save_config.clicked.connect(self.save_config_from_ui)
        btn_test = QPushButton("Tester")
        btn_test.clicked.connect(self.test_connection)
        conn_buttons.addWidget(self.btn_save_config)
        conn_buttons.addWidget(btn_test)
        form.addRow(conn_buttons)
        layout.addWidget(self.connection_box)

        sync_box = QGroupBox("Synchronisation")
        sync_layout = QVBoxLayout(sync_box)
        self.sync_library_combo = QComboBox()
        btn_sync_selected = QPushButton("Synchroniser bibliothèque")
        btn_sync_selected.clicked.connect(self.sync_selected_library)
        btn_sync_all = QPushButton("Synchroniser toutes hors exclusions")
        btn_sync_all.clicked.connect(self.sync_all_libraries)
        sync_layout.addWidget(self.sync_library_combo)
        sync_layout.addWidget(btn_sync_selected)
        sync_layout.addWidget(btn_sync_all)
        sync_layout.addWidget(QLabel("Exclusions fixes V1 : Divers, Magazines"))
        sync_layout.addWidget(QLabel("Exclusions locales temporaires : .kora_local_exclusions.json"))
        layout.addWidget(sync_box)

        filter_box = QGroupBox("Filtres")
        filter_layout = QFormLayout(filter_box)
        self.filter_library_combo = QComboBox()
        self.filter_library_combo.currentIndexChanged.connect(lambda _index=0: self.refresh_series_table())
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self.on_filter_search_changed)
        self.filter_genre_combo = QComboBox()
        self.filter_genre_combo.addItem("Tous", "")
        for slug in KORA_GENRES:
            self.filter_genre_combo.addItem(genre_label(slug), slug)
        self.filter_genre_combo.currentIndexChanged.connect(lambda _index=0: self.refresh_series_table())
        self.no_genre_check = QCheckBox("Sans genre Kora")
        self.no_genre_check.stateChanged.connect(self.refresh_series_table)
        self.has_kora_check = QCheckBox("Avec tags kora:*")
        self.has_kora_check.stateChanged.connect(self.refresh_series_table)
        self.no_kora_check = QCheckBox("Sans tags kora:*")
        self.no_kora_check.stateChanged.connect(self.refresh_series_table)
        self.multi_genre_check = QCheckBox("Plusieurs genres")
        self.multi_genre_check.stateChanged.connect(self.refresh_series_table)
        self.show_local_exclusions_check = QCheckBox("Afficher exclusions locales")
        self.show_local_exclusions_check.stateChanged.connect(self.refresh_series_table)
        filter_layout.addRow("Bibliothèque", self.filter_library_combo)
        filter_layout.addRow("Recherche", self.search_edit)
        filter_layout.addRow("Genre Kora", self.filter_genre_combo)
        filter_layout.addRow(self.no_genre_check)
        filter_layout.addRow(self.has_kora_check)
        filter_layout.addRow(self.no_kora_check)
        filter_layout.addRow(self.multi_genre_check)
        filter_layout.addRow(self.show_local_exclusions_check)
        layout.addWidget(filter_box)

        bulk_box = QGroupBox("Édition en masse")
        bulk_layout = QVBoxLayout(bulk_box)
        self.bulk_genre_combo = QComboBox()
        for slug in KORA_GENRES:
            self.bulk_genre_combo.addItem(genre_label(slug), slug)
        row1 = QHBoxLayout()
        btn_bulk_add = QPushButton("Ajouter")
        btn_bulk_add.clicked.connect(lambda: self.bulk_edit("add"))
        btn_bulk_remove = QPushButton("Retirer")
        btn_bulk_remove.clicked.connect(lambda: self.bulk_edit("remove"))
        btn_bulk_replace = QPushButton("Remplacer par")
        btn_bulk_replace.clicked.connect(lambda: self.bulk_edit("replace"))
        row1.addWidget(btn_bulk_add)
        row1.addWidget(btn_bulk_remove)
        row1.addWidget(btn_bulk_replace)
        bulk_layout.addWidget(self.bulk_genre_combo)
        bulk_layout.addLayout(row1)
        row2 = QHBoxLayout()
        btn_local_exclude = QPushButton("Ignorer partout")
        btn_local_exclude.clicked.connect(self.local_exclude_selected)
        btn_local_reinclude = QPushButton("Réintégrer partout")
        btn_local_reinclude.clicked.connect(self.local_reinclude_selected)
        row2.addWidget(btn_local_exclude)
        row2.addWidget(btn_local_reinclude)
        bulk_layout.addLayout(row2)
        layout.addWidget(bulk_box)

        layout.addStretch(1)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        search_row = QHBoxLayout()
        self.series_table_search_edit = QLineEdit()
        self.series_table_search_edit.setPlaceholderText("Rechercher une série…")
        self.series_table_search_edit.textChanged.connect(self.on_series_top_search_changed)
        search_row.addWidget(QLabel("Recherche"))
        search_row.addWidget(self.series_table_search_edit, 1)
        layout.addLayout(search_row)

        self.series_table = QTableWidget(0, 8)
        self.series_table.setHorizontalHeaderLabels([
            "Bibliothèque", "Titre", "Genres Kora", "Tags Kora", "Genres Komga", "Tags Komga", "Livres", "tagsLock"
        ])
        self.series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.series_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.series_table, widths=[150, 360, 300, 220, 240, 280, 70, 80])
        self.series_table.itemSelectionChanged.connect(self.on_series_selection_changed)
        self.series_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.series_table.customContextMenuRequested.connect(self.show_series_context_menu)
        layout.addWidget(QLabel("Séries"))
        layout.addWidget(self.series_table, 1)

        self.pending_table = QTableWidget(0, 5)
        self.pending_table.setHorizontalHeaderLabels(["Bibliothèque", "Titre", "Genres prévus", "Source", "Note"])
        self.pending_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.pending_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.pending_table, widths=[150, 360, 300, 150, 260])
        pending_buttons = QHBoxLayout()
        btn_apply_dry = QPushButton("Dry-run")
        btn_apply_dry.clicked.connect(lambda: self.apply_pending(dry_run=True))
        btn_apply = QPushButton("Appliquer")
        btn_apply.clicked.connect(lambda: self.apply_pending(dry_run=False))
        btn_clear = QPushButton("Vider pending")
        btn_clear.clicked.connect(self.clear_pending)
        pending_buttons.addWidget(btn_apply_dry)
        pending_buttons.addWidget(btn_apply)
        pending_buttons.addWidget(btn_clear)
        layout.addWidget(QLabel("Modifications en attente"))
        layout.addWidget(self.pending_table, 0)
        layout.addLayout(pending_buttons)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.detail_title = QLabel("Aucune série sélectionnée")
        self.detail_title.setWordWrap(True)
        self.detail_meta = QLabel("")
        self.detail_meta.setWordWrap(True)
        layout.addWidget(self.detail_title)
        layout.addWidget(self.detail_meta)
        current_genres_box = QGroupBox("Genres Kora actuellement affectés")
        current_genres_layout = QVBoxLayout(current_genres_box)
        self.current_genres_label = QLabel("Aucun")
        self.current_genres_label.setWordWrap(True)
        current_genres_layout.addWidget(self.current_genres_label)
        layout.addWidget(current_genres_box)

        genre_box = QGroupBox(f"Genres Kora max {MAX_KORA_GENRES}")
        genre_layout = QVBoxLayout(genre_box)
        for slug in KORA_GENRES:
            cb = QCheckBox(genre_label(slug))
            cb.stateChanged.connect(self.on_detail_genre_changed)
            self.genre_checks[slug] = cb
            genre_layout.addWidget(cb)
        btn_row = QHBoxLayout()
        btn_queue = QPushButton("Mettre en attente sélection")
        btn_queue.clicked.connect(self.queue_selected_detail_change)
        btn_direct = QPushButton("Sauver sélection")
        btn_direct.clicked.connect(self.save_selected_series_direct)
        btn_row.addWidget(btn_queue)
        btn_row.addWidget(btn_direct)
        genre_layout.addLayout(btn_row)
        layout.addWidget(genre_box)

        readonly_box = QGroupBox("Contexte lecture seule")
        readonly_layout = QVBoxLayout(readonly_box)
        self.readonly_text = QTextEdit()
        self.readonly_text.setReadOnly(True)
        readonly_layout.addWidget(self.readonly_text)
        layout.addWidget(readonly_box, 1)
        return panel


    def _build_genre_inventory_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        top = QHBoxLayout()
        self.inventory_library_combo = QComboBox()
        self.inventory_library_combo.currentIndexChanged.connect(lambda _index=0: self.on_inventory_library_changed())
        self.inventory_library_combo.setMinimumWidth(320)
        btn_refresh = QPushButton("Rafraîchir l’inventaire")
        btn_refresh.clicked.connect(self.refresh_genre_inventory)
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.inventory_library_combo, 1)
        top.addStretch(1)
        top.addWidget(btn_refresh)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Genres Kora de la bibliothèque"))
        self.inventory_genre_table = QTableWidget(0, 3)
        self.inventory_genre_table.setHorizontalHeaderLabels(["Genre", "Séries", "Slug"])
        self.inventory_genre_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.inventory_genre_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.inventory_genre_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.inventory_genre_table, widths=[220, 70, 120])
        self.inventory_genre_table.setColumnHidden(2, True)
        self.inventory_genre_table.itemSelectionChanged.connect(self.refresh_genre_inventory_members)
        left_layout.addWidget(self.inventory_genre_table, 1)
        splitter.addWidget(left)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.addWidget(QLabel("Séries actuellement dans le genre sélectionné"))
        self.inventory_member_search_edit = QLineEdit()
        self.inventory_member_search_edit.setPlaceholderText("Rechercher dans ce genre…")
        self.inventory_member_search_edit.textChanged.connect(lambda _text="": self.refresh_genre_inventory_members())
        middle_layout.addWidget(self.inventory_member_search_edit)
        self.inventory_member_table = QTableWidget(0, 5)
        self.inventory_member_table.setHorizontalHeaderLabels(["Titre", "Genres Kora", "Tags Kora", "Livres", "tagsLock"])
        self.inventory_member_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.inventory_member_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.inventory_member_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.inventory_member_table, widths=[360, 320, 220, 70, 80])
        middle_layout.addWidget(self.inventory_member_table, 1)
        btn_remove = QPushButton("Retirer le genre sélectionné des séries sélectionnées")
        btn_remove.clicked.connect(self.inventory_remove_selected_from_genre)
        middle_layout.addWidget(btn_remove)
        splitter.addWidget(middle)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Toutes les séries de la bibliothèque"))
        self.inventory_search_edit = QLineEdit()
        self.inventory_search_edit.setPlaceholderText("Rechercher dans la bibliothèque…")
        self.inventory_search_edit.textChanged.connect(lambda _text="": self.refresh_genre_inventory_all_series())
        right_layout.addWidget(self.inventory_search_edit)
        self.inventory_all_series_table = QTableWidget(0, 5)
        self.inventory_all_series_table.setHorizontalHeaderLabels(["Titre", "Genres Kora", "Tags Kora", "Livres", "tagsLock"])
        self.inventory_all_series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.inventory_all_series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.inventory_all_series_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._configure_series_table(self.inventory_all_series_table, widths=[360, 320, 220, 70, 80])
        right_layout.addWidget(self.inventory_all_series_table, 1)
        btn_add = QPushButton("Ajouter les séries sélectionnées au genre sélectionné")
        btn_add.clicked.connect(self.inventory_add_selected_to_genre)
        right_layout.addWidget(btn_add)
        splitter.addWidget(right)

        splitter.setSizes([260, 700, 700])
        layout.addWidget(splitter, 1)

        help_text = QLabel(
            "Inventaire inverse : choisis une bibliothèque, sélectionne un genre à gauche, "
            "puis ajoute ou retire des séries. Les actions vont dans la même file ‘Modifications en attente’."
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        return panel

    def _configure_series_table(self, table: QTableWidget, widths: list[int]) -> None:
        table.setWordWrap(False)
        table.setAlternatingRowColors(True)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        table.verticalHeader().setVisible(False)
        header_height = table.horizontalHeader().height() if table.horizontalHeader() else 24
        row_height = max(table.verticalHeader().defaultSectionSize(), 28)
        table.setMinimumHeight(header_height + (row_height * MIN_TABLE_VISIBLE_ROWS) + table.frameWidth() * 2 + 10)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for col, width in enumerate(widths):
            table.setColumnWidth(col, width)
        table.setProperty("koraWidthsInitialized", True)
        viewport = table.viewport()
        viewport_key = id(viewport)
        if viewport_key not in self._table_viewports:
            self._table_viewports[viewport_key] = table
            viewport.installEventFilter(self)
            viewport.destroyed.connect(
                lambda *_args, key=viewport_key: self._table_viewports.pop(key, None)
            )
        self._schedule_table_fill(table)

    def _restore_table_widths(self, table: QTableWidget, widths: list[int]) -> None:
        if not table.property("koraWidthsInitialized"):
            for col, width in enumerate(widths):
                if col < table.columnCount():
                    table.setColumnWidth(col, width)
            table.setProperty("koraWidthsInitialized", True)
        self._schedule_table_fill(table)

    def _schedule_table_fill(self, table: QTableWidget) -> None:
        QTimer.singleShot(0, lambda t=table: self._fill_table_to_viewport(t))

    def _fill_table_to_viewport(self, table: QTableWidget) -> None:
        if table.columnCount() <= 0 or table.viewport().width() <= 0:
            return
        visible = [column for column in range(table.columnCount()) if not table.isColumnHidden(column)]
        if not visible:
            return
        headers = [
            table.horizontalHeaderItem(column).text() if table.horizontalHeaderItem(column) else ""
            for column in range(table.columnCount())
        ]
        preferred = [
            column
            for column in visible
            if headers[column] in {"Titre", "Genres Kora", "Tags Kora", "Genres Komga", "Tags Komga", "Motif"}
        ]
        flexible = preferred or [visible[min(1, len(visible) - 1)]]
        extra = max(
            0,
            table.viewport().width() - 4 - sum(table.columnWidth(column) for column in visible),
        )
        if extra <= 0:
            return
        share, remainder = divmod(extra, len(flexible))
        for position, column in enumerate(flexible):
            table.setColumnWidth(
                column,
                table.columnWidth(column) + share + (1 if position < remainder else 0),
            )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Resize:
            table = self._table_viewports.get(id(watched))
            if table is not None:
                self._schedule_table_fill(table)
        return super().eventFilter(watched, event)

    def _set_table_item(self, table: QTableWidget, row: int, col: int, value: str, series_id: str | None = None) -> None:
        item = QTableWidgetItem(value)
        item.setToolTip(value)
        if series_id:
            item.setData(Qt.UserRole, series_id)
        table.setItem(row, col, item)

    def on_filter_search_changed(self, text: str) -> None:
        if hasattr(self, "series_table_search_edit") and self.series_table_search_edit.text() != text:
            self.series_table_search_edit.blockSignals(True)
            self.series_table_search_edit.setText(text)
            self.series_table_search_edit.blockSignals(False)
        self.refresh_series_table()

    def on_series_top_search_changed(self, text: str) -> None:
        if hasattr(self, "search_edit") and self.search_edit.text() != text:
            self.search_edit.blockSignals(True)
            self.search_edit.setText(text)
            self.search_edit.blockSignals(False)
        self.refresh_series_table()

    def on_inventory_library_changed(self) -> None:
        self.refresh_genre_inventory()

    # ------------------------------------------------------------------
    # Config/API/cache
    # ------------------------------------------------------------------
    def _config_to_ui(self) -> None:
        cfg = self.config.komga
        self.url_edit.setText(cfg.url)
        self.auth_mode.setCurrentText(cfg.auth_mode)
        self.api_key_edit.setText(cfg.api_key)
        self.username_edit.setText(cfg.username)
        self.password_edit.setText(cfg.password)
        self.timeout_edit.setText(str(cfg.timeout_seconds))

    def _configure_shared_connection_ui(self) -> None:
        self.connection_box.setTitle("Connexion Komga partagée")
        self.url_edit.setText("Connexion fournie par Komga DB Tool")
        self.auth_mode.setCurrentText("none")
        self.api_key_edit.clear()
        self.username_edit.clear()
        self.password_edit.clear()
        self.timeout_edit.setText("30")
        for widget in (
            self.url_edit,
            self.auth_mode,
            self.api_key_edit,
            self.username_edit,
            self.password_edit,
            self.timeout_edit,
            self.btn_save_config,
        ):
            widget.setEnabled(False)

    def save_config_from_ui(self) -> None:
        if self.api_provider:
            self.log("La connexion est gérée par la fenêtre principale")
            return
        timeout = 30
        try:
            timeout = int(self.timeout_edit.text().strip() or "30")
        except ValueError:
            QMessageBox.warning(self, APP_NAME, "Timeout invalide")
            return
        komga = replace(
            self.config.komga,
            url=self.url_edit.text().strip(),
            auth_mode=self.auth_mode.currentText(),
            api_key=self.api_key_edit.text().strip(),
            username=self.username_edit.text().strip(),
            password=self.password_edit.text(),
            timeout_seconds=timeout,
        )
        self.config = replace(self.config, komga=komga)
        self.log("Paramètres appliqués pour la session")

    def komga_api(self) -> KomgaApi:
        if self.api_provider:
            return self.api_provider()
        self.save_config_from_ui()
        return KomgaApi(self.config.komga.url, auth=self.config.komga.auth(), timeout=self.config.komga.timeout_seconds)

    def connection_problem(self) -> str:
        if not self.connection_check:
            return ""
        ready, message = self.connection_check()
        return "" if ready else str(message or "Connexion Komga non disponible")

    def require_connection(self, action: str) -> bool:
        problem = self.connection_problem()
        if not problem:
            return True
        self.log(f"⚠️ {action} différée : {problem}")
        QMessageBox.information(self, APP_NAME, problem)
        return False

    def run_worker(self, label: str, fn: Callable[[], Any], done: Callable[[Any], None] | None = None) -> None:
        self.log(f"▶ {label}")
        worker = Worker(fn)
        self.active_workers.add(worker)
        if done:
            worker.signals.result.connect(done)
        worker.signals.error.connect(lambda text: self.log_error(label, text))
        worker.signals.finished.connect(lambda: self.active_workers.discard(worker))
        self.thread_pool.start(worker)

    def test_connection(self) -> None:
        if not self.require_connection("Test connexion"):
            return
        self.run_worker("Test connexion", lambda: self.komga_api().test(), lambda result: self.log(str(result)))

    def reload_libraries_from_cache(self) -> None:
        libs = self.cache.libraries(include_excluded=False)
        combos = [self.sync_library_combo, self.filter_library_combo]
        if hasattr(self, "tag_suggestion_library_combo"):
            combos.append(self.tag_suggestion_library_combo)
        if hasattr(self, "inventory_library_combo"):
            combos.append(self.inventory_library_combo)
        for combo in combos:
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Toutes", "")
            for lib in libs:
                combo.addItem(lib.name, lib.id)
            if current:
                index = combo.findData(current)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)

    def sync_all_libraries(self) -> None:
        if not self.require_connection("Synchronisation complète"):
            return
        def work() -> dict[str, int]:
            api = self.komga_api()
            libs = api.libraries()
            excluded_names = self.local_exclusions.excluded_library_names()
            self.cache.upsert_libraries(libs, excluded_names)
            included = [l for l in libs if l.name.strip().lower() not in {x.lower() for x in excluded_names}]
            name_by_id = {l.id: l.name for l in libs}
            count = 0
            for lib in included:
                rows = api.series(library_id=lib.id, page_size=self.config.page_size)
                self.cache.upsert_series(rows, name_by_id)
                count += len(rows)
            return {"libraries": len(included), "series": count}
        self.run_worker("Synchronisation complète", work, self.after_sync)

    def sync_selected_library(self) -> None:
        if not self.require_connection("Synchronisation bibliothèque"):
            return
        library_id = self.sync_library_combo.currentData()
        if not library_id:
            self.sync_all_libraries()
            return
        def work() -> dict[str, int]:
            api = self.komga_api()
            libs = api.libraries()
            self.cache.upsert_libraries(libs, self.local_exclusions.excluded_library_names())
            lib = next((x for x in libs if x.id == library_id), None)
            if not lib:
                raise RuntimeError("Bibliothèque introuvable côté Komga")
            rows = api.series(library_id=library_id, page_size=self.config.page_size)
            self.cache.upsert_series(rows, {lib.id: lib.name})
            return {"libraries": 1, "series": len(rows)}
        self.run_worker("Synchronisation bibliothèque", work, self.after_sync)

    def after_sync(self, result: Any) -> None:
        self.log(f"Sync terminée : {result}")
        self.reload_libraries_from_cache()
        self.refresh_series_table()
        self.refresh_genre_inventory()
        self.refresh_exclusions_panel()

    def save_excluded_libraries(self) -> None:
        names = [
            value.strip()
            for value in self.excluded_libraries_edit.text().replace(",", ";").split(";")
            if value.strip()
        ]
        self.local_exclusions.set_excluded_library_names(names)
        self.refresh_exclusions_panel()
        self._notify_exclusions_changed()
        self.log(f"Bibliothèques exclues enregistrées : {', '.join(names) or 'aucune'}")

    def add_exclusion_rule(self) -> None:
        pattern = self.exclusion_rule_pattern.text().strip()
        try:
            added = self.local_exclusions.add_title_rule(
                pattern,
                str(self.exclusion_rule_type.currentData() or "contains"),
            )
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, f"Expression régulière invalide : {exc}")
            return
        if not added:
            QMessageBox.information(self, APP_NAME, "Règle vide, invalide ou déjà présente.")
            return
        self.exclusion_rule_pattern.clear()
        self.refresh_exclusions_panel()
        self.refresh_series_table()
        self._notify_exclusions_changed()

    def _selected_rule_indexes(self) -> list[int]:
        return sorted({item.row() for item in self.exclusion_rules_table.selectedItems()})

    def toggle_selected_exclusion_rules(self) -> None:
        rules = self.local_exclusions.title_rules()
        for index in self._selected_rule_indexes():
            if 0 <= index < len(rules):
                self.local_exclusions.set_title_rule_enabled(
                    index,
                    not bool(rules[index].get("enabled", True)),
                )
        self.refresh_exclusions_panel()
        self.refresh_series_table()
        self._notify_exclusions_changed()

    def remove_selected_exclusion_rules(self) -> None:
        self.local_exclusions.remove_title_rules(self._selected_rule_indexes())
        self.refresh_exclusions_panel()
        self.refresh_series_table()
        self._notify_exclusions_changed()

    def remove_selected_manual_exclusions(self) -> None:
        ids = {
            str(item.data(Qt.UserRole) or "")
            for item in self.manual_exclusions_table.selectedItems()
            if item.data(Qt.UserRole)
        }
        self.local_exclusions.remove_many(ids)
        self.refresh_exclusions_panel()
        self.refresh_series_table()
        self.refresh_genre_inventory()
        self._notify_exclusions_changed()

    def refresh_exclusions_panel(self) -> None:
        if not hasattr(self, "exclusion_rules_table"):
            return
        libraries = self.local_exclusions.excluded_library_names()
        self.excluded_libraries_edit.setText("; ".join(libraries))

        labels = {
            "exact": "Exact",
            "contains": "Contient",
            "suffix": "Finit par",
            "regex": "Expression régulière",
        }
        rules = self.local_exclusions.title_rules()
        self.exclusion_rules_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            values = [
                "oui" if bool(rule.get("enabled", True)) else "non",
                labels.get(str(rule.get("match_type")), str(rule.get("match_type") or "")),
                str(rule.get("pattern") or ""),
            ]
            for column, value in enumerate(values):
                self._set_table_item(self.exclusion_rules_table, row, column, value)

        entries = self.local_exclusions.entries()
        ordered = sorted(
            entries.items(),
            key=lambda item: (
                str(item[1].get("library") or "").casefold(),
                str(item[1].get("title") or "").casefold(),
            ),
        )
        self.manual_exclusions_table.setRowCount(len(ordered))
        for row, (series_id, entry) in enumerate(ordered):
            values = [
                entry.get("library", ""),
                entry.get("title", ""),
                entry.get("reason", ""),
                entry.get("created_at", ""),
                series_id,
            ]
            for column, value in enumerate(values):
                self._set_table_item(
                    self.manual_exclusions_table,
                    row,
                    column,
                    str(value),
                    series_id=series_id,
                )

        cached = self.cache.query_series(include_excluded=True)
        rule_count = sum(
            1
            for record in cached
            if not self.local_exclusions.is_excluded(record.id)
            and self.local_exclusions.matching_rule(record.title)
        )
        self.exclusions_summary.setText(
            f"{len(entries)} exclusion(s) manuelle(s), {rule_count} série(s) du cache touchée(s) par les règles"
        )

    # ------------------------------------------------------------------
    # Table/detail
    # ------------------------------------------------------------------
    def refresh_series_table(self) -> None:
        if not hasattr(self, "series_table"):
            return
        selected_genre = self.filter_genre_combo.currentData() or "" if hasattr(self, "filter_genre_combo") else ""
        no_genre = self.no_genre_check.isChecked() if hasattr(self, "no_genre_check") else False
        has_kora_tags = self.has_kora_check.isChecked() if hasattr(self, "has_kora_check") else False
        no_kora_tags = self.no_kora_check.isChecked() if hasattr(self, "no_kora_check") else False
        multiple_genres = self.multi_genre_check.isChecked() if hasattr(self, "multi_genre_check") else False
        rows = self.cache.query_series(
            library_id=self.filter_library_combo.currentData() or "",
            search=self.search_edit.text().strip() if hasattr(self, "search_edit") else "",
        )
        rows = [
            rec for rec in rows
            if self._record_matches_effective_kora_filters(
                rec,
                selected_genre=selected_genre,
                no_genre=no_genre,
                has_kora_tags=has_kora_tags,
                no_kora_tags=no_kora_tags,
                multiple_genres=multiple_genres,
            )
        ]
        include_local_exclusions = (
            self.show_local_exclusions_check.isChecked()
            if hasattr(self, "show_local_exclusions_check")
            else False
        )
        total_before_local_filter = len(rows)
        self.current_rows = self.local_exclusions.filter_records(rows, include_excluded=include_local_exclusions)
        hidden_local_count = total_before_local_filter - len(self.current_rows)
        self.series_table.setUpdatesEnabled(False)
        self.series_table.blockSignals(True)
        try:
            self.series_table.setRowCount(len(self.current_rows))
            for row_idx, rec in enumerate(self.current_rows):
                effective_genres = self.effective_kora_genres(rec)
                values = [
                    rec.library_name,
                    rec.title,
                    readable_genres(effective_genres),
                    " | ".join(rec.kora_tags),
                    " | ".join(rec.genres),
                    " | ".join(rec.tags),
                    str(rec.book_count),
                    "oui" if rec.tags_lock else "non",
                ]
                for col, value in enumerate(values):
                    self._set_table_item(self.series_table, row_idx, col, value, series_id=rec.id)
        finally:
            self.series_table.blockSignals(False)
            self.series_table.setUpdatesEnabled(True)
        self._restore_table_widths(self.series_table, [150, 360, 300, 220, 240, 280, 70, 80])
        status = f"{len(self.current_rows)} série(s) affichée(s)"
        if hidden_local_count:
            status += f" — {hidden_local_count} exclusion(s) locale(s) masquée(s)"
        self.statusBar().showMessage(status)

    def _record_matches_effective_kora_filters(
        self,
        rec: SeriesRecord,
        *,
        selected_genre: str = "",
        no_genre: bool = False,
        has_kora_tags: bool = False,
        no_kora_tags: bool = False,
        multiple_genres: bool = False,
    ) -> bool:
        effective_genres = self.effective_kora_genres(rec)
        has_any_kora_tag = bool(effective_genres or rec.kora_tags)
        if selected_genre and selected_genre not in effective_genres:
            return False
        if no_genre and effective_genres:
            return False
        if has_kora_tags and not has_any_kora_tag:
            return False
        if no_kora_tags and has_any_kora_tag:
            return False
        if multiple_genres and len(effective_genres) <= 1:
            return False
        return True

    @staticmethod
    def _kora_tag_source_values(rec: SeriesRecord) -> list[str]:
        values: list[str] = []
        for source in (rec.tags, rec.genres):
            for value in source:
                text = str(value or "").strip()
                lower = text.casefold()
                if not text or lower.startswith(("kora:genre:", "kora:tag:", "kora:taxonomy:")):
                    continue
                if text not in values:
                    values.append(text)
        return values

    @staticmethod
    def _suggest_kora_genre_for_tag(value: str) -> tuple[str, int]:
        slug = normalize_slug(value)
        if slug in KORA_GENRES:
            return slug, 100
        if slug in TAG_TO_GENRE_HINTS:
            return TAG_TO_GENRE_HINTS[slug], 90
        for token, genre in TAG_TO_GENRE_HINTS.items():
            if token in slug:
                return genre, 75
        for genre in KORA_GENRES:
            if genre in slug:
                return genre, 70
        return "", 0

    def analyze_tag_genre_suggestions(self) -> None:
        if not hasattr(self, "tag_suggestion_table"):
            return
        try:
            min_count = max(1, int(self.tag_suggestion_min_count.text().strip() or "1"))
        except ValueError:
            QMessageBox.warning(self, APP_NAME, "Min séries doit être un nombre.")
            return
        library_id = self.tag_suggestion_library_combo.currentData() or ""
        only_missing = self.tag_suggestion_only_missing.isChecked()
        records = self.cache.query_series(library_id=library_id)
        records = self.local_exclusions.filter_records(records, include_excluded=False)
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        for rec in records:
            effective = self.effective_kora_genres(rec)
            if only_missing and effective:
                continue
            for source_value in self._kora_tag_source_values(rec):
                genre, confidence = self._suggest_kora_genre_for_tag(source_value)
                if not genre:
                    continue
                key = (source_value, genre)
                group = groups.setdefault(key, {
                    "source": source_value,
                    "genre": genre,
                    "confidence": confidence,
                    "series_ids": [],
                    "already_count": 0,
                    "examples": [],
                })
                if rec.id not in group["series_ids"]:
                    group["series_ids"].append(rec.id)
                if genre in effective:
                    group["already_count"] += 1
                if len(group["examples"]) < 12:
                    group["examples"].append(f"{rec.library_name} — {rec.title}")
                group["confidence"] = max(int(group["confidence"]), confidence)
        rows = [
            row for row in groups.values()
            if len(row.get("series_ids") or []) >= min_count
        ]
        rows.sort(key=lambda row: (-int(row.get("confidence", 0)), -len(row.get("series_ids") or []), str(row.get("source")).casefold()))

        self.tag_suggestion_table.setUpdatesEnabled(False)
        try:
            self.tag_suggestion_table.setRowCount(len(rows))
            for row_idx, row in enumerate(rows):
                apply_item = QTableWidgetItem("")
                apply_item.setFlags((apply_item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
                apply_item.setCheckState(Qt.Checked if int(row.get("confidence", 0)) >= 90 else Qt.Unchecked)
                apply_item.setData(Qt.UserRole, row)
                self.tag_suggestion_table.setItem(row_idx, 0, apply_item)
                values = [
                    row.get("source", ""),
                    genre_label(str(row.get("genre", ""))),
                    str(len(row.get("series_ids") or [])),
                    str(row.get("already_count", 0)),
                    str(row.get("confidence", 0)),
                    " | ".join(row.get("examples") or []),
                ]
                for offset, value in enumerate(values, start=1):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    if offset != 2:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.tag_suggestion_table.setItem(row_idx, offset, item)
        finally:
            self.tag_suggestion_table.setUpdatesEnabled(True)
        self._restore_table_widths(self.tag_suggestion_table, [80, 260, 220, 80, 110, 90, 520])
        self.tag_suggestion_detail.setPlainText(
            f"{len(rows)} proposition(s) depuis {len(records)} série(s). "
            "La colonne Genre Kora proposé est éditable avant mise en attente."
        )

    def _selected_tag_suggestion_row(self) -> dict[str, Any]:
        if not hasattr(self, "tag_suggestion_table"):
            return {}
        rows = sorted({item.row() for item in self.tag_suggestion_table.selectedItems()})
        if not rows:
            return {}
        item = self.tag_suggestion_table.item(rows[0], 0)
        data = item.data(Qt.UserRole) if item is not None else None
        return data if isinstance(data, dict) else {}

    def update_tag_suggestion_detail(self) -> None:
        row = self._selected_tag_suggestion_row()
        if not row or not hasattr(self, "tag_suggestion_detail"):
            return
        lines = [
            f"Source : {row.get('source', '')}",
            f"Genre proposé : {genre_label(str(row.get('genre', '')))}",
            f"Séries touchées : {len(row.get('series_ids') or [])}",
            f"Confiance : {row.get('confidence', 0)}",
            "",
            "Exemples :",
            *[f"- {value}" for value in (row.get("examples") or [])],
        ]
        self.tag_suggestion_detail.setPlainText("\n".join(lines))

    def queue_checked_tag_suggestions(self) -> None:
        if not hasattr(self, "tag_suggestion_table"):
            return
        queued = 0
        skipped = 0
        for row_idx in range(self.tag_suggestion_table.rowCount()):
            check_item = self.tag_suggestion_table.item(row_idx, 0)
            if check_item is None or check_item.checkState() != Qt.Checked:
                continue
            row = check_item.data(Qt.UserRole)
            if not isinstance(row, dict):
                continue
            genre_text = self.tag_suggestion_table.item(row_idx, 2).text() if self.tag_suggestion_table.item(row_idx, 2) else ""
            genre = normalize_slug(genre_text)
            if genre not in KORA_GENRES:
                skipped += len(row.get("series_ids") or [])
                continue
            for series_id in row.get("series_ids") or []:
                rec = self.cache.get_series(str(series_id))
                if not rec:
                    skipped += 1
                    continue
                genres = list(self.effective_kora_genres(rec))
                if genre in genres:
                    skipped += 1
                    continue
                if len(genres) >= MAX_KORA_GENRES:
                    skipped += 1
                    continue
                genres.append(genre)
                self.cache.add_pending(PendingChange(
                    rec.id,
                    rec.library_name,
                    rec.title,
                    validate_genres(genres),
                    source="tag-suggestion",
                    note=str(row.get("source") or ""),
                ))
                queued += 1
        self.invalidate_pending_genres_cache()
        self.refresh_pending_table()
        self.refresh_series_table()
        self.log(f"Suggestions tags : {queued} changement(s) mis en attente, {skipped} ignoré(s).")

    def selected_records(self) -> list[SeriesRecord]:
        selected: list[SeriesRecord] = []
        seen: set[str] = set()
        for item in self.series_table.selectedItems():
            series_id = item.data(Qt.UserRole)
            if series_id and series_id not in seen:
                rec = self.cache.get_series(series_id)
                if rec:
                    selected.append(rec)
                    seen.add(series_id)
        return selected

    def on_series_selection_changed(self) -> None:
        records = self.selected_records()
        self.current_record = records[0] if records else None
        self.populate_detail_selection(records)

    def show_series_context_menu(self, position) -> None:
        item = self.series_table.itemAt(position)
        if item is not None and not item.isSelected():
            self.series_table.selectRow(item.row())
        menu = QMenu(self)
        act_exclude = QAction("Ignorer la série partout", self)
        act_exclude.triggered.connect(self.local_exclude_selected)
        menu.addAction(act_exclude)
        act_reinclude = QAction("Réintégrer partout", self)
        act_reinclude.triggered.connect(self.local_reinclude_selected)
        menu.addAction(act_reinclude)
        menu.exec(self.series_table.viewport().mapToGlobal(position))

    def _notify_exclusions_changed(self) -> None:
        if self.exclusions_changed:
            self.exclusions_changed()

    def populate_detail_selection(self, records: list[SeriesRecord]) -> None:
        if not records:
            self.populate_detail(None)
            return
        if len(records) == 1:
            self.populate_detail(records[0])
            return

        common_genres = set(self.effective_kora_genres(records[0]))
        for rec in records[1:]:
            common_genres.intersection_update(self.effective_kora_genres(rec))

        for slug, cb in self.genre_checks.items():
            cb.blockSignals(True)
            cb.setChecked(slug in common_genres)
            cb.blockSignals(False)

        excluded_count = sum(1 for rec in records if self.local_exclusions.exclusion_reason(rec))
        tags_locked_count = sum(1 for rec in records if rec.tags_lock)
        libraries = sorted({rec.library_name for rec in records})
        self.detail_title.setText(f"{len(records)} séries sélectionnées")
        self.detail_meta.setText(
            "Bibliothèques : " + ", ".join(libraries) +
            f" — tagsLock: {tags_locked_count}/{len(records)}" +
            (f" — exclusions locales: {excluded_count}/{len(records)}" if excluded_count else "")
        )
        common_text = readable_genres(sorted(common_genres)) if common_genres else "Aucun genre commun"
        self.current_genres_label.setText(f"Genres communs : {common_text}")
        self.readonly_text.setText(
            "Édition multiple active.\n"
            "Les cases cochées correspondent aux genres communs aux séries sélectionnées.\n"
            "En cliquant sur ‘Mettre en attente sélection’ ou ‘Sauver sélection’, les genres cochés remplaceront les genres Kora de toutes les séries sélectionnées.\n\n"
            "Séries sélectionnées :\n" + "\n".join(f"- {rec.library_name} — {rec.title}" for rec in records)
        )

    def populate_detail(self, rec: SeriesRecord | None) -> None:
        for cb in self.genre_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        if rec is None:
            self.detail_title.setText("Aucune série sélectionnée")
            self.detail_meta.setText("")
            self.current_genres_label.setText("Aucun")
            self.readonly_text.setText("")
            return
        self.detail_title.setText(f"{rec.title}")
        reason = self.local_exclusions.exclusion_reason(rec)
        local_state = f" — exclusion {reason}" if reason else ""
        self.detail_meta.setText(f"{rec.library_name} — {rec.id} — tagsLock: {'oui' if rec.tags_lock else 'non'}{local_state}")
        effective_genres = self.effective_kora_genres(rec)
        self.current_genres_label.setText(readable_genres(effective_genres) or "Aucun")
        for slug in effective_genres:
            if slug in self.genre_checks:
                self.genre_checks[slug].blockSignals(True)
                self.genre_checks[slug].setChecked(True)
                self.genre_checks[slug].blockSignals(False)
        self.readonly_text.setText(
            "Genres Komga:\n" + "\n".join(rec.genres) +
            "\n\nTags Komga non filtrés:\n" + "\n".join(rec.tags) +
            "\n\nTags secondaires Kora lecture seule:\n" + "\n".join(rec.kora_tags)
        )

    def detail_selected_genres(self) -> list[str]:
        return [slug for slug, cb in self.genre_checks.items() if cb.isChecked()]

    def on_detail_genre_changed(self) -> None:
        selected = self.detail_selected_genres()
        if len(selected) > MAX_KORA_GENRES:
            sender = self.sender()
            if isinstance(sender, QCheckBox):
                sender.blockSignals(True)
                sender.setChecked(False)
                sender.blockSignals(False)
            QMessageBox.warning(self, APP_NAME, f"Maximum {MAX_KORA_GENRES} genres Kora par série.")

    def queue_selected_detail_change(self) -> int:
        records = self.selected_records()
        if not records:
            return 0
        try:
            genres = validate_genres(self.detail_selected_genres())
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return 0
        for rec in records:
            self.cache.add_pending(PendingChange(rec.id, rec.library_name, rec.title, genres, source="manual", note="detail-selection"))
        self.invalidate_pending_genres_cache()
        self.refresh_pending_table()
        self.refresh_genre_inventory()
        self.log(f"Ajout pending sélection : {len(records)} série(s) -> {readable_genres(genres)}")
        return len(records)

    def save_selected_series_direct(self) -> None:
        queued = self.queue_selected_detail_change()
        if queued:
            self.apply_pending(dry_run=False, only_selected=True)

    def queue_current_detail_change(self) -> None:
        self.queue_selected_detail_change()

    def save_current_series_direct(self) -> None:
        self.save_selected_series_direct()

    def local_exclude_selected(self) -> None:
        records = self.selected_records()
        if not records:
            QMessageBox.information(self, APP_NAME, "Sélectionne au moins une série.")
            return
        count = self.local_exclusions.add_many(records, reason="manual")
        self.refresh_series_table()
        self.refresh_genre_inventory()
        self.populate_detail(None)
        self._notify_exclusions_changed()
        self.log(f"Exclusions locales ajoutées : {count} série(s). Fichier : {self.local_exclusions.path}")

    def local_reinclude_selected(self) -> None:
        records = self.selected_records()
        if not records:
            QMessageBox.information(self, APP_NAME, "Sélectionne au moins une série. Active d’abord ‘Afficher exclusions locales’ si elle est masquée.")
            return
        count = self.local_exclusions.remove_many(rec.id for rec in records)
        self.refresh_series_table()
        self.refresh_genre_inventory()
        self._notify_exclusions_changed()
        self.log(f"Exclusions locales retirées : {count} série(s).")

    # ------------------------------------------------------------------
    # Pending/bulk/apply
    # ------------------------------------------------------------------
    def bulk_edit(self, mode: str) -> None:
        genre = self.bulk_genre_combo.currentData()
        records = self.selected_records()
        if not records:
            QMessageBox.information(self, APP_NAME, "Sélectionne au moins une série.")
            return
        skipped = 0
        queued = 0
        for rec in records:
            genres = list(rec.kora_genres)
            if mode == "add":
                if genre not in genres:
                    if len(genres) >= MAX_KORA_GENRES:
                        skipped += 1
                        continue
                    genres.append(genre)
            elif mode == "remove":
                genres = [g for g in genres if g != genre]
            elif mode == "replace":
                genres = [genre]
            try:
                genres = validate_genres(genres)
            except ValueError:
                skipped += 1
                continue
            self.cache.add_pending(PendingChange(rec.id, rec.library_name, rec.title, genres, source=f"bulk:{mode}", note=genre))
            queued += 1
        self.invalidate_pending_genres_cache()
        self.refresh_pending_table()
        self.refresh_genre_inventory()
        self.log(f"Batch {mode}: {queued} mis en attente, {skipped} ignoré(s).")
        if skipped:
            QMessageBox.information(self, APP_NAME, f"{skipped} série(s) ignorée(s), souvent parce que la limite de {MAX_KORA_GENRES} genres serait dépassée.")

    def refresh_pending_table(self) -> None:
        rows = self.cache.pending()
        self.pending_table.setRowCount(len(rows))
        for i, change in enumerate(rows):
            vals = [change.library_name, change.title, readable_genres(change.new_kora_genres), change.source, change.note]
            for col, val in enumerate(vals):
                self._set_table_item(self.pending_table, i, col, val, series_id=change.series_id)
        self._restore_table_widths(self.pending_table, [150, 360, 300, 150, 260])

    def clear_pending(self) -> None:
        if QMessageBox.question(self, APP_NAME, "Vider toutes les modifications en attente ?") == QMessageBox.Yes:
            self.cache.clear_pending()
            self.invalidate_pending_genres_cache()
            self.refresh_pending_table()
            self.refresh_genre_inventory()

    def apply_pending(self, dry_run: bool, only_current: bool = False, only_selected: bool = False) -> None:
        if not self.require_connection("Application des genres"):
            return
        changes = self.cache.pending()
        if only_selected:
            selected_ids = {rec.id for rec in self.selected_records()}
            changes = [c for c in changes if c.series_id in selected_ids]
        elif only_current and self.current_record:
            changes = [c for c in changes if c.series_id == self.current_record.id]
        if not changes:
            QMessageBox.information(self, APP_NAME, "Aucune modification en attente.")
            return
        if not dry_run:
            reply = QMessageBox.question(self, APP_NAME, f"Appliquer {len(changes)} modification(s) dans Komga ?")
            if reply != QMessageBox.Yes:
                return
        def work() -> dict[str, Any]:
            return apply_pending_changes(self.komga_api(), self.cache, self.backup, changes, dry_run=dry_run)
        self.run_worker("Dry-run" if dry_run else "Application Komga", work, self.after_apply)

    def after_apply(self, result: Any) -> None:
        self.log(f"Résultat : {result}")
        self.invalidate_pending_genres_cache()
        self.refresh_series_table()
        self.refresh_genre_inventory()
        self.refresh_pending_table()
        QMessageBox.information(self, APP_NAME, f"Terminé. Backup JSON : {result.get('backup_json')}")


    # ------------------------------------------------------------------
    # Inventaire inverse par genre/bibliothèque
    # ------------------------------------------------------------------
    def inventory_selected_genre(self) -> str:
        if not hasattr(self, "inventory_genre_table"):
            return ""
        row = self.inventory_genre_table.currentRow()
        if row < 0:
            return ""
        item = self.inventory_genre_table.item(row, 0)
        return str(item.data(Qt.UserRole) or "") if item else ""

    def invalidate_pending_genres_cache(self) -> None:
        self._pending_genres_by_series_id = None

    def pending_genres_by_series_id(self) -> dict[str, list[str]]:
        if self._pending_genres_by_series_id is None:
            self._pending_genres_by_series_id = self.cache.pending_genres_by_series_id()
        return self._pending_genres_by_series_id

    def effective_kora_genres(self, rec: SeriesRecord) -> list[str]:
        pending_genres = self.pending_genres_by_series_id().get(rec.id)
        return list(pending_genres) if pending_genres is not None else list(rec.kora_genres)

    def refresh_genre_inventory(self, _checked: bool = False, *, refresh_all_series: bool = True) -> None:
        if not hasattr(self, "inventory_genre_table"):
            return
        selected_slug = self.inventory_selected_genre()
        library_id = self.inventory_library_combo.currentData() or ""
        records_for_counts = self.cache.query_series(library_id=library_id)
        counts: dict[str, int] = {}
        for record in records_for_counts:
            for genre in self.effective_kora_genres(record):
                counts[genre] = counts.get(genre, 0) + 1

        self.inventory_genre_table.blockSignals(True)
        self.inventory_genre_table.setRowCount(len(KORA_GENRES))
        selected_row = -1
        for row, slug in enumerate(KORA_GENRES):
            values = [genre_label(slug), str(counts.get(slug, 0)), slug]
            for col, value in enumerate(values):
                self._set_table_item(self.inventory_genre_table, row, col, value, series_id=slug)
            if slug == selected_slug:
                selected_row = row
        self._restore_table_widths(self.inventory_genre_table, [220, 70, 120])
        self.inventory_genre_table.blockSignals(False)
        if selected_row >= 0:
            self.inventory_genre_table.selectRow(selected_row)
        elif self.inventory_genre_table.rowCount() > 0:
            self.inventory_genre_table.selectRow(0)

        self.refresh_genre_inventory_members()
        if refresh_all_series:
            self.refresh_genre_inventory_all_series()
        elif hasattr(self, "inventory_all_series_table"):
            self.inventory_all_series_table.setRowCount(0)

    def refresh_genre_inventory_members(self) -> None:
        if not hasattr(self, "inventory_member_table"):
            return
        slug = self.inventory_selected_genre()
        library_id = self.inventory_library_combo.currentData() or ""
        search = self.inventory_member_search_edit.text().strip() if hasattr(self, "inventory_member_search_edit") else ""
        records = self.cache.query_series(library_id=library_id, search=search, genre=slug) if slug else []
        self.fill_inventory_series_table(self.inventory_member_table, records)

    def refresh_genre_inventory_all_series(self) -> None:
        if not hasattr(self, "inventory_all_series_table"):
            return
        library_id = self.inventory_library_combo.currentData() or ""
        search = self.inventory_search_edit.text().strip() if hasattr(self, "inventory_search_edit") else ""
        records = self.cache.query_series(library_id=library_id, search=search)
        self.fill_inventory_series_table(self.inventory_all_series_table, records)

    def fill_inventory_series_table(self, table: QTableWidget, records: list[SeriesRecord]) -> None:
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.setRowCount(len(records))
            for row, rec in enumerate(records):
                effective_genres = self.effective_kora_genres(rec)
                values = [
                    rec.title,
                    readable_genres(effective_genres),
                    " | ".join(rec.kora_tags),
                    str(rec.book_count),
                    "oui" if rec.tags_lock else "non",
                ]
                for col, value in enumerate(values):
                    self._set_table_item(table, row, col, value, series_id=rec.id)
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(True)
        self._restore_table_widths(table, [360, 320, 220, 70, 80])

    def selected_records_from_table(self, table: QTableWidget) -> list[SeriesRecord]:
        selected: list[SeriesRecord] = []
        seen: set[str] = set()
        for item in table.selectedItems():
            series_id = item.data(Qt.UserRole)
            if series_id and series_id not in seen:
                rec = self.cache.get_series(series_id)
                if rec:
                    selected.append(rec)
                    seen.add(series_id)
        return selected

    def inventory_add_selected_to_genre(self) -> None:
        slug = self.inventory_selected_genre()
        if not slug:
            QMessageBox.information(self, APP_NAME, "Sélectionne un genre à gauche.")
            return
        records = self.selected_records_from_table(self.inventory_all_series_table)
        if not records:
            QMessageBox.information(self, APP_NAME, "Sélectionne au moins une série à droite.")
            return
        queued = 0
        skipped_limit = 0
        skipped_existing = 0
        for rec in records:
            genres = self.effective_kora_genres(rec)
            if slug in genres:
                skipped_existing += 1
                continue
            if len(genres) >= MAX_KORA_GENRES:
                skipped_limit += 1
                continue
            genres.append(slug)
            try:
                genres = validate_genres(genres)
            except ValueError:
                skipped_limit += 1
                continue
            self.cache.add_pending(PendingChange(rec.id, rec.library_name, rec.title, genres, source="inventory:add", note=slug))
            queued += 1
        self.invalidate_pending_genres_cache()
        self.refresh_pending_table()
        self.refresh_genre_inventory()
        self.log(f"Inventaire add {slug}: {queued} pending, {skipped_existing} déjà présents, {skipped_limit} limite {MAX_KORA_GENRES}.")
        if skipped_limit:
            QMessageBox.information(self, APP_NAME, f"{skipped_limit} série(s) ignorée(s) car elles ont déjà {MAX_KORA_GENRES} genres Kora.")

    def inventory_remove_selected_from_genre(self) -> None:
        slug = self.inventory_selected_genre()
        if not slug:
            QMessageBox.information(self, APP_NAME, "Sélectionne un genre à gauche.")
            return
        records = self.selected_records_from_table(self.inventory_member_table)
        if not records:
            QMessageBox.information(self, APP_NAME, "Sélectionne au moins une série au centre.")
            return
        queued = 0
        skipped_missing = 0
        for rec in records:
            genres = self.effective_kora_genres(rec)
            if slug not in genres:
                skipped_missing += 1
                continue
            genres = [g for g in genres if g != slug]
            try:
                genres = validate_genres(genres)
            except ValueError:
                skipped_missing += 1
                continue
            self.cache.add_pending(PendingChange(rec.id, rec.library_name, rec.title, genres, source="inventory:remove", note=slug))
            queued += 1
        self.invalidate_pending_genres_cache()
        self.refresh_pending_table()
        self.refresh_genre_inventory()
        self.log(f"Inventaire remove {slug}: {queued} pending, {skipped_missing} ignoré(s).")

    # ------------------------------------------------------------------
    # CSV import
    # ------------------------------------------------------------------
    def import_csv_to_pending(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importer CSV kora", "", "CSV (*.csv);;Tous les fichiers (*)")
        if not path:
            return
        try:
            changes = read_csv_changes(path)
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Import CSV impossible:\n{exc}")
            return
        missing = 0
        queued = 0
        for change in changes:
            rec = self.cache.get_series(change.series_id)
            if not rec:
                missing += 1
                # Still queue with CSV labels; apply can work if Komga ID is valid, but UI context is weaker.
                self.cache.add_pending(PendingChange(change.series_id, change.library_name, change.title, change.kora_genres, source="csv", note=change.source_file))
            else:
                self.cache.add_pending(PendingChange(rec.id, rec.library_name, rec.title, change.kora_genres, source="csv", note=change.source_file))
            queued += 1
        self.invalidate_pending_genres_cache()
        self.refresh_pending_table()
        self.refresh_genre_inventory()
        self.log(f"CSV importé : {queued} pending, {missing} absents du cache.")
        QMessageBox.information(self, APP_NAME, f"{queued} modification(s) ajoutée(s) à la file. {missing} série(s) absente(s) du cache.")

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        known = [
            self.api_key_edit.text(),
            self.password_edit.text(),
        ] if hasattr(self, "api_key_edit") else []
        safe = SecretRedactor.redact(message, known)
        logging.info(safe)
        if hasattr(self, "log_text"):
            self.log_text.append(safe)

    def log_error(self, label: str, trace: str) -> None:
        known = [
            self.api_key_edit.text(),
            self.password_edit.text(),
        ] if hasattr(self, "api_key_edit") else []
        safe = SecretRedactor.redact(f"{label}\n{trace}", known)
        logging.error("%s", safe)
        if "HTTP 401 " in safe or '"status":401' in safe or '"status": 401' in safe:
            message = (
                "Authentification Komga refusée (HTTP 401). "
                "Retourne dans l'onglet Connexion principal et valide la connexion avant de synchroniser Kora."
            )
            self.log_text.append(f"Erreur : {message}")
            QMessageBox.warning(self, APP_NAME, message)
            return
        self.log_text.append(f"Erreur : {safe}")
        QMessageBox.critical(self, APP_NAME, safe[-3000:])


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
