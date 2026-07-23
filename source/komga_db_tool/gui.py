from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta, timezone
import re
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import asdict
from typing import Any, Callable, Dict, Iterable, List, Optional
from urllib import request as urlrequest
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, Qt, Signal, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .api import AuthConfig, CollectionItem, KomfApi, KomgaApi, ReadlistItem, parse_cell_value, safe_str
from .backup import BackupManager, list_rollback_records, load_rollback_snapshot
from .bedetheque import BedethequeCandidate, BedethequeClient, BedethequeSearchResult, normalize_volume_number, title_similarity
from .bedetheque_csv import BedethequeCsvClient
from .comicvine import DEFAULT_COMICVINE_API_BASE_URL, ComicVineCandidate, ComicVineClient, ComicVineIssueCandidate, ComicVineSearchResult
from .mangabaka import DEFAULT_API_BASE_URL, MangaBakaCandidate, MangaBakaClient, MangaBakaNextReleaseCandidate, MangaBakaSearchResult
from .manga_news import DEFAULT_MANGA_NEWS_API_BASE_URL, MangaNewsCandidate, MangaNewsClient, MangaNewsNextReleaseCandidate, MangaNewsSearchResult, MangaNewsVolumeCandidate, series_slug_from_manga_news_url
from .app_settings import AppConfig, DEFAULT_CONFIG_FILE, MatchingConfig, load_config, save_config
from .kora.local_exclusions import LocalExclusionsStore
from .csv_tools import (
    BOOK_INVENTORY_COLUMNS,
    COMICINFO_DIRECTOR_COLUMNS,
    DIRECTOR_COLUMNS,
    SPECIALIZED_COLUMNS,
    book_inventory_row,
    comicinfo_director_summary,
    looks_like_comicinfo_director,
    parse_director_actions,
    read_csv,
    write_csv,
)
from .external_rate_limit import ExternalSourceBlocked, RateLimitedSourceClient
from .enrichment_history import EnrichmentHistoryStore, format_search_timestamp
from .metadata_quality import (
    SUMMARY_MIN_SIGNIFICANT_CHARS as QUALITY_SUMMARY_MIN_SIGNIFICANT_CHARS,
    bedetheque_main_album_count as quality_bedetheque_main_album_count,
    build_search_queries,
    clean_title_for_compare,
    clean_title_for_search,
    clean_title_for_write,
    is_low_value_summary as quality_is_low_value_summary,
    normalize_isbn_value as quality_normalize_isbn_value,
    metadata_field_update_report as quality_metadata_field_update_report,
    normalize_write_language as quality_normalize_write_language,
    is_supported_write_language as quality_is_supported_write_language,
    path_has_chap_scan_segment as quality_path_has_chap_scan_segment,
    normalize_series_status_for_tracking as quality_normalize_series_status_for_tracking,
    release_tracking_status_decision as quality_release_tracking_status_decision,
    release_tracking_total_decision as quality_release_tracking_total_decision,
    combine_release_tracking_risk as quality_combine_release_tracking_risk,
    normalized_summary_text as quality_normalized_summary_text,
    scalar_metadata_text as quality_scalar_metadata_text,
    should_auto_apply_changed_metadata_field as quality_should_auto_apply_changed_metadata_field,
    significant_summary_length as quality_significant_summary_length,
)
from .runtime import SecretRedactor
from .source_books import SourceBookRow, match_source_books
from .book_explorer import (
    BOOK_SOURCE_LABELS,
    DEFAULT_BOOK_ENRICHMENT_FIELDS,
    book_enrichment_payload,
    book_explorer_row,
    choose_book_source,
    filter_book_rows,
    series_source_links,
    sort_book_rows,
)

APP_TITLE = "Komga DB Tool"
APP_VERSION = "3.12.0rc3"
MIN_TABLE_VISIBLE_ROWS = 5
NEXT_RELEASE_TAG_PREFIX = "nextrelease:"

SERIES_STATUS_VALUES = ("ONGOING", "ENDED", "HIATUS", "ABANDONED")
READING_DIRECTION_VALUES = ("LEFT_TO_RIGHT", "RIGHT_TO_LEFT", "VERTICAL", "WEBTOON")
AUTHOR_ROLE_VALUES = (
    "writer",
    "penciller",
    "inker",
    "colorist",
    "letterer",
    "cover",
    "editor",
    "translator",
)
SERIES_TYPED_FIELDS = (
    "title",
    "titleSort",
    "summary",
    "status",
    "publisher",
    "language",
    "readingDirection",
    "ageRating",
    "totalBookCount",
    "genres",
    "tags",
    "sharingLabels",
    "alternateTitles",
    "links",
)
BOOK_TYPED_FIELDS = (
    "title",
    "summary",
    "number",
    "numberSort",
    "releaseDate",
    "authors",
    "tags",
    "links",
)

SERIES_METADATA_FIELDS = [
    "title", "titleSort", "summary", "status", "publisher", "language", "readingDirection",
    "ageRating", "totalBookCount", "genres", "tags", "sharingLabels", "alternateTitles", "links",
    "titleLock", "titleSortLock", "summaryLock", "statusLock", "publisherLock", "languageLock",
    "readingDirectionLock", "ageRatingLock", "totalBookCountLock", "genresLock", "tagsLock",
    "sharingLabelsLock", "alternateTitlesLock", "linksLock",
]
SERIES_PREVIEW_FIELDS = [
    *SERIES_METADATA_FIELDS[:13],
    "authors",
    *SERIES_METADATA_FIELDS[13:],
]
BOOK_METADATA_FIELDS = [
    "title", "titleSort", "summary", "number", "numberSort", "releaseDate", "publisher", "language",
    "isbn", "numberOfPages", "authors", "tags", "links", "ageRating", "titleLock", "titleSortLock", "summaryLock",
    "numberLock", "releaseDateLock", "publisherLock", "languageLock", "isbnLock", "authorsLock",
    "tagsLock", "linksLock", "ageRatingLock",
]

METADATA_FIELD_LABELS = {
    "title": "Titre",
    "titleSort": "Titre de tri",
    "summary": "Résumé",
    "status": "Statut",
    "publisher": "Éditeur",
    "language": "Langue",
    "readingDirection": "Sens de lecture",
    "ageRating": "Âge conseillé",
    "totalBookCount": "Nombre total de tomes",
    "genres": "Genres",
    "tags": "Tags",
    "sharingLabels": "Labels de partage",
    "alternateTitles": "Titres alternatifs",
    "links": "Liens externes",
    "authors": "Auteurs",
    "number": "Numéro",
    "numberSort": "Numéro de tri",
    "releaseDate": "Date de sortie",
    "isbn": "ISBN",
    "numberOfPages": "Nombre de pages",
}


def metadata_field_label(field: str) -> str:
    key = str(field or "")
    if key.endswith("Lock"):
        base = key[:-4]
        return f"Verrou — {METADATA_FIELD_LABELS.get(base, base)}"
    return METADATA_FIELD_LABELS.get(key, key)

SERIES_TABLE_FIELD_OPTIONS = [
    ("titleSort", "Titre de tri"),
    ("summary", "Résumé"),
    ("status", "Statut"),
    ("publisher", "Éditeur"),
    ("language", "Langue"),
    ("readingDirection", "Sens de lecture"),
    ("ageRating", "Âge conseillé"),
    ("totalBookCount", "Nombre total de tomes"),
    ("genres", "Genres"),
    ("tags", "Tags"),
    ("sharingLabels", "Labels de partage"),
    ("alternateTitles", "Titres alternatifs"),
    ("links", "Liens"),
    ("authors", "Auteurs des livres agrégés"),
    ("releaseDate", "Dates des livres agrégées"),
]
DEFAULT_SERIES_TABLE_FIELDS = [
    "summary",
    "status",
    "publisher",
    "language",
    "totalBookCount",
    "genres",
    "tags",
    "links",
    "authors",
]

BOOK_TABLE_FIELD_OPTIONS = [
    ("titleSort", "Titre de tri"),
    ("numberSort", "Numéro de tri"),
    ("summary", "Résumé"),
    ("publisher", "Éditeur"),
    ("language", "Langue"),
    ("releaseDate", "Date de sortie"),
    ("isbn", "ISBN"),
    ("numberOfPages", "Nombre de pages"),
    ("authors", "Auteurs"),
    ("tags", "Tags"),
    ("links", "Liens"),
    ("ageRating", "Âge conseillé"),
]
DEFAULT_BOOK_TABLE_FIELDS = [
    "releaseDate",
    "isbn",
    "numberOfPages",
    "authors",
    "publisher",
    "language",
    "tags",
    "links",
]

STRING_METADATA_FIELDS = {
    "title", "titleSort", "summary", "status", "publisher", "language", "readingDirection",
    "number", "numberSort", "releaseDate", "isbn",
}
INTEGER_METADATA_FIELDS = {"ageRating", "totalBookCount"}
CRITICAL_SERIES_UPDATE_FIELDS = {"status", "totalBookCount"}
LIST_STRING_METADATA_FIELDS = {"genres", "tags", "sharingLabels"}
JSON_LIST_METADATA_FIELDS = {"alternateTitles", "links", "authors"}
SEARCH_TAG_RE = re.compile(r"\s*[\(\[\{]\s*(?:EN|INT|OS)\s*[\)\]\}]\s*", re.IGNORECASE)
SEARCH_SEPARATOR_RE = re.compile(r"[!?:;\-_–—]+")
SEARCH_QUOTES_RE = re.compile(r"[\"“”‘’`]+")
SUMMARY_MIN_SIGNIFICANT_CHARS = 80
LOW_VALUE_SUMMARY_PREFIX_RE = re.compile(r"^(?:tout\s+sur\s+la\s+s[ée]rie|all\s+about\s+the\s+series)\b", re.IGNORECASE)
BDT_TOME_MATCH_MAX_CANDIDATES = 10
BDT_TOME_MATCH_MIN_BOOKS = 2
BDT_TOME_MATCH_MIN_RATIO = 0.60
BDT_TOME_MATCH_MIN_AVG_SCORE = 0.85
BDT_TOME_MATCH_PAIR_THRESHOLD = 0.85
BDT_EXACT_TITLE_MATCH_THRESHOLD = 0.999
SUPPORTED_LINK_UPDATE_PROVIDERS = {"bedetheque", "mangabaka", "comicvine"}
LINK_UPDATE_PROVIDER_PRIORITY = ["bedetheque", "mangabaka", "comicvine"]

def clean_search_title(value: Any) -> str:
    return clean_title_for_search(value)


def normalize_bcp47_tag(value: Any) -> str:
    text = str(value or "").strip().replace("_", "-")
    if not text:
        return ""
    if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", text):
        return ""
    parts = text.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 2 and part.isalpha():
            normalized.append(part.upper())
        elif len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        else:
            normalized.append(part)
    return "-".join(normalized)


def _typed_metadata_values_equal(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, default=str) == json.dumps(
        right,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def build_typed_metadata_payload(
    target_type: str,
    current: Dict[str, Any],
    values: Dict[str, Any],
    included_fields: Iterable[str],
    lock_values: Optional[Dict[str, bool]] = None,
) -> tuple[Dict[str, Any], List[str]]:
    """Validate a typed editor submission and return a strictly partial PATCH."""
    current = current or {}
    allowed = set(SERIES_TYPED_FIELDS if target_type == "series" else BOOK_TYPED_FIELDS)
    included = {str(field) for field in included_fields}
    payload: Dict[str, Any] = {}
    errors: List[str] = []

    unknown = sorted(included - allowed)
    if unknown:
        errors.append(f"Champs non modifiables pour {target_type} : {', '.join(unknown)}")

    for field in (SERIES_TYPED_FIELDS if target_type == "series" else BOOK_TYPED_FIELDS):
        if field not in included:
            continue
        value = values.get(field)

        if field in {"title", "titleSort", "number"}:
            value = str(value or "").strip()
            if not value:
                errors.append(f"{field} ne peut pas être vide lorsqu'il est modifié.")
                continue
        elif field == "status":
            value = str(value or "").strip()
            if value not in SERIES_STATUS_VALUES:
                errors.append(f"status doit être choisi dans : {', '.join(SERIES_STATUS_VALUES)}.")
                continue
        elif field == "readingDirection":
            value = str(value or "").strip() or None
            if value is not None and value not in READING_DIRECTION_VALUES:
                errors.append(
                    f"readingDirection doit être choisi dans : {', '.join(READING_DIRECTION_VALUES)}."
                )
                continue
        elif field == "language":
            raw_language = str(value or "").strip()
            value = normalize_bcp47_tag(raw_language) if raw_language else ""
            if raw_language and not value:
                errors.append("language doit être un code BCP-47 valide, par exemple fr, en ou zh-Hant-TW.")
                continue
        elif field == "ageRating":
            if value in ("", None):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    errors.append("ageRating doit être un entier positif ou nul.")
                    continue
                if value < 0:
                    errors.append("ageRating doit être supérieur ou égal à 0.")
                    continue
        elif field == "totalBookCount":
            if value in ("", None):
                value = None
            else:
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    errors.append("totalBookCount doit être un entier strictement positif.")
                    continue
                if value < 1:
                    errors.append("totalBookCount doit être supérieur ou égal à 1.")
                    continue
        elif field == "numberSort":
            try:
                value = float(value)
            except (TypeError, ValueError):
                errors.append("numberSort doit être un nombre.")
                continue
        elif field == "releaseDate":
            value = str(value or "").strip() or None
            if value is not None:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    errors.append("releaseDate doit respecter le format YYYY-MM-DD.")
                    continue
        elif field in {"genres", "tags", "sharingLabels"}:
            value = list(dict.fromkeys(str(item).strip() for item in (value or []) if str(item).strip()))
        elif field in {"alternateTitles", "links", "authors"}:
            clean_entries: List[Dict[str, str]] = []
            for index, item in enumerate(value or [], start=1):
                if not isinstance(item, dict):
                    errors.append(f"{field} ligne {index} est invalide.")
                    continue
                if field == "alternateTitles":
                    entry = {
                        "label": str(item.get("label") or "").strip(),
                        "title": str(item.get("title") or "").strip(),
                    }
                    if not entry["label"] or not entry["title"]:
                        errors.append(f"alternateTitles ligne {index} exige un label et un titre.")
                        continue
                elif field == "links":
                    entry = {
                        "label": str(item.get("label") or "").strip(),
                        "url": str(item.get("url") or "").strip(),
                    }
                    parsed = urlparse(entry["url"])
                    if not entry["label"] or parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        errors.append(f"links ligne {index} exige un label et une URL HTTP/HTTPS valide.")
                        continue
                else:
                    entry = {
                        "name": str(item.get("name") or "").strip(),
                        "role": str(item.get("role") or "").strip(),
                    }
                    if not entry["name"] or not entry["role"]:
                        errors.append(f"authors ligne {index} exige un nom et un rôle.")
                        continue
                clean_entries.append(entry)
            value = clean_entries
        elif field in {"summary", "publisher"}:
            value = str(value or "").strip()

        if not _typed_metadata_values_equal(current.get(field), value):
            payload[field] = value

    for field, checked in (lock_values or {}).items():
        if field not in allowed:
            continue
        lock_field = f"{field}Lock"
        checked = bool(checked)
        if bool(current.get(lock_field, False)) != checked:
            payload[lock_field] = checked

    return payload, errors


class TypedMetadataDialog(QDialog):
    def __init__(self, target_type: str, current: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.target_type = target_type
        self.current = dict(current or {})
        self.includes: Dict[str, QCheckBox] = {}
        self.editors: Dict[str, QWidget] = {}
        self.locks: Dict[str, QCheckBox] = {}
        self.structured_tables: Dict[str, QTableWidget] = {}
        self._payload: Dict[str, Any] = {}
        self._title_sort_manually_edited = False

        self.setWindowTitle(f"Modifier les métadonnées — {'série' if target_type == 'series' else 'tome'}")
        self.resize(980, 760)
        root = QVBoxLayout(self)
        notice = QLabel(
            "PATCH partiel : aucun champ n'est envoyé par défaut. "
            "Modifier une valeur coche explicitement le champ correspondant."
        )
        notice.setWordWrap(True)
        root.addWidget(notice)

        tabs = QTabWidget()
        root.addWidget(tabs, 1)
        general = QWidget()
        general_form = QFormLayout(general)
        tabs.addTab(self._scrollable(general), "Général")

        if target_type == "series":
            self._add_line_field(general_form, "title", "Titre", required=True)
            self._add_line_field(general_form, "titleSort", "Titre de tri", required=True)
            self._add_text_field(general_form, "summary", "Résumé")
            self._add_combo_field(general_form, "status", "Statut", SERIES_STATUS_VALUES, allow_empty=False)
            self._add_line_field(general_form, "publisher", "Éditeur")
            self._add_line_field(general_form, "language", "Langue BCP-47")
            self._add_combo_field(
                general_form,
                "readingDirection",
                "Sens de lecture",
                READING_DIRECTION_VALUES,
                allow_empty=True,
            )
            self._add_line_field(general_form, "ageRating", "Âge conseillé (>= 0)")
            self._add_line_field(general_form, "totalBookCount", "Nombre total de tomes (>= 1)")
            self._add_list_tab(tabs, "genres", "Genres")
            self._add_list_tab(tabs, "tags", "Tags")
            self._add_list_tab(tabs, "sharingLabels", "Labels de partage")
            self._add_structured_tab(tabs, "alternateTitles", "Titres alternatifs", ("label", "title"))
            self._add_structured_tab(tabs, "links", "Liens", ("label", "url"))
            title_editor = self.editors.get("title")
            title_sort_editor = self.editors.get("titleSort")
            if isinstance(title_editor, QLineEdit):
                title_editor.textEdited.connect(self._sync_title_sort)
            if isinstance(title_sort_editor, QLineEdit):
                title_sort_editor.textEdited.connect(self._mark_title_sort_manual)
        else:
            self._add_line_field(general_form, "title", "Titre", required=True)
            self._add_line_field(general_form, "number", "Numéro", required=True)
            self._add_line_field(general_form, "numberSort", "Numéro de tri", required=True)
            self._add_text_field(general_form, "summary", "Résumé")
            self._add_line_field(general_form, "releaseDate", "Date de sortie (YYYY-MM-DD)")
            isbn_note = QLineEdit(str(self.current.get("isbn") or ""))
            isbn_note.setReadOnly(True)
            isbn_note.setToolTip(
                "Lecture seule dans cet outil : la compatibilité de mise à jour ISBN varie selon la version Komga."
            )
            general_form.addRow("ISBN (lecture seule)", isbn_note)
            self._add_structured_tab(tabs, "authors", "Auteurs", ("name", "role"))
            self._add_list_tab(tabs, "tags", "Tags")
            self._add_structured_tab(tabs, "links", "Liens", ("label", "url"))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Appliquer")
        preview = buttons.addButton("Prévisualiser", QDialogButtonBox.ActionRole)
        preview.clicked.connect(self._preview)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _scrollable(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def _field_container(self, field: str, editor: QWidget, required: bool = False) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        include = QCheckBox("Modifier")
        include.setToolTip("Seuls les champs cochés sont inclus dans le PATCH.")
        row.addWidget(include)
        row.addWidget(editor, 1)
        lock = QCheckBox("Verrouiller")
        lock.setChecked(bool(self.current.get(f"{field}Lock", False)))
        lock.setToolTip("Le verrou n'est envoyé que si son état change.")
        row.addWidget(lock)
        if required:
            include.setToolTip(
                "Ce champ n'est obligatoire que si tu choisis de le modifier. "
                "Sinon il reste totalement absent du PATCH."
            )
        self.includes[field] = include
        self.editors[field] = editor
        self.locks[field] = lock
        return container

    def _add_line_field(self, form: QFormLayout, field: str, label: str, required: bool = False) -> None:
        editor = QLineEdit("" if self.current.get(field) is None else str(self.current.get(field)))
        form.addRow(label, self._field_container(field, editor, required))
        editor.textEdited.connect(lambda _text, f=field: self.includes[f].setChecked(True))

    def _add_text_field(self, form: QFormLayout, field: str, label: str) -> None:
        editor = QTextEdit("" if self.current.get(field) is None else str(self.current.get(field)))
        editor.setMinimumHeight(110)
        form.addRow(label, self._field_container(field, editor))
        editor.textChanged.connect(lambda f=field: self.includes[f].setChecked(True))

    def _add_combo_field(
        self,
        form: QFormLayout,
        field: str,
        label: str,
        values: Iterable[str],
        *,
        allow_empty: bool,
    ) -> None:
        editor = QComboBox()
        if allow_empty:
            editor.addItem("Non défini", "")
        current = str(self.current.get(field) or "")
        for value in values:
            editor.addItem(value, value)
        if current and editor.findData(current) < 0:
            editor.insertItem(0, f"Valeur actuelle invalide : {current}", current)
        index = editor.findData(current)
        editor.setCurrentIndex(index if index >= 0 else 0)
        form.addRow(label, self._field_container(field, editor, required=not allow_empty))
        editor.activated.connect(lambda _index, f=field: self.includes[f].setChecked(True))

    def _add_list_tab(self, tabs: QTabWidget, field: str, label: str) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        include = QCheckBox(f"Modifier {label.lower()}")
        include.setToolTip("Une valeur par ligne. Une liste vide efface la liste uniquement si cette case est cochée.")
        editor = QTextEdit()
        current = self.current.get(field) or []
        if isinstance(current, (list, tuple, set)):
            editor.setPlainText("\n".join(str(item) for item in current))
        elif current:
            editor.setPlainText(str(current))
        lock = QCheckBox("Verrouiller")
        lock.setChecked(bool(self.current.get(f"{field}Lock", False)))
        layout.addWidget(include)
        layout.addWidget(QLabel("Une valeur par ligne."))
        layout.addWidget(editor, 1)
        layout.addWidget(lock)
        self.includes[field] = include
        self.editors[field] = editor
        self.locks[field] = lock
        editor.textChanged.connect(lambda f=field: self.includes[f].setChecked(True))
        tabs.addTab(page, label)

    def _add_structured_tab(
        self,
        tabs: QTabWidget,
        field: str,
        label: str,
        columns: tuple[str, str],
    ) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        include = QCheckBox(f"Modifier {label.lower()}")
        include.setToolTip("Le tableau n'est envoyé que si cette case est cochée.")
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(list(columns))
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.horizontalHeader().setStretchLastSection(False)
        table.setColumnWidth(0, 260)
        table.setColumnWidth(1, 580)
        table.setMinimumHeight(table.horizontalHeader().height() + (max(table.verticalHeader().defaultSectionSize(), 28) * MIN_TABLE_VISIBLE_ROWS) + table.frameWidth() * 2 + 10)
        for entry in self.current.get(field) or []:
            if isinstance(entry, dict):
                self._append_structured_row(table, field, columns, entry)
        actions = QHBoxLayout()
        add_button = QPushButton("Ajouter")
        remove_button = QPushButton("Supprimer la ligne")
        actions.addWidget(add_button)
        actions.addWidget(remove_button)
        actions.addStretch(1)
        lock = QCheckBox("Verrouiller")
        lock.setChecked(bool(self.current.get(f"{field}Lock", False)))
        layout.addWidget(include)
        layout.addWidget(table, 1)
        layout.addLayout(actions)
        layout.addWidget(lock)
        self.includes[field] = include
        self.structured_tables[field] = table
        self.locks[field] = lock
        table.itemChanged.connect(lambda _item, f=field: self.includes[f].setChecked(True))
        add_button.clicked.connect(lambda _checked=False, f=field, c=columns: self._add_structured_row(f, c))
        remove_button.clicked.connect(lambda _checked=False, f=field: self._remove_structured_row(f))
        tabs.addTab(page, label)

    def _append_structured_row(
        self,
        table: QTableWidget,
        field: str,
        columns: tuple[str, str],
        entry: Optional[Dict[str, Any]] = None,
    ) -> None:
        row = table.rowCount()
        table.insertRow(row)
        entry = entry or {}
        table.setItem(row, 0, QTableWidgetItem(str(entry.get(columns[0]) or "")))
        if field == "authors":
            role = QComboBox()
            role.setEditable(True)
            role.addItems(AUTHOR_ROLE_VALUES)
            current_role = str(entry.get("role") or "")
            if current_role and role.findText(current_role) < 0:
                role.addItem(current_role)
            role.setCurrentText(current_role)
            table.setCellWidget(row, 1, role)
            role.currentTextChanged.connect(lambda _text, f=field: self.includes[f].setChecked(True))
        else:
            table.setItem(row, 1, QTableWidgetItem(str(entry.get(columns[1]) or "")))

    def _add_structured_row(self, field: str, columns: tuple[str, str]) -> None:
        self._append_structured_row(self.structured_tables[field], field, columns)
        self.includes[field].setChecked(True)

    def _remove_structured_row(self, field: str) -> None:
        table = self.structured_tables[field]
        rows = sorted({item.row() for item in table.selectedItems()}, reverse=True)
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]
        for row in rows:
            table.removeRow(row)
        if rows:
            self.includes[field].setChecked(True)

    def _sync_title_sort(self, text: str) -> None:
        editor = self.editors.get("titleSort")
        if isinstance(editor, QLineEdit) and not self._title_sort_manually_edited:
            editor.setText(text)
            self.includes["titleSort"].setChecked(True)

    def _mark_title_sort_manual(self, _text: str) -> None:
        self._title_sort_manually_edited = True

    def _field_value(self, field: str) -> Any:
        editor = self.editors.get(field)
        if isinstance(editor, QLineEdit):
            return editor.text()
        if isinstance(editor, QTextEdit):
            if field in {"genres", "tags", "sharingLabels"}:
                return [line.strip() for line in editor.toPlainText().splitlines() if line.strip()]
            return editor.toPlainText()
        if isinstance(editor, QComboBox):
            return editor.currentData()
        table = self.structured_tables.get(field)
        if table is not None:
            entries: List[Dict[str, str]] = []
            if field == "alternateTitles":
                keys = ("label", "title")
            elif field == "links":
                keys = ("label", "url")
            else:
                keys = ("name", "role")
            for row in range(table.rowCount()):
                first = table.item(row, 0).text() if table.item(row, 0) else ""
                if field == "authors":
                    combo = table.cellWidget(row, 1)
                    second = combo.currentText() if isinstance(combo, QComboBox) else ""
                else:
                    second = table.item(row, 1).text() if table.item(row, 1) else ""
                if first.strip() or second.strip():
                    entries.append({keys[0]: first, keys[1]: second})
            return entries
        return None

    def result_payload(self) -> tuple[Dict[str, Any], List[str]]:
        fields = SERIES_TYPED_FIELDS if self.target_type == "series" else BOOK_TYPED_FIELDS
        values = {field: self._field_value(field) for field in fields}
        included = [field for field, checkbox in self.includes.items() if checkbox.isChecked()]
        locks = {field: checkbox.isChecked() for field, checkbox in self.locks.items()}
        return build_typed_metadata_payload(self.target_type, self.current, values, included, locks)

    def _preview(self) -> None:
        payload, errors = self.result_payload()
        if errors:
            QMessageBox.warning(self, "Validation", "\n".join(errors))
            return
        QMessageBox.information(
            self,
            "Prévisualisation du PATCH",
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) if payload else "Aucun changement.",
        )

    def accept(self) -> None:
        payload, errors = self.result_payload()
        if errors:
            QMessageBox.warning(self, "Validation", "\n".join(errors))
            return
        if not payload:
            QMessageBox.information(self, "Métadonnées", "Aucun changement à appliquer.")
            return
        self._payload = payload
        super().accept()

    @property
    def payload(self) -> Dict[str, Any]:
        return dict(self._payload)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception:
            self._safe_emit("error", traceback.format_exc())
        else:
            self._safe_emit("result", result)
        finally:
            self._safe_emit("finished")

    def _safe_emit(self, name: str, *args: Any) -> None:
        try:
            getattr(self.signals, name).emit(*args)
        except RuntimeError:
            # The window may be closing while a QRunnable finishes. Qt can delete
            # the signal source before the Python worker unwinds; do not turn app
            # shutdown into noisy stderr tracebacks.
            return


def json_text(value: Any, indent: int = 2) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=True)


def one_line(value: Any) -> str:
    if value is None:
        return "<NULL>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def expanded_column_widths(widths: List[int], available: int, flexible_columns: Iterable[int]) -> List[int]:
    """Expand selected columns so a table uses its available viewport width."""
    result = [max(0, int(width)) for width in widths]
    flexible = [index for index in dict.fromkeys(int(x) for x in flexible_columns) if 0 <= index < len(result)]
    extra = max(0, int(available) - sum(result))
    if extra <= 0 or not flexible:
        return result
    share, remainder = divmod(extra, len(flexible))
    for position, column in enumerate(flexible):
        result[column] += share + (1 if position < remainder else 0)
    return result


def is_blank_metadata_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def normalized_summary_text(value: Any) -> str:
    return quality_normalized_summary_text(value)


def significant_summary_length(value: Any) -> int:
    return quality_significant_summary_length(value)


def is_low_value_summary(value: Any) -> bool:
    return quality_is_low_value_summary(value)


def should_auto_include_metadata_field(field: str, value: Any) -> bool:
    if is_blank_metadata_value(value):
        return False
    if field == "language" and not quality_is_supported_write_language(value):
        return False
    if field == "summary" and is_low_value_summary(value):
        return False
    return True


def candidate_with_linked_title_sort(
    current: Optional[Dict[str, Any]],
    candidate: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Keep titleSort aligned when a source proposes a changed title."""
    prepared = dict(candidate or {})
    title = prepared.get("title")
    if (
        not is_blank_metadata_value(title)
        and one_line((current or {}).get("title", "")) != one_line(title)
        and is_blank_metadata_value(prepared.get("titleSort"))
    ):
        prepared["titleSort"] = title
    return prepared


def ranked_title_results(query: str, rows: Iterable[Any]) -> List[tuple[float, Any]]:
    """Rank source search results deterministically by title similarity."""
    ranked = [
        (title_similarity(clean_search_title(query), clean_search_title(getattr(row, "title", ""))), row)
        for row in rows
    ]
    ranked.sort(
        key=lambda item: (
            -item[0],
            str(getattr(item[1], "title", "")).casefold(),
            str(
                getattr(item[1], "id", "")
                or getattr(item[1], "slug", "")
                or getattr(item[1], "url", "")
            ),
        )
    )
    return ranked


def should_auto_apply_changed_metadata_field(field: str, target_type: str = "series") -> bool:
    return quality_should_auto_apply_changed_metadata_field(field, target_type)


def bedetheque_main_album_count(albums: Any) -> int:
    return quality_bedetheque_main_album_count(albums)


def normalized_language_code(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("_", "-")
    if not text:
        return ""
    aliases = {
        "fre": "fr",
        "fra": "fr",
        "french": "fr",
        "français": "fr",
        "francais": "fr",
        "eng": "en",
        "english": "en",
        "anglais": "en",
    }
    text = aliases.get(text, text)
    if text.startswith("fr-"):
        return "fr"
    if text.startswith("en-"):
        return "en"
    return text


def metadata_language_matches(value: Any, expected: str) -> bool:
    expected = normalized_language_code(expected)
    if not expected:
        return True
    return normalized_language_code(value) == expected


def normalized_status_code(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return text


def metadata_status_matches(value: Any, expected: str) -> bool:
    expected = normalized_status_code(expected)
    if not expected or expected == "ALL":
        return True
    if expected == "VIDE":
        return is_blank_metadata_value(value)
    return normalized_status_code(value) == expected


def _link_label_from_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        host = urlparse(text if "://" in text else "https://" + text).netloc.casefold()
    except Exception:
        host = ""
    host = host.removeprefix("www.")
    if not host:
        return ""
    first = host.split(".")[0].strip()
    known = {
        "bedetheque": "bedetheque",
        "mangabaka": "mangabaka",
        "nautiljon": "nautiljon",
        "anilist": "anilist",
        "mangaupdates": "mangaupdates",
        "manga-updates": "manga-updates",
        "manga-news": "manga_news",
        "manganews": "manga_news",
        "myanimelist": "myanimelist",
        "kitsu": "kitsu",
        "shikimori": "shikimori",
        "anime-planet": "anime-planet",
        "animeplanet": "anime-planet",
        "comicvine": "comicvine",
        "gamespot": "comicvine",
    }
    for token, label in known.items():
        if token in host:
            return label
    return first


def normalized_link_label(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"\s+", " ", text)
    aliases = {
        "bdtheque": "bedetheque",
        "bédéthèque": "bedetheque",
        "bedetheque.com": "bedetheque",
        "manga baka": "mangabaka",
        "nautiljon.com": "nautiljon",
        "manga updates": "mangaupdates",
        "mangaupdates.com": "mangaupdates",
        "manga news": "manga_news",
        "manga-news": "manga_news",
        "manga-news.com": "manga_news",
        "manga-updates.com": "manga-updates",
        "my anime list": "myanimelist",
        "myanimelist.net": "myanimelist",
        "comic vine": "comicvine",
        "comicvine.com": "comicvine",
        "comicvine.gamespot.com": "comicvine",
        "gamespot.com": "comicvine",
    }
    return aliases.get(text, text)


WITHOUT_LINK_LABEL_PREFIX = "__WITHOUT_LINK_LABEL__:"


def without_link_label_filter_value(label: Any) -> str:
    normalized = normalized_link_label(label)
    return f"{WITHOUT_LINK_LABEL_PREFIX}{normalized}" if normalized else ""


def is_without_link_label_filter(value: Any) -> bool:
    return str(value or "").startswith(WITHOUT_LINK_LABEL_PREFIX)


def without_link_label_from_filter(value: Any) -> str:
    text = str(value or "")
    if not text.startswith(WITHOUT_LINK_LABEL_PREFIX):
        return ""
    return text[len(WITHOUT_LINK_LABEL_PREFIX):]


def metadata_link_labels(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                return metadata_link_labels(json.loads(text))
            except Exception:
                pass
        labels: List[str] = []
        for part in re.split(r"[;\n]", text):
            part = part.strip()
            if not part:
                continue
            labels.append(_link_label_from_url(part) or part)
        return _dedupe_link_labels(labels)
    if isinstance(value, dict):
        labels: List[str] = []
        for key in ("label", "name", "provider", "source", "site", "type"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                labels.append(candidate.strip())
                break
        for key in ("url", "href", "link"):
            inferred = _link_label_from_url(value.get(key))
            if inferred:
                labels.append(inferred)
                break
        return _dedupe_link_labels(labels)
    if isinstance(value, (list, tuple, set)):
        labels: List[str] = []
        for item in value:
            labels.extend(metadata_link_labels(item))
        return _dedupe_link_labels(labels)
    return []


def _dedupe_link_labels(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = normalized_link_label(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def metadata_link_label_matches(value: Any, expected: str) -> bool:
    expected_norm = normalized_link_label(expected)
    labels = metadata_link_labels(value)
    if not expected_norm or expected_norm == "all":
        return True
    if expected_norm == "__no_link__":
        return len(labels) == 0
    return any(normalized_link_label(label) == expected_norm for label in labels)


def metadata_link_filter_matches(value: Any, expected: str) -> bool:
    if is_without_link_label_filter(expected):
        excluded_label = without_link_label_from_filter(expected)
        if not excluded_label:
            return True
        return not metadata_link_label_matches(value, excluded_label)
    return metadata_link_label_matches(value, expected)


def metadata_link_entries(value: Any) -> List[Dict[str, str]]:
    """Return normalized link entries from Komga metadata.links."""
    entries: List[Dict[str, str]] = []

    def add(label: Any, url: Any) -> None:
        text_url = str(url or "").strip()
        if not text_url:
            return
        text_label = str(label or "").strip() or _link_label_from_url(text_url) or "link"
        if not text_url.startswith(("http://", "https://")):
            return
        key = (normalized_link_label(text_label), text_url)
        if any((normalized_link_label(item.get("label")), item.get("url")) == key for item in entries):
            return
        entries.append({"label": text_label, "url": text_url})

    if value is None or value == "":
        return entries
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return entries
        if text.startswith("[") or text.startswith("{"):
            try:
                return metadata_link_entries(json.loads(text))
            except Exception:
                pass
        for part in re.split(r"[;\n]", text):
            part = part.strip()
            if part:
                add(_link_label_from_url(part), part)
        return entries
    if isinstance(value, dict):
        label = ""
        for key in ("label", "name", "provider", "source", "site", "type"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                label = candidate.strip()
                break
        url = ""
        for key in ("url", "href", "link"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                url = candidate.strip()
                break
        add(label, url)
        return entries
    if isinstance(value, (list, tuple, set)):
        for item in value:
            for entry in metadata_link_entries(item):
                add(entry.get("label"), entry.get("url"))
        return entries
    return entries


def is_bedetheque_series_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.casefold()
    return "bedetheque.com" in host and "serie-" in path and path.endswith(".html")


def extract_mangabaka_series_id_from_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else "https://" + text)
    host = parsed.netloc.casefold().removeprefix("www.")
    if "mangabaka.org" not in host:
        return ""
    query = parse_qs(parsed.query or "")
    for key in ("id", "series_id", "seriesId"):
        values = query.get(key)
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    lowered = [part.casefold() for part in parts]
    if "series" in lowered:
        index = lowered.index("series")
        if index + 1 < len(parts):
            return parts[index + 1].strip()
    if parts and lowered[0] not in {"v1", "api", "data", "search", "series"}:
        return parts[0].strip()
    return ""




def extract_manga_news_series_slug_from_url(url: str) -> str:
    return series_slug_from_manga_news_url(url)


def extract_comicvine_volume_id_from_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else "https://" + text)
    host = parsed.netloc.casefold().removeprefix("www.")
    if "comicvine.gamespot.com" not in host and "comicvine.com" not in host:
        return ""
    query = parse_qs(parsed.query or "")
    for key in ("id", "volume_id", "volumeId"):
        values = query.get(key)
        if values and str(values[0]).strip():
            return str(values[0]).strip()
    match = re.search(r"(?:^|/)(?:4050-)?(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else ""

def listish(value: Any) -> bool:
    return isinstance(value, list) or (isinstance(value, str) and ";" in value)


def value_as_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                pass
        if ";" in text:
            return [x.strip() for x in text.split(";") if x.strip()]
    return [value]


def merge_list_values(current: Any, candidate: Any) -> List[Any]:
    merged: List[Any] = []
    seen: set[str] = set()
    for item in value_as_list(current) + value_as_list(candidate):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def proposed_metadata_value(current: Any, candidate: Any) -> Any:
    if listish(current) or listish(candidate) or isinstance(current, list) or isinstance(candidate, list):
        return merge_list_values(current, candidate)
    return candidate


def scalar_metadata_text(value: Any) -> str:
    return quality_scalar_metadata_text(value)




def _isbn10_is_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dXx]", value or ""):
        return False
    total = 0
    for index, char in enumerate(value[:9], start=1):
        total += index * int(char)
    check = 10 if value[-1].upper() == "X" else int(value[-1])
    total += 10 * check
    return total % 11 == 0


def _isbn13_is_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{13}", value or ""):
        return False
    total = 0
    for index, char in enumerate(value[:12]):
        total += int(char) * (1 if index % 2 == 0 else 3)
    check = (10 - (total % 10)) % 10
    return check == int(value[-1])


def normalize_isbn_value(value: Any) -> str:
    return quality_normalize_isbn_value(value)

def normalize_alternate_title_entries(value: Any) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []

    def guess_label(text: str) -> str:
        if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text):
            return "ja"
        if any("\uac00" <= ch <= "\ud7af" for ch in text):
            return "ko"
        if any("\u0400" <= ch <= "\u04ff" for ch in text):
            return "ru"
        return "alt"

    def add(label: Any, title: Any) -> None:
        text = scalar_metadata_text(title)
        if not text:
            return
        item = {"label": str(label or "").strip() or guess_label(text), "title": text}
        key = (item["label"].casefold(), item["title"].casefold())
        if any((x.get("label", "").casefold(), x.get("title", "").casefold()) == key for x in entries):
            return
        entries.append(item)

    if value is None or value == "":
        return entries
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") or text.startswith("{"):
            try:
                return normalize_alternate_title_entries(json.loads(text))
            except Exception:
                pass
        for part in re.split(r"[;\n]", text):
            add("", part)
        return entries
    if isinstance(value, dict):
        title = value.get("title") or value.get("name") or value.get("value")
        label = value.get("label") or value.get("language") or value.get("lang")
        add(label, title)
        return entries
    if isinstance(value, (list, tuple, set)):
        for item in value:
            for entry in normalize_alternate_title_entries(item):
                add(entry.get("label"), entry.get("title"))
        return entries
    add("", value)
    return entries


def normalize_metadata_payload_value(field: str, value: Any) -> Any:
    """Normalize one metadata value to Komga PATCH DTO-compatible shapes."""
    if value is None:
        return None
    if field.endswith("Lock"):
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() == "true"
    if field == "isbn":
        return normalize_isbn_value(value)
    if field in {"title", "titleSort"}:
        return clean_title_for_write(value)
    if field == "language":
        return quality_normalize_write_language(value)
    if field in STRING_METADATA_FIELDS:
        return scalar_metadata_text(value)
    if field in INTEGER_METADATA_FIELDS:
        if value == "":
            return ""
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field == "links":
        return metadata_link_entries(value)
    if field == "alternateTitles":
        return normalize_alternate_title_entries(value)
    if field in LIST_STRING_METADATA_FIELDS:
        return [scalar_metadata_text(x) for x in value_as_list(value) if scalar_metadata_text(x)]
    if field in JSON_LIST_METADATA_FIELDS:
        return value_as_list(value)
    return value


def proposed_metadata_value_for_field(field: str, current: Any, candidate: Any) -> Any:
    if field in STRING_METADATA_FIELDS:
        return scalar_metadata_text(candidate)
    if field in INTEGER_METADATA_FIELDS or field.endswith("Lock"):
        return normalize_metadata_payload_value(field, candidate)
    return normalize_metadata_payload_value(field, proposed_metadata_value(current, candidate))


def id_lines(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(x) for x in value if str(x).strip())
    return ""


def ids_from_text(text: str) -> List[str]:
    out: List[str] = []
    for line in (text or "").replace(",", "\n").splitlines():
        val = line.strip().strip('"')
        if val and val not in out:
            out.append(val)
    return out


class MainWindow(QMainWindow):
    auto_match_progress_signal = Signal(str, int, int)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION} — Desktop V2")
        self.resize(1680, 980)
        self.config_path = DEFAULT_CONFIG_FILE
        self.config = load_config(self.config_path, include_secrets=False)
        self._secrets_loaded = False
        self.backup = BackupManager(self.config.backup_root)
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[Worker] = set()
        self.operation_history: List[Dict[str, Any]] = []
        self._operation_sequence = 0
        self.audit_rows: List[Dict[str, Any]] = []
        self._external_rate_limit_state: Dict[str, Dict[str, Any]] = {
            "bedetheque": {"next_allowed": 0.0, "lock": threading.Lock()},
            "mangabaka": {"next_allowed": 0.0, "lock": threading.Lock()},
            "manga_news": {"next_allowed": 0.0, "lock": threading.Lock()},
            "comicvine": {"next_allowed": 0.0, "lock": threading.Lock()},
        }
        self._diagnostic_lock = threading.Lock()
        self._diagnostic_counter = 0
        self.diagnostic_log_path = os.path.join(self.backup.session_dir, "audit", "diagnostic_requests.jsonl")

        self.libraries: List[Any] = []
        self.library_combos: Dict[str, QComboBox] = {}
        self._main_tab_indices: Dict[str, int] = {}
        self._main_tab_source_widgets: Dict[int, int] = {}
        self._navigation_items_by_index: Dict[int, QListWidgetItem] = {}
        self._active_context_library_name = "Toutes les bibliothèques"
        self._active_context_library_id = ""
        self._active_context_series_title = ""
        self._active_context_series_id = ""
        self._active_context_book_title = ""
        self._active_context_book_id = ""
        self._source_context_labels: Dict[str, QLabel] = {}
        self._source_series_unfiltered_rows: Dict[str, List[Any]] = {}
        self.series_rows: List[Any] = []
        self.book_rows: List[Any] = []
        self.readlist_book_rows: List[Any] = []
        self.collection_rows: List[CollectionItem] = []
        self.collection_member_rows: List[Any] = []
        self.collection_library_series_rows: List[Any] = []
        self.collection_bulk_series_rows: List[Any] = []
        self.collection_suggestion_rows: List[Dict[str, Any]] = []
        self.readlist_rows: List[ReadlistItem] = []
        self.loaded_csv_actions: List[Any] = []
        self.readlist_library_series_rows: List[Any] = []
        self.readlist_library_book_rows: List[Any] = []
        self.readlist_bulk_book_rows: List[Any] = []
        self.bedetheque_results: List[BedethequeSearchResult] = []
        self.bedetheque_candidate: Optional[BedethequeCandidate] = None
        self.bdt_komga_series_rows: List[Any] = []
        self.bdt_komga_book_rows: List[Any] = []
        self.bdt_series_candidate: Optional[BedethequeCandidate] = None
        self.bdt_album_candidates_by_url: Dict[str, BedethequeCandidate] = {}
        self.bdt_matches: List[Dict[str, Any]] = []
        self.bdt_queue: List[Any] = []
        self.bdt_queue_index: int = -1
        self.bdt_queue_dialog: Optional[QDialog] = None
        # Incrémente à chaque changement de série Bedetheque cible.
        # Les recherches/scrapes en retard ne doivent jamais réécrire le diff
        # d'une autre série quand on avance vite dans la file.
        self.bdt_context_generation: int = 0
        self.mangabaka_results: List[MangaBakaSearchResult] = []
        self.mangabaka_candidate: Optional[MangaBakaCandidate] = None
        self.mbk_komga_series_rows: List[Any] = []
        self.mbk_komga_book_rows: List[Any] = []
        self.mbk_context_generation: int = 0
        self.manga_news_results: List[MangaNewsSearchResult] = []
        self.manga_news_candidate: Optional[MangaNewsCandidate] = None
        self.mn_volume_candidate: Optional[MangaNewsVolumeCandidate] = None
        self.mn_komga_book_rows: List[Any] = []
        self.mn_komga_series_rows: List[Any] = []
        self.mn_context_generation: int = 0
        self.nr_series_rows: List[Any] = []
        self.nr_series_unfiltered_rows: List[Any] = []
        self.nr_filter_counts: Dict[str, int] = {}
        self.nr_rows: List[Dict[str, Any]] = []
        self.nr_visible_rows: List[Dict[str, Any]] = []
        self.comicvine_results: List[ComicVineSearchResult] = []
        self.comicvine_candidate: Optional[ComicVineCandidate] = None
        self.cv_komga_series_rows: List[Any] = []
        self.cv_komga_book_rows: List[Any] = []
        self.cv_issue_rows: List[ComicVineIssueCandidate] = []
        self.cv_issue_candidates_by_id: Dict[str, ComicVineIssueCandidate] = {}
        self.cv_book_matches: List[Dict[str, Any]] = []
        self.cv_context_generation: int = 0
        self.current_metadata: Dict[str, Any] = {}
        self._metadata_preview_signature: Optional[str] = None
        self.local_exclusions = LocalExclusionsStore()
        self.enrichment_history = EnrichmentHistoryStore()
        self.meta_target_rows: List[Any] = []
        self._registered_tables: Dict[str, QTableWidget] = {}
        self._table_default_hidden: Dict[str, set[str]] = {}
        self._series_table_row_attributes: Dict[str, str] = {}
        self._table_viewports: Dict[int, QTableWidget] = {}
        self._selection_detail_panels: Dict[str, Dict[str, Any]] = {}
        self.explorer_current_detail: Dict[str, Any] = {}
        self.explorer_current_target_type = ""
        self.explorer_current_target_id = ""
        self.explorer_series_detail: Dict[str, Any] = {}
        self.explorer_series_target_id = ""
        self.explorer_book_detail: Dict[str, Any] = {}
        self.explorer_book_target_id = ""
        self.book_explorer_rows: List[Dict[str, Any]] = []
        self.book_explorer_visible_rows: List[Dict[str, Any]] = []
        self.book_explorer_analysis_rows: List[Dict[str, Any]] = []
        self._restoring_table_ui_state = False
        self._table_state_save_timer = QTimer(self)
        self._table_state_save_timer.setSingleShot(True)
        self._table_state_save_timer.timeout.connect(self._save_all_table_ui_states)
        self.release_tracking_series_rows: List[Any] = []
        self.rt_series_rows: List[Any] = []
        self.rt_series_unfiltered_rows: List[Any] = []
        self.rt_filter_counts: Dict[str, int] = {}
        self.release_tracking_rows: List[Dict[str, Any]] = []
        self.release_tracking_last_csv_path: str = ""
        self._komga_connection_validated = False
        ui_config = self.config.ui if isinstance(getattr(self.config, "ui", None), dict) else {}
        self._diagnostics_enabled_cached = bool(ui_config.get("diagnostic_requests", True))
        # Async Komga series reload guard. A slower, older reload must never
        # overwrite a newer filtered table after a filter/source change.
        self._series_load_generation: Dict[str, int] = {}

        self._build_ui()
        self.auto_match_progress_signal.connect(self._set_auto_match_progress)
        self._config_to_ui()
        self.log(f"Session backup : {self.backup.session_dir}")
        QTimer.singleShot(0, self.load_secrets_on_startup)
        QTimer.singleShot(1200, self.refresh_rollback_records)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    def run_worker(self, label: str, fn: Callable[[], Any], done: Optional[Callable[[Any], None]] = None) -> None:
        self.log(f"▶ {label}")
        self._set_auto_match_progress(f"{label} — en cours", 0, 0)
        event_id = self._next_diagnostic_id(label)
        self._register_operation_task(event_id, label, fn, done)

        def wrapped_fn() -> Any:
            started = time.monotonic()
            self._write_diagnostic_event({"event": "worker_start", "id": event_id, "label": label})
            try:
                result = fn()
            except Exception as exc:
                duration_ms = round((time.monotonic() - started) * 1000, 2)
                self._write_diagnostic_event({
                    "event": "worker_error",
                    "id": event_id,
                    "label": label,
                    "duration_ms": duration_ms,
                    "error": str(exc),
                })
                raise
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            self._write_diagnostic_event({
                "event": "worker_success",
                "id": event_id,
                "label": label,
                "duration_ms": duration_ms,
                "result": self._diagnostic_result_summary(result),
            })
            return result

        worker = Worker(wrapped_fn)
        self.active_workers.add(worker)
        self._refresh_active_worker_status()

        def handle_result(result: Any) -> None:
            try:
                if done:
                    done(result)
            except Exception:
                trace = traceback.format_exc()
                self._fail_operation_task(event_id, trace, phase="Traitement du résultat")
                self._worker_error_feedback(label, trace)
                return
            self._complete_operation_task(event_id, result)
            self._worker_result_feedback(label, result)

        worker.signals.result.connect(handle_result)
        worker.signals.error.connect(
            lambda text, task_id=event_id: self._fail_operation_task(task_id, text)
        )
        worker.signals.error.connect(lambda text: self._worker_error_feedback(label, text))
        worker.signals.finished.connect(
            lambda worker=worker, task_id=event_id: self._on_worker_finished(worker, task_id)
        )
        self.thread_pool.start(worker)

    def _on_worker_finished(self, worker: Worker, task_id: str = "") -> None:
        self.active_workers.discard(worker)
        task = self._operation_task(task_id)
        if task is not None and task.get("status") == "En cours":
            self._finish_operation_task(task, "Terminée", "Opération terminée sans résumé de résultat.")
        self._refresh_active_worker_status()

    def _refresh_active_worker_status(self) -> None:
        if not hasattr(self, "context_tasks_label"):
            return
        count = len(self.active_workers)
        self.context_tasks_label.setText("Aucune tâche active" if count == 0 else f"{count} tâche(s) active(s)")
        self.context_tasks_label.setStyleSheet(
            "font-weight: 600; color: #8bd3ff;" if count else "color: #c5c8cc;"
        )

    @staticmethod
    def _operation_is_retryable(label: str) -> bool:
        normalized = str(label or "").casefold()
        safe_markers = (
            "audit",
            "chargement",
            "charger",
            "recherche",
            "scan",
            "test ",
            "prévisualisation",
            "inventaire",
            "rafraîchissement",
        )
        write_markers = (
            "application",
            "appliquer",
            "écriture",
            "rollback",
            "création",
            "ajout",
            "retrait",
            "suppression",
            "upload",
            "identify",
        )
        return any(marker in normalized for marker in safe_markers) and not any(
            marker in normalized for marker in write_markers
        )

    def _register_operation_task(
        self,
        task_id: str,
        label: str,
        fn: Callable[[], Any],
        done: Optional[Callable[[Any], None]],
    ) -> None:
        self._operation_sequence += 1
        task = {
            "id": task_id,
            "sequence": self._operation_sequence,
            "label": str(label or "Opération"),
            "status": "En cours",
            "started_at": datetime.now(),
            "started_monotonic": time.monotonic(),
            "duration_ms": None,
            "summary": "Démarrage…",
            "detail": "L'opération est en cours.",
            "retryable": self._operation_is_retryable(label),
            "fn": fn,
            "done": done,
        }
        self.operation_history.insert(0, task)
        finished = [item for item in self.operation_history if item.get("status") != "En cours"]
        if len(finished) > 200:
            removable_ids = {item.get("id") for item in finished[200:]}
            self.operation_history = [
                item for item in self.operation_history if item.get("id") not in removable_ids
            ]
        self._populate_operations_table()

    def _operation_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return next(
            (task for task in self.operation_history if str(task.get("id") or "") == str(task_id or "")),
            None,
        )

    def _finish_operation_task(self, task: Dict[str, Any], status: str, detail: str) -> None:
        started = float(task.get("started_monotonic") or time.monotonic())
        task["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        task["status"] = status
        task["detail"] = self._redact_text(detail)
        if status == "Réussie":
            task["fn"] = None
            task["done"] = None
        self._populate_operations_table()
        self._refresh_health_dashboard()

    def _complete_operation_task(self, task_id: str, result: Any) -> None:
        task = self._operation_task(task_id)
        if task is None:
            return
        summary = self._diagnostic_result_summary(result)
        count_parts = [
            (f"éléments={value}" if key == "count" else f"{key}={value}")
            for key, value in summary.items()
            if key == "count" or key.endswith("_count")
        ]
        task["summary"] = "Aucun résultat" if summary.get("empty") else (", ".join(count_parts) or "Terminée")
        self._finish_operation_task(task, "Réussie", json_text(summary))

    def _fail_operation_task(self, task_id: str, trace: str, phase: str = "Exécution") -> None:
        task = self._operation_task(task_id)
        if task is None:
            return
        task["summary"] = f"Erreur — {phase}"
        self._finish_operation_task(task, "Erreur", trace)

    def _diagnostics_enabled(self) -> bool:
        return bool(getattr(self, "_diagnostics_enabled_cached", True))

    def _set_diagnostics_enabled_cached(self, value: Any) -> None:
        self._diagnostics_enabled_cached = bool(value)

    def _next_diagnostic_id(self, label: str = "") -> str:
        with self._diagnostic_lock:
            self._diagnostic_counter += 1
            counter = self._diagnostic_counter
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", label or "event").strip("_")[:40] or "event"
        return f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{counter:05d}_{safe}"

    def _write_diagnostic_event(self, event: Dict[str, Any]) -> None:
        if not self._diagnostics_enabled():
            return
        try:
            row = self._redact_diagnostic_value(dict(event or {}))
            row.setdefault("timestamp", datetime.now().isoformat(timespec="milliseconds"))
            os.makedirs(os.path.dirname(self.diagnostic_log_path), exist_ok=True)
            with self._diagnostic_lock:
                with open(self.diagnostic_log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Diagnostic logging must never break the application workflow.
            return

    def _known_secrets(self) -> List[str]:
        values: List[str] = []
        for name in ("api_key", "username", "password", "manga_news_token", "comicvine_api_key"):
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "text"):
                values.append(str(widget.text() or ""))
        return values

    def _redact_text(self, value: Any) -> str:
        return SecretRedactor.redact(value, self._known_secrets())

    def _redact_diagnostic_value(self, value: Any) -> Any:
        sensitive = {
            "api_key",
            "apikey",
            "authorization",
            "login",
            "password",
            "passwd",
            "secret",
            "token",
            "username",
        }
        if isinstance(value, dict):
            return {
                key: (
                    "[SECRET MASQUÉ]"
                    if str(key).casefold().replace("-", "_") in sensitive
                    else self._redact_diagnostic_value(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._redact_diagnostic_value(item) for item in value]
        if isinstance(value, str):
            return self._redact_text(value)
        return value

    def _diagnostic_result_summary(self, result: Any) -> Dict[str, Any]:
        if result is None:
            return {"type": "none", "empty": True}
        if isinstance(result, (list, tuple, set)):
            return {"type": type(result).__name__, "count": len(result), "empty": len(result) == 0}
        if isinstance(result, dict):
            summary: Dict[str, Any] = {"type": "dict", "keys": sorted(str(k) for k in result.keys())[:20]}
            for key in ("rows", "data", "items", "candidates"):
                value = result.get(key)
                if isinstance(value, (list, tuple, set)):
                    summary[f"{key}_count"] = len(value)
                    if len(value) == 0:
                        summary["empty"] = True
            if "error" in result and result.get("error"):
                summary["error"] = str(result.get("error"))[:500]
            return summary
        return {"type": type(result).__name__, "text": str(result)[:500]}

    def _worker_result_feedback(self, label: str, result: Any) -> None:
        summary = self._diagnostic_result_summary(result)
        empty = bool(summary.get("empty"))
        if empty:
            message = f"{label} — terminé : aucun résultat"
        else:
            count_parts = [
                (f"{v} élément(s) reçu(s)" if k == "count" else f"{k}={v}")
                for k, v in summary.items()
                if k == "count" or k.endswith("_count")
            ]
            suffix = f" ({', '.join(count_parts)})" if count_parts else ""
            message = f"{label} — terminé{suffix}"
        self._set_auto_match_progress(message, 1, 1)

    def _worker_error_feedback(self, label: str, trace: str) -> None:
        self._set_auto_match_progress(f"{label} — erreur", 1, 1)
        if "HTTP 401 " in trace or '"status":401' in trace or '"status": 401' in trace:
            self._komga_connection_validated = False
            self._refresh_context_header()
            self.log(
                f"⚠️ {label} : authentification Komga refusée (HTTP 401). "
                "Vérifie le mode d'authentification et saisis à nouveau les identifiants "
                "dans Connexion, puis sauvegarde les réglages."
            )
            self.tabs.setCurrentIndex(0)
            return
        self.log_error(label, trace)

    def log(self, message: str) -> None:
        self.log_text.append(self._redact_text(message))

    def log_error(self, label: str, trace: str) -> None:
        self.log_text.append(self._redact_text(f"❌ {label}\n{trace}"))
        self._set_current_tab_for_widget(self.tab_logs)

    def _auth_config(self) -> AuthConfig:
        return AuthConfig(
            mode=self.auth_mode.currentText(),
            api_key=self.api_key.text().strip(),
            username=self.username.text().strip(),
            password=self.password.text(),
        )

    def komga_api(self) -> KomgaApi:
        return KomgaApi(self.komga_url.text().strip(), auth=self._auth_config(), timeout=self.timeout_seconds.value())

    def _komga_credentials_missing_reason(self) -> str:
        if not self.komga_url.text().strip():
            return "URL Komga absente"
        mode = self.auth_mode.currentText()
        if mode == "api_key" and not self.api_key.text().strip():
            return "clé API Komga absente"
        if mode == "basic" and not self.username.text().strip():
            return "login Komga absent"
        if mode == "basic" and not self.password.text():
            return "mot de passe Komga absent"
        return ""

    def load_libraries_on_startup(self) -> None:
        reason = self._komga_credentials_missing_reason()
        if reason:
            self.log(
                f"ℹ️ Chargement automatique différé : {reason}. "
                "Renseigne la connexion puis clique sur « Sauvegarder les réglages »."
            )
            self.tabs.setCurrentIndex(0)
            self._set_auto_match_progress("Connexion Komga requise", 0, 1)
            return
        self.load_libraries()

    def load_secrets_on_startup(self) -> None:
        def done(config: AppConfig) -> None:
            self.config = config
            self._secrets_loaded = True
            self._config_to_ui()
            self.log("✅ Secrets chargés depuis le coffre système")
            QTimer.singleShot(0, self.load_libraries_on_startup)

        self.run_worker(
            "Chargement secrets",
            lambda: load_config(self.config_path, include_secrets=True),
            done,
        )

    def komf_api(self) -> KomfApi:
        return KomfApi(self.komf_url.text().strip(), timeout=self.komf_timeout_seconds.value())

    def _external_rate_limited_client(self, provider: str, client: Any) -> RateLimitedSourceClient:
        return RateLimitedSourceClient(
            provider,
            client,
            self._external_rate_limit_state[provider],
            self.auto_match_progress_signal.emit,
        )

    def bedetheque_client(self) -> Any:
        if hasattr(self, "bdt_csv_only") and self.bdt_csv_only.isChecked():
            return BedethequeCsvClient(self.bdt_csv_path.text().strip())
        return self._external_rate_limited_client("bedetheque", BedethequeClient(timeout=self.timeout_seconds.value()))

    def mangabaka_client(self) -> RateLimitedSourceClient:
        client = MangaBakaClient(
            base_url=self.mangabaka_base_url.text().strip() or DEFAULT_API_BASE_URL,
            timeout=self.mangabaka_timeout_seconds.value(),
            cache_enabled=self.mangabaka_cache_enabled.isChecked(),
            cache_dir=self.mangabaka_cache_dir.text().strip() or ".komga_db_tool_cache/mangabaka",
        )
        return self._external_rate_limited_client("mangabaka", client)

    def manga_news_client(self) -> RateLimitedSourceClient:
        client = MangaNewsClient(
            base_url=self.manga_news_base_url.text().strip() or DEFAULT_MANGA_NEWS_API_BASE_URL,
            timeout=self.manga_news_timeout_seconds.value(),
            token=self.manga_news_token.text().strip(),
            cache_enabled=self.manga_news_cache_enabled.isChecked(),
            cache_dir=self.manga_news_cache_dir.text().strip() or ".komga_db_tool_cache/manga_news",
            diagnostic_callback=self._write_diagnostic_event if self._diagnostics_enabled() else None,
        )
        return self._external_rate_limited_client("manga_news", client)

    def comicvine_client(self) -> RateLimitedSourceClient:
        client = ComicVineClient(
            base_url=self.comicvine_base_url.text().strip() or DEFAULT_COMICVINE_API_BASE_URL,
            api_key=self.comicvine_api_key.text().strip(),
            timeout=self.comicvine_timeout_seconds.value(),
            cache_enabled=self.comicvine_cache_enabled.isChecked(),
            cache_dir=self.comicvine_cache_dir.text().strip() or ".komga_db_tool_cache/comicvine",
        )
        return self._external_rate_limited_client("comicvine", client)

    def simulation_enabled(self) -> bool:
        return self.simulation_check.isChecked()

    def _make_library_combo(self, name: str) -> QComboBox:
        combo = QComboBox()
        combo.setMinimumWidth(260)
        combo.addItem("Toutes / non filtré", "")
        self.library_combos[name] = combo
        combo.currentIndexChanged.connect(
            lambda _index, combo=combo, name=name: self._on_context_library_changed(name, combo)
        )
        return combo

    def _populate_library_combos(self) -> None:
        previous = {name: combo.currentData() for name, combo in self.library_combos.items()}
        for name, combo in self.library_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Toutes / non filtré", "")
            for lib in self.libraries:
                combo.addItem(f"{lib.name} — {lib.id}", lib.id)
            if previous.get(name):
                idx = combo.findData(previous[name])
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _refresh_explorer_link_filter_options(self, rows: List[Any]) -> None:
        combo = getattr(self, "filter_series_link_label", None)
        if combo is None:
            return
        previous = combo.currentData() or "ALL"
        labels: List[str] = []
        for row in rows:
            metadata = getattr(row, "metadata", {}) or {}
            labels.extend(metadata_link_labels(metadata.get("links")))
        labels = sorted(_dedupe_link_labels(labels), key=lambda value: normalized_link_label(value))

        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Tous", "ALL")
        combo.addItem("Sans lien", "__NO_LINK__")
        for label in labels:
            combo.addItem(label, label)
        idx = combo.findData(previous)
        if idx < 0 and previous not in {"ALL", "__NO_LINK__"}:
            for index in range(combo.count()):
                if normalized_link_label(combo.itemData(index)) == normalized_link_label(previous):
                    idx = index
                    break
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_release_tracking_link_filter_options(self, rows: List[Any]) -> None:
        combo = getattr(self, "rt_filter_link_label", None)
        if combo is None:
            return
        previous = combo.currentData() or "ALL"
        labels: List[str] = []
        for row in rows:
            metadata = getattr(row, "metadata", {}) or {}
            labels.extend(metadata_link_labels(metadata.get("links")))
        labels = sorted(_dedupe_link_labels(labels), key=lambda value: normalized_link_label(value))

        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Tous", "ALL")
        combo.addItem("Sans lien", "__NO_LINK__")
        for label in labels:
            combo.addItem(label, label)
        idx = combo.findData(previous)
        if idx < 0 and previous not in {"ALL", "__NO_LINK__"}:
            for index in range(combo.count()):
                if normalized_link_label(combo.itemData(index)) == normalized_link_label(previous):
                    idx = index
                    break
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _refresh_link_filter_combo(self, combo: Optional[QComboBox], rows: List[Any], fixed_labels: Optional[Iterable[str]] = None) -> None:
        """Refresh a metadata.links filter combo while preserving the previous choice."""
        if combo is None:
            return
        previous = combo.currentData() or "ALL"
        labels: List[str] = []
        fixed = _dedupe_link_labels(list(fixed_labels or []))
        fixed_norms = {normalized_link_label(label) for label in fixed}
        for row in rows or []:
            metadata = getattr(row, "metadata", {}) or {}
            labels.extend(metadata_link_labels(metadata.get("links")))
        labels = [label for label in labels if normalized_link_label(label) not in fixed_norms]
        labels = sorted(_dedupe_link_labels(labels), key=lambda value: normalized_link_label(value))

        combo.blockSignals(True)
        combo.clear()
        combo.addItem("ALL", "ALL")
        combo.addItem("SANS LINK", "__NO_LINK__")
        for label in fixed:
            value = without_link_label_filter_value(label)
            if value:
                combo.addItem(f"SANS {label}", value)
        for label in fixed:
            combo.addItem(str(label), str(label))
        for label in labels:
            combo.addItem(label, label)
        idx = combo.findData(previous)
        if idx < 0 and previous not in {"ALL", "__NO_LINK__"}:
            for index in range(combo.count()):
                if normalized_link_label(combo.itemData(index)) == normalized_link_label(previous):
                    idx = index
                    break
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _make_source_series_filters_row(
        self,
        prefix: str,
        reload_fn: Callable[[], None],
        *,
        fixed_link_labels: Optional[Iterable[str]] = None,
    ) -> QHBoxLayout:
        """Create the common Komga-series filters used inside source tabs."""
        row = QHBoxLayout()
        row.setSpacing(6)
        empty_summary = QCheckBox("Résumé vide")
        empty_summary.setToolTip("Afficher uniquement les séries dont le résumé est vide")
        language = QComboBox()
        language.setMinimumWidth(80)
        language.setToolTip("Filtrer les séries affichées selon metadata.language")
        language.addItem("Toutes", "")
        language.addItem("FR", "fr")
        language.addItem("EN", "en")
        status = QComboBox()
        status.setMinimumWidth(105)
        status.setToolTip("Filtrer les séries affichées selon metadata.status")
        status.addItem("Tous", "ALL")
        for value in SERIES_STATUS_VALUES:
            status.addItem(value, value)
        status.addItem("VIDE", "VIDE")
        link_label = QComboBox()
        link_label.setMinimumWidth(160)
        link_label.setToolTip("Filtrer les séries selon leurs liens externes")
        link_label.addItem("Tous", "ALL")
        link_label.addItem("Sans lien", "__NO_LINK__")
        for label in fixed_link_labels or []:
            without_value = without_link_label_filter_value(label)
            if without_value:
                link_label.addItem(f"SANS {label}", without_value)
            link_label.addItem(str(label), str(label))
        recent_search = QComboBox()
        recent_search.setMinimumWidth(190)
        recent_search.setToolTip("Masquer les séries déjà recherchées récemment avec cette source")
        recent_search.addItem("Toutes les recherches", 0)
        recent_search.addItem("Exclure recherchées < 7 j", 7)
        recent_search.addItem("Exclure recherchées < 15 j", 15)
        recent_search.addItem("Exclure recherchées < 30 j", 30)
        recent_search_all = QComboBox()
        recent_search_all.setMinimumWidth(190)
        recent_search_all.setToolTip("Masquer les séries recherchées récemment dans n'importe quelle source")
        recent_search_all.addItem("Toutes les recherches", 0)
        recent_search_all.addItem("Exclure recherchées < 7 j", 7)
        recent_search_all.addItem("Exclure recherchées < 15 j", 15)
        recent_search_all.addItem("Exclure recherchées < 30 j", 30)

        setattr(self, f"{prefix}_filter_empty_summary", empty_summary)
        setattr(self, f"{prefix}_filter_language", language)
        setattr(self, f"{prefix}_filter_status", status)
        setattr(self, f"{prefix}_filter_link_label", link_label)
        setattr(self, f"{prefix}_filter_recent_search", recent_search)
        setattr(self, f"{prefix}_filter_recent_search_all", recent_search_all)
        if not hasattr(self, "_source_filter_fixed_link_labels"):
            self._source_filter_fixed_link_labels = {}
        self._source_filter_fixed_link_labels[prefix] = list(fixed_link_labels or [])

        row.addWidget(QLabel("Filtres"))
        row.addWidget(empty_summary)
        row.addWidget(QLabel("Langue"))
        row.addWidget(language)
        row.addWidget(QLabel("Statut"))
        row.addWidget(status)
        row.addWidget(QLabel("Liens"))
        row.addWidget(link_label)
        row.addWidget(QLabel("Historique source"))
        row.addWidget(recent_search)
        row.addWidget(QLabel("Toutes sources"))
        row.addWidget(recent_search_all)
        row.addStretch(1)

        empty_summary.stateChanged.connect(lambda *_: self._on_source_series_filter_changed(prefix, reload_fn))
        language.currentIndexChanged.connect(lambda *_: self._on_source_series_filter_changed(prefix, reload_fn))
        status.currentIndexChanged.connect(lambda *_: self._on_source_series_filter_changed(prefix, reload_fn))
        link_label.currentIndexChanged.connect(lambda *_: self._on_source_series_filter_changed(prefix, reload_fn))
        recent_search.currentIndexChanged.connect(lambda *_: self._on_source_series_filter_changed(prefix, reload_fn))
        recent_search_all.currentIndexChanged.connect(lambda *_: self._on_source_series_filter_changed(prefix, reload_fn))
        return row

    def _refresh_source_link_filter_options(self, prefix: str, rows: List[Any]) -> None:
        combo = getattr(self, f"{prefix}_filter_link_label", None)
        fixed = getattr(self, "_source_filter_fixed_link_labels", {}).get(prefix, []) if hasattr(self, "_source_filter_fixed_link_labels") else []
        self._refresh_link_filter_combo(combo, rows, fixed)

    def _source_series_view_config(self, prefix: str) -> Dict[str, Any]:
        return {
            "bdt": {
                "table": "bdt_komga_series_table",
                "rows": "bdt_komga_series_rows",
                "name": "Bedetheque",
                "selection": QAbstractItemView.ExtendedSelection,
            },
            "mbk": {"table": "mbk_komga_series_table", "rows": "mbk_komga_series_rows", "name": "MangaBaka"},
            "mn": {"table": "mn_komga_series_table", "rows": "mn_komga_series_rows", "name": "Manga News"},
            "cv": {"table": "cv_komga_series_table", "rows": "cv_komga_series_rows", "name": "ComicVine"},
        }.get(prefix, {})

    def _display_source_series_rows(self, prefix: str, rows: List[Any], *, log_result: bool = True) -> None:
        config = self._source_series_view_config(prefix)
        table = getattr(self, str(config.get("table", "")), None)
        rows_attribute = str(config.get("rows", ""))
        if table is None or not rows_attribute:
            return
        filtered_rows, active_filters = self._apply_source_series_filters(prefix, rows)
        setattr(self, rows_attribute, filtered_rows)
        self._set_table(
            table,
            self._series_table_headers(include_history=True),
            self._series_table_rows_for_source(prefix, filtered_rows),
            stretch_from=1,
            selection_mode=config.get("selection"),
        )
        if log_result:
            suffix = f" - {', '.join(active_filters)}" if active_filters else ""
            self.log(f"{config.get('name', prefix)} : {len(filtered_rows)} series Komga affichees{suffix}")

    def _on_source_series_filter_changed(self, prefix: str, reload_fn: Callable[[], None]) -> None:
        rows = self._source_series_unfiltered_rows.get(prefix)
        if rows is None:
            reload_fn()
            return
        self._display_source_series_rows(prefix, rows)

    def _apply_source_series_filters(self, prefix: str, rows: List[Any]) -> tuple[List[Any], List[str]]:
        rows = list(rows or [])
        empty_summary = getattr(self, f"{prefix}_filter_empty_summary", None)
        language_combo = getattr(self, f"{prefix}_filter_language", None)
        status_combo = getattr(self, f"{prefix}_filter_status", None)
        link_combo = getattr(self, f"{prefix}_filter_link_label", None)
        recent_combo = getattr(self, f"{prefix}_filter_recent_search", None)
        recent_all_combo = getattr(self, f"{prefix}_filter_recent_search_all", None)
        language_filter = language_combo.currentData() if language_combo is not None else ""
        status_filter = status_combo.currentData() if status_combo is not None else "ALL"
        link_filter = link_combo.currentData() if link_combo is not None else "ALL"
        recent_days = int(recent_combo.currentData() or 0) if recent_combo is not None else 0
        recent_all_days = int(recent_all_combo.currentData() or 0) if recent_all_combo is not None else 0

        active_filters: List[str] = []
        if empty_summary is not None and empty_summary.isChecked():
            rows = [x for x in rows if is_blank_metadata_value((getattr(x, "metadata", {}) or {}).get("summary"))]
            active_filters.append("summary vide")
        if language_filter:
            rows = [x for x in rows if metadata_language_matches((getattr(x, "metadata", {}) or {}).get("language"), language_filter)]
            active_filters.append(f"langue={str(language_filter).upper()}")
        if status_filter and normalized_status_code(status_filter) != "ALL":
            rows = [x for x in rows if metadata_status_matches((getattr(x, "metadata", {}) or {}).get("status"), status_filter)]
            active_filters.append(f"status={normalized_status_code(status_filter)}")
        if link_filter and normalized_link_label(link_filter) != "all":
            rows = [x for x in rows if metadata_link_filter_matches((getattr(x, "metadata", {}) or {}).get("links"), link_filter)]
            display = link_combo.currentText() if link_combo is not None else str(link_filter)
            active_filters.append(f"links={display}")
        if recent_days > 0:
            source = self._enrichment_source_for_prefix(prefix)
            history = self.enrichment_history.last_searches(source, [getattr(x, "id", "") for x in rows])
            cutoff = datetime.now().astimezone().timestamp() - (recent_days * 86400)
            rows = [
                x
                for x in rows
                if getattr(x, "id", "") not in history or history[getattr(x, "id", "")].timestamp() < cutoff
            ]
            active_filters.append(f"source: pas recherchée depuis {recent_days} j")
        if recent_all_days > 0:
            history_all = self.enrichment_history.last_searches_any_source(
                [getattr(x, "id", "") for x in rows]
            )
            cutoff = datetime.now().astimezone().timestamp() - (recent_all_days * 86400)
            rows = [
                x
                for x in rows
                if getattr(x, "id", "") not in history_all
                or history_all[getattr(x, "id", "")].timestamp() < cutoff
            ]
            active_filters.append(f"toutes sources: pas recherchée depuis {recent_all_days} j")
        return rows, active_filters

    def _enrichment_source_for_prefix(self, prefix: str) -> str:
        return {"bdt": "bedetheque", "mbk": "mangabaka", "mn": "manga_news", "cv": "comicvine"}.get(prefix, prefix)

    def _series_table_rows_for_source(self, prefix: str, rows: List[Any]) -> List[List[Any]]:
        series_ids = [getattr(x, "id", "") for x in rows]
        history = self.enrichment_history.last_searches(
            self._enrichment_source_for_prefix(prefix),
            series_ids,
        )
        history_all = self.enrichment_history.last_searches_any_source(series_ids)
        return [
            self._series_table_row(x)
            + [
                format_search_timestamp(history.get(str(getattr(x, "id", "")))),
                format_search_timestamp(history_all.get(str(getattr(x, "id", "")))),
            ]
            for x in rows
        ]

    def _series_title_for_enrichment_id(self, prefix: str, series_id: str) -> str:
        attribute = {
            "bdt": "bdt_komga_series_rows",
            "mbk": "mbk_komga_series_rows",
            "mn": "mn_komga_series_rows",
            "cv": "cv_komga_series_rows",
        }.get(prefix)
        for row in getattr(self, attribute, []) if attribute else []:
            if str(getattr(row, "id", "")) == str(series_id or ""):
                return str(getattr(row, "title", ""))
        return ""

    def _record_enrichment_search(self, prefix: str, series_id: str) -> None:
        if not series_id:
            return
        timestamp = datetime.now().astimezone()
        self.enrichment_history.record_search(
            self._enrichment_source_for_prefix(prefix),
            series_id,
            self._series_title_for_enrichment_id(prefix, series_id),
            searched_at=timestamp,
        )
        mappings = {
            "bdt": ("bdt_komga_series_table", "bdt_komga_series_rows"),
            "mbk": ("mbk_komga_series_table", "mbk_komga_series_rows"),
            "mn": ("mn_komga_series_table", "mn_komga_series_rows"),
            "cv": ("cv_komga_series_table", "cv_komga_series_rows"),
        }
        display = format_search_timestamp(timestamp)
        for candidate_prefix, (table_attribute, rows_attribute) in mappings.items():
            table = getattr(self, table_attribute, None)
            rows = getattr(self, rows_attribute, [])
            if table is None:
                continue
            for index, row in enumerate(rows):
                if str(getattr(row, "id", "")) != str(series_id):
                    continue
                source_column = self._table_headers(table).index("Dernière recherche source")
                global_column = self._table_headers(table).index("Dernière recherche globale")
                if candidate_prefix == prefix:
                    source_item = QTableWidgetItem(display)
                    source_item.setFlags(source_item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(index, source_column, source_item)
                global_item = QTableWidgetItem(display)
                global_item.setFlags(global_item.flags() & ~Qt.ItemIsEditable)
                table.setItem(index, global_column, global_item)
                break

    def _show_chap_scan_series(self) -> bool:
        checkbox = getattr(self, "show_chap_scan_series", None)
        if checkbox is not None:
            return checkbox.isChecked()
        ui = getattr(self.config, "ui", {}) if isinstance(getattr(self.config, "ui", {}), dict) else {}
        return bool(ui.get("show_chap_scan_series", False))

    def _series_url_for_chap_scan_filter(self, series: Any) -> str:
        metadata = getattr(series, "metadata", {}) if isinstance(getattr(series, "metadata", {}), dict) else {}
        raw = getattr(series, "raw", {}) if isinstance(getattr(series, "raw", {}), dict) else {}
        for source in (metadata, raw):
            value = source.get("url") if isinstance(source, dict) else ""
            if value:
                return str(value)
        return ""

    def _is_chap_scan_series(self, series: Any) -> bool:
        return quality_path_has_chap_scan_segment(self._series_url_for_chap_scan_filter(series))

    def _filter_global_series_visibility(self, rows: List[Any]) -> List[Any]:
        visible = list(rows or [])
        if not self._show_chap_scan_series():
            visible = [row for row in visible if not self._is_chap_scan_series(row)]

        excluded_libraries = {name.casefold() for name in self.local_exclusions.excluded_library_names()}
        library_names = {
            str(getattr(library, "id", "")): str(getattr(library, "name", ""))
            for library in self.libraries
        }
        visible = [
            row
            for row in visible
            if library_names.get(str(getattr(row, "library_id", "")), "").casefold() not in excluded_libraries
        ]
        return list(self.local_exclusions.filter_records(visible))

    def _next_series_load_generation(self, key: str) -> int:
        """Return a new generation number for an async series reload."""
        value = int(self._series_load_generation.get(key, 0)) + 1
        self._series_load_generation[key] = value
        return value

    def _is_current_series_load_generation(self, key: str, generation: int) -> bool:
        return int(self._series_load_generation.get(key, 0)) == int(generation)

    def _reload_series_views_after_visibility_change(self) -> None:
        for loader_name in (
            "load_series",
            "load_metadata_targets",
            "load_bedetheque_komga_series",
            "load_mangabaka_komga_series",
            "load_manga_news_komga_series",
            "load_comicvine_komga_series",
            "load_release_tracking_series",
        ):
            loader = getattr(self, loader_name, None)
            if callable(loader):
                try:
                    loader()
                except Exception as exc:
                    self.log(f"⚠️ Rechargement {loader_name} impossible : {exc}")

    def _library_id(self, name: str) -> str:
        combo = self.library_combos.get(name)
        return combo.currentData() if combo else ""

    def _set_library_combo(self, name: str, library_id: str) -> None:
        if not library_id:
            return
        combo = self.library_combos.get(name)
        if combo is None:
            return
        index = combo.findData(library_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _on_context_library_changed(self, _name: str, combo: QComboBox) -> None:
        library_id = str(combo.currentData() or "")
        library_name = "Toutes les bibliothèques"
        if library_id:
            library_name = next(
                (
                    str(getattr(library, "name", "") or library_id)
                    for library in self.libraries
                    if str(getattr(library, "id", "")) == library_id
                ),
                combo.currentText().split(" — ", 1)[0] or library_id,
            )
        if library_id != self._active_context_library_id:
            self._active_context_series_title = ""
            self._active_context_series_id = ""
            self._active_context_book_title = ""
            self._active_context_book_id = ""
        self._active_context_library_id = library_id
        self._active_context_library_name = library_name
        self._refresh_context_header()

    def _set_context_selection(self, *, series: Any = None, book: Any = None) -> None:
        if series is not None:
            self._active_context_series_title = str(getattr(series, "title", "") or "")
            self._active_context_series_id = str(getattr(series, "id", "") or "")
            self._active_context_book_title = ""
            self._active_context_book_id = ""
        if book is not None:
            self._active_context_book_title = str(getattr(book, "title", "") or "")
            self._active_context_book_id = str(getattr(book, "id", "") or "")
        self._refresh_context_header()

    def _refresh_context_header(self) -> None:
        if not hasattr(self, "context_page_label"):
            return
        current_title = "Accueil"
        if hasattr(self, "tabs") and self.tabs.currentIndex() >= 0:
            current_title = self.tabs.tabText(self.tabs.currentIndex()) or current_title
        self.context_page_label.setText(f"Espace : {current_title}")
        connection_text = "Komga connecté" if self._komga_connection_validated else "Komga non validé"
        self.context_connection_label.setText(connection_text)
        self.context_library_label.setText(f"Bibliothèque : {self._active_context_library_name}")
        series_text = self._active_context_series_title or "aucune"
        book_text = self._active_context_book_title or "aucun"
        self.context_series_label.setText(f"Série : {series_text}")
        self.context_book_label.setText(f"Tome : {book_text}")
        if hasattr(self, "context_mode_toggle"):
            simulation = self.context_mode_toggle.isChecked()
            self.context_mode_toggle.setText(
                "Mode sécurisé : simulation" if simulation else "Écriture réelle : sauvegarde avant modification"
            )
            color = "#1f6f43" if simulation else "#8a4b08"
            # Avoid reparsing a QSS rule whenever the context changes. Some
            # Windows Qt styles rejected that rule repeatedly and printed
            # "Could not parse stylesheet of object QCheckBox(...)".
            font = self.context_mode_toggle.font()
            font.setBold(True)
            self.context_mode_toggle.setFont(font)
            palette = self.context_mode_toggle.palette()
            palette.setColor(QPalette.WindowText, QColor("white"))
            palette.setColor(QPalette.Window, QColor(color))
            self.context_mode_toggle.setAutoFillBackground(True)
            self.context_mode_toggle.setPalette(palette)
            self.context_mode_toggle.setContentsMargins(10, 6, 10, 6)
        if hasattr(self, "enrichment_context_label"):
            if self._active_context_series_title:
                self.enrichment_context_label.setText(
                    f"Série prête pour l'enrichissement : {self._active_context_series_title}"
                )
            else:
                self.enrichment_context_label.setText(
                    "Aucune série sélectionnée. Commencez par choisir une série dans l'Explorateur."
                )
        for source_name, label in getattr(self, "_source_context_labels", {}).items():
            if self._active_context_series_title:
                label.setText(f"Cible actuelle : {self._active_context_series_title}")
            else:
                label.setText(f"Aucune série cible pour {source_name}. Sélectionnez-en une dans l'Explorateur.")
        if hasattr(self, "kora_context_label"):
            self.kora_context_label.setText(
                f"Série active : {self._active_context_series_title}"
                if self._active_context_series_title
                else "Aucune série active. Kora peut néanmoins travailler sur toute une bibliothèque."
            )
        self._refresh_health_dashboard()

    def _sync_context_mode_from_header(self, checked: bool) -> None:
        if hasattr(self, "simulation_check") and self.simulation_check.isChecked() != checked:
            self.simulation_check.setChecked(checked)
        self._refresh_context_header()

    def _sync_context_mode_from_settings(self, checked: bool) -> None:
        if hasattr(self, "context_mode_toggle") and self.context_mode_toggle.isChecked() != checked:
            self.context_mode_toggle.setChecked(checked)
        if hasattr(self, "meta_apply_button"):
            self.meta_apply_button.setText("Terminer la simulation" if checked else "Appliquer réellement")
        self._refresh_context_header()

    def _set_current_tab_by_title(self, title: str) -> None:
        registered = self._main_tab_indices.get(title)
        if registered is not None:
            self.tabs.setCurrentIndex(registered)
            return
        for index in range(self.tabs.count()):
            if self.tabs.tabText(index) == title:
                self.tabs.setCurrentIndex(index)
                return

    def _set_current_tab_for_widget(self, widget: QWidget) -> None:
        index = self._main_tab_source_widgets.get(id(widget))
        if index is not None:
            self.tabs.setCurrentIndex(index)

    def _selected_id_from_table(self, table: QTableWidget) -> str:
        row = self._selected_row_index(table)
        if row < 0:
            return ""
        item = table.item(row, 0)
        return item.text() if item else ""

    def _selected_row_index(self, table: QTableWidget) -> int:
        rows = sorted({i.row() for i in table.selectedIndexes()})
        current = table.currentRow()
        if current >= 0 and (not rows or current in rows):
            return current
        return rows[0] if rows else -1

    def _selected_row_indexes(self, table: QTableWidget) -> List[int]:
        return sorted({i.row() for i in table.selectedIndexes()})

    def _selected_ids_from_table(self, table: QTableWidget) -> List[str]:
        ids: List[str] = []
        for row in self._selected_row_indexes(table):
            item = table.item(row, 0)
            value = item.text().strip() if item else ""
            if value and value not in ids:
                ids.append(value)
        return ids

    def _selected_row_data(self, table: QTableWidget) -> Any:
        row = self._selected_row_index(table)
        if row < 0:
            return None
        item = table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _reset_table_to_first_row(self, table: QTableWidget, *, select: bool = True) -> None:
        table.clearSelection()
        table.scrollToTop()
        if not select or table.rowCount() <= 0 or table.columnCount() <= 0:
            return
        table.setCurrentCell(0, 0)
        table.selectRow(0)
        item = table.item(0, 0)
        if item is not None:
            table.scrollToItem(item, QAbstractItemView.PositionAtTop)

    def _ensure_min_table_visible_rows(self, table: QTableWidget, rows: int = MIN_TABLE_VISIBLE_ROWS) -> None:
        header_height = table.horizontalHeader().height() if table.horizontalHeader() else 24
        row_height = max(table.verticalHeader().defaultSectionSize(), 28)
        frame = table.frameWidth() * 2 + 10
        table.setMinimumHeight(header_height + (row_height * max(1, rows)) + frame)

    def _configure_resizable_table(
        self,
        table: QTableWidget,
        stretch_from: Optional[int] = None,
        *,
        restore_state: bool = True,
    ) -> None:
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setWordWrap(False)
        self._ensure_min_table_visible_rows(table)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        for col in range(table.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            width = table.columnWidth(col)
            if width < 70:
                table.setColumnWidth(col, 90)
            elif width > 560:
                table.setColumnWidth(col, 560)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.verticalHeader().setDefaultSectionSize(28)
        self._install_table_header_context_menu(table)
        self._install_table_viewport_fill(table, stretch_from)
        if restore_state:
            self._restore_table_ui_state(table)
        self._schedule_table_viewport_fill(table)

    def _default_table_fill_columns(self, table: QTableWidget, stretch_from: Optional[int]) -> List[int]:
        preferred_labels = {
            "Titre",
            "Nom",
            "Actuel",
            "Nouveau",
            "Valeur",
            "Résumé",
            "Summary",
            "Détails",
            "Notes",
            "Erreur",
            "Match",
            "Série Komga",
        }
        headers = self._table_headers(table)
        preferred = [
            index
            for index, label in enumerate(headers)
            if label in preferred_labels and not table.isColumnHidden(index)
        ]
        if preferred:
            return preferred
        if stretch_from is not None and 0 <= stretch_from < table.columnCount():
            return [stretch_from]
        return [0] if table.columnCount() else []

    def _install_table_viewport_fill(self, table: QTableWidget, stretch_from: Optional[int]) -> None:
        table.setProperty("komgaFillColumns", self._default_table_fill_columns(table, stretch_from))
        viewport = table.viewport()
        viewport_key = id(viewport)
        if viewport_key not in self._table_viewports:
            self._table_viewports[viewport_key] = table
            viewport.installEventFilter(self)
            viewport.destroyed.connect(
                lambda *_args, key=viewport_key: self._table_viewports.pop(key, None)
            )

    def _schedule_table_viewport_fill(self, table: QTableWidget) -> None:
        QTimer.singleShot(0, lambda t=table: self._fill_table_to_viewport(t))

    def _fill_table_to_viewport(self, table: QTableWidget) -> None:
        if table.columnCount() <= 0 or table.viewport().width() <= 0:
            return
        visible = [column for column in range(table.columnCount()) if not table.isColumnHidden(column)]
        if not visible:
            return
        flexible = [
            int(column)
            for column in (table.property("komgaFillColumns") or [])
            if int(column) in visible
        ]
        if not flexible:
            flexible = [visible[-1]]
        widths = [table.columnWidth(column) if column in visible else 0 for column in range(table.columnCount())]
        expanded = expanded_column_widths(widths, max(0, table.viewport().width() - 4), flexible)
        if expanded == widths:
            return
        self._restoring_table_ui_state = True
        try:
            for column in visible:
                if expanded[column] != widths[column]:
                    table.setColumnWidth(column, expanded[column])
        finally:
            self._restoring_table_ui_state = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Resize:
            table = self._table_viewports.get(id(watched))
            if table is not None:
                self._schedule_table_viewport_fill(table)
        return super().eventFilter(watched, event)

    def _register_table(self, table: QTableWidget, key: str, default_hidden: Optional[Iterable[str]] = None) -> None:
        table.setObjectName(key)
        table.setProperty("komgaTableKey", key)
        self._registered_tables[key] = table
        self._table_default_hidden[key] = set(default_hidden or [])
        self._ensure_min_table_visible_rows(table)
        self._install_table_header_context_menu(table)

    def _register_series_table_rows(self, table: QTableWidget, rows_attribute: str) -> None:
        key = self._table_key(table)
        if key:
            self._series_table_row_attributes[key] = rows_attribute
        table.setProperty("komgaSeriesRowsAttribute", rows_attribute)

    def _series_rows_for_table(self, table: QTableWidget) -> List[Any]:
        attribute = str(table.property("komgaSeriesRowsAttribute") or "")
        return list(getattr(self, attribute, []) or []) if attribute else []

    @staticmethod
    def _metadata_display_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, list):
            parts: List[str] = []
            for entry in value:
                if isinstance(entry, dict):
                    name = str(entry.get("name") or entry.get("title") or entry.get("label") or "").strip()
                    role = str(entry.get("role") or "").strip()
                    url = str(entry.get("url") or "").strip()
                    text = name or url or one_line(entry)
                    if role and name:
                        text = f"{name} ({role})"
                    elif url and name:
                        text = f"{name}: {url}"
                    parts.append(text)
                else:
                    parts.append(str(entry))
            return "; ".join(part for part in parts if part)
        if isinstance(value, dict):
            return "; ".join(f"{key}: {item}" for key, item in value.items())
        return str(value)

    def _configured_table_fields(self, target_type: str) -> List[str]:
        if target_type == "series":
            options = SERIES_TABLE_FIELD_OPTIONS
            defaults = DEFAULT_SERIES_TABLE_FIELDS
            checks = getattr(self, "series_table_field_checks", {})
            config_key = "series_table_fields"
        else:
            options = BOOK_TABLE_FIELD_OPTIONS
            defaults = DEFAULT_BOOK_TABLE_FIELDS
            checks = getattr(self, "book_table_field_checks", {})
            config_key = "book_table_fields"
        allowed = [key for key, _label in options]
        if checks:
            return [key for key in allowed if key in checks and checks[key].isChecked()]
        ui = self.config.ui if isinstance(getattr(self.config, "ui", None), dict) else {}
        configured = ui.get(config_key)
        if not isinstance(configured, list):
            configured = defaults
        return [key for key in allowed if key in configured]

    def _series_field_value(self, series: Any, field: str) -> str:
        if isinstance(series, dict):
            metadata = series.get("metadata") if isinstance(series.get("metadata"), dict) else {}
            raw = series
        else:
            metadata = getattr(series, "metadata", {}) if isinstance(getattr(series, "metadata", {}), dict) else {}
            raw = getattr(series, "raw", {}) if isinstance(getattr(series, "raw", {}), dict) else {}
        books_metadata = raw.get("booksMetadata") if isinstance(raw.get("booksMetadata"), dict) else {}
        value = books_metadata.get(field) if field in {"authors", "releaseDate"} else metadata.get(field)
        if field in {"authors", "releaseDate"} and is_blank_metadata_value(value):
            value = metadata.get(field)
        return self._metadata_display_value(value)

    def _series_table_headers(self, *, include_library: bool = False, include_history: bool = False) -> List[str]:
        labels = dict(SERIES_TABLE_FIELD_OPTIONS)
        headers = ["ID", "Titre", "Livres"]
        if include_library:
            headers.append("Library")
        headers.extend(labels[field] for field in self._configured_table_fields("series"))
        if include_history:
            headers.extend(["Dernière recherche source", "Dernière recherche globale"])
        return headers

    def _series_table_row(self, series: Any, *, include_library: bool = False) -> List[Any]:
        if isinstance(series, dict):
            metadata = series.get("metadata") if isinstance(series.get("metadata"), dict) else {}
            library = series.get("library") if isinstance(series.get("library"), dict) else {}
            row: List[Any] = [
                series.get("id", ""),
                metadata.get("title") or series.get("name") or series.get("title") or "",
                series.get("booksCount") or series.get("bookCount") or "",
            ]
            library_id = series.get("libraryId") or library.get("id") or ""
        else:
            row = [
                getattr(series, "id", ""),
                getattr(series, "title", ""),
                getattr(series, "book_count", ""),
            ]
            library_id = getattr(series, "library_id", "")
        if include_library:
            row.append(library_id)
        row.extend(self._series_field_value(series, field) for field in self._configured_table_fields("series"))
        return row

    @staticmethod
    def _record_id(record: Any) -> str:
        if isinstance(record, dict):
            return str(record.get("id") or "")
        return str(getattr(record, "id", "") or "")

    def _book_metadata_map(self, book: Any) -> Dict[str, Any]:
        if isinstance(book, dict):
            return book.get("metadata") if isinstance(book.get("metadata"), dict) else {}
        metadata = getattr(book, "metadata", {})
        return metadata if isinstance(metadata, dict) else {}

    def _book_table_headers(self, *, include_series: bool = False, include_library: bool = False) -> List[str]:
        labels = dict(BOOK_TABLE_FIELD_OPTIONS)
        headers = ["ID", "Titre", "Numéro"]
        if include_series:
            headers.append("Series")
        if include_library:
            headers.append("Library")
        headers.extend(labels[field] for field in self._configured_table_fields("book"))
        return headers

    def _book_table_row(self, book: Any, *, include_series: bool = False, include_library: bool = False) -> List[Any]:
        metadata = self._book_metadata_map(book)
        if isinstance(book, dict):
            book_id = book.get("id", "")
            title = metadata.get("title") or book.get("name", "")
            number = metadata.get("number", "")
            series_id = book.get("seriesId") or (book.get("series") or {}).get("id", "")
            library_id = book.get("libraryId") or (book.get("library") or {}).get("id", "")
        else:
            book_id = getattr(book, "id", "")
            title = getattr(book, "title", "")
            number = getattr(book, "number", "")
            series_id = getattr(book, "series_id", "")
            library_id = getattr(book, "library_id", "")
        row: List[Any] = [book_id, title, number]
        if include_series:
            row.append(series_id)
        if include_library:
            row.append(library_id)
        row.extend(
            self._metadata_display_value(metadata.get(field))
            for field in self._configured_table_fields("book")
        )
        return row

    def _refresh_loaded_tables_for_display_fields(self) -> None:
        if hasattr(self, "series_table"):
            self._set_table(
                self.series_table,
                self._series_table_headers(include_library=True),
                [self._series_table_row(row, include_library=True) for row in self.series_rows],
                selection_mode=QAbstractItemView.ExtendedSelection,
            )
        if hasattr(self, "books_table"):
            self._set_table(
                self.books_table,
                self._book_table_headers(include_series=True, include_library=True),
                [self._book_table_row(row, include_series=True, include_library=True) for row in self.book_rows],
            )
        for prefix, table_name, rows_name in (
            ("bdt", "bdt_komga_series_table", "bdt_komga_series_rows"),
            ("mbk", "mbk_komga_series_table", "mbk_komga_series_rows"),
            ("mn", "mn_komga_series_table", "mn_komga_series_rows"),
            ("cv", "cv_komga_series_table", "cv_komga_series_rows"),
        ):
            table = getattr(self, table_name, None)
            rows = getattr(self, rows_name, [])
            if table is not None:
                self._set_table(
                    table,
                    self._series_table_headers(include_history=True),
                    self._series_table_rows_for_source(prefix, rows),
                    stretch_from=1,
                    selection_mode=QAbstractItemView.ExtendedSelection if prefix == "bdt" else None,
                )
        if hasattr(self, "rt_series_table"):
            self._set_table(
                self.rt_series_table,
                self._series_table_headers(include_library=True),
                [self._series_table_row(row, include_library=True) for row in self.rt_series_rows],
                selection_mode=QAbstractItemView.ExtendedSelection,
            )
        if hasattr(self, "collection_members_table"):
            self._set_table(
                self.collection_members_table,
                self._series_table_headers(),
                [self._series_table_row(row) for row in self.collection_member_rows],
            )
        if hasattr(self, "bdt_komga_books_table"):
            self._set_table(
                self.bdt_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(row) for row in self.bdt_komga_book_rows],
            )
        if hasattr(self, "readlist_books_table"):
            self._set_table(
                self.readlist_books_table,
                self._book_table_headers(include_series=True),
                [self._book_table_row(row, include_series=True) for row in self.readlist_book_rows],
            )

    def _table_headers(self, table: QTableWidget) -> List[str]:
        headers: List[str] = []
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            headers.append(item.text() if item else f"Colonne {col + 1}")
        return headers

    def _table_state_root(self) -> Dict[str, Any]:
        if not isinstance(getattr(self.config, "ui", None), dict):
            self.config.ui = {}
        ui = self.config.ui
        tables = ui.setdefault("tables", {})
        if not isinstance(tables, dict):
            ui["tables"] = {}
        return ui["tables"]

    def _table_key(self, table: QTableWidget) -> str:
        return str(table.property("komgaTableKey") or table.objectName() or "")

    def _restore_table_ui_state(self, table: QTableWidget) -> None:
        key = self._table_key(table)
        if not key or table.columnCount() <= 0:
            return
        states = self._table_state_root()
        state = states.get(key) if isinstance(states.get(key), dict) else {}
        headers = self._table_headers(table)
        header = table.horizontalHeader()
        self._restoring_table_ui_state = True
        try:
            order = state.get("order") if isinstance(state.get("order"), list) else []
            if order:
                for target_visual, label in enumerate(order):
                    if label in headers:
                        logical = headers.index(label)
                        current_visual = header.visualIndex(logical)
                        if current_visual >= 0 and current_visual != target_visual:
                            header.moveSection(current_visual, target_visual)
            widths = state.get("widths") if isinstance(state.get("widths"), dict) else {}
            for col, label in enumerate(headers):
                width = widths.get(label)
                try:
                    width_int = int(width)
                except (TypeError, ValueError):
                    width_int = 0
                if width_int > 0:
                    table.setColumnWidth(col, max(40, min(width_int, 1400)))
            hidden_labels = set(state.get("hidden", [])) if isinstance(state.get("hidden"), list) else set()
            if not state:
                hidden_labels = set(self._table_default_hidden.get(key, set()))
            for col, label in enumerate(headers):
                table.setColumnHidden(col, label in hidden_labels)
        finally:
            self._restoring_table_ui_state = False

    def _install_table_header_context_menu(self, table: QTableWidget) -> None:
        header = table.horizontalHeader()
        if header.property("komgaHeaderContextMenuInstalled"):
            return
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(lambda point, t=table: self.show_table_header_context_menu(t, point))
        header.sectionResized.connect(lambda *_args, t=table: self._schedule_table_ui_state_save())
        header.sectionMoved.connect(lambda *_args, t=table: self._schedule_table_ui_state_save())
        header.setProperty("komgaHeaderContextMenuInstalled", True)

    def show_table_header_context_menu(self, table: QTableWidget, point: Any) -> None:
        if table.columnCount() <= 0:
            return
        menu = QMenu(self)
        action_fit = menu.addAction("Ajuster colonnes")
        action_show_all = menu.addAction("Tout afficher")
        action_save = menu.addAction("Sauvegarder largeurs")
        menu.addSeparator()
        column_actions: Dict[Any, int] = {}
        for col, label in enumerate(self._table_headers(table)):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not table.isColumnHidden(col))
            column_actions[action] = col
        selected = menu.exec(table.horizontalHeader().mapToGlobal(point))
        if selected is None:
            return
        if selected == action_fit:
            table.resizeColumnsToContents()
            self._configure_resizable_table(table, restore_state=False)
            self._fill_table_to_viewport(table)
            self._save_table_ui_state(table)
            return
        if selected == action_show_all:
            for col in range(table.columnCount()):
                table.setColumnHidden(col, False)
            table.setProperty("komgaFillColumns", self._default_table_fill_columns(table, None))
            self._fill_table_to_viewport(table)
            self._save_table_ui_state(table)
            return
        if selected == action_save:
            self._save_table_ui_state(table)
            self.log("✅ Largeurs/colonnes sauvegardées")
            return
        if selected in column_actions:
            col = column_actions[selected]
            table.setColumnHidden(col, not selected.isChecked())
            table.setProperty("komgaFillColumns", self._default_table_fill_columns(table, None))
            self._fill_table_to_viewport(table)
            self._save_table_ui_state(table)

    def _save_table_ui_state(self, table: QTableWidget) -> None:
        key = self._table_key(table)
        if not key or table.columnCount() <= 0:
            return
        headers = self._table_headers(table)
        header = table.horizontalHeader()
        order: List[str] = []
        for visual in range(table.columnCount()):
            logical = header.logicalIndex(visual)
            if 0 <= logical < len(headers):
                order.append(headers[logical])
        state = {
            "widths": {label: int(table.columnWidth(col)) for col, label in enumerate(headers)},
            "hidden": [label for col, label in enumerate(headers) if table.isColumnHidden(col)],
            "order": order,
        }
        self._table_state_root()[key] = state
        save_config(self.config, self.config_path)

    def _save_all_table_ui_states(self) -> None:
        if self._restoring_table_ui_state:
            return
        for table in list(self._registered_tables.values()):
            if table is not None and table.columnCount() > 0:
                self._save_table_ui_state(table)

    def _schedule_table_ui_state_save(self) -> None:
        if self._restoring_table_ui_state:
            return
        if hasattr(self, "_table_state_save_timer"):
            self._table_state_save_timer.start(700)

    def _ensure_generic_table_context_menu(self, table: QTableWidget) -> None:
        if table.property("komgaGenericContextMenuInstalled"):
            return
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(lambda point, t=table: self.show_table_cell_context_menu(t, point))
        table.setProperty("komgaGenericContextMenuInstalled", True)

    def show_table_cell_context_menu(self, table: QTableWidget, point: Any) -> None:
        row = table.rowAt(point.y())
        col = table.columnAt(point.x())
        if row < 0 or col < 0:
            return
        item = table.item(row, col)
        if item is None:
            return
        if not item.isSelected():
            table.selectRow(row)
        menu = QMenu(self)
        action_copy = menu.addAction("Copier")
        action_edit = menu.addAction("Modifier…")
        action_ignore = None
        action_summary_from_tome1 = None
        if self._series_rows_for_table(table):
            menu.addSeparator()
            action_summary_from_tome1 = menu.addAction("Remplir summary série depuis le tome 1")
            action_ignore = menu.addAction("Ignorer la série partout")
        selected = menu.exec(table.viewport().mapToGlobal(point))
        if selected == action_copy:
            self._copy_table_cell(item)
        elif selected == action_edit:
            self._edit_table_cell_dialog(table, item)
        elif action_summary_from_tome1 is not None and selected == action_summary_from_tome1:
            self.fill_selected_series_summary_from_first_book(table)
        elif action_ignore is not None and selected == action_ignore:
            self._ignore_selected_series_from_table(table)

    def _series_identity(self, series: Any) -> tuple[str, str, str]:
        if isinstance(series, dict):
            metadata = series.get("metadata") if isinstance(series.get("metadata"), dict) else {}
            library = series.get("library") if isinstance(series.get("library"), dict) else {}
            return (
                str(series.get("id") or series.get("series_id") or ""),
                str(metadata.get("title") or series.get("name") or series.get("title") or series.get("komga_title") or ""),
                str(library.get("name") or series.get("libraryName") or ""),
            )
        raw = getattr(series, "raw", {}) if isinstance(getattr(series, "raw", {}), dict) else {}
        library = raw.get("library") if isinstance(raw.get("library"), dict) else {}
        library_name = str(getattr(series, "library_name", "") or library.get("name") or "")
        if not library_name:
            library_id = str(getattr(series, "library_id", "") or "")
            library_name = next(
                (
                    str(getattr(item, "name", ""))
                    for item in self.libraries
                    if str(getattr(item, "id", "")) == library_id
                ),
                "",
            )
        return (
            str(getattr(series, "id", "") or ""),
            str(getattr(series, "title", "") or ""),
            library_name,
        )

    def _ignore_selected_series_from_table(self, table: QTableWidget) -> None:
        rows = self._series_rows_for_table(table)
        indexes = self._selected_row_indexes(table)
        selected = [rows[index] for index in indexes if 0 <= index < len(rows)]
        if not selected:
            return
        added = 0
        ignored_ids: List[str] = []
        for series in selected:
            series_id, title, library_name = self._series_identity(series)
            if not series_id:
                continue
            if not self.local_exclusions.is_excluded(series_id):
                added += 1
            self.local_exclusions.add(series_id, title, library_name, reason="manual")
            ignored_ids.append(series_id)
        if not ignored_ids:
            return
        self.log(f"✅ {added} série(s) ajoutée(s) aux exclusions globales.")
        self._refresh_after_global_exclusions_change(refresh_kora=True)

    def fill_selected_series_summary_from_first_book(self, table: QTableWidget) -> None:
        rows = self._series_rows_for_table(table)
        indexes = self._selected_row_indexes(table)
        selected = [rows[index] for index in indexes if 0 <= index < len(rows)]
        if not selected:
            return
        simulation = self.simulation_enabled()
        total = len(selected)
        self._set_auto_match_progress("Summary série depuis tome 1 — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def do_fill() -> Dict[str, Any]:
            api = self.komga_api()
            report_rows: List[Dict[str, Any]] = []
            for index, series in enumerate(selected, start=1):
                series_id, title, _library_name = self._series_identity(series)
                self._emit_auto_match_progress(progress, "Summary depuis tome 1", index - 1, total, title)
                row = {
                    "index": index,
                    "series_id": series_id,
                    "title": title,
                    "book_id": "",
                    "book_title": "",
                    "summary_chars": 0,
                    "status": "",
                    "error": "",
                }
                try:
                    if not series_id:
                        row["status"] = "Ignoré : ID série absent"
                    else:
                        current = self._fetch_current_metadata("series", series_id)
                        if not is_blank_metadata_value(current.get("summary")):
                            row["status"] = "Ignoré : summary série déjà rempli"
                        else:
                            books = api.books(series_id=series_id, page_size=50)
                            first_book = books[0] if books else None
                            if first_book is None:
                                row["status"] = "Ignoré : aucun tome"
                            else:
                                row["book_id"] = getattr(first_book, "id", "")
                                row["book_title"] = getattr(first_book, "title", "")
                                summary = scalar_metadata_text((getattr(first_book, "metadata", {}) or {}).get("summary"))
                                if is_blank_metadata_value(summary) or summary.strip().upper() == "<NULL>":
                                    row["status"] = "Ignoré : summary tome 1 vide"
                                else:
                                    payload = {"summary": summary}
                                    row["summary_chars"] = len(summary)
                                    if simulation:
                                        row["status"] = "OK simulation"
                                    else:
                                        self.backup.save_json(
                                            "operation",
                                            "series",
                                            series_id,
                                            {
                                                "current": current,
                                                "first_book": {
                                                    "id": row["book_id"],
                                                    "title": row["book_title"],
                                                    "metadata": getattr(first_book, "metadata", {}),
                                                },
                                                "payload": payload,
                                            },
                                            "avant PATCH summary série depuis tome 1",
                                        )
                                        self._write_metadata_update(
                                            api,
                                            "series",
                                            series_id,
                                            payload,
                                            current,
                                            source="summary_from_first_book",
                                            note="Summary série depuis tome 1",
                                        )
                                        row["status"] = "OK appliqué"
                except Exception as exc:
                    row["status"] = "Erreur"
                    row["error"] = str(exc)
                report_rows.append(row)
                self._emit_auto_match_progress(progress, "Summary depuis tome 1", index, total, title)
            csv_path = self.backup.export_csv(f"summary_from_first_book_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", report_rows)
            return {"rows": report_rows, "csv_path": csv_path}

        def done(result: Dict[str, Any]) -> None:
            rows = result.get("rows") or []
            summary = self._status_counts_line(rows, status_key="status")
            self.log(f"✅ Summary depuis tome 1 terminé — {summary} — CSV : {result.get('csv_path', '')}")
            self._set_auto_match_progress(f"Summary depuis tome 1 terminé — {summary}", total, total)
            self._reload_series_views_after_visibility_change()

        self.run_worker("Summary série depuis tome 1", do_fill, done)

    def _refresh_after_global_exclusions_change(self, *, refresh_kora: bool) -> None:
        excluded_ids = self.local_exclusions.ids()
        self.collection_member_rows = [
            row
            for row in self.collection_member_rows
            if self._series_identity(row)[0] not in excluded_ids
        ]
        if hasattr(self, "collection_members_table") and self.collection_member_rows:
            self._set_table(
                self.collection_members_table,
                self._series_table_headers(),
                [self._series_table_row(row) for row in self.collection_member_rows],
            )
        elif hasattr(self, "collection_members_table"):
            self.collection_members_table.setRowCount(0)
        self.bdt_queue = [
            row
            for row in self.bdt_queue
            if str(getattr(row, "id", "")) not in excluded_ids
        ]
        if self.bdt_queue_index >= len(self.bdt_queue):
            self.bdt_queue_index = len(self.bdt_queue) - 1
        if getattr(self, "bdt_queue_dialog", None) is not None:
            self.refresh_bedetheque_queue_dialog()
        for field_name in ("bdt_target_id", "mbk_target_id", "mn_target_id", "cv_target_id"):
            field = getattr(self, field_name, None)
            if field is not None and field.text().strip() in excluded_ids:
                field.clear()
        if (
            hasattr(self, "meta_target_type")
            and self._metadata_target_type() == "series"
            and self.meta_target_id.text().strip() in excluded_ids
        ):
            self.meta_target_id.clear()
        if refresh_kora and getattr(self, "kora_window", None) is not None:
            self.kora_window.refresh_exclusions_panel()
            self.kora_window.refresh_series_table()
            self.kora_window.refresh_genre_inventory()
        self._reload_series_views_after_visibility_change()

    def _on_kora_exclusions_changed(self) -> None:
        self._refresh_after_global_exclusions_change(refresh_kora=False)

    def _copy_table_cell(self, item: QTableWidgetItem) -> None:
        QApplication.clipboard().setText(item.text())

    def _edit_table_cell_dialog(self, table: QTableWidget, item: QTableWidgetItem) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Modifier la cellule")
        layout = QVBoxLayout(dialog)
        editor = QTextEdit()
        editor.setPlainText(item.text())
        editor.setMinimumSize(640, 260)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            text = editor.toPlainText()
            item.setText(text)
            item.setToolTip(text)
            table.resizeRowToContents(item.row())

    def _show_text_popup(self, title: str, text: str, minimum_size: tuple[int, int] = (980, 620)) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(text or "Aucun contenu à afficher.")
        viewer.setLineWrapMode(QTextEdit.WidgetWidth)
        viewer.setStyleSheet("font-family: monospace;")
        viewer.setMinimumSize(*minimum_size)
        layout.addWidget(viewer)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()


    def _show_structured_report_dialog(
        self,
        title: str,
        text: str,
        rows: List[Dict[str, Any]],
        csv_path: str = "",
        columns: Optional[List[tuple[str, str]]] = None,
        secondary_filter_keys: Optional[List[str]] = None,
        status_filter_key: str = "status",
    ) -> None:
        """Show a batch/matching report as a filterable table plus raw details."""
        self.log(text)
        safe_rows = [row for row in (rows or []) if isinstance(row, dict)]
        if columns is None:
            preferred = [
                "index", "komga_title", "status", "provider", "match_strategy",
                "matched_title", "loaded_title", "payload_fields", "error",
            ]
            discovered: List[str] = []
            for row in safe_rows:
                for key in row.keys():
                    if key not in discovered:
                        discovered.append(key)
            keys = [key for key in preferred if key in discovered]
            keys.extend(key for key in discovered if key not in keys and key not in {"payload_json"})
            columns = [(key, key) for key in keys[:12]]
        secondary_filter_keys = secondary_filter_keys or ["match_strategy", "provider", "matched_type"]

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(1280, 760)
        layout = QVBoxLayout(dialog)

        summary = QLabel(text.split("Détail :", 1)[0].strip() if text else title)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        controls = QHBoxLayout()
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Filtrer dans le rapport…")
        status_combo = QComboBox()
        status_combo.addItem("Tous statuts", "")
        for status in sorted({str(row.get(status_filter_key) or row.get("status") or "Sans statut") for row in safe_rows}):
            status_combo.addItem(status, status)
        secondary_combo = QComboBox()
        secondary_combo.addItem("Toutes stratégies/sources", "")
        secondary_values: set[str] = set()
        for row in safe_rows:
            for key in secondary_filter_keys:
                value = str(row.get(key) or "").strip()
                if value:
                    secondary_values.add(value)
        for value in sorted(secondary_values):
            secondary_combo.addItem(value, value)
        visible_count = QLabel()
        controls.addWidget(QLabel("Recherche"))
        controls.addWidget(search_edit, 1)
        controls.addWidget(QLabel("Statut"))
        controls.addWidget(status_combo)
        controls.addWidget(QLabel("Stratégie/source"))
        controls.addWidget(secondary_combo)
        controls.addWidget(visible_count)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Vertical)
        table = QTableWidget()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels([label for _key, label in columns])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSortingEnabled(True)
        table.horizontalHeader().setStretchLastSection(False)
        for col in range(len(columns)):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Interactive)
            table.setColumnWidth(col, 150 if col else 70)
        self._ensure_generic_table_context_menu(table)
        self._configure_resizable_table(
            table,
            stretch_from=1 if len(columns) > 1 else 0,
            restore_state=False,
        )
        splitter.addWidget(table)

        detail = QTextEdit()
        detail.setReadOnly(True)
        detail.setStyleSheet("font-family: monospace;")
        detail.setLineWrapMode(QTextEdit.WidgetWidth)
        detail.setMinimumHeight(220)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        filtered_rows: List[Dict[str, Any]] = []

        def row_text(row: Dict[str, Any]) -> str:
            parts: List[str] = []
            for value in row.values():
                if isinstance(value, (dict, list)):
                    parts.append(json_text(value, indent=0))
                else:
                    parts.append(str(value))
            return " ".join(parts).casefold()

        def current_row() -> Optional[Dict[str, Any]]:
            selected = table.selectedItems()
            if not selected:
                return None
            first = table.item(selected[0].row(), 0)
            data = first.data(Qt.UserRole) if first is not None else None
            return data if isinstance(data, dict) else None

        def update_detail() -> None:
            row = current_row()
            if not row:
                detail.setPlainText("Sélectionne une ligne pour voir le détail complet.")
                return
            detail.setPlainText(json_text(row))

        def refresh() -> None:
            nonlocal filtered_rows
            needle = search_edit.text().strip().casefold()
            wanted_status = str(status_combo.currentData() or "")
            wanted_secondary = str(secondary_combo.currentData() or "")
            filtered_rows = []
            for row in safe_rows:
                if wanted_status and str(row.get(status_filter_key) or row.get("status") or "Sans statut") != wanted_status:
                    continue
                if wanted_secondary and not any(str(row.get(key) or "") == wanted_secondary for key in secondary_filter_keys):
                    continue
                if needle and needle not in row_text(row):
                    continue
                filtered_rows.append(row)

            table.setSortingEnabled(False)
            table.setRowCount(len(filtered_rows))
            for row_index, row in enumerate(filtered_rows):
                for col_index, (key, _label) in enumerate(columns or []):
                    value = row.get(key, "")
                    if isinstance(value, (dict, list)):
                        text_value = json_text(value, indent=0)
                    else:
                        text_value = str(value or "")
                    item = QTableWidgetItem(text_value)
                    item.setToolTip(text_value)
                    if col_index == 0:
                        item.setData(Qt.UserRole, row)
                    table.setItem(row_index, col_index, item)
            table.setSortingEnabled(True)
            table.resizeRowsToContents()
            visible_count.setText(f"{len(filtered_rows)}/{len(safe_rows)} ligne(s)")
            if filtered_rows:
                table.selectRow(0)
            else:
                detail.setPlainText("Aucune ligne ne correspond aux filtres.")

        def copy_detail() -> None:
            row = current_row()
            QApplication.clipboard().setText(json_text(row) if row else text)

        def open_csv() -> None:
            if csv_path and os.path.exists(csv_path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(csv_path))
            else:
                QMessageBox.warning(dialog, "CSV", "Fichier CSV introuvable ou non renseigné.")

        search_edit.textChanged.connect(refresh)
        status_combo.currentIndexChanged.connect(refresh)
        secondary_combo.currentIndexChanged.connect(refresh)
        table.itemSelectionChanged.connect(update_detail)

        buttons_row = QHBoxLayout()
        btn_copy = QPushButton("Copier détail")
        btn_raw = QPushButton("Texte brut")
        btn_csv = QPushButton("Ouvrir CSV")
        btn_copy.clicked.connect(copy_detail)
        btn_raw.clicked.connect(lambda _=False: self._show_text_popup(f"{title} — texte brut", text, (1100, 720)))
        btn_csv.clicked.connect(open_csv)
        buttons_row.addWidget(btn_copy)
        buttons_row.addWidget(btn_raw)
        buttons_row.addWidget(btn_csv)
        buttons_row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        buttons_row.addWidget(buttons)
        layout.addLayout(buttons_row)

        refresh()
        dialog.exec()

    def _show_batch_report(
        self,
        title: str,
        rows: List[Dict[str, Any]],
        columns: Optional[List[tuple[str, str]]] = None,
        status_filter_key: str = "status",
    ) -> None:
        safe_rows = [row for row in (rows or []) if isinstance(row, dict)]
        csv_name = f"{clean_title_for_write(title).replace(' ', '_').lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = self.backup.export_csv(csv_name, safe_rows) if safe_rows else ""
        error_count = sum(1 for row in safe_rows if str(row.get(status_filter_key) or "").casefold().startswith("erreur"))
        summary = f"{title} : {len(safe_rows)} ligne(s), {error_count} erreur(s)"
        if csv_path:
            summary += f"\nCSV : {csv_path}"
        self._show_structured_report_dialog(
            title,
            summary,
            safe_rows,
            csv_path=csv_path,
            columns=columns,
            status_filter_key=status_filter_key,
        )

    def _make_selection_detail_panel(self, key: str, title: str = "Détails sélection") -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        actions = QHBoxLayout()
        btn_copy_title = QPushButton("Copier titre")
        btn_copy_url = QPushButton("Copier URL")
        btn_open_url = QPushButton("Ouvrir URL")
        btn_json = QPushButton("JSON")
        actions.addWidget(btn_copy_title)
        actions.addWidget(btn_copy_url)
        actions.addWidget(btn_open_url)
        actions.addWidget(btn_json)
        actions.addStretch(1)
        layout.addLayout(actions)
        table = QTableWidget()
        self._register_table(table, f"details.{key}", default_hidden=[])
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["Champ", "Valeur"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        table.setWordWrap(True)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._ensure_min_table_visible_rows(table)
        self._ensure_generic_table_context_menu(table)
        layout.addWidget(table, 1)
        self._selection_detail_panels[key] = {
            "table": table,
            "title": "",
            "url": "",
            "data": {},
        }
        btn_copy_title.clicked.connect(lambda _=False, k=key: self._copy_selection_detail_title(k))
        btn_copy_url.clicked.connect(lambda _=False, k=key: self._copy_selection_detail_url(k))
        btn_open_url.clicked.connect(lambda _=False, k=key: self._open_selection_detail_url(k))
        btn_json.clicked.connect(lambda _=False, k=key: self._show_selection_detail_json(k))
        self._set_selection_detail(key, title="", data={"info": "Sélectionne une ligne."}, url="")
        return box

    def _detail_rows_from_data(self, data: Dict[str, Any]) -> List[List[Any]]:
        preferred = [
            "source", "id", "kind", "type", "title", "name", "number", "status", "year",
            "publisher", "genres", "url", "source_url", "cover_url", "libraryId", "seriesId", "bookId",
        ]
        rows: List[List[Any]] = []
        seen: set[str] = set()
        for key in preferred:
            if key in data:
                rows.append([key, one_line(data.get(key))])
                seen.add(key)
        for key in sorted(data.keys()):
            if key not in seen and key not in {"raw", "metadata", "series_metadata", "book_metadata"}:
                rows.append([key, one_line(data.get(key))])
        return rows

    def _set_selection_detail(self, key: str, title: str, data: Dict[str, Any], url: str = "") -> None:
        panel = self._selection_detail_panels.get(key)
        if not panel:
            return
        payload = data or {}
        panel["title"] = title or str(payload.get("title") or payload.get("name") or "")
        panel["url"] = url or str(payload.get("url") or payload.get("source_url") or payload.get("cover_url") or "")
        panel["data"] = payload
        rows = self._detail_rows_from_data(payload)
        if not rows:
            rows = [["info", "Aucun détail disponible."]]
        table = panel["table"]
        self._set_table(table, ["Champ", "Valeur"], rows, stretch_from=1)
        table.setWordWrap(True)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _copy_selection_detail_title(self, key: str) -> None:
        panel = self._selection_detail_panels.get(key) or {}
        QApplication.clipboard().setText(str(panel.get("title") or ""))

    def _copy_selection_detail_url(self, key: str) -> None:
        panel = self._selection_detail_panels.get(key) or {}
        QApplication.clipboard().setText(str(panel.get("url") or ""))

    def _open_selection_detail_url(self, key: str) -> None:
        panel = self._selection_detail_panels.get(key) or {}
        url = str(panel.get("url") or "").strip()
        if not url:
            QMessageBox.information(self, "URL", "Aucune URL disponible pour cette sélection.")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        QDesktopServices.openUrl(QUrl(url))

    def _show_selection_detail_json(self, key: str) -> None:
        panel = self._selection_detail_panels.get(key) or {}
        self._show_text_popup("Détails sélection — JSON", json_text(panel.get("data") or {}))

    def _json_from_text(self, text_edit: QTextEdit) -> Dict[str, Any]:
        text = text_edit.toPlainText().strip()
        if not text:
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Le JSON doit être un objet")
        return data

    def _set_table(
        self,
        table: QTableWidget,
        headers: List[str],
        rows: List[List[Any]],
        stretch_from: int = 1,
        selection_mode: Any = None,
        row_data: Optional[List[Any]] = None,
    ) -> None:
        sorting_enabled = table.isSortingEnabled()
        if sorting_enabled:
            table.setSortingEnabled(False)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        table.setWordWrap(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(selection_mode if selection_mode is not None else QAbstractItemView.SingleSelection)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem("" if value is None else str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip("" if value is None else str(value))
                if c == 0 and row_data is not None and r < len(row_data):
                    item.setData(Qt.UserRole, row_data[r])
                table.setItem(r, c, item)
        if sorting_enabled:
            table.setSortingEnabled(True)
        if headers:
            table.resizeColumnsToContents()
            self._configure_resizable_table(table, stretch_from=stretch_from)
        if table not in {
            getattr(self, "series_table", None),
            getattr(self, "book_explorer_table", None),
        }:
            self._ensure_generic_table_context_menu(table)

    def _set_detail_table(self, table: QTableWidget, data: Dict[str, Any]) -> None:
        rows: List[List[Any]] = []
        preferred = [
            "id", "title", "titleSort", "number", "numberSort", "summary", "status",
            "publisher", "releaseDate", "isbn", "language", "readingDirection",
            "ageRating", "totalBookCount", "genres", "tags", "authors", "links",
            "bookCount", "booksCount", "libraryId", "seriesId", "url",
        ]
        flat: Dict[str, Any] = {}
        for key, value in (data or {}).items():
            if key == "metadata" and isinstance(value, dict):
                for meta_key, meta_value in value.items():
                    flat.setdefault(meta_key, meta_value)
            elif key not in {"metadata", "media"}:
                flat.setdefault(key, value)
        for key in preferred:
            if key in flat:
                rows.append([metadata_field_label(key), one_line(flat[key])])
        for key in sorted(flat.keys()):
            if key not in preferred:
                rows.append([metadata_field_label(key), one_line(flat[key])])
        self._set_table(table, ["Champ", "Valeur"], rows, stretch_from=1)
        table.setWordWrap(True)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _show_cover(self, target_type: str, target_id: str, label: QLabel) -> None:
        if not target_id:
            label.setText("Aucun ID")
            label.setPixmap(QPixmap())
            return

        def done(data: bytes) -> None:
            pix = QPixmap()
            pix.loadFromData(data)
            if pix.isNull():
                label.setText("Image illisible")
                return
            label.setText("")
            label.setPixmap(pix.scaled(210, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        self.run_worker("Chargement couverture", lambda: self.komga_api().thumbnail_bytes(target_type, target_id), done)

    # ------------------------------------------------------------------
    # Metadata table helpers
    # ------------------------------------------------------------------
    def _init_metadata_table(self, table: QTableWidget) -> None:
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Champ", "Avant", "Après", "Appliquer", "Effacer"])
        table.setWordWrap(False)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        for col in range(5):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        table.setColumnWidth(0, 170)
        table.setColumnWidth(1, 360)
        table.setColumnWidth(2, 520)
        table.setColumnWidth(3, 80)
        table.setColumnWidth(4, 80)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.verticalHeader().setDefaultSectionSize(30)
        self._install_table_header_context_menu(table)
        self._install_table_viewport_fill(table, 1)
        self._restore_table_ui_state(table)
        self._schedule_table_viewport_fill(table)
        self._ensure_generic_table_context_menu(table)

    def _compact_metadata_table(self, table: QTableWidget) -> None:
        table.setWordWrap(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Interactive)
        table.verticalHeader().setDefaultSectionSize(30)
        for row in range(table.rowCount()):
            table.setRowHeight(row, 30)

    def _fit_metadata_table_rows(self, table: QTableWidget) -> None:
        table.setWordWrap(True)
        table.resizeRowsToContents()

    def _fit_metadata_table_columns(self, table: QTableWidget) -> None:
        table.resizeColumnsToContents()
        if table.columnWidth(0) < 140:
            table.setColumnWidth(0, 140)
        if table.columnWidth(1) < 280:
            table.setColumnWidth(1, 280)
        if table.columnWidth(2) < 420:
            table.setColumnWidth(2, 420)
        for col in range(table.columnCount()):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.Interactive)
        table.setProperty("komgaFillColumns", [1, 2])
        self._fill_table_to_viewport(table)

    def _fill_metadata_table(
        self,
        table: QTableWidget,
        current: Dict[str, Any],
        candidate: Optional[Dict[str, Any]] = None,
        preferred_fields: Optional[List[str]] = None,
    ) -> None:
        """Fill a metadata diff table with conservative default checks.

        Defaults:
        - scalar fields are checked only when Komga is blank;
        - list fields are merged safely and checked only when the merge adds
          something new;
        - identical values are never checked by default.
        """
        candidate = candidate_with_linked_title_sort(current, candidate)
        fields: List[str] = []
        for key in preferred_fields or []:
            if key not in fields:
                fields.append(key)
        for source in (current, candidate):
            for key in source.keys():
                if key not in fields and not key.endswith("Date") and key not in {"created", "lastModified"}:
                    fields.append(key)
        table.setRowCount(len(fields))
        self._compact_metadata_table(table)
        for r, field in enumerate(fields):
            has_candidate = field in candidate and not is_blank_metadata_value(candidate.get(field))
            current_value = current.get(field, "")
            candidate_value = candidate.get(field, "") if has_candidate else ""
            new_value = proposed_metadata_value_for_field(field, current_value, candidate_value) if has_candidate else ""
            auto_include_allowed = should_auto_include_metadata_field(field, candidate_value)

            current_blank = is_blank_metadata_value(current_value)
            identical = one_line(current_value) == one_line(new_value)
            merged_adds = isinstance(new_value, list) and one_line(new_value) != one_line(current_value) and has_candidate
            # Règle métier validée : STATUS Bedetheque remplace automatiquement
            # un status Komga non vide quand il est différent (ex: ONGOING -> ENDED).
            critical_change = should_auto_apply_changed_metadata_field(field, "series") and has_candidate and not identical
            checked_by_default = bool(
                has_candidate
                and auto_include_allowed
                and not identical
                and (critical_change or current_blank or merged_adds)
            )

            field_item = QTableWidgetItem(metadata_field_label(field))
            field_item.setFlags(field_item.flags() & ~Qt.ItemIsEditable)
            field_item.setData(Qt.UserRole, field)
            field_item.setToolTip(f"Champ technique : {field}")
            current_item = QTableWidgetItem(one_line(current_value))
            current_item.setFlags(current_item.flags() & ~Qt.ItemIsEditable)
            current_item.setToolTip(one_line(current_value))
            new_item = QTableWidgetItem(one_line(new_value))
            new_item.setToolTip(one_line(new_value))
            if field == "summary" and has_candidate and not auto_include_allowed:
                reason = (
                    f"Summary non inclus par défaut : trop court ou peu informatif "
                    f"(< {SUMMARY_MIN_SIGNIFICANT_CHARS} caractères significatifs, ou ligne 'Tout sur la série ...')."
                )
                field_item.setToolTip(reason)
                new_item.setToolTip(f"{reason}\n\n{one_line(new_value)}")
            if field == "language" and has_candidate and not auto_include_allowed:
                reason = "Language non inclus : seules les valeurs fr/en sont autorisées en écriture automatique."
                field_item.setToolTip(reason)
                new_item.setToolTip(f"{reason}\n\n{one_line(new_value)}")

            include_item = QTableWidgetItem("")
            include_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            include_item.setCheckState(Qt.Checked if checked_by_default else Qt.Unchecked)

            clear_item = QTableWidgetItem("")
            clear_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            clear_item.setCheckState(Qt.Unchecked)

            table.setItem(r, 0, field_item)
            table.setItem(r, 1, current_item)
            table.setItem(r, 2, new_item)
            table.setItem(r, 3, include_item)
            table.setItem(r, 4, clear_item)
        self._init_metadata_table(table)

    def _fill_series_preview_metadata_table(
        self,
        table: QTableWidget,
        current: Dict[str, Any],
        candidate: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._fill_metadata_table(table, current, candidate, SERIES_PREVIEW_FIELDS)
        for row in range(table.rowCount()):
            field_item = table.item(row, 0)
            if field_item is None or self._metadata_field_key(field_item) != "authors":
                continue
            explanation = (
                "Auteurs agrégés par Komga depuis les ComicInfo des livres. "
                "Affichage uniquement : ce champ n'est pas envoyé au PATCH de série."
            )
            field_item.setToolTip(explanation)
            for column in (3, 4):
                item = table.item(row, column)
                if item is not None:
                    item.setCheckState(Qt.Unchecked)
                    item.setFlags(Qt.NoItemFlags)
                    item.setToolTip(explanation)
            break
        self._reset_table_to_first_row(table, select=False)

    def _fetch_current_series_preview_metadata(self, series_id: str) -> Dict[str, Any]:
        raw = self.komga_api().get_series(series_id)
        metadata = dict(raw.get("metadata") or {}) if isinstance(raw, dict) else {}
        books_metadata = raw.get("booksMetadata") if isinstance(raw, dict) and isinstance(raw.get("booksMetadata"), dict) else {}
        authors = books_metadata.get("authors")
        if not is_blank_metadata_value(authors):
            metadata["authors"] = authors
        return metadata

    def _metadata_cell_value(self, field: str, text: str) -> Any:
        text = (text or "").strip()
        if text == "":
            return ""
        if text.upper() == "<NULL>":
            return None
        if field.endswith("Lock"):
            return text.lower() == "true"
        if field in STRING_METADATA_FIELDS:
            # Important: keep semicolons in scalar Komga fields.
            # Generic parse_cell_value would turn "A; B" into a JSON array,
            # which Komga rejects for fields such as publisher or summary.
            return text
        if field in INTEGER_METADATA_FIELDS:
            try:
                return int(text)
            except ValueError:
                return parse_cell_value(text)
        if field in JSON_LIST_METADATA_FIELDS:
            if text.startswith("[") or text.startswith("{"):
                return parse_cell_value(text)
            return value_as_list(text)
        if field in LIST_STRING_METADATA_FIELDS:
            return value_as_list(text)
        return parse_cell_value(text)

    @staticmethod
    def _metadata_field_key(item: Optional[QTableWidgetItem]) -> str:
        if item is None:
            return ""
        technical = item.data(Qt.UserRole)
        return str(technical or item.text() or "").strip()

    def _payload_from_metadata_table(self, table: QTableWidget) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for r in range(table.rowCount()):
            field_item = table.item(r, 0)
            include_item = table.item(r, 3)
            clear_item = table.item(r, 4)
            if not field_item or not include_item or include_item.checkState() != Qt.Checked:
                continue
            field = self._metadata_field_key(field_item)
            if not field:
                continue
            if clear_item and clear_item.checkState() == Qt.Checked:
                payload[field] = None
            else:
                text = table.item(r, 2).text() if table.item(r, 2) else ""
                value = normalize_metadata_payload_value(field, self._metadata_cell_value(field, text))
                if value != "":
                    payload[field] = value
        return payload

    def _payload_from_metadata_maps(
        self,
        current: Dict[str, Any],
        candidate: Dict[str, Any],
        preferred_fields: Optional[List[str]] = None,
        target_type: str = "series",
        critical_changes: bool = True,
    ) -> Dict[str, Any]:
        """Build the same conservative default payload as the metadata diff table.

        This is used by batch operations so the auto workflow cannot diverge from
        the manual Bedetheque/MangaBaka apply rules.
        """
        candidate = candidate_with_linked_title_sort(current, candidate)
        fields: List[str] = []
        for key in preferred_fields or []:
            if key not in fields:
                fields.append(key)
        for source in (current or {}, candidate):
            for key in source.keys():
                if key not in fields and not key.endswith("Date") and key not in {"created", "lastModified"}:
                    fields.append(key)

        payload: Dict[str, Any] = {}
        for field in fields:
            has_candidate = field in candidate and not is_blank_metadata_value(candidate.get(field))
            if not has_candidate:
                continue
            current_value = (current or {}).get(field, "")
            candidate_value = candidate.get(field, "")
            if not should_auto_include_metadata_field(field, candidate_value):
                continue
            new_value = proposed_metadata_value_for_field(field, current_value, candidate_value)
            identical = one_line(normalize_metadata_payload_value(field, current_value)) == one_line(new_value)
            if identical:
                continue
            current_blank = is_blank_metadata_value(current_value)
            merged_adds = isinstance(new_value, list) and one_line(new_value) != one_line(current_value)
            critical_change = critical_changes and should_auto_apply_changed_metadata_field(field, target_type)
            checked_by_default = bool(critical_change or current_blank or merged_adds)
            if checked_by_default and new_value != "":
                normalized = normalize_metadata_payload_value(field, new_value)
                if normalized != "":
                    payload[field] = normalized
        return self._normalize_payload_for_target(target_type, payload)

    def _normalize_payload_for_target(
        self,
        target_type: str,
        payload: Dict[str, Any],
        *,
        allow_all_languages: bool = False,
        preserve_empty_strings: bool = False,
    ) -> Dict[str, Any]:
        """Final guard before PATCH.

        Komga 1.24.x rejects book ISBN metadata updates in the observed API path
        even after local cleanup. To avoid batch failures, book payloads never
        send isbn. Other fields are still normalized by type.
        """
        normalized: Dict[str, Any] = {}
        for field, value in (payload or {}).items():
            if target_type == "book" and field == "isbn":
                continue
            if field == "language" and allow_all_languages:
                clean_value = normalize_bcp47_tag(value) if value is not None else None
            else:
                clean_value = normalize_metadata_payload_value(field, value)
            if clean_value != "" or (preserve_empty_strings and value == ""):
                normalized[field] = clean_value
        return normalized

    def _metadata_update_endpoint(self, target_type: str, target_id: str) -> str:
        if target_type == "series":
            return f"/api/v1/series/{target_id}/metadata"
        if target_type == "book":
            return f"/api/v1/books/{target_id}/metadata"
        return f"metadata:{target_type}:{target_id}"

    def _write_metadata_update(
        self,
        api: KomgaApi,
        target_type: str,
        target_id: str,
        payload: Dict[str, Any],
        current: Optional[Dict[str, Any]] = None,
        source: str = "manual",
        note: str = "metadata update",
        allow_all_languages: bool = False,
        preserve_empty_strings: bool = False,
    ) -> Any:
        """Final guarded write path for Komga metadata PATCH calls.

        All manual, batch and update-with-link workflows should pass through
        this method so payload normalization, audit JSON and rollback snapshots
        cannot diverge again.
        """
        normalized_payload = self._normalize_payload_for_target(
            target_type,
            payload,
            allow_all_languages=allow_all_languages,
            preserve_empty_strings=preserve_empty_strings,
        )
        current_metadata = current or {}
        audit_base = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "note": note,
            "target_type": target_type,
            "target_id": target_id,
            "endpoint": self._metadata_update_endpoint(target_type, target_id),
            "simulation": self.simulation_enabled(),
            "old_metadata": current_metadata,
            "new_payload": normalized_payload,
        }
        if not normalized_payload:
            audit_base["status"] = "skipped_empty_payload"
            self.backup.save_audit(target_type, target_id, audit_base, f"skip empty payload — {source}")
            return {"skipped": True, "reason": "empty payload"}
        self.backup.save_rollback_candidate(target_type, target_id, current_metadata, f"rollback avant {source}")
        try:
            if target_type == "series":
                response = api.update_series_metadata(target_id, normalized_payload)
            elif target_type == "book":
                response = api.update_book_metadata(target_id, normalized_payload)
            else:
                raise ValueError(f"Unsupported metadata target type: {target_type}")
        except Exception as exc:
            audit_base["status"] = "error"
            audit_base["error"] = str(exc)
            self.backup.save_audit(target_type, target_id, audit_base, f"error — {source}")
            raise
        audit_base["status"] = "ok"
        audit_base["response"] = response
        self.backup.save_audit(target_type, target_id, audit_base, f"ok — {source}")
        return response

    def _format_diff(self, current: Dict[str, Any], payload: Dict[str, Any], endpoint: str) -> str:
        mode = "SIMULATION — aucune écriture" if self.simulation_enabled() else "ÉCRITURE RÉELLE — sauvegarde avant modification"
        lines = [
            "PRÉVISUALISATION AVANT / APRÈS",
            f"Mode : {mode}",
            f"Cible technique : {endpoint}",
            f"Champs modifiés : {len(payload)}",
            "",
        ]
        if not payload:
            lines.append("Aucun changement inclus.")
        for key, new_value in payload.items():
            lines.append(f"{metadata_field_label(key)}  [{key}]")
            lines.append(f"  Avant : {one_line(current.get(key, ''))}")
            lines.append(f"  Après : {one_line(new_value)}")
            lines.append("")
        lines.append("DÉTAIL TECHNIQUE — données envoyées :")
        lines.append(json_text(payload))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _add_main_tab(self, widget: QWidget, title: str) -> int:
        layout = widget.layout()
        if isinstance(widget, QScrollArea) or (
            layout is not None
            and layout.count() == 1
            and isinstance(layout.itemAt(0).widget(), QScrollArea)
        ):
            index = self.tabs.addTab(widget, title)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            scroll.setWidget(widget)
            index = self.tabs.addTab(scroll, title)
        self._main_tab_indices[title] = index
        self._main_tab_source_widgets[id(widget)] = index
        return index

    def _build_context_bar(self, parent_layout: QVBoxLayout) -> None:
        context = QWidget()
        context.setObjectName("globalContextBar")
        context.setStyleSheet(
            "QWidget#globalContextBar { background: #25282d; border: 1px solid #3b3f46; border-radius: 6px; }"
        )
        row = QHBoxLayout(context)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(12)
        self.navigation_toggle_button = QPushButton("Masquer la navigation")
        self.navigation_toggle_button.setToolTip("Libère de l'espace pour les tableaux et affiche de nouveau le menu sur demande.")
        self.context_page_label = QLabel("Espace : Accueil")
        self.context_page_label.setStyleSheet("font-weight: 700;")
        self.context_connection_label = QLabel("Komga non validé")
        self.context_tasks_label = QPushButton("Aucune tâche active")
        self.context_tasks_label.setFlat(True)
        self.context_tasks_label.setToolTip("Ouvrir le centre des opérations")
        self.context_tasks_label.clicked.connect(lambda: self._set_current_tab_by_title("Opérations"))
        self.context_library_label = QLabel("Bibliothèque : Toutes les bibliothèques")
        self.context_series_label = QLabel("Série : aucune")
        self.context_book_label = QLabel("Tome : aucun")
        self.context_mode_toggle = QCheckBox("Mode sécurisé : simulation")
        self.context_mode_toggle.setChecked(True)
        self.context_mode_toggle.setToolTip(
            "Coché : aucune écriture dans Komga. Décoché : écriture réelle avec sauvegarde avant modification."
        )
        self.context_rollback_button = QPushButton("Restaurer la dernière opération")
        self.context_rollback_button.setToolTip(
            "Ouvre le snapshot le plus récent dans le parcours de restauration."
        )
        self.context_rollback_button.clicked.connect(self.open_latest_rollback)
        row.addWidget(self.navigation_toggle_button)
        row.addWidget(self.context_page_label)
        row.addWidget(self.context_connection_label)
        row.addWidget(self.context_tasks_label)
        row.addWidget(self.context_library_label, 1)
        row.addWidget(self.context_series_label, 1)
        row.addWidget(self.context_book_label, 1)
        row.addWidget(self.context_rollback_button)
        row.addWidget(self.context_mode_toggle)
        parent_layout.addWidget(context)

    def _build_home_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        title = QLabel("Que voulez-vous faire ?")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        intro = QLabel(
            "Choisissez un objectif. Tous les écrans et outils de la version précédente restent disponibles "
            "dans la navigation de gauche."
        )
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        actions = QGridLayout()
        home_actions = [
            ("Explorer la bibliothèque", "Parcourir les séries et les tomes.", "Explorateur"),
            ("Modifier les métadonnées", "Préparer et contrôler une modification manuelle.", "Métadonnées"),
            ("Enrichir depuis une source", "Rechercher et comparer des métadonnées externes.", "Enrichissement"),
            ("Organiser les collections", "Créer, compléter ou corriger les collections.", "Collections"),
            ("Organiser les readlists", "Gérer les listes de lecture et leur complétude.", "Readlists"),
            ("Gérer les genres Kora", "Examiner et appliquer les genres Kora.", "Genres Kora"),
            ("Scanner les prochaines sorties", "Rechercher les dates et préparer les tags.", "Prochaines sorties"),
            ("Auditer la bibliothèque", "Détecter les métadonnées incomplètes ou incohérentes.", "Audit"),
            ("Voir la santé de la bibliothèque", "Résumer les problèmes et ouvrir les corrections prioritaires.", "Santé"),
            ("Restaurer une modification", "Consulter les sauvegardes et préparer un rollback.", "Rollback"),
            ("Suivre les opérations", "Voir les tâches en cours, les erreurs et les relances disponibles.", "Opérations"),
            ("Configurer les connexions", "Vérifier Komga et les sources externes.", "Connexion"),
        ]
        for position, (label, description, target) in enumerate(home_actions):
            box = QGroupBox(label)
            box_layout = QVBoxLayout(box)
            text = QLabel(description)
            text.setWordWrap(True)
            button = QPushButton("Ouvrir")
            button.clicked.connect(lambda _checked=False, target=target: self._set_current_tab_by_title(target))
            box_layout.addWidget(text)
            box_layout.addWidget(button)
            actions.addWidget(box, position // 2, position % 2)
        layout.addLayout(actions)
        layout.addStretch(1)
        self.home_tab_index = self._add_main_tab(tab, "Accueil")

    def _build_enrichment_hub_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        title = QLabel("Enrichir une série")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        intro = QLabel(
            "Choisissez la source selon le résultat recherché. Les écrans historiques complets sont conservés "
            "et s'ouvrent depuis ce point d'entrée."
        )
        intro.setWordWrap(True)
        self.enrichment_context_label = QLabel()
        self.enrichment_context_label.setStyleSheet("font-weight: 700; padding: 8px;")
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(self.enrichment_context_label)

        steps = QGroupBox("Parcours recommandé")
        steps_layout = QHBoxLayout(steps)
        for text in (
            "1. Sélectionner une série",
            "2. Choisir la source",
            "3. Comparer les candidats",
            "4. Prévisualiser et appliquer",
        ):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("font-weight: 600; padding: 8px; border: 1px solid #555; border-radius: 4px;")
            steps_layout.addWidget(label, 1)
        layout.addWidget(steps)

        choose_series = QPushButton("Choisir ou changer la série dans l'Explorateur")
        choose_series.clicked.connect(lambda: self._set_current_tab_by_title("Explorateur"))
        layout.addWidget(choose_series)

        sources_grid = QGridLayout()
        sources = [
            ("Bedetheque", "BD franco-belge : résumé, statut, liens et correspondance des tomes."),
            ("MangaBaka", "Mangas : métadonnées de série, tomes et liens existants."),
            ("Manga News", "Mangas : résumés, volumes et prochaines parutions."),
            ("ComicVine", "Comics : volumes, numéros, métadonnées et couvertures."),
        ]
        for position, (source, description) in enumerate(sources):
            box = QGroupBox(source)
            box_layout = QVBoxLayout(box)
            text = QLabel(description)
            text.setWordWrap(True)
            button = QPushButton(f"Ouvrir {source}")
            button.clicked.connect(lambda _checked=False, source=source: self._set_current_tab_by_title(source))
            box_layout.addWidget(text)
            box_layout.addStretch(1)
            box_layout.addWidget(button)
            sources_grid.addWidget(box, position // 2, position % 2)
        layout.addLayout(sources_grid)
        layout.addStretch(1)
        self._add_main_tab(tab, "Enrichissement")

    def _add_source_workflow_header(self, layout: QVBoxLayout, source_name: str, purpose: str) -> None:
        box = QGroupBox(f"Parcours {source_name}")
        box_layout = QVBoxLayout(box)
        top = QHBoxLayout()
        context_label = QLabel()
        context_label.setStyleSheet("font-weight: 700;")
        self._source_context_labels[source_name] = context_label
        description = QLabel(purpose)
        description.setWordWrap(True)
        back_button = QPushButton("Changer de source")
        explorer_button = QPushButton("Choisir la série")
        back_button.clicked.connect(lambda: self._set_current_tab_by_title("Enrichissement"))
        explorer_button.clicked.connect(lambda: self._set_current_tab_by_title("Explorateur"))
        top.addWidget(context_label, 1)
        top.addWidget(explorer_button)
        top.addWidget(back_button)
        box_layout.addLayout(top)
        box_layout.addWidget(description)

        steps = QHBoxLayout()
        for step in (
            "1. Cible Komga",
            "2. Candidat source",
            "3. Comparaison",
            "4. Prévisualisation",
            "5. Application",
        ):
            label = QLabel(step)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("padding: 5px; border: 1px solid #555; border-radius: 3px;")
            steps.addWidget(label, 1)
        box_layout.addLayout(steps)
        layout.addWidget(box)
        self._refresh_context_header()

    def _confirm_source_write(
        self,
        *,
        source_name: str,
        target_label: str,
        field_count: int = 0,
    ) -> bool:
        if self.simulation_enabled():
            return True
        fields = f"\nChamps inclus : {field_count}" if field_count else ""
        message = (
            f"Appliquer les données {source_name} ?\n\n"
            f"Cible : {target_label}{fields}\n\n"
            "Une sauvegarde et un audit seront créés avant l'écriture."
        )
        return QMessageBox.question(self, "Confirmer l'enrichissement", message) == QMessageBox.Yes

    def _configure_grouped_navigation(self) -> None:
        display_labels = {
            "CSV / Bulk": "Imports et actions en masse",
            "Suivi sorties": "Suivi des sorties",
            "Rollback": "Historique et restauration",
            "Logs / Backups": "Journaux et sauvegardes",
            "Paramètres": "Réglages avancés",
        }
        groups = [
            ("ACCUEIL", ["Accueil"]),
            ("BIBLIOTHÈQUE", ["Explorateur", "Métadonnées", "Couvertures"]),
            ("ORGANISER", ["Collections", "Readlists", "Genres Kora"]),
            (
                "ENRICHIR ET SUIVRE",
                ["Enrichissement", "Bedetheque", "MangaBaka", "Manga News", "ComicVine", "Prochaines sorties", "Suivi sorties"],
            ),
            ("CONTRÔLER ET RÉPARER", ["Santé", "Audit", "CSV / Bulk", "Opérations", "Rollback", "Outils", "Logs / Backups"]),
            ("RÉGLAGES", ["Connexion", "Paramètres"]),
        ]
        self.navigation_list.clear()
        self._navigation_items_by_index.clear()
        assigned: set[int] = set()
        for group_name, titles in groups:
            available = [title for title in titles if title in self._main_tab_indices]
            if not available:
                continue
            header = QListWidgetItem(group_name)
            header.setFlags(Qt.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self.navigation_list.addItem(header)
            for title in available:
                index = self._main_tab_indices[title]
                item = QListWidgetItem(f"  {display_labels.get(title, title)}")
                item.setData(Qt.UserRole, index)
                item.setToolTip(f"Ouvrir : {title}")
                self.navigation_list.addItem(item)
                self._navigation_items_by_index[index] = item
                assigned.add(index)

        unassigned = [index for index in range(self.tabs.count()) if index not in assigned]
        if unassigned:
            header = QListWidgetItem("AUTRES OUTILS")
            header.setFlags(Qt.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self.navigation_list.addItem(header)
            for index in unassigned:
                title = self.tabs.tabText(index)
                item = QListWidgetItem(f"  {title}")
                item.setData(Qt.UserRole, index)
                self.navigation_list.addItem(item)
                self._navigation_items_by_index[index] = item
        self.tabs.tabBar().hide()

    def _on_navigation_item_changed(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            return
        index = current.data(Qt.UserRole)
        if index is not None and int(index) != self.tabs.currentIndex():
            self.tabs.setCurrentIndex(int(index))

    def _filter_navigation(self, text: str) -> None:
        query = str(text or "").strip().casefold()
        current_header: Optional[QListWidgetItem] = None
        current_group_items: List[QListWidgetItem] = []

        def apply_group_visibility() -> None:
            if current_header is not None:
                current_header.setHidden(bool(query) and not any(not item.isHidden() for item in current_group_items))

        for row in range(self.navigation_list.count()):
            item = self.navigation_list.item(row)
            if item.data(Qt.UserRole) is None:
                apply_group_visibility()
                current_header = item
                current_group_items = []
                item.setHidden(False)
                continue
            visible = not query or query in item.text().casefold()
            item.setHidden(not visible)
            current_group_items.append(item)
        apply_group_visibility()

    def _sync_navigation_to_tab(self, index: int) -> None:
        item = self._navigation_items_by_index.get(index)
        if item is None or self.navigation_list.currentItem() is item:
            return
        self.navigation_list.blockSignals(True)
        self.navigation_list.setCurrentItem(item)
        self.navigation_list.scrollToItem(item)
        self.navigation_list.blockSignals(False)

    def _toggle_navigation(self) -> None:
        if not hasattr(self, "navigation_panel"):
            return
        visible = not self.navigation_panel.isHidden()
        self.navigation_panel.setVisible(not visible)
        self.navigation_toggle_button.setText(
            "Afficher la navigation" if visible else "Masquer la navigation"
        )

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 4)
        self._build_context_bar(layout)

        workspace = QSplitter(Qt.Horizontal)
        navigation_panel = QWidget()
        self.workspace_splitter = workspace
        self.navigation_panel = navigation_panel
        navigation_layout = QVBoxLayout(navigation_panel)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        navigation_title = QLabel("KOMGA TOOLKIT — DESKTOP V2")
        navigation_title.setStyleSheet("font-weight: 700; padding: 6px;")
        self.navigation_search = QLineEdit()
        self.navigation_search.setPlaceholderText("Rechercher un écran…")
        self.navigation_search.setClearButtonEnabled(True)
        self.navigation_list = QListWidget()
        self.navigation_list.setMinimumWidth(245)
        self.navigation_list.setMaximumWidth(330)
        self.navigation_list.setSpacing(2)
        self.navigation_list.setStyleSheet(
            "QListWidget { border: 1px solid #3b3f46; border-radius: 5px; padding: 4px; } "
            "QListWidget::item { padding: 6px; } "
            "QListWidget::item:selected { background: #315c8a; color: white; border-radius: 3px; }"
        )
        navigation_layout.addWidget(navigation_title)
        navigation_layout.addWidget(self.navigation_search)
        navigation_layout.addWidget(self.navigation_list, 1)

        self.tabs = QTabWidget()
        workspace.addWidget(navigation_panel)
        workspace.addWidget(self.tabs)
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([270, 1410])
        layout.addWidget(workspace, 1)

        self._build_connection_tab()
        self._build_matching_settings_tab()
        self._build_explorer_tab()
        self._build_kora_tab()
        self._build_metadata_tab()
        self._build_collections_tab()
        self._build_readlists_tab()
        self._build_posters_tab()
        self._build_csv_tab()
        self._build_bedetheque_tab()
        self._build_mangabaka_tab()
        self._build_manga_news_tab()
        self._build_next_releases_tab()
        self._build_comicvine_tab()
        self._build_health_tab()
        self._build_audit_tab()
        self._build_release_tracking_tab()
        self._build_operations_tab()
        self._build_rollback_tab()
        self._build_tools_tab()
        self._build_logs_tab()
        self._build_enrichment_hub_tab()
        self._build_home_tab()
        self._build_bottom_progress_bar()
        self._configure_grouped_navigation()
        self.navigation_toggle_button.clicked.connect(self._toggle_navigation)
        self.navigation_search.textChanged.connect(self._filter_navigation)
        self.navigation_list.currentItemChanged.connect(self._on_navigation_item_changed)
        self.context_mode_toggle.toggled.connect(self._sync_context_mode_from_header)
        self.simulation_check.toggled.connect(self._sync_context_mode_from_settings)
        self.tabs.currentChanged.connect(self._on_top_level_tab_changed)
        self._set_current_tab_by_title("Accueil")
        self._sync_context_mode_from_settings(self.simulation_check.isChecked())
        self._on_top_level_tab_changed(self.tabs.currentIndex())

    def _build_kora_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.kora_tab = tab
        self.kora_window = None
        header = QGroupBox("Gérer les genres Kora")
        header_layout = QVBoxLayout(header)
        header_top = QHBoxLayout()
        self.kora_context_label = QLabel()
        self.kora_context_label.setStyleSheet("font-weight: 700;")
        btn_open_explorer = QPushButton("Choisir une série")
        btn_open_explorer.clicked.connect(lambda: self._set_current_tab_by_title("Explorateur"))
        header_top.addWidget(self.kora_context_label, 1)
        header_top.addWidget(btn_open_explorer)
        header_layout.addLayout(header_top)
        header_help = QLabel(
            "Inventoriez les genres, préparez les suggestions, contrôlez les exclusions puis appliquez la file de modifications."
        )
        header_help.setWordWrap(True)
        header_layout.addWidget(header_help)
        layout.addWidget(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.kora_tab_layout = content_layout
        layout.addWidget(content, 1)
        info = QLabel(
            "Le gestionnaire Genres Kora charge un cache potentiellement lourd. "
            "Il est chargé uniquement sur demande pour garder l'onglet rapide."
        )
        info.setWordWrap(True)
        btn_load_kora = QPushButton("Charger Genres Kora")
        btn_load_kora.clicked.connect(self._ensure_kora_window_built)
        content_layout.addWidget(info)
        content_layout.addWidget(btn_load_kora)
        content_layout.addStretch(1)
        self.kora_tab_index = self._add_main_tab(tab, "Genres Kora")
        self._refresh_context_header()

    def _ensure_kora_window_built(self, _checked: bool = False) -> bool:
        if getattr(self, "kora_window", None) is not None:
            return True
        from .integrations import KoraSharedApiAdapter
        from .kora.gui import MainWindow as KoraWindow

        layout = getattr(self, "kora_tab_layout", None)
        tab = getattr(self, "kora_tab", None)
        if layout is None or tab is None:
            return False
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.kora_window = KoraWindow(
            api_provider=lambda: KoraSharedApiAdapter(self.komga_api()),
            connection_check=self._kora_connection_status,
            exclusions_changed=self._on_kora_exclusions_changed,
            parent=tab,
        )
        self.kora_window.setWindowFlags(Qt.Widget)
        layout.addWidget(self.kora_window, 1)
        return True

    def _on_top_level_tab_changed(self, index: int) -> None:
        self._sync_navigation_to_tab(index)
        self._refresh_context_header()
        if index != getattr(self, "kora_tab_index", -1):
            return
        if getattr(self, "kora_window", None) is None:
            self.statusBar().showMessage("Genres Kora prêt à être chargé.")

    def _kora_connection_status(self) -> tuple[bool, str]:
        reason = self._komga_credentials_missing_reason()
        if reason:
            return False, f"{reason}. Renseigne puis valide la connexion dans l'onglet Connexion principal."
        if not self._komga_connection_validated:
            return False, "La connexion Komga n'a pas encore été validée."
        return True, ""

    def _build_tools_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        title = QLabel("Outils spécialisés")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        intro = QLabel(
            "Cet assistant historique reste disponible dans sa fenêtre dédiée et utilise la même "
            "connexion Komga que la V2. Son fonctionnement n'a pas été simplifié ni amputé."
        )
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        tools_grid = QGridLayout()

        series_box = QGroupBox("Correction des séries")
        series_layout = QVBoxLayout(series_box)
        series_description = QLabel(
            "Détecter et corriger les incohérences de séries avec l'assistant historique. "
            "À utiliser après un audit ou lorsqu'une structure Komga doit être réparée."
        )
        series_description.setWordWrap(True)
        btn_series_fix = QPushButton("Ouvrir l'assistant de correction")
        btn_series_fix.clicked.connect(self.open_series_fix_tool)
        series_layout.addWidget(series_description)
        series_layout.addStretch(1)
        series_layout.addWidget(btn_series_fix)
        tools_grid.addWidget(series_box, 0, 0)

        layout.addLayout(tools_grid)
        connection_button = QPushButton("Vérifier les connexions avant d'ouvrir un outil")
        connection_button.clicked.connect(lambda: self._set_current_tab_by_title("Connexion"))
        layout.addWidget(connection_button)
        layout.addStretch(1)
        self._add_main_tab(tab, "Outils")

    def open_series_fix_tool(self) -> None:
        from .integrations import SeriesFixApiAdapter
        from .tools.series_fix import MainWindow as SeriesFixWindow

        window = SeriesFixWindow(
            api_provider=lambda: SeriesFixApiAdapter(self.komga_api()),
            parent=self,
        )
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.show()
        self._series_fix_window = window

    def open_lightnovel_tool(self) -> None:
        from .integrations import LightNovelKomgaApiAdapter
        from .tools.lightnovel_queue import MainWindow as LightNovelWindow

        window = LightNovelWindow(
            komga_api_provider=lambda: LightNovelKomgaApiAdapter(self.komga_api()),
            komf_api_provider=self.komf_api,
            parent=self,
        )
        window.setAttribute(Qt.WA_DeleteOnClose)
        window.show()
        self._lightnovel_window = window

    def _build_bottom_progress_bar(self) -> None:
        self.auto_match_status_label = QLabel("Prêt")
        self.auto_match_progress_bar = QProgressBar()
        self.auto_match_progress_bar.setMinimumWidth(380)
        self.auto_match_progress_bar.setMaximumWidth(620)
        self.auto_match_progress_bar.setRange(0, 100)
        self.auto_match_progress_bar.setValue(0)
        self.auto_match_progress_bar.setFormat("0/0")
        self.statusBar().addWidget(self.auto_match_status_label, 1)
        self.statusBar().addPermanentWidget(self.auto_match_progress_bar)

    def _set_auto_match_progress(self, message: str, current: int, total: int) -> None:
        total = max(0, int(total or 0))
        current = max(0, int(current or 0))
        if total <= 0:
            self.auto_match_progress_bar.setRange(0, 0)
            self.auto_match_progress_bar.setFormat("En cours…")
        else:
            current = min(current, total)
            self.auto_match_progress_bar.setRange(0, total)
            self.auto_match_progress_bar.setValue(current)
            self.auto_match_progress_bar.setFormat(f"{current}/{total} — %p%")
        self.auto_match_status_label.setText(message or "Prêt")

    def _auto_match_progress_callback(self) -> Callable[[str, int, int], None]:
        return lambda message, current, total: self.auto_match_progress_signal.emit(str(message or ""), int(current or 0), int(total or 0))

    def _emit_auto_match_progress(self, progress: Optional[Callable[[str, int, int], None]], label: str, current: int, total: int, detail: str = "") -> None:
        if progress is None:
            return
        message = label
        if detail:
            message = f"{label} — {detail}"
        progress(message, current, total)

    def _safe_double_value(self, widget_name: str, fallback: float) -> float:
        widget = getattr(self, widget_name, None)
        if widget is None:
            return float(fallback)
        try:
            return float(widget.value())
        except Exception:
            return float(fallback)

    def _safe_int_value(self, widget_name: str, fallback: int) -> int:
        widget = getattr(self, widget_name, None)
        if widget is None:
            return int(fallback)
        try:
            return int(widget.value())
        except Exception:
            return int(fallback)

    def _matching_title_score_min(self) -> float:
        return self._safe_double_value("matching_title_score_min", self.config.matching.title_score_min)

    def _matching_loaded_title_score_min(self) -> float:
        return self._safe_double_value("matching_loaded_title_score_min", self.config.matching.loaded_title_score_min)

    def _matching_exact_title_score_min(self) -> float:
        return self._safe_double_value("matching_exact_title_score_min", self.config.matching.exact_title_score_min)

    def _matching_tome_pair_score_min(self) -> float:
        return self._safe_double_value("matching_tome_pair_score_min", self.config.matching.tome_pair_score_min)

    def _matching_tome_min_books(self) -> int:
        return self._safe_int_value("matching_tome_min_books", self.config.matching.tome_match_min_books)

    def _matching_tome_min_ratio(self) -> float:
        return self._safe_double_value("matching_tome_min_ratio", self.config.matching.tome_match_min_ratio)

    def _matching_tome_min_avg_score(self) -> float:
        return self._safe_double_value("matching_tome_min_avg_score", self.config.matching.tome_match_min_avg_score)

    def _matching_max_bedetheque_candidates(self) -> int:
        return self._safe_int_value("matching_max_bedetheque_candidates", self.config.matching.max_bedetheque_candidates)

    def _matching_rules_summary(self) -> str:
        return (
            f"titre >= {self._matching_title_score_min():.2f}, "
            f"titre chargé/scrapé >= {self._matching_loaded_title_score_min():.2f}, "
            f"titre exact >= {self._matching_exact_title_score_min():.3f}, "
            f"tomes: paire >= {self._matching_tome_pair_score_min():.2f}, "
            f"min {self._matching_tome_min_books()} tome(s), "
            f"ratio >= {self._matching_tome_min_ratio():.2f}, "
            f"moyenne >= {self._matching_tome_min_avg_score():.2f}, "
            f"candidats BDT max {self._matching_max_bedetheque_candidates()}"
        )

    def _build_connection_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        intro = QLabel(
            "Configurez d'abord Komga, puis activez uniquement les services externes nécessaires. "
            "Les valeurs sensibles restent masquées et ne sont jamais affichées dans les journaux."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        connection_tabs = QTabWidget()
        self.connection_tabs = connection_tabs
        komga_panel = QWidget()
        komga_panel_layout = QVBoxLayout(komga_panel)
        services_panel = QWidget()
        services_panel_layout = QVBoxLayout(services_panel)
        options_panel = QWidget()
        options_panel_layout = QVBoxLayout(options_panel)
        connection_tabs.addTab(komga_panel, "Komga principal")
        connection_tabs.addTab(services_panel, "Services externes")
        connection_tabs.addTab(options_panel, "Sécurité et sauvegardes")
        layout.addWidget(connection_tabs, 1)

        komga_box = QGroupBox("Connexion Komga")
        form = QFormLayout(komga_box)
        self.komga_url = QLineEdit()
        self.auth_mode = QComboBox()
        self.auth_mode.addItems(["api_key", "basic", "none"])
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("Laisser vide conserve la clé déjà enregistrée")
        self.username = QLineEdit()
        self.username.setPlaceholderText("Laisser vide conserve le login déjà enregistré")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Laisser vide conserve le mot de passe déjà enregistré")
        self.timeout_seconds = QSpinBox()
        self.timeout_seconds.setRange(1, 600)
        form.addRow("URL Komga", self.komga_url)
        form.addRow("Auth", self.auth_mode)
        form.addRow("Clé API", self.api_key)
        form.addRow("Identifiant", self.username)
        form.addRow("Mot de passe", self.password)
        form.addRow("Timeout", self.timeout_seconds)
        komga_panel_layout.addWidget(komga_box)
        komga_panel_layout.addStretch(1)

        # Compatibilité interne : les valeurs historiques restent préservées lors
        # d'une sauvegarde, mais Komf n'est plus exposé dans l'interface V2.
        self.komf_url = QLineEdit()
        self.komf_enabled = QCheckBox("Activer module Komf")
        self.komf_timeout_seconds = QSpinBox()
        self.komf_timeout_seconds.setRange(1, 600)

        mangabaka_box = QGroupBox("Connexion MangaBaka")
        mbf = QFormLayout(mangabaka_box)
        self.mangabaka_base_url = QLineEdit()
        self.mangabaka_enabled = QCheckBox("Activer module MangaBaka")
        self.mangabaka_timeout_seconds = QSpinBox()
        self.mangabaka_timeout_seconds.setRange(1, 600)
        self.mangabaka_cache_enabled = QCheckBox("Cache local recherches/lookups")
        self.mangabaka_cache_dir = QLineEdit()
        mbf.addRow("URL API MangaBaka", self.mangabaka_base_url)
        mbf.addRow("", self.mangabaka_enabled)
        mbf.addRow("Timeout", self.mangabaka_timeout_seconds)
        mbf.addRow("", self.mangabaka_cache_enabled)
        mbf.addRow("Dossier cache", self.mangabaka_cache_dir)
        services_panel_layout.addWidget(mangabaka_box)

        manga_news_box = QGroupBox("Connexion Manga News perso")
        mnf = QFormLayout(manga_news_box)
        self.manga_news_base_url = QLineEdit()
        self.manga_news_enabled = QCheckBox("Activer module Manga News")
        self.manga_news_timeout_seconds = QSpinBox()
        self.manga_news_timeout_seconds.setRange(1, 600)
        self.manga_news_token = QLineEdit()
        self.manga_news_token.setEchoMode(QLineEdit.Password)
        self.manga_news_token.setPlaceholderText("Laisser vide conserve le token déjà enregistré")
        self.manga_news_cache_enabled = QCheckBox("Cache local recherches/lookups")
        self.manga_news_cache_dir = QLineEdit()
        mnf.addRow("URL API Manga News", self.manga_news_base_url)
        mnf.addRow("", self.manga_news_enabled)
        mnf.addRow("Timeout", self.manga_news_timeout_seconds)
        mnf.addRow("Bearer token", self.manga_news_token)
        mnf.addRow("", self.manga_news_cache_enabled)
        mnf.addRow("Dossier cache", self.manga_news_cache_dir)
        services_panel_layout.addWidget(manga_news_box)

        comicvine_box = QGroupBox("Connexion ComicVine")
        cvf = QFormLayout(comicvine_box)
        self.comicvine_base_url = QLineEdit()
        self.comicvine_enabled = QCheckBox("Activer module ComicVine")
        self.comicvine_timeout_seconds = QSpinBox()
        self.comicvine_timeout_seconds.setRange(1, 600)
        self.comicvine_api_key = QLineEdit()
        self.comicvine_api_key.setEchoMode(QLineEdit.Password)
        self.comicvine_api_key.setPlaceholderText("Laisser vide conserve la clé déjà enregistrée")
        self.comicvine_cache_enabled = QCheckBox("Cache local recherches/lookups")
        self.comicvine_cache_dir = QLineEdit()
        cvf.addRow("URL API ComicVine", self.comicvine_base_url)
        cvf.addRow("", self.comicvine_enabled)
        cvf.addRow("Timeout", self.comicvine_timeout_seconds)
        cvf.addRow("Clé API", self.comicvine_api_key)
        cvf.addRow("", self.comicvine_cache_enabled)
        cvf.addRow("Dossier cache", self.comicvine_cache_dir)
        services_panel_layout.addWidget(comicvine_box)
        services_panel_layout.addStretch(1)

        options_box = QGroupBox("Options")
        f3 = QFormLayout(options_box)
        self.simulation_check = QCheckBox("Simulation par défaut — aucune écriture API")
        self.diagnostic_requests_enabled = QCheckBox("Mode diagnostic requêtes — journal JSONL")
        self.diagnostic_requests_enabled.setToolTip("Trace les workers et les appels Manga News dans audit/diagnostic_requests.jsonl pour diagnostiquer lenteurs, timeouts, résultats vides et déclencheurs.")
        self.backup_root = QLineEdit()
        f3.addRow("Mode", self.simulation_check)
        f3.addRow("Diagnostic", self.diagnostic_requests_enabled)
        f3.addRow("Dossier backup", self.backup_root)
        options_panel_layout.addWidget(options_box)
        options_panel_layout.addStretch(1)

        actions = QGridLayout()
        btn_save = QPushButton("Sauvegarder les réglages")
        btn_test_komga = QPushButton("Tester Komga")
        btn_test_mangabaka = QPushButton("Tester MangaBaka")
        btn_test_manga_news = QPushButton("Tester Manga News")
        btn_test_comicvine = QPushButton("Tester ComicVine")
        btn_load_libs = QPushButton("Charger bibliothèques partout")
        btn_open_backup = QPushButton("Ouvrir backups")
        actions.addWidget(btn_save, 0, 0)
        actions.addWidget(btn_test_komga, 0, 1)
        actions.addWidget(btn_load_libs, 0, 2)
        actions.addWidget(btn_open_backup, 0, 3)
        actions.addWidget(btn_test_mangabaka, 1, 0)
        actions.addWidget(btn_test_manga_news, 1, 1)
        actions.addWidget(btn_test_comicvine, 1, 2)
        layout.addLayout(actions)

        btn_save.clicked.connect(self.save_config_action)
        btn_test_komga.clicked.connect(self.test_komga)
        btn_test_mangabaka.clicked.connect(self.test_mangabaka)
        btn_test_manga_news.clicked.connect(self.test_manga_news)
        btn_test_comicvine.clicked.connect(self.test_comicvine)
        btn_load_libs.clicked.connect(self.load_libraries)
        btn_open_backup.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.backup.session_dir)))
        self.diagnostic_requests_enabled.stateChanged.connect(
            lambda state: self._set_diagnostics_enabled_cached(state != 0)
        )
        self._add_main_tab(tab, "Connexion")

    def _build_matching_settings_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title = QLabel("Paramètres avancés")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        intro = QLabel(
            "Les valeurs par défaut privilégient les correspondances fiables. Ne les modifiez que pour ajuster un cas "
            "identifié, puis contrôlez le résultat en mode simulation."
        )
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        box = QGroupBox("Seuils de correspondance — utilisateurs avancés")
        form = QFormLayout(box)

        def make_score_spin(default: float, minimum: float = 0.0, maximum: float = 1.0, step: float = 0.01) -> QDoubleSpinBox:
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(minimum, maximum)
            spin.setSingleStep(step)
            spin.setValue(default)
            return spin

        self.matching_title_score_min = make_score_spin(self.config.matching.title_score_min)
        self.matching_title_score_min.setToolTip("Score minimum pour accepter un résultat unique Bedetheque/MangaBaka.")
        self.matching_loaded_title_score_min = make_score_spin(self.config.matching.loaded_title_score_min)
        self.matching_loaded_title_score_min.setToolTip("Score minimum après scrape/chargement complet. Si le résultat de recherche était fiable, le meilleur des deux scores est utilisé.")
        self.matching_exact_title_score_min = make_score_spin(self.config.matching.exact_title_score_min, 0.90, 1.0, 0.001)
        self.matching_exact_title_score_min.setToolTip("Seuil pour accepter un titre exact unique parmi plusieurs résultats Bedetheque.")
        self.matching_tome_pair_score_min = make_score_spin(self.config.matching.tome_pair_score_min)
        self.matching_tome_pair_score_min.setToolTip("Score minimum pour considérer qu'un tome Komga correspond à un album Bedetheque.")
        self.matching_tome_min_books = QSpinBox()
        self.matching_tome_min_books.setRange(1, 99)
        self.matching_tome_min_books.setValue(int(self.config.matching.tome_match_min_books))
        self.matching_tome_min_books.setToolTip("Nombre minimal de tomes matchés pour accepter un départage par tomes.")
        self.matching_tome_min_ratio = make_score_spin(self.config.matching.tome_match_min_ratio)
        self.matching_tome_min_ratio.setToolTip("Ratio minimal de tomes Komga qui doivent matcher.")
        self.matching_tome_min_avg_score = make_score_spin(self.config.matching.tome_match_min_avg_score)
        self.matching_tome_min_avg_score.setToolTip("Score moyen minimal des tomes matchés.")
        self.matching_max_bedetheque_candidates = QSpinBox()
        self.matching_max_bedetheque_candidates.setRange(1, 50)
        self.matching_max_bedetheque_candidates.setValue(int(self.config.matching.max_bedetheque_candidates))
        self.matching_max_bedetheque_candidates.setToolTip("Nombre maximum de résultats Bedetheque à scraper pour départager par tomes.")

        form.addRow("Score titre minimum", self.matching_title_score_min)
        form.addRow("Score titre chargé/scrapé", self.matching_loaded_title_score_min)
        form.addRow("Score titre exact unique", self.matching_exact_title_score_min)
        form.addRow("Score minimum paire tome", self.matching_tome_pair_score_min)
        form.addRow("Tomes matchés minimum", self.matching_tome_min_books)
        form.addRow("Ratio tomes minimum", self.matching_tome_min_ratio)
        form.addRow("Score moyen tomes minimum", self.matching_tome_min_avg_score)
        form.addRow("Candidats Bedetheque max", self.matching_max_bedetheque_candidates)
        layout.addWidget(box)

        visibility_box = QGroupBox("Visibilité globale des séries")
        visibility_layout = QVBoxLayout(visibility_box)
        self.show_chap_scan_series = QCheckBox("Afficher les pseudo-séries Chap/Scan")
        self.show_chap_scan_series.setToolTip(
            "Décoché par défaut : masque partout les séries dont le champ url contient un segment exact /Chap/ ou /Scan/."
        )
        visibility_layout.addWidget(self.show_chap_scan_series)
        layout.addWidget(visibility_box)
        self.show_chap_scan_series.stateChanged.connect(lambda *_: self._reload_series_views_after_visibility_change())

        columns_box = QGroupBox("Données ComicInfo / Komga affichées dans les tableaux")
        columns_layout = QGridLayout(columns_box)
        columns_help = QLabel(
            "Les auteurs d'une série proviennent de l'agrégat booksMetadata calculé par Komga à partir des ComicInfo des livres."
        )
        columns_help.setWordWrap(True)
        columns_layout.addWidget(columns_help, 0, 0, 1, 4)
        columns_layout.addWidget(QLabel("Séries"), 1, 0, 1, 2)
        columns_layout.addWidget(QLabel("Livres"), 1, 2, 1, 2)
        self.series_table_field_checks: Dict[str, QCheckBox] = {}
        self.book_table_field_checks: Dict[str, QCheckBox] = {}
        ui = self.config.ui if isinstance(getattr(self.config, "ui", None), dict) else {}
        series_fields = ui.get("series_table_fields")
        if not isinstance(series_fields, list):
            series_fields = DEFAULT_SERIES_TABLE_FIELDS
        book_fields = ui.get("book_table_fields")
        if not isinstance(book_fields, list):
            book_fields = DEFAULT_BOOK_TABLE_FIELDS
        series_split = (len(SERIES_TABLE_FIELD_OPTIONS) + 1) // 2
        for index, (key, label) in enumerate(SERIES_TABLE_FIELD_OPTIONS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in series_fields)
            self.series_table_field_checks[key] = checkbox
            columns_layout.addWidget(checkbox, 2 + (index % series_split), index // series_split)
        book_split = (len(BOOK_TABLE_FIELD_OPTIONS) + 1) // 2
        for index, (key, label) in enumerate(BOOK_TABLE_FIELD_OPTIONS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(key in book_fields)
            self.book_table_field_checks[key] = checkbox
            columns_layout.addWidget(checkbox, 2 + (index % book_split), 2 + (index // book_split))
        layout.addWidget(columns_box)

        help_text = QTextEdit()
        help_text.setReadOnly(True)
        help_text.setMaximumHeight(220)
        help_text.setPlainText(
            "Ces réglages pilotent les auto-matchs prudents.\n\n"
            "Recommandation : ne baisse les seuils qu'en simulation, puis vérifie le rapport avant écriture réelle.\n"
            "Le départage par tomes reste volontairement strict : le numéro seul ne suffit jamais.\n"
            "Les réglages publics sont sauvegardés localement. Les identifiants restent dans le coffre système."
        )
        layout.addWidget(help_text)

        row = QHBoxLayout()
        btn_defaults = QPushButton("Restaurer valeurs prudentes")
        btn_save = QPushButton("Sauvegarder les réglages")
        btn_connection = QPushButton("Revenir aux connexions")
        row.addWidget(btn_defaults)
        row.addWidget(btn_save)
        row.addWidget(btn_connection)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

        btn_defaults.clicked.connect(self.reset_matching_settings_to_defaults)
        btn_save.clicked.connect(self.save_config_action)
        btn_connection.clicked.connect(lambda: self._set_current_tab_by_title("Connexion"))
        self._add_main_tab(tab, "Paramètres")

    def reset_matching_settings_to_defaults(self) -> None:
        defaults = MatchingConfig()
        self.matching_title_score_min.setValue(defaults.title_score_min)
        self.matching_loaded_title_score_min.setValue(defaults.loaded_title_score_min)
        self.matching_exact_title_score_min.setValue(defaults.exact_title_score_min)
        self.matching_tome_pair_score_min.setValue(defaults.tome_pair_score_min)
        self.matching_tome_min_books.setValue(defaults.tome_match_min_books)
        self.matching_tome_min_ratio.setValue(defaults.tome_match_min_ratio)
        self.matching_tome_min_avg_score.setValue(defaults.tome_match_min_avg_score)
        self.matching_max_bedetheque_candidates.setValue(defaults.max_bedetheque_candidates)
        for field, checkbox in getattr(self, "series_table_field_checks", {}).items():
            checkbox.setChecked(field in DEFAULT_SERIES_TABLE_FIELDS)
        for field, checkbox in getattr(self, "book_table_field_checks", {}).items():
            checkbox.setChecked(field in DEFAULT_BOOK_TABLE_FIELDS)
        self.log("Réglages matching restaurés aux valeurs prudentes. Sauvegarde nécessaire pour persister.")

    def _build_explorer_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.explorer_mode_tabs = QTabWidget()
        series_page = QWidget()
        series_page_layout = QVBoxLayout(series_page)
        top = QVBoxLayout()
        top.setSpacing(6)
        source_row = QHBoxLayout()
        source_row.setSpacing(6)
        refresh_row = QHBoxLayout()
        refresh_row.setSpacing(6)
        filters_actions_row = QHBoxLayout()
        filters_actions_row.setSpacing(6)
        saved_views_row = QHBoxLayout()
        saved_views_row.setSpacing(6)
        self.library_combo = self._make_library_combo("explorer")
        self.search_series_text = QLineEdit()
        self.search_series_text.setPlaceholderText("Recherche série...")
        self.search_books_text = QLineEdit()
        self.search_books_text.setPlaceholderText("Recherche livre/tome...")
        self.filter_series_empty_summary = QCheckBox("Résumé vide")
        self.filter_series_empty_summary.setToolTip("Afficher uniquement les séries dont le champ summary est vide")
        self.filter_series_language = QComboBox()
        self.filter_series_language.setMinimumWidth(80)
        self.filter_series_language.setToolTip("Filtrer les séries affichées selon metadata.language")
        self.filter_series_language.addItem("Toutes", "")
        self.filter_series_language.addItem("FR", "fr")
        self.filter_series_language.addItem("EN", "en")
        self.filter_series_status = QComboBox()
        self.filter_series_status.setMinimumWidth(105)
        self.filter_series_status.setToolTip("Filtrer les séries affichées selon metadata.status")
        self.filter_series_status.addItem("Tous", "ALL")
        for status in SERIES_STATUS_VALUES:
            self.filter_series_status.addItem(status, status)
        self.filter_series_status.addItem("VIDE", "VIDE")
        self.filter_series_link_label = QComboBox()
        self.filter_series_link_label.setMinimumWidth(160)
        self.filter_series_link_label.setToolTip("Filtrer les séries selon leurs liens externes")
        self.filter_series_link_label.addItem("Tous", "ALL")
        self.filter_series_link_label.addItem("Sans lien", "__NO_LINK__")
        btn_load_libs = QPushButton("Actualiser les bibliothèques")
        btn_load_series = QPushButton("Actualiser les séries")
        btn_load_books = QPushButton("Actualiser les tomes")
        btn_export_inventory = QPushButton("Exporter inventaire livres")
        btn_auto_bdt = QPushButton("Enrichir avec Bedetheque")
        btn_auto_bdt.setToolTip("Traite uniquement les séries sélectionnées. Refuse les cas ambigus.")
        btn_auto_mbk = QPushButton("Enrichir avec MangaBaka")
        btn_auto_mbk.setToolTip("Traite uniquement les séries sélectionnées. Refuse les cas ambigus et impose type=manga.")
        btn_auto_mn = QPushButton("Enrichir avec Manga News")
        btn_auto_mn.setToolTip("Traite uniquement les séries sélectionnées. Source surtout utile pour compléter les summary.")
        btn_update_links = QPushButton("Mettre à jour via les liens existants")
        btn_update_links.setToolTip("Met à jour uniquement les séries sélectionnées depuis leurs liens Bedetheque/MangaBaka existants. Respecte la simulation.")
        source_row.addWidget(QLabel("Bibliothèque"))
        source_row.addWidget(self.library_combo, 2)
        source_row.addWidget(self.search_series_text, 1)
        source_row.addWidget(self.search_books_text, 1)
        refresh_row.addWidget(QLabel("Actualiser"))
        refresh_row.addWidget(btn_load_libs)
        refresh_row.addWidget(btn_load_series)
        refresh_row.addWidget(btn_load_books)
        refresh_row.addWidget(btn_export_inventory)
        refresh_row.addStretch(1)
        filters_actions_row.addWidget(QLabel("Filtres"))
        filters_actions_row.addWidget(self.filter_series_empty_summary)
        filters_actions_row.addWidget(QLabel("Langue"))
        filters_actions_row.addWidget(self.filter_series_language)
        filters_actions_row.addWidget(QLabel("Statut"))
        filters_actions_row.addWidget(self.filter_series_status)
        filters_actions_row.addWidget(QLabel("Liens"))
        filters_actions_row.addWidget(self.filter_series_link_label)
        filters_actions_row.addStretch(1)
        self.explorer_saved_view_combo = QComboBox()
        self.explorer_saved_view_combo.setMinimumWidth(240)
        self.explorer_saved_view_combo.setToolTip(
            "Mémorise la bibliothèque, les recherches et les filtres de l'Explorateur."
        )
        btn_apply_saved_view = QPushButton("Appliquer la vue")
        btn_save_saved_view = QPushButton("Enregistrer la vue actuelle")
        btn_delete_saved_view = QPushButton("Supprimer la vue")
        saved_views_row.addWidget(QLabel("Vues enregistrées"))
        saved_views_row.addWidget(self.explorer_saved_view_combo, 1)
        saved_views_row.addWidget(btn_apply_saved_view)
        saved_views_row.addWidget(btn_save_saved_view)
        saved_views_row.addWidget(btn_delete_saved_view)
        top.addLayout(source_row)
        top.addLayout(refresh_row)
        top.addLayout(filters_actions_row)
        top.addLayout(saved_views_row)
        series_page_layout.addLayout(top)

        selection_actions_box = QGroupBox("Actions sur les séries sélectionnées")
        selection_actions_layout = QGridLayout(selection_actions_box)
        self.explorer_selection_label = QLabel("Aucune série sélectionnée")
        self.explorer_selection_label.setStyleSheet("font-weight: 600;")
        self.explorer_action_buttons = [btn_auto_bdt, btn_auto_mbk, btn_auto_mn, btn_update_links]
        for button in self.explorer_action_buttons:
            button.setEnabled(False)
        selection_actions_layout.addWidget(self.explorer_selection_label, 0, 0, 1, 4)
        selection_actions_layout.addWidget(btn_auto_bdt, 1, 0)
        selection_actions_layout.addWidget(btn_auto_mbk, 1, 1)
        selection_actions_layout.addWidget(btn_auto_mn, 1, 2)
        selection_actions_layout.addWidget(btn_update_links, 1, 3)
        series_page_layout.addWidget(selection_actions_box)

        split = QSplitter(Qt.Horizontal)

        series_box = QGroupBox("Séries Komga")
        series_layout = QVBoxLayout(series_box)
        self.series_table = QTableWidget()
        self._register_table(self.series_table, "explorer.series", default_hidden=["ID", "Library"])
        self._register_series_table_rows(self.series_table, "series_rows")
        self.series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        series_layout.addWidget(self.series_table, 1)
        split.addWidget(series_box)

        books_box = QGroupBox("Tomes / livres de la série sélectionnée")
        books_layout = QVBoxLayout(books_box)
        books_split = QSplitter(Qt.Vertical)
        self.books_table = QTableWidget()
        self._register_table(self.books_table, "explorer.books", default_hidden=["ID", "Series", "Library"])
        books_split.addWidget(self.books_table)

        book_details_box = QGroupBox("Détails tome sélectionné")
        book_details_layout = QVBoxLayout(book_details_box)
        book_actions = QHBoxLayout()
        btn_book_copy_id = QPushButton("Copier ID")
        btn_book_copy_url = QPushButton("Copier URL")
        btn_book_open_url = QPushButton("Ouvrir URL")
        btn_book_json = QPushButton("JSON")
        btn_book_edit = QPushButton("Modifier métadonnées")
        book_actions.addWidget(btn_book_copy_id)
        book_actions.addWidget(btn_book_copy_url)
        book_actions.addWidget(btn_book_open_url)
        book_actions.addWidget(btn_book_json)
        book_actions.addWidget(btn_book_edit)
        book_actions.addStretch(1)
        book_details_layout.addLayout(book_actions)
        self.explorer_book_details_table = QTableWidget()
        self._register_table(self.explorer_book_details_table, "explorer.book_details")
        self.explorer_book_details_table.setColumnCount(2)
        self.explorer_book_details_table.setHorizontalHeaderLabels(["Champ", "Valeur"])
        self.explorer_book_details_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.explorer_book_details_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.explorer_book_details_table.setColumnWidth(0, 150)
        self.explorer_book_details_table.setColumnWidth(1, 420)
        self.explorer_book_details_table.setWordWrap(True)
        self._ensure_generic_table_context_menu(self.explorer_book_details_table)
        book_details_layout.addWidget(self.explorer_book_details_table, 1)
        books_split.addWidget(book_details_box)
        books_split.setSizes([520, 300])
        books_layout.addWidget(books_split, 1)
        split.addWidget(books_box)

        details_box = QGroupBox("Détails série")
        details_layout = QVBoxLayout(details_box)
        self.explorer_cover = QLabel("Sélectionne une série")
        self.explorer_cover.setAlignment(Qt.AlignCenter)
        self.explorer_cover.setMinimumHeight(260)
        self.explorer_cover.setStyleSheet("border: 1px solid #555; background: #222; color: #aaa;")
        details_layout.addWidget(self.explorer_cover)
        explorer_actions = QHBoxLayout()
        btn_series_copy_id = QPushButton("Copier ID")
        btn_series_copy_url = QPushButton("Copier URL")
        btn_series_open_url = QPushButton("Ouvrir URL")
        btn_series_json = QPushButton("JSON")
        btn_series_edit = QPushButton("Modifier métadonnées")
        explorer_actions.addWidget(btn_series_copy_id)
        explorer_actions.addWidget(btn_series_copy_url)
        explorer_actions.addWidget(btn_series_open_url)
        explorer_actions.addWidget(btn_series_json)
        explorer_actions.addWidget(btn_series_edit)
        explorer_actions.addStretch(1)
        details_layout.addLayout(explorer_actions)
        self.explorer_series_details_table = QTableWidget()
        self._register_table(self.explorer_series_details_table, "explorer.series_details")
        self.explorer_series_details_table.setColumnCount(2)
        self.explorer_series_details_table.setHorizontalHeaderLabels(["Champ", "Valeur"])
        self.explorer_series_details_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.explorer_series_details_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.explorer_series_details_table.setColumnWidth(0, 160)
        self.explorer_series_details_table.setColumnWidth(1, 420)
        self.explorer_series_details_table.setWordWrap(True)
        self._ensure_generic_table_context_menu(self.explorer_series_details_table)
        details_layout.addWidget(self.explorer_series_details_table, 1)
        split.addWidget(details_box)

        split.setSizes([600, 720, 520])
        series_page_layout.addWidget(split, 1)

        btn_load_libs.clicked.connect(self.load_libraries)
        btn_load_series.clicked.connect(self.load_series)
        btn_load_books.clicked.connect(self.load_books)
        btn_export_inventory.clicked.connect(self.export_book_inventory)
        self.library_combo.currentIndexChanged.connect(lambda *_: self.load_series())
        btn_auto_bdt.clicked.connect(self.auto_match_bedetheque_prudent_from_explorer)
        btn_auto_mbk.clicked.connect(self.auto_match_mangabaka_prudent_from_explorer)
        btn_auto_mn.clicked.connect(self.auto_match_manga_news_prudent_from_explorer)
        btn_update_links.clicked.connect(self.update_selected_series_with_existing_links)
        self.search_series_text.returnPressed.connect(self.load_series)
        self.search_books_text.returnPressed.connect(self.load_books)
        self.filter_series_empty_summary.stateChanged.connect(lambda *_: self.load_series())
        self.filter_series_language.currentIndexChanged.connect(lambda *_: self.load_series())
        self.filter_series_status.currentIndexChanged.connect(lambda *_: self.load_series())
        self.filter_series_link_label.currentIndexChanged.connect(lambda *_: self.load_series())
        self.series_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.series_table.customContextMenuRequested.connect(self.show_explorer_series_context_menu)
        self.series_table.itemSelectionChanged.connect(self.on_series_selected)
        self.series_table.itemDoubleClicked.connect(lambda *_: self.load_books())
        self.books_table.itemSelectionChanged.connect(self.on_book_selected)
        btn_series_copy_id.clicked.connect(lambda: self.copy_explorer_detail_id("series"))
        btn_series_copy_url.clicked.connect(lambda: self.copy_explorer_detail_url("series"))
        btn_series_open_url.clicked.connect(lambda: self.open_explorer_detail_url("series"))
        btn_series_json.clicked.connect(lambda: self.show_explorer_detail_json("series"))
        btn_series_edit.clicked.connect(lambda: self.open_explorer_metadata_editor("series"))
        btn_book_copy_id.clicked.connect(lambda: self.copy_explorer_detail_id("book"))
        btn_book_copy_url.clicked.connect(lambda: self.copy_explorer_detail_url("book"))
        btn_book_open_url.clicked.connect(lambda: self.open_explorer_detail_url("book"))
        btn_book_json.clicked.connect(lambda: self.show_explorer_detail_json("book"))
        btn_book_edit.clicked.connect(lambda: self.open_explorer_metadata_editor("book"))
        btn_apply_saved_view.clicked.connect(self.apply_selected_explorer_view)
        btn_save_saved_view.clicked.connect(self.save_current_explorer_view)
        btn_delete_saved_view.clicked.connect(self.delete_selected_explorer_view)
        self._refresh_explorer_saved_views()
        self.explorer_mode_tabs.addTab(series_page, "Séries")
        self.explorer_mode_tabs.addTab(self._build_book_explorer_page(), "Tomes")
        layout.addWidget(self.explorer_mode_tabs, 1)
        self._add_main_tab(tab, "Explorateur")

    def _build_book_explorer_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        intro = QLabel(
            "Tous les tomes de la bibliothèque sélectionnée. La date d'ajout Komga utilise le champ created ; "
            "la date du fichier et la date de sortie restent distinctes."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        filters_box = QGroupBox("Recherche, filtres et tri")
        filters = QGridLayout(filters_box)
        self.book_explorer_library_combo = self._make_library_combo("book_explorer")
        self.book_explorer_search = QLineEdit()
        self.book_explorer_search.setPlaceholderText("Série, tome, numéro, ISBN, auteur, éditeur…")
        self.book_explorer_added_filter = QComboBox()
        for label, value in (
            ("Toutes les dates", 0),
            ("Aujourd'hui", 1),
            ("7 derniers jours", 7),
            ("30 derniers jours", 30),
            ("90 derniers jours", 90),
        ):
            self.book_explorer_added_filter.addItem(label, value)
        self.book_explorer_language_filter = QComboBox()
        self.book_explorer_language_filter.addItem("Toutes", "")
        self.book_explorer_language_filter.addItem("FR", "fr")
        self.book_explorer_language_filter.addItem("EN", "en")
        self.book_explorer_status_filter = QComboBox()
        self.book_explorer_status_filter.addItem("Tous", "ALL")
        for status in SERIES_STATUS_VALUES:
            self.book_explorer_status_filter.addItem(status, status)
        self.book_explorer_source_filter = QComboBox()
        for label, value in (
            ("Toutes", "all"),
            ("Avec au moins une source", "with_any"),
            ("Série sans aucune source", "without_any"),
            ("Avec Manga News", "with:manga_news"),
            ("Sans Manga News", "without:manga_news"),
            ("Avec Bedetheque", "with:bedetheque"),
            ("Sans Bedetheque", "without:bedetheque"),
            ("Avec ComicVine", "with:comicvine"),
            ("Sans ComicVine", "without:comicvine"),
            ("Avec MangaBaka", "with:mangabaka"),
            ("Sans MangaBaka", "without:mangabaka"),
        ):
            self.book_explorer_source_filter.addItem(label, value)
        self.book_explorer_missing_filter = QComboBox()
        for label, value in (
            ("Tous les champs", ""),
            ("Résumé manquant", "summary"),
            ("Date de sortie manquante", "release_date"),
            ("ISBN manquant", "isbn"),
            ("Auteurs manquants", "authors"),
            ("Éditeur manquant", "publisher"),
            ("Titre manquant", "title"),
            ("Titre de tri manquant", "title_sort"),
        ):
            self.book_explorer_missing_filter.addItem(label, value)
        self.book_explorer_empty_summary_filter = QCheckBox("Résumé vide")
        self.book_explorer_empty_summary_filter.setToolTip(
            "Afficher uniquement les tomes dont le champ summary est vide."
        )
        self.book_explorer_sort = QComboBox()
        for label, value in (
            ("Date d'ajout Komga", "added_at"),
            ("Nom de série", "series_title"),
            ("Titre du tome", "title"),
            ("Numéro du tome", "number"),
            ("Date de sortie", "release_date"),
        ):
            self.book_explorer_sort.addItem(label, value)
        self.book_explorer_order = QComboBox()
        self.book_explorer_order.addItem("Décroissant", True)
        self.book_explorer_order.addItem("Croissant", False)
        btn_load = QPushButton("Charger les tomes")
        btn_reset = QPushButton("Réinitialiser les filtres")

        filters.addWidget(QLabel("Bibliothèque"), 0, 0)
        filters.addWidget(self.book_explorer_library_combo, 0, 1)
        filters.addWidget(QLabel("Recherche"), 0, 2)
        filters.addWidget(self.book_explorer_search, 0, 3, 1, 3)
        filters.addWidget(btn_load, 0, 6)
        filters.addWidget(QLabel("Ajout Komga"), 1, 0)
        filters.addWidget(self.book_explorer_added_filter, 1, 1)
        filters.addWidget(QLabel("Langue"), 1, 2)
        filters.addWidget(self.book_explorer_language_filter, 1, 3)
        filters.addWidget(QLabel("Statut série"), 1, 4)
        filters.addWidget(self.book_explorer_status_filter, 1, 5)
        filters.addWidget(btn_reset, 1, 6)
        filters.addWidget(QLabel("Sources série"), 2, 0)
        filters.addWidget(self.book_explorer_source_filter, 2, 1)
        filters.addWidget(QLabel("Métadonnées"), 2, 2)
        filters.addWidget(self.book_explorer_missing_filter, 2, 3)
        filters.addWidget(QLabel("Trier par"), 2, 4)
        filters.addWidget(self.book_explorer_sort, 2, 5)
        filters.addWidget(self.book_explorer_order, 2, 6)
        filters.addWidget(self.book_explorer_empty_summary_filter, 3, 0, 1, 2)
        filters.setColumnStretch(3, 1)
        layout.addWidget(filters_box)

        actions_box = QGroupBox("Enrichissement de la sélection")
        actions = QHBoxLayout(actions_box)
        self.book_explorer_selection_label = QLabel("Aucun tome sélectionné")
        self.book_explorer_selection_label.setStyleSheet("font-weight: 600;")
        self.book_explorer_enrichment_source = QComboBox()
        self.book_explorer_enrichment_source.addItem("Automatique — sources associées", "auto")
        self.book_explorer_enrichment_source.addItem("Manga News", "manga_news")
        self.book_explorer_enrichment_source.addItem("Bedetheque", "bedetheque")
        self.book_explorer_enrichment_source.addItem("ComicVine", "comicvine")
        self.book_explorer_enrichment_source.addItem("MangaBaka", "mangabaka")
        self.book_explorer_analyze_button = QPushButton("Analyser la sélection")
        self.book_explorer_analyze_button.setEnabled(False)
        self.book_explorer_analyze_button.setToolTip(
            "Le mode automatique utilise uniquement les sources déjà associées à chaque série."
        )
        actions.addWidget(self.book_explorer_selection_label, 1)
        actions.addWidget(QLabel("Source"))
        actions.addWidget(self.book_explorer_enrichment_source)
        actions.addWidget(self.book_explorer_analyze_button)
        layout.addWidget(actions_box)

        split = QSplitter(Qt.Horizontal)
        table_box = QGroupBox("Tomes")
        table_layout = QVBoxLayout(table_box)
        self.book_explorer_count_label = QLabel("0 reçu — 0 affiché")
        table_layout.addWidget(self.book_explorer_count_label)
        self.book_explorer_table = QTableWidget()
        self._register_table(
            self.book_explorer_table,
            "explorer.all_books",
            default_hidden=["ID", "Library"],
        )
        self.book_explorer_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.book_explorer_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.book_explorer_table.setContextMenuPolicy(Qt.CustomContextMenu)
        table_layout.addWidget(self.book_explorer_table, 1)
        split.addWidget(table_box)

        detail_box = QGroupBox("Détails du tome")
        detail_layout = QVBoxLayout(detail_box)
        self.book_explorer_cover = QLabel("Sélectionnez un tome")
        self.book_explorer_cover.setAlignment(Qt.AlignCenter)
        self.book_explorer_cover.setMinimumHeight(230)
        self.book_explorer_cover.setStyleSheet("border: 1px solid #555; background: #222; color: #aaa;")
        detail_layout.addWidget(self.book_explorer_cover)
        self.book_explorer_details_table = QTableWidget()
        self._register_table(self.book_explorer_details_table, "explorer.all_books_details")
        detail_layout.addWidget(self.book_explorer_details_table, 1)
        split.addWidget(detail_box)
        split.setSizes([1250, 450])
        layout.addWidget(split, 1)

        btn_load.clicked.connect(self.load_book_explorer)
        btn_reset.clicked.connect(self.reset_book_explorer_filters)
        self.book_explorer_library_combo.currentIndexChanged.connect(lambda *_: self.load_book_explorer())
        self.book_explorer_search.textChanged.connect(lambda *_: self.apply_book_explorer_filters())
        for combo in (
            self.book_explorer_added_filter,
            self.book_explorer_language_filter,
            self.book_explorer_status_filter,
            self.book_explorer_source_filter,
            self.book_explorer_missing_filter,
            self.book_explorer_sort,
            self.book_explorer_order,
        ):
            combo.currentIndexChanged.connect(lambda *_: self.apply_book_explorer_filters())
        self.book_explorer_empty_summary_filter.stateChanged.connect(
            lambda *_: self.apply_book_explorer_filters()
        )
        self.book_explorer_table.itemSelectionChanged.connect(self.on_book_explorer_selected)
        self.book_explorer_table.customContextMenuRequested.connect(self.show_book_explorer_context_menu)
        self.book_explorer_analyze_button.clicked.connect(self.analyze_book_explorer_selection)
        return page

    def reset_book_explorer_filters(self) -> None:
        self.book_explorer_search.clear()
        self.book_explorer_empty_summary_filter.setChecked(False)
        for combo in (
            self.book_explorer_added_filter,
            self.book_explorer_language_filter,
            self.book_explorer_status_filter,
            self.book_explorer_source_filter,
            self.book_explorer_missing_filter,
            self.book_explorer_sort,
            self.book_explorer_order,
        ):
            combo.setCurrentIndex(0)
        self.apply_book_explorer_filters()

    def load_book_explorer(self) -> None:
        library_id = self._library_id("book_explorer")
        generation = self._next_series_load_generation("book_explorer")
        self.book_explorer_rows = []
        self.book_explorer_visible_rows = []
        self._set_table(self.book_explorer_table, self._book_explorer_headers(), [], row_data=[])
        if not library_id:
            self.book_explorer_count_label.setText("Choisissez une bibliothèque pour charger les tomes.")
            return

        def work() -> tuple[List[Any], List[Any]]:
            api = self.komga_api()
            return (
                api.series(library_id=library_id, page_size=500),
                api.books(library_id=library_id, page_size=500, timeout=min(int(self.timeout_seconds.value()), 20)),
            )

        def done(result: tuple[List[Any], List[Any]]) -> None:
            if not self._is_current_series_load_generation("book_explorer", generation):
                return
            series_rows, books = result
            series_by_id = {self._record_id(series): series for series in series_rows}
            self.book_explorer_rows = [
                book_explorer_row(book, series_by_id.get(str(getattr(book, "series_id", "") or "")))
                for book in books
            ]
            self.apply_book_explorer_filters()
            self.log(
                f"✅ Explorateur de tomes : {len(books)} tome(s) chargé(s) dans la bibliothèque sélectionnée."
            )

        self.run_worker("Chargement explorateur de tomes", work, done)

    @staticmethod
    def _book_explorer_headers() -> List[str]:
        return [
            "ID",
            "Ajout Komga",
            "Série",
            "N°",
            "Titre",
            "Titre de tri",
            "Langue",
            "Statut série",
            "Date de sortie",
            "ISBN",
            "Pages",
            "Résumé",
            "Sources",
            "Library",
        ]

    def _book_explorer_table_row(self, row: Dict[str, Any]) -> List[Any]:
        added = row.get("added_at")
        added_text = added.astimezone().strftime("%Y-%m-%d %H:%M") if isinstance(added, datetime) else ""
        sources = ", ".join(BOOK_SOURCE_LABELS.get(source, source) for source in row.get("source_names") or ())
        return [
            row.get("book_id", ""),
            added_text,
            row.get("series_title", ""),
            row.get("number", ""),
            row.get("title", ""),
            row.get("title_sort", ""),
            row.get("language", ""),
            row.get("series_status", ""),
            row.get("release_date", ""),
            row.get("isbn", ""),
            row.get("number_of_pages", ""),
            "Oui" if row.get("summary") else "Non",
            sources,
            row.get("library_id", ""),
        ]

    def apply_book_explorer_filters(self) -> None:
        if not hasattr(self, "book_explorer_table"):
            return
        days = int(self.book_explorer_added_filter.currentData() or 0)
        added_since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
        rows = filter_book_rows(
            self.book_explorer_rows,
            query=self.book_explorer_search.text(),
            added_since=added_since,
            language=str(self.book_explorer_language_filter.currentData() or ""),
            series_status=str(self.book_explorer_status_filter.currentData() or "ALL"),
            source_filter=str(self.book_explorer_source_filter.currentData() or "all"),
            missing_field=str(self.book_explorer_missing_filter.currentData() or ""),
            empty_summary=self.book_explorer_empty_summary_filter.isChecked(),
        )
        rows = sort_book_rows(
            rows,
            field=str(self.book_explorer_sort.currentData() or "added_at"),
            descending=bool(self.book_explorer_order.currentData()),
        )
        self.book_explorer_visible_rows = rows
        self._set_table(
            self.book_explorer_table,
            self._book_explorer_headers(),
            [self._book_explorer_table_row(row) for row in rows],
            stretch_from=2,
            selection_mode=QAbstractItemView.ExtendedSelection,
            row_data=rows,
        )
        hidden = len(self.book_explorer_rows) - len(rows)
        self.book_explorer_count_label.setText(
            f"{len(self.book_explorer_rows)} reçu(s) — {len(rows)} affiché(s) — {hidden} masqué(s)"
        )
        self.on_book_explorer_selected()

    def _update_book_explorer_rows_locally(
        self,
        metadata_by_book_id: Dict[str, Dict[str, Any]],
    ) -> tuple[int, int]:
        """Refresh only enriched books, then reapply the current local filters."""
        updates = {
            str(book_id): dict(metadata)
            for book_id, metadata in (metadata_by_book_id or {}).items()
            if str(book_id) and isinstance(metadata, dict)
        }
        if not updates:
            return 0, 0

        updated_ids: set[str] = set()
        refreshed_rows: List[Dict[str, Any]] = []
        for row in self.book_explorer_rows:
            book_id = str(row.get("book_id") or "")
            metadata = updates.get(book_id)
            if metadata is None:
                refreshed_rows.append(row)
                continue
            book = row.get("book")
            if book is None:
                refreshed_rows.append(row)
                continue
            book.metadata = dict(metadata)
            raw = dict(getattr(book, "raw", {}) or {})
            raw["metadata"] = dict(metadata)
            book.raw = raw
            if metadata.get("title") not in (None, ""):
                book.title = str(metadata.get("title"))
            if metadata.get("number") not in (None, ""):
                book.number = str(metadata.get("number"))
            refreshed_rows.append(book_explorer_row(book, row.get("series")))
            updated_ids.add(book_id)

        self.book_explorer_rows = refreshed_rows
        self.apply_book_explorer_filters()
        visible_ids = {
            str(row.get("book_id") or "")
            for row in self.book_explorer_visible_rows
        }
        return len(updated_ids), len(updated_ids - visible_ids)

    def _selected_book_explorer_rows(self) -> List[Dict[str, Any]]:
        return [
            self.book_explorer_visible_rows[index]
            for index in self._selected_row_indexes(self.book_explorer_table)
            if 0 <= index < len(self.book_explorer_visible_rows)
        ]

    def on_book_explorer_selected(self) -> None:
        selected = self._selected_book_explorer_rows()
        count = len(selected)
        self.book_explorer_selection_label.setText(
            "Aucun tome sélectionné" if not count else f"{count} tome(s) sélectionné(s)"
        )
        self.book_explorer_analyze_button.setEnabled(count > 0)
        if not selected:
            self._set_detail_table(self.book_explorer_details_table, {})
            self.book_explorer_cover.setText("Sélectionnez un tome")
            self.book_explorer_cover.setPixmap(QPixmap())
            return
        row = selected[0]
        book = row.get("book")
        series = row.get("series")
        if book is not None:
            self._set_context_selection(series=series, book=book)
            self._set_detail_table(self.book_explorer_details_table, getattr(book, "raw", {}) or {})
            self._show_cover("book", str(row.get("book_id") or ""), self.book_explorer_cover)

    def show_book_explorer_context_menu(self, point: Any) -> None:
        row_index = self.book_explorer_table.rowAt(point.y())
        if row_index < 0 or row_index >= len(self.book_explorer_visible_rows):
            return
        item = self.book_explorer_table.item(row_index, max(0, self.book_explorer_table.columnAt(point.x())))
        if item is None or not item.isSelected():
            self.book_explorer_table.clearSelection()
            self.book_explorer_table.selectRow(row_index)
        menu = QMenu(self)
        auto_action = menu.addAction("Enrichir automatiquement")
        source_menu = menu.addMenu("Enrichir avec…")
        source_actions = {
            source_menu.addAction(label): source
            for source, label in (
                ("manga_news", "Manga News"),
                ("bedetheque", "Bedetheque"),
                ("comicvine", "ComicVine"),
                ("mangabaka", "MangaBaka"),
            )
        }
        menu.addSeparator()
        manage_menu = menu.addMenu("Associer / gérer la série dans…")
        manage_actions = {
            manage_menu.addAction(label): source
            for source, label in (
                ("manga_news", "Manga News"),
                ("bedetheque", "Bedetheque"),
                ("comicvine", "ComicVine"),
                ("mangabaka", "MangaBaka"),
            )
        }
        chosen = menu.exec(self.book_explorer_table.viewport().mapToGlobal(point))
        if chosen == auto_action:
            self.analyze_book_explorer_selection("auto")
        elif chosen in source_actions:
            self.analyze_book_explorer_selection(source_actions[chosen])
        elif chosen in manage_actions:
            self.open_book_explorer_series_in_source(manage_actions[chosen])

    def open_book_explorer_series_in_source(self, source: str) -> None:
        selected = self._selected_book_explorer_rows()
        if not selected:
            return
        row = selected[0]
        series = row.get("series")
        book = row.get("book")
        if series is None:
            QMessageBox.warning(self, "Source", "La série parente de ce tome est introuvable.")
            return
        self._set_context_selection(series=series, book=book)
        library_id = str(row.get("library_id") or "")
        title = clean_search_title(getattr(series, "title", "") or row.get("series_title", ""))
        if source == "bedetheque":
            self._set_current_tab_by_title("Bedetheque")
            self._set_library_combo("bedetheque", library_id)
            self.bdt_target_type.setCurrentText("book")
            self.bdt_target_id.setText(str(row.get("book_id") or ""))
            self.bdt_query.setText(title)
            self.bdt_album_number.setText(str(row.get("number") or ""))
            self.search_bedetheque()
        elif source == "manga_news":
            self._set_current_tab_by_title("Manga News")
            self._set_library_combo("manga_news", library_id)
            self.mn_target_id.setText(str(getattr(series, "id", "") or ""))
            self.mn_query.setText(title)
            slug, url = self._manga_news_link_for_series(series)
            if slug or url:
                self.fetch_manga_news_series_direct(slug=slug, url=url)
            else:
                self.search_manga_news()
        elif source == "comicvine":
            self._set_current_tab_by_title("ComicVine")
            self._set_library_combo("comicvine", library_id)
            self.cv_target_id.setText(str(getattr(series, "id", "") or ""))
            self.cv_query.setText(title)
            volume_id, url = self._comicvine_link_for_series(series)
            if volume_id:
                self.fetch_comicvine_series_direct(volume_id=volume_id, url=url)
            else:
                self.search_comicvine()
        elif source == "mangabaka":
            self._set_current_tab_by_title("MangaBaka")
            self._set_library_combo("mangabaka", library_id)
            self.mbk_target_id.setText(str(getattr(series, "id", "") or ""))
            self.mbk_query.setText(title)
            self.search_mangabaka()

    def _book_explorer_enrichment_payload(
        self,
        current: Dict[str, Any],
        candidate: Dict[str, Any],
        *,
        include_titles: bool = False,
    ) -> tuple[Dict[str, Any], bool]:
        return book_enrichment_payload(current, candidate, include_titles=include_titles)

    def _new_book_explorer_analysis_row(
        self,
        row: Dict[str, Any],
        *,
        source: str = "",
        source_ref: str = "",
        matched_title: str = "",
        confidence: str = "",
        score: float = 0.0,
        candidate: Optional[Dict[str, Any]] = None,
        status: str = "",
        error: str = "",
    ) -> Dict[str, Any]:
        current = dict(self._book_metadata_map(row.get("book")) or {})
        proposed = dict(candidate or {})
        payload, title_confirmation = self._book_explorer_enrichment_payload(current, proposed)
        high_confidence = confidence == "high"
        needs_confirmation = bool(not high_confidence or title_confirmation)
        if not status:
            if not proposed:
                status = "Aucune métadonnée source"
            elif not payload:
                status = "Aucun changement"
            elif needs_confirmation:
                status = "Validation utilisateur requise"
            else:
                status = "Prêt — confiance élevée"
        return {
            **row,
            "source": source,
            "source_ref": source_ref,
            "matched_title": matched_title,
            "confidence": confidence,
            "score": round(float(score or 0.0), 3),
            "candidate_metadata": proposed,
            "current_metadata": current,
            "payload": payload,
            "needs_confirmation": needs_confirmation,
            "status": status,
            "error": error,
        }

    def _analyze_manga_news_books(
        self,
        rows: List[Dict[str, Any]],
        series: Any,
        source_url: str,
    ) -> List[Dict[str, Any]]:
        client = self.manga_news_client()
        slug, _url = self._manga_news_link_for_series(series)
        if not slug:
            slug = extract_manga_news_series_slug_from_url(source_url)
        if not slug:
            return [
                self._new_book_explorer_analysis_row(
                    row,
                    source="manga_news",
                    source_ref=source_url,
                    status="Lien Manga News inutilisable",
                    error="Le lien associé ne contient aucun slug de série exploitable.",
                )
                for row in rows
            ]
        results: List[Dict[str, Any]] = []
        for row in rows:
            try:
                candidate = client.get_volume_by_number(slug, row.get("number", ""))
                exact = bool(
                    normalize_volume_number(row.get("number", ""))
                    and normalize_volume_number(row.get("number", "")) == normalize_volume_number(candidate.number)
                )
                results.append(
                    self._new_book_explorer_analysis_row(
                        row,
                        source="manga_news",
                        source_ref=candidate.source_url,
                        matched_title=candidate.title,
                        confidence="high" if exact else "ambiguous",
                        score=1.0 if exact else title_similarity(row.get("title", ""), candidate.title),
                        candidate=candidate.book_metadata,
                    )
                )
            except Exception as exc:
                results.append(
                    self._new_book_explorer_analysis_row(
                        row,
                        source="manga_news",
                        source_ref=source_url,
                        status="Tome Manga News introuvable",
                        error=str(exc),
                    )
                )
        return results

    def _analyze_bedetheque_books(
        self,
        rows: List[Dict[str, Any]],
        source_url: str,
    ) -> List[Dict[str, Any]]:
        client = self.bedetheque_client()
        try:
            series_candidate = client.scrape_series(source_url)
            albums = list((series_candidate.raw or {}).get("albums") or [])
        except Exception as exc:
            return [
                self._new_book_explorer_analysis_row(
                    row,
                    source="bedetheque",
                    source_ref=source_url,
                    status="Erreur de chargement Bedetheque",
                    error=str(exc),
                )
                for row in rows
            ]
        source_rows = [
            SourceBookRow(
                id=str(album.get("url") or index),
                number=str(album.get("number") or ""),
                title=str(album.get("title") or ""),
                url=str(album.get("url") or ""),
                raw=album,
            )
            for index, album in enumerate(albums)
        ]
        books = [row.get("book") for row in rows]
        matches = match_source_books(books, source_rows)[: len(rows)]
        results: List[Dict[str, Any]] = []
        for row, match in zip(rows, matches):
            source_index = int(match.get("source_index", -1))
            if source_index < 0 or source_index >= len(source_rows):
                results.append(
                    self._new_book_explorer_analysis_row(
                        row,
                        source="bedetheque",
                        source_ref=source_url,
                        status="Aucun album correspondant",
                    )
                )
                continue
            source_row = source_rows[source_index]
            exact = str(match.get("confidence") or "").casefold().startswith("exact")
            try:
                candidate = client.scrape_album(source_row.url)
                results.append(
                    self._new_book_explorer_analysis_row(
                        row,
                        source="bedetheque",
                        source_ref=candidate.source_url,
                        matched_title=candidate.album_title or source_row.title,
                        confidence="high" if exact else "ambiguous",
                        score=float(match.get("score") or 0.0),
                        candidate=candidate.book_metadata,
                    )
                )
            except Exception as exc:
                results.append(
                    self._new_book_explorer_analysis_row(
                        row,
                        source="bedetheque",
                        source_ref=source_row.url,
                        matched_title=source_row.title,
                        confidence="high" if exact else "ambiguous",
                        score=float(match.get("score") or 0.0),
                        status="Erreur de chargement album",
                        error=str(exc),
                    )
                )
        return results

    def _analyze_comicvine_books(
        self,
        rows: List[Dict[str, Any]],
        series: Any,
        source_url: str,
    ) -> List[Dict[str, Any]]:
        volume_id, _url = self._comicvine_link_for_series(series)
        if not volume_id:
            volume_id = extract_comicvine_volume_id_from_url(source_url)
        if not volume_id:
            return [
                self._new_book_explorer_analysis_row(
                    row,
                    source="comicvine",
                    source_ref=source_url,
                    status="Lien ComicVine inutilisable",
                )
                for row in rows
            ]
        try:
            issues = self.comicvine_client().list_volume_issues(volume_id, limit=500)
        except Exception as exc:
            return [
                self._new_book_explorer_analysis_row(
                    row,
                    source="comicvine",
                    source_ref=source_url,
                    status="Erreur de chargement ComicVine",
                    error=str(exc),
                )
                for row in rows
            ]
        source_rows = [
            SourceBookRow(
                id=issue.issue_id,
                number=issue.issue_number,
                title=issue.title,
                url=issue.source_url,
                metadata=issue.book_metadata,
                raw=issue,
            )
            for issue in issues
        ]
        matches = match_source_books([row.get("book") for row in rows], source_rows)[: len(rows)]
        results: List[Dict[str, Any]] = []
        for row, match in zip(rows, matches):
            source_index = int(match.get("source_index", -1))
            if source_index < 0 or source_index >= len(source_rows):
                results.append(
                    self._new_book_explorer_analysis_row(
                        row,
                        source="comicvine",
                        source_ref=source_url,
                        status="Aucune issue correspondante",
                    )
                )
                continue
            source_row = source_rows[source_index]
            exact = str(match.get("confidence") or "").casefold().startswith("exact")
            results.append(
                self._new_book_explorer_analysis_row(
                    row,
                    source="comicvine",
                    source_ref=source_row.url,
                    matched_title=source_row.title,
                    confidence="high" if exact else "ambiguous",
                    score=float(match.get("score") or 0.0),
                    candidate=source_row.metadata,
                )
            )
        return results

    def _run_book_explorer_analysis(
        self,
        selected: List[Dict[str, Any]],
        requested_source: str,
    ) -> List[Dict[str, Any]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in selected:
            groups.setdefault(str(row.get("series_id") or ""), []).append(row)
        results: List[Dict[str, Any]] = []
        total = len(groups)
        for group_index, rows in enumerate(groups.values(), start=1):
            series = rows[0].get("series")
            if series is None:
                results.extend(
                    self._new_book_explorer_analysis_row(
                        row,
                        status="Série parente introuvable",
                    )
                    for row in rows
                )
                continue
            choice = choose_book_source(series, requested_source, DEFAULT_BOOK_ENRICHMENT_FIELDS)
            self.auto_match_progress_signal.emit(
                f"Explorateur de tomes — {group_index}/{total} — {getattr(series, 'title', '')}",
                group_index - 1,
                total,
            )
            if not choice.source:
                results.extend(
                    self._new_book_explorer_analysis_row(
                        row,
                        status="Source à associer",
                        error=choice.reason,
                    )
                    for row in rows
                )
                continue
            self.enrichment_history.record_search(
                choice.source,
                str(getattr(series, "id", "") or ""),
                str(getattr(series, "title", "") or ""),
            )
            if choice.source == "manga_news":
                results.extend(self._analyze_manga_news_books(rows, series, choice.url))
            elif choice.source == "bedetheque":
                results.extend(self._analyze_bedetheque_books(rows, choice.url))
            elif choice.source == "comicvine":
                results.extend(self._analyze_comicvine_books(rows, series, choice.url))
        self.auto_match_progress_signal.emit("Explorateur de tomes — analyse terminée", total, total)
        return results

    def analyze_book_explorer_selection(self, source: Any = None) -> None:
        selected = self._selected_book_explorer_rows()
        if not selected:
            QMessageBox.warning(self, "Enrichissement des tomes", "Sélectionnez au moins un tome.")
            return
        requested = source if isinstance(source, str) and source else str(self.book_explorer_enrichment_source.currentData() or "auto")
        self._set_auto_match_progress("Explorateur de tomes — préparation", 0, len(selected))

        def done(rows: List[Dict[str, Any]]) -> None:
            self.book_explorer_analysis_rows = rows
            for row in rows:
                self.enrichment_history.record_book_enrichment(
                    str(row.get("source") or "unmatched"),
                    str(row.get("book_id") or ""),
                    series_id=str(row.get("series_id") or ""),
                    book_title=str(row.get("title") or ""),
                    status=str(row.get("status") or ""),
                    fields=(row.get("payload") or {}).keys(),
                    confidence=str(row.get("confidence") or ""),
                    source_ref=str(row.get("source_ref") or ""),
                )
            self.show_book_explorer_analysis_dialog(rows)

        self.run_worker(
            "Analyse enrichissement des tomes",
            lambda: self._run_book_explorer_analysis(selected, requested),
            done,
        )

    def show_book_explorer_analysis_dialog(self, rows: List[Dict[str, Any]]) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Enrichissement des tomes — validation")
        dialog.resize(1450, 820)
        layout = QVBoxLayout(dialog)
        ready = sum(1 for row in rows if row.get("payload") and not row.get("needs_confirmation"))
        ambiguous = sum(1 for row in rows if row.get("payload") and row.get("needs_confirmation"))
        missing = sum(1 for row in rows if not row.get("source"))
        summary = QLabel(
            f"{len(rows)} tome(s) analysé(s) — {ready} confiance élevée — "
            f"{ambiguous} à valider — {missing} sans source compatible.\n"
            "Les champs peuvent être cochés tome par tome. Titre et titre de tri sont décochés par défaut. "
            "Numéro et numéro de tri sont toujours protégés ; "
            "l'ISBN reste affiché mais non applicable tant que le garde-fou de compatibilité Komga est actif."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        split = QSplitter(Qt.Vertical)
        table = QTableWidget()
        headers = ["Appliquer", "Confiance", "Série", "N°", "Tome Komga", "Source", "Tome source", "Changements", "Statut", "Erreur"]
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(rows))
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        for row_index, row in enumerate(rows):
            include = QTableWidgetItem("")
            include.setFlags((include.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            include.setCheckState(
                Qt.Checked
                if row.get("payload") and not row.get("needs_confirmation")
                else Qt.Unchecked
            )
            include.setData(Qt.UserRole, row)
            table.setItem(row_index, 0, include)
            values = [
                "Élevée" if row.get("confidence") == "high" else ("À valider" if row.get("confidence") else ""),
                row.get("series_title", ""),
                row.get("number", ""),
                row.get("title", ""),
                BOOK_SOURCE_LABELS.get(str(row.get("source") or ""), row.get("source", "")),
                row.get("matched_title", ""),
                ", ".join(metadata_field_label(field) for field in (row.get("payload") or {})),
                row.get("status", ""),
                row.get("error", ""),
            ]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(str(value or ""))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip(str(value or ""))
                table.setItem(row_index, column, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        split.addWidget(table)

        diff = QTableWidget()
        self._init_metadata_table(diff)
        split.addWidget(diff)
        split.setSizes([520, 260])
        layout.addWidget(split, 1)

        active_diff_index = [-1]

        def save_current_diff() -> None:
            index = active_diff_index[0]
            if index < 0 or index >= len(rows):
                return
            payload = self._payload_from_metadata_table(diff)
            allowed = set(DEFAULT_BOOK_ENRICHMENT_FIELDS) | {"tags", "links", "ageRating"}
            payload = {
                field: value
                for field, value in payload.items()
                if field in allowed and field not in {"isbn", "number", "numberSort"}
            }
            rows[index]["payload"] = payload
            changes_item = table.item(index, 7)
            if changes_item is not None:
                changes_item.setText(", ".join(metadata_field_label(field) for field in payload))

        def show_diff() -> None:
            save_current_diff()
            index = table.currentRow()
            if index < 0 or index >= len(rows):
                active_diff_index[0] = -1
                self._fill_metadata_table(diff, {}, {}, BOOK_METADATA_FIELDS)
                return
            row = rows[index]
            self._fill_metadata_table(
                diff,
                row.get("current_metadata") or {},
                row.get("candidate_metadata") or {},
                BOOK_METADATA_FIELDS,
            )
            selected_fields = set((row.get("payload") or {}).keys())
            for diff_row in range(diff.rowCount()):
                field = self._metadata_field_key(diff.item(diff_row, 0))
                include_item = diff.item(diff_row, 3)
                clear_item = diff.item(diff_row, 4)
                if include_item is None:
                    continue
                include_item.setCheckState(Qt.Checked if field in selected_fields else Qt.Unchecked)
                if clear_item is not None:
                    clear_item.setCheckState(Qt.Unchecked)
                if field in {"number", "numberSort", "isbn"}:
                    include_item.setCheckState(Qt.Unchecked)
                    include_item.setFlags(include_item.flags() & ~Qt.ItemIsEnabled)
                    include_item.setToolTip(
                        "Champ protégé par l'Explorateur de tomes."
                        if field != "isbn"
                        else "ISBN non envoyé par le garde-fou de compatibilité Komga actuel."
                    )
            active_diff_index[0] = index

        table.itemSelectionChanged.connect(show_diff)
        if rows:
            table.setCurrentCell(0, 0)
            show_diff()

        buttons = QHBoxLayout()
        manage_button = QPushButton("Gérer la source du tome sélectionné")
        select_high_button = QPushButton(f"Sélectionner toutes les confiances élevées ({ready})")
        apply_button = QPushButton("Simuler la sélection" if self.simulation_enabled() else "Appliquer la sélection")
        close_button = QPushButton("Fermer")
        buttons.addWidget(manage_button)
        buttons.addWidget(select_high_button)
        buttons.addStretch(1)
        buttons.addWidget(apply_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        def manage_source() -> None:
            index = table.currentRow()
            if index < 0 or index >= len(rows):
                return
            source = str(rows[index].get("source") or self.book_explorer_enrichment_source.currentData() or "")
            if source == "auto" or not source:
                labels = ["Manga News", "Bedetheque", "ComicVine", "MangaBaka"]
                label, accepted = QInputDialog.getItem(
                    dialog,
                    "Choisir la source",
                    "Ouvrir la série dans :",
                    labels,
                    0,
                    False,
                )
                if not accepted:
                    return
                source = {
                    "Manga News": "manga_news",
                    "Bedetheque": "bedetheque",
                    "ComicVine": "comicvine",
                    "MangaBaka": "mangabaka",
                }[label]
            dialog.close()
            self.open_book_explorer_series_in_source(source)

        def apply_selected() -> None:
            save_current_diff()
            selected_rows = [
                rows[index]
                for index in range(table.rowCount())
                if table.item(index, 0) is not None
                and table.item(index, 0).checkState() == Qt.Checked
                and rows[index].get("payload")
            ]
            if not selected_rows:
                QMessageBox.information(dialog, "Enrichissement", "Aucun changement sélectionné.")
                return
            dialog.close()
            self.apply_book_explorer_analysis(selected_rows)

        def select_all_high_confidence() -> None:
            save_current_diff()
            selected_count = 0
            for index, row in enumerate(rows):
                include = table.item(index, 0)
                if include is None:
                    continue
                checked = row.get("confidence") == "high" and bool(row.get("payload"))
                include.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                selected_count += int(checked)
            summary.setText(
                f"{len(rows)} tome(s) analysé(s) — {selected_count} confiance élevée sélectionnée(s) — "
                f"{ambiguous} à valider — {missing} sans source compatible.\n"
                "Les champs peuvent être cochés tome par tome. Titre et titre de tri sont décochés par défaut. "
                "Numéro et numéro de tri sont toujours protégés ; "
                "l'ISBN reste affiché mais non applicable tant que le garde-fou de compatibilité Komga est actif."
            )

        manage_button.clicked.connect(manage_source)
        select_high_button.clicked.connect(select_all_high_confidence)
        apply_button.clicked.connect(apply_selected)
        close_button.clicked.connect(dialog.reject)
        dialog.exec()

    def apply_book_explorer_analysis(self, rows: List[Dict[str, Any]]) -> None:
        simulation = self.simulation_enabled()
        if not simulation:
            answer = QMessageBox.question(
                self,
                "Confirmer l'enrichissement",
                f"Appliquer les changements préparés sur {len(rows)} tome(s) ?\n\n"
                "Le numéro et la couverture ne seront jamais modifiés. Un snapshot de restauration sera créé avant chaque écriture.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        def work() -> Dict[str, Any]:
            api = self.komga_api()
            reports: List[Dict[str, Any]] = []
            updated_metadata_by_book_id: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                report = {
                    "book_id": row.get("book_id", ""),
                    "series_id": row.get("series_id", ""),
                    "series_title": row.get("series_title", ""),
                    "book_number": row.get("number", ""),
                    "book_title": row.get("title", ""),
                    "source": row.get("source", ""),
                    "confidence": row.get("confidence", ""),
                    "fields": ";".join((row.get("payload") or {}).keys()),
                    "status": "",
                    "error": "",
                }
                try:
                    current = self._fetch_current_metadata("book", str(row.get("book_id") or ""))
                    selected_fields = set((row.get("payload") or {}).keys())
                    selected_candidate = {
                        field: value
                        for field, value in (row.get("candidate_metadata") or {}).items()
                        if field in selected_fields
                    }
                    payload, _title_confirmation = self._book_explorer_enrichment_payload(
                        current,
                        selected_candidate,
                        include_titles=True,
                    )
                    if not payload:
                        report["status"] = "Aucun changement"
                    elif simulation:
                        report["status"] = "Simulation"
                    else:
                        self._write_metadata_update(
                            api,
                            "book",
                            str(row.get("book_id") or ""),
                            payload,
                            current,
                            source=f"book_explorer_{row.get('source') or 'manual'}",
                            note="Enrichissement depuis l'Explorateur de tomes",
                        )
                        report["status"] = "Appliqué"
                        updated_metadata_by_book_id[str(row.get("book_id") or "")] = {
                            **current,
                            **payload,
                        }
                    self.enrichment_history.record_book_enrichment(
                        str(row.get("source") or "unknown"),
                        str(row.get("book_id") or ""),
                        series_id=str(row.get("series_id") or ""),
                        book_title=str(row.get("title") or ""),
                        status=report["status"],
                        fields=payload.keys(),
                        confidence=str(row.get("confidence") or ""),
                        source_ref=str(row.get("source_ref") or ""),
                    )
                except Exception as exc:
                    report["status"] = "Erreur"
                    report["error"] = str(exc)
                reports.append(report)
            csv_path = self.backup.export_csv(
                f"book_explorer_enrichment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                reports,
            )
            return {
                "rows": reports,
                "csv_path": csv_path,
                "simulation": simulation,
                "updated_metadata_by_book_id": updated_metadata_by_book_id,
            }

        def done(result: Dict[str, Any]) -> None:
            reports = result.get("rows") or []
            counts: Dict[str, int] = {}
            for row in reports:
                status = str(row.get("status") or "Sans statut")
                counts[status] = counts.get(status, 0) + 1
            lines = [
                "Enrichissement des tomes terminé",
                f"Mode : {'simulation' if result.get('simulation') else 'écriture réelle'}",
                f"Rapport : {result.get('csv_path') or ''}",
                "",
                *[f"- {status}: {count}" for status, count in sorted(counts.items())],
            ]
            if not simulation:
                updated_count, hidden_count = self._update_book_explorer_rows_locally(
                    result.get("updated_metadata_by_book_id") or {}
                )
                lines.append("")
                lines.append(
                    f"Liste mise à jour localement : {updated_count} tome(s), sans recharger la bibliothèque."
                )
                if hidden_count:
                    lines.append(
                        f"{hidden_count} tome(s) modifié(s) ne correspondent plus aux filtres actifs et sont maintenant masqués."
                    )
            self._show_text_popup("Rapport enrichissement des tomes", "\n".join(lines))

        self.run_worker("Application enrichissement des tomes", work, done)

    def _explorer_saved_views(self) -> Dict[str, Dict[str, Any]]:
        if not isinstance(getattr(self.config, "ui", None), dict):
            self.config.ui = {}
        views = self.config.ui.setdefault("explorer_saved_views", {})
        if not isinstance(views, dict):
            self.config.ui["explorer_saved_views"] = {}
            views = self.config.ui["explorer_saved_views"]
        return views

    def _refresh_explorer_saved_views(self, selected_name: str = "") -> None:
        combo = getattr(self, "explorer_saved_view_combo", None)
        if combo is None:
            return
        previous = selected_name or str(combo.currentData() or "")
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Choisir une vue…", "")
        for name in sorted(self._explorer_saved_views(), key=str.casefold):
            combo.addItem(name, name)
        index = combo.findData(previous)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _current_explorer_view_payload(self) -> Dict[str, Any]:
        return {
            "library_id": str(self._library_id("explorer") or ""),
            "series_search": self.search_series_text.text(),
            "book_search": self.search_books_text.text(),
            "empty_summary": self.filter_series_empty_summary.isChecked(),
            "language": str(self.filter_series_language.currentData() or ""),
            "status": str(self.filter_series_status.currentData() or "ALL"),
            "link": str(self.filter_series_link_label.currentData() or "ALL"),
        }

    def _save_explorer_view(self, name: str) -> bool:
        clean_name = str(name or "").strip()
        if not clean_name:
            return False
        self._explorer_saved_views()[clean_name] = self._current_explorer_view_payload()
        save_config(self.config, self.config_path)
        self._refresh_explorer_saved_views(clean_name)
        self.log(f"✅ Vue Explorateur enregistrée : {clean_name}")
        return True

    def save_current_explorer_view(self) -> None:
        suggested = str(self.explorer_saved_view_combo.currentData() or "")
        name, accepted = QInputDialog.getText(
            self,
            "Enregistrer la vue",
            "Nom de la vue :",
            text=suggested,
        )
        if accepted:
            self._save_explorer_view(name)

    def apply_selected_explorer_view(self) -> None:
        name = str(self.explorer_saved_view_combo.currentData() or "")
        view = self._explorer_saved_views().get(name)
        if not isinstance(view, dict):
            QMessageBox.information(self, "Vues enregistrées", "Choisissez d'abord une vue.")
            return
        widgets = (
            self.library_combo,
            self.search_series_text,
            self.search_books_text,
            self.filter_series_empty_summary,
            self.filter_series_language,
            self.filter_series_status,
            self.filter_series_link_label,
        )
        for widget in widgets:
            widget.blockSignals(True)
        try:
            library_index = self.library_combo.findData(str(view.get("library_id") or ""))
            if library_index >= 0:
                self.library_combo.setCurrentIndex(library_index)
            self.search_series_text.setText(str(view.get("series_search") or ""))
            self.search_books_text.setText(str(view.get("book_search") or ""))
            self.filter_series_empty_summary.setChecked(bool(view.get("empty_summary")))
            for combo, key, fallback in (
                (self.filter_series_language, "language", ""),
                (self.filter_series_status, "status", "ALL"),
                (self.filter_series_link_label, "link", "ALL"),
            ):
                index = combo.findData(str(view.get(key) or fallback))
                combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self.log(f"ℹ️ Vue Explorateur appliquée : {name}")
        self.load_series()

    def delete_selected_explorer_view(self) -> None:
        name = str(self.explorer_saved_view_combo.currentData() or "")
        if not name:
            return
        if QMessageBox.question(
            self,
            "Supprimer la vue",
            f"Supprimer la vue « {name} » ?",
        ) != QMessageBox.Yes:
            return
        self._explorer_saved_views().pop(name, None)
        save_config(self.config, self.config_path)
        self._refresh_explorer_saved_views()
        self.log(f"Vue Explorateur supprimée : {name}")

    def _build_metadata_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        target_box = QGroupBox("1. Choisir la cible")
        target_layout = QGridLayout(target_box)
        self.meta_library_combo = self._make_library_combo("metadata")
        self.meta_target_type = QComboBox()
        self.meta_target_type.addItem("Série", "series")
        self.meta_target_type.addItem("Tome / livre", "book")
        self.meta_target_combo = QComboBox()
        self.meta_target_combo.setEditable(True)
        self.meta_target_combo.setInsertPolicy(QComboBox.NoInsert)
        self.meta_target_combo.setMinimumWidth(320)
        self.meta_target_id = QLineEdit()
        self.meta_target_id.setPlaceholderText("ID technique — saisie manuelle avancée")
        btn_from_selection = QPushButton("Reprendre la sélection de l'Explorateur")
        btn_load_current = QPushButton("Charger les métadonnées actuelles")
        target_layout.addWidget(QLabel("Bibliothèque"), 0, 0)
        target_layout.addWidget(self.meta_library_combo, 0, 1)
        target_layout.addWidget(QLabel("Type de contenu"), 0, 2)
        target_layout.addWidget(self.meta_target_type, 0, 3)
        target_layout.addWidget(btn_from_selection, 0, 4)
        target_layout.addWidget(QLabel("Cible"), 1, 0)
        target_layout.addWidget(self.meta_target_combo, 1, 1, 1, 3)
        target_layout.addWidget(btn_load_current, 1, 4)
        target_layout.addWidget(QLabel("ID technique"), 2, 0)
        target_layout.addWidget(self.meta_target_id, 2, 1, 1, 4)
        layout.addWidget(target_box)

        workflow_box = QGroupBox("2. Préparer, vérifier, puis appliquer")
        workflow_layout = QHBoxLayout(workflow_box)
        self.meta_workflow_status_label = QLabel("Choisissez une cible pour commencer.")
        self.meta_workflow_status_label.setWordWrap(True)
        self.meta_workflow_status_label.setStyleSheet("font-weight: 600;")
        btn_build_payload = QPushButton("Prévisualiser les changements")
        btn_apply = QPushButton("Appliquer")
        btn_apply.setEnabled(False)
        self.meta_apply_button = btn_apply
        workflow_layout.addWidget(self.meta_workflow_status_label, 1)
        workflow_layout.addWidget(btn_build_payload)
        workflow_layout.addWidget(btn_apply)
        layout.addWidget(workflow_box)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        editor_help = QLabel(
            "Modifiez uniquement les champs nécessaires. « Inclure » ajoute le champ à l'opération ; "
            "« Effacer » envoie une valeur vide contrôlée."
        )
        editor_help.setWordWrap(True)
        left_layout.addWidget(editor_help)
        self.meta_table = QTableWidget()
        self._register_table(self.meta_table, "metadata.diff")
        self._init_metadata_table(self.meta_table)
        left_layout.addWidget(self.meta_table, 1)
        self.meta_advanced_toggle = QCheckBox("Afficher le payload JSON additionnel — avancé")
        self.meta_extra_json = QTextEdit()
        self.meta_extra_json.setMaximumHeight(110)
        self.meta_extra_json.setPlaceholderText("Données techniques JSON optionnelles. Elles sont fusionnées après le tableau.")
        self.meta_extra_json.setVisible(False)
        left_layout.addWidget(self.meta_advanced_toggle)
        left_layout.addWidget(self.meta_extra_json)
        split.addWidget(left)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_title = QLabel("Prévisualisation avant / après")
        preview_title.setStyleSheet("font-weight: 700;")
        self.meta_preview = QTextEdit()
        self.meta_preview.setReadOnly(True)
        self.meta_preview.setPlaceholderText("La comparaison apparaîtra ici avant toute application.")
        preview_layout.addWidget(preview_title)
        preview_layout.addWidget(self.meta_preview, 1)
        split.addWidget(preview_panel)
        split.setSizes([900, 650])
        layout.addWidget(split, 1)

        btn_from_selection.clicked.connect(self.use_selected_for_metadata)
        btn_load_current.clicked.connect(self.load_current_metadata)
        btn_build_payload.clicked.connect(self.simulate_metadata)
        btn_apply.clicked.connect(self.apply_metadata)
        self.meta_advanced_toggle.toggled.connect(self.meta_extra_json.setVisible)
        self.meta_table.itemChanged.connect(lambda *_: self._invalidate_metadata_preview())
        self.meta_extra_json.textChanged.connect(self._invalidate_metadata_preview)
        self.meta_target_id.textChanged.connect(lambda *_: self._invalidate_metadata_preview())
        self.meta_library_combo.currentIndexChanged.connect(lambda *_: self.load_metadata_targets())
        self.meta_target_type.currentIndexChanged.connect(self.on_metadata_target_type_changed)
        self.meta_target_combo.currentIndexChanged.connect(self.on_metadata_target_selected)
        self._add_main_tab(tab, "Métadonnées")

    def _build_collections_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.collection_library_combo = self._make_library_combo("collections")
        self.collection_search = QLineEdit()
        self.collection_search.setPlaceholderText("Filtrer collections...")
        btn_load = QPushButton("Charger collections")
        btn_use_selected = QPushButton("Ouvrir sélection")
        btn_use_explorer = QPushButton("Ajouter série sélectionnée")
        btn_series_collections = QPushButton("Voir collections de la série")
        btn_create = QPushButton("Créer")
        btn_update = QPushButton("Mettre à jour")
        self.collection_id = QLineEdit()
        self.collection_id.setPlaceholderText("ID collection pour update")
        row.addWidget(QLabel("Bibliothèque"))
        row.addWidget(self.collection_library_combo)
        row.addWidget(self.collection_search)
        row.addWidget(btn_load)
        row.addWidget(btn_use_selected)
        row.addWidget(btn_use_explorer)
        row.addWidget(btn_series_collections)
        row.addWidget(btn_create)
        row.addWidget(btn_update)
        layout.addLayout(row)

        self.collection_technical_toggle = QCheckBox("Afficher les identifiants techniques — avancé")
        self.collection_technical_panel = QWidget()
        collection_technical_layout = QHBoxLayout(self.collection_technical_panel)
        collection_technical_layout.setContentsMargins(0, 0, 0, 0)
        collection_technical_layout.addWidget(QLabel("ID de la collection ouverte"))
        collection_technical_layout.addWidget(self.collection_id, 1)
        self.collection_technical_panel.setVisible(False)
        layout.addWidget(self.collection_technical_toggle)
        layout.addWidget(self.collection_technical_panel)

        subtabs = QTabWidget()
        split = QSplitter(Qt.Horizontal)
        self.collections_table = QTableWidget()
        self._register_table(self.collections_table, "collections.list", default_hidden=["ID"])
        split.addWidget(self.collections_table)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        fields = QGroupBox("Champs collection")
        ff = QFormLayout(fields)
        self.collection_name = QLineEdit()
        self.collection_summary = QTextEdit()
        self.collection_summary.setMaximumHeight(80)
        self.collection_series_ids = QTextEdit()
        self.collection_series_ids.setPlaceholderText("Un seriesId par ligne")
        ff.addRow("Nom", self.collection_name)
        ff.addRow("Résumé", self.collection_summary)
        right_layout.addWidget(fields)
        self.collection_member_ids_toggle = QCheckBox("Afficher/modifier les IDs des séries — avancé")
        self.collection_member_ids_panel = QWidget()
        collection_member_ids_layout = QVBoxLayout(self.collection_member_ids_panel)
        collection_member_ids_layout.setContentsMargins(0, 0, 0, 0)
        collection_member_ids_layout.addWidget(QLabel("Un identifiant de série par ligne"))
        collection_member_ids_layout.addWidget(self.collection_series_ids)
        self.collection_member_ids_panel.setVisible(False)
        right_layout.addWidget(self.collection_member_ids_toggle)
        right_layout.addWidget(self.collection_member_ids_panel)
        member_row = QHBoxLayout()
        member_row.addWidget(QLabel("Membres affichés clairement"))
        btn_col_up = QPushButton("Monter")
        btn_col_down = QPushButton("Descendre")
        member_row.addWidget(btn_col_up)
        member_row.addWidget(btn_col_down)
        member_row.addStretch(1)
        right_layout.addLayout(member_row)
        self.collection_members_table = QTableWidget()
        self._register_table(self.collection_members_table, "collections.members", default_hidden=["ID"])
        self._register_series_table_rows(self.collection_members_table, "collection_member_rows")
        right_layout.addWidget(self.collection_members_table, 1)
        self.collection_payload_preview = QTextEdit()
        self.collection_payload_preview.setReadOnly(True)
        self.collection_payload_preview.setMaximumHeight(150)
        right_layout.addWidget(self.collection_payload_preview)
        right_layout.addWidget(QLabel("Collections associées à la série sélectionnée"))
        self.series_collections_table = QTableWidget()
        self._register_table(self.series_collections_table, "collections.series_links", default_hidden=["ID"])
        self._ensure_min_table_visible_rows(self.series_collections_table)
        right_layout.addWidget(self.series_collections_table)
        split.addWidget(right)
        split.setSizes([650, 950])
        subtabs.addTab(split, "Par collection")

        by_series = QWidget()
        by_series_layout = QVBoxLayout(by_series)
        series_toolbar = QHBoxLayout()
        self.collection_series_search = QLineEdit()
        self.collection_series_search.setPlaceholderText("Filtrer séries de la bibliothèque...")
        self.collection_series_without_collection = QCheckBox("Sans collection")
        btn_load_series = QPushButton("Charger séries")
        btn_add_series_to_form = QPushButton("Ajouter sélection → collection")
        btn_add_series_and_save = QPushButton("Ajouter + enregistrer")
        btn_add_series_to_targets = QPushButton("Ajouter série aux collections cochées")
        series_toolbar.addWidget(self.collection_series_search, 1)
        series_toolbar.addWidget(self.collection_series_without_collection)
        series_toolbar.addWidget(btn_load_series)
        series_toolbar.addWidget(btn_add_series_to_form)
        series_toolbar.addWidget(btn_add_series_and_save)
        series_toolbar.addWidget(btn_add_series_to_targets)
        by_series_layout.addLayout(series_toolbar)
        series_split = QSplitter(Qt.Horizontal)
        left_series = QWidget()
        left_series_layout = QVBoxLayout(left_series)
        left_series_layout.addWidget(QLabel("Séries de la bibliothèque"))
        self.collection_library_series_table = QTableWidget()
        self._register_table(self.collection_library_series_table, "collections.library_series", default_hidden=["ID"])
        left_series_layout.addWidget(self.collection_library_series_table, 1)
        middle_series = QWidget()
        middle_series_layout = QVBoxLayout(middle_series)
        middle_series_layout.addWidget(QLabel("Collections existantes (cibles)"))
        collection_target_create_row = QHBoxLayout()
        self.collection_target_new_name = QLineEdit()
        self.collection_target_new_name.setPlaceholderText("Nouvelle collection")
        btn_create_collection_target = QPushButton("Créer")
        collection_target_create_row.addWidget(self.collection_target_new_name, 1)
        collection_target_create_row.addWidget(btn_create_collection_target)
        middle_series_layout.addLayout(collection_target_create_row)
        self.collection_target_collections_table = QTableWidget()
        self._register_table(self.collection_target_collections_table, "collections.by_series_targets", default_hidden=["ID"])
        middle_series_layout.addWidget(self.collection_target_collections_table, 1)
        right_series = QWidget()
        right_series_layout = QVBoxLayout(right_series)
        right_series_layout.addWidget(QLabel("Collections de la série sélectionnée"))
        self.collection_series_collections_table = QTableWidget()
        self._register_table(self.collection_series_collections_table, "collections.by_series_links", default_hidden=["ID"])
        right_series_layout.addWidget(self.collection_series_collections_table, 1)
        series_split.addWidget(left_series)
        series_split.addWidget(middle_series)
        series_split.addWidget(right_series)
        series_split.setSizes([700, 500, 500])
        by_series_layout.addWidget(series_split, 1)
        subtabs.addTab(by_series, "Par série")

        bulk = QWidget()
        bulk_layout = QVBoxLayout(bulk)
        bulk_toolbar = QHBoxLayout()
        self.collection_bulk_series_search = QLineEdit()
        self.collection_bulk_series_search.setPlaceholderText("Filtrer séries de la bibliothèque...")
        self.collection_bulk_without_collection = QCheckBox("Sans collection")
        btn_bulk_reload = QPushButton("Recharger séries")
        btn_bulk_add = QPushButton("Ajouter sélection → collection")
        btn_bulk_save = QPushButton("Ajouter sélection + enregistrer")
        btn_bulk_add_targets = QPushButton("Ajouter aux collections cochées")
        btn_bulk_remove_targets = QPushButton("Retirer des collections cochées")
        bulk_toolbar.addWidget(QLabel("Sélection multiple de séries pour alimenter la collection ouverte"))
        bulk_toolbar.addWidget(self.collection_bulk_series_search, 1)
        bulk_toolbar.addWidget(self.collection_bulk_without_collection)
        bulk_toolbar.addStretch(1)
        bulk_toolbar.addWidget(btn_bulk_reload)
        bulk_toolbar.addWidget(btn_bulk_add)
        bulk_toolbar.addWidget(btn_bulk_save)
        bulk_toolbar.addWidget(btn_bulk_add_targets)
        bulk_toolbar.addWidget(btn_bulk_remove_targets)
        bulk_layout.addLayout(bulk_toolbar)
        bulk_split = QSplitter(Qt.Horizontal)
        bulk_left = QWidget()
        bulk_left_layout = QVBoxLayout(bulk_left)
        bulk_left_layout.addWidget(QLabel("Séries"))
        self.collection_bulk_series_table = QTableWidget()
        self._register_table(self.collection_bulk_series_table, "collections.bulk_series", default_hidden=["ID"])
        bulk_left_layout.addWidget(self.collection_bulk_series_table, 1)
        bulk_right = QWidget()
        bulk_right_layout = QVBoxLayout(bulk_right)
        bulk_right_layout.addWidget(QLabel("Collections existantes (cibles)"))
        collection_bulk_create_row = QHBoxLayout()
        self.collection_bulk_new_name = QLineEdit()
        self.collection_bulk_new_name.setPlaceholderText("Nouvelle collection")
        btn_create_collection_bulk = QPushButton("Créer")
        collection_bulk_create_row.addWidget(self.collection_bulk_new_name, 1)
        collection_bulk_create_row.addWidget(btn_create_collection_bulk)
        bulk_right_layout.addLayout(collection_bulk_create_row)
        self.collection_bulk_target_collections_table = QTableWidget()
        self._register_table(self.collection_bulk_target_collections_table, "collections.bulk_targets", default_hidden=["ID"])
        bulk_right_layout.addWidget(self.collection_bulk_target_collections_table, 1)
        bulk_split.addWidget(bulk_left)
        bulk_split.addWidget(bulk_right)
        bulk_split.setSizes([900, 500])
        bulk_layout.addWidget(bulk_split, 1)
        subtabs.addTab(bulk, "Bulk")

        suggestions = QWidget()
        suggestions_layout = QVBoxLayout(suggestions)
        suggestions_toolbar = QHBoxLayout()
        self.collection_suggestion_path_search = QLineEdit()
        self.collection_suggestion_path_search.setPlaceholderText("Rechercher dans les chemins, ex: /Superman/")
        self.collection_suggestion_source_mode = QComboBox()
        self.collection_suggestion_source_mode.addItem("Depuis collection", "collection")
        self.collection_suggestion_source_mode.addItem("Recherche chemin", "manual")
        self.collection_suggestion_source_mode.addItem("Collection + recherche", "combined")
        self.collection_suggestion_source_mode.setCurrentIndex(2)
        self.collection_suggestion_match_mode = QComboBox()
        self.collection_suggestion_match_mode.addItem("Contient", "contains")
        self.collection_suggestion_match_mode.addItem("Commence par", "starts")
        self.collection_suggestion_match_mode.addItem("Segment exact", "segment")
        self.collection_suggestion_group_mode = QComboBox()
        self.collection_suggestion_group_mode.addItem("Dossier recherché", "anchor")
        self.collection_suggestion_group_mode.addItem("Une suggestion", "single")
        self.collection_suggestion_group_mode.addItem("Dossier parent commun", "parent")
        self.collection_suggestion_group_mode.addItem("Dossier final", "folder")
        self.collection_suggestion_ignore_case = QCheckBox("Ignorer casse")
        self.collection_suggestion_ignore_case.setChecked(True)
        self.collection_suggestion_one_per_series = QCheckBox("Une entrée par série")
        self.collection_suggestion_one_per_series.setChecked(True)
        btn_analyze_collection_suggestions = QPushButton("Analyser")
        suggestions_toolbar.addWidget(self.collection_suggestion_path_search, 1)
        suggestions_toolbar.addWidget(self.collection_suggestion_source_mode)
        suggestions_toolbar.addWidget(self.collection_suggestion_match_mode)
        suggestions_toolbar.addWidget(self.collection_suggestion_group_mode)
        suggestions_toolbar.addWidget(self.collection_suggestion_ignore_case)
        suggestions_toolbar.addWidget(self.collection_suggestion_one_per_series)
        suggestions_toolbar.addWidget(btn_analyze_collection_suggestions)
        suggestions_layout.addLayout(suggestions_toolbar)
        suggestions_split = QSplitter(Qt.Horizontal)
        suggestion_left = QWidget()
        suggestion_left_layout = QVBoxLayout(suggestion_left)
        suggestion_left_layout.addWidget(QLabel("Collections existantes"))
        suggestion_collection_filter_row = QHBoxLayout()
        self.collection_suggestion_collection_search = QLineEdit()
        self.collection_suggestion_collection_search.setPlaceholderText("Filtrer collections...")
        btn_load_suggestion_collections = QPushButton("Charger")
        suggestion_collection_filter_row.addWidget(self.collection_suggestion_collection_search, 1)
        suggestion_collection_filter_row.addWidget(btn_load_suggestion_collections)
        suggestion_left_layout.addLayout(suggestion_collection_filter_row)
        self.collection_suggestion_target_collections_table = QTableWidget()
        self._register_table(self.collection_suggestion_target_collections_table, "collections.suggestion_targets", default_hidden=["ID"])
        suggestion_left_layout.addWidget(self.collection_suggestion_target_collections_table, 1)
        suggestion_center = QWidget()
        suggestion_center_layout = QVBoxLayout(suggestion_center)
        suggestion_center_layout.addWidget(QLabel("Suggestions à ajouter à la collection sélectionnée"))
        self.collection_suggestion_table = QTableWidget()
        self._register_table(self.collection_suggestion_table, "collections.suggestions", default_hidden=["Series IDs"])
        suggestion_center_layout.addWidget(self.collection_suggestion_table, 1)
        suggestion_actions = QHBoxLayout()
        self.collection_suggestion_name = QLineEdit()
        self.collection_suggestion_name.setPlaceholderText("Nom nouvelle collection")
        btn_create_suggested_collection = QPushButton("Créer collection proposée")
        btn_add_suggestion_to_target = QPushButton("Compléter collection sélectionnée")
        suggestion_actions.addWidget(self.collection_suggestion_name, 1)
        suggestion_actions.addWidget(btn_create_suggested_collection)
        suggestion_actions.addWidget(btn_add_suggestion_to_target)
        suggestion_center_layout.addLayout(suggestion_actions)
        suggestion_right = QWidget()
        suggestion_right_layout = QVBoxLayout(suggestion_right)
        suggestion_right_layout.addWidget(QLabel("Détail de la suggestion"))
        self.collection_suggestion_detail = QTextEdit()
        self.collection_suggestion_detail.setReadOnly(True)
        suggestion_right_layout.addWidget(self.collection_suggestion_detail, 1)
        suggestions_split.addWidget(suggestion_left)
        suggestions_split.addWidget(suggestion_center)
        suggestions_split.addWidget(suggestion_right)
        suggestions_split.setSizes([520, 620, 420])
        suggestions_layout.addWidget(suggestions_split, 1)
        subtabs.addTab(suggestions, "Suggestions")

        layout.addWidget(subtabs, 1)

        btn_load.clicked.connect(self.load_collections)
        self.collection_technical_toggle.toggled.connect(self.collection_technical_panel.setVisible)
        self.collection_member_ids_toggle.toggled.connect(self.collection_member_ids_panel.setVisible)
        btn_use_selected.clicked.connect(self.use_selected_collection)
        btn_use_explorer.clicked.connect(self.add_selected_series_to_collection_form)
        btn_series_collections.clicked.connect(self.load_collections_for_selected_series)
        btn_col_up.clicked.connect(lambda: self.move_collection_member(-1))
        btn_col_down.clicked.connect(lambda: self.move_collection_member(1))
        btn_create.clicked.connect(self.create_collection)
        btn_update.clicked.connect(self.update_collection)
        self.collections_table.itemDoubleClicked.connect(lambda *_: self.use_selected_collection())
        self.collections_table.itemSelectionChanged.connect(self.use_selected_collection)
        self.collection_library_combo.currentIndexChanged.connect(lambda *_: self.load_collections())
        self.collection_library_combo.currentIndexChanged.connect(lambda *_: self.load_collection_library_series())
        self.collection_search.returnPressed.connect(self.load_collections)
        btn_load_series.clicked.connect(self.load_collection_library_series)
        self.collection_series_without_collection.stateChanged.connect(lambda *_: self.load_collection_library_series())
        self.collection_series_search.returnPressed.connect(self.load_collection_library_series)
        self.collection_library_series_table.itemSelectionChanged.connect(self.load_collections_for_selected_series)
        self.collection_library_series_table.itemDoubleClicked.connect(lambda *_: self.add_collection_series_selection_to_form())
        btn_add_series_to_form.clicked.connect(self.add_collection_series_selection_to_form)
        btn_add_series_and_save.clicked.connect(self.add_collection_series_selection_and_update)
        btn_add_series_to_targets.clicked.connect(self.add_selected_series_to_selected_collections)
        self.collection_target_collections_table.itemDoubleClicked.connect(lambda *_: self.add_selected_series_to_selected_collections())
        btn_create_collection_target.clicked.connect(lambda: self.create_collection_from_series_selection(self.collection_target_new_name, self.collection_library_series_table))
        btn_bulk_reload.clicked.connect(self.load_collection_library_series)
        self.collection_bulk_series_search.returnPressed.connect(self.load_collection_library_series)
        self.collection_bulk_without_collection.stateChanged.connect(lambda *_: self.load_collection_library_series())
        btn_bulk_add.clicked.connect(self.add_collection_bulk_series_selection_to_form)
        btn_bulk_save.clicked.connect(self.add_collection_bulk_series_selection_and_update)
        btn_bulk_add_targets.clicked.connect(self.add_collection_bulk_series_to_selected_collections)
        btn_bulk_remove_targets.clicked.connect(self.remove_collection_bulk_series_from_selected_collections)
        self.collection_bulk_target_collections_table.itemDoubleClicked.connect(lambda *_: self.add_collection_bulk_series_to_selected_collections())
        btn_create_collection_bulk.clicked.connect(lambda: self.create_collection_from_series_selection(self.collection_bulk_new_name, self.collection_bulk_series_table))
        btn_load_suggestion_collections.clicked.connect(self.load_collection_suggestion_targets)
        self.collection_suggestion_collection_search.returnPressed.connect(self.load_collection_suggestion_targets)
        self.collection_suggestion_target_collections_table.itemSelectionChanged.connect(self.update_collection_suggestion_detail)
        btn_analyze_collection_suggestions.clicked.connect(self.analyze_collection_path_suggestions)
        self.collection_suggestion_path_search.returnPressed.connect(self.analyze_collection_path_suggestions)
        self.collection_suggestion_table.itemSelectionChanged.connect(self.update_collection_suggestion_detail)
        btn_create_suggested_collection.clicked.connect(self.create_collection_from_selected_suggestion)
        btn_add_suggestion_to_target.clicked.connect(self.add_selected_collection_suggestion_to_target)
        self.collection_suggestion_target_collections_table.itemDoubleClicked.connect(lambda *_: self.analyze_collection_path_suggestions())
        subtabs.currentChanged.connect(
            lambda index: self.load_collection_suggestion_targets()
            if subtabs.tabText(index) == "Suggestions" and self.collection_suggestion_target_collections_table.rowCount() == 0
            else None
        )
        self._add_main_tab(tab, "Collections")

    def _build_readlists_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.readlist_library_combo = self._make_library_combo("readlists")
        self.readlist_search = QLineEdit()
        self.readlist_search.setPlaceholderText("Filtrer readlists...")
        btn_load = QPushButton("Charger readlists")
        btn_use_selected = QPushButton("Ouvrir sélection")
        btn_use_explorer = QPushButton("Ajouter livre sélectionné")
        btn_book_readlists = QPushButton("Voir readlists du livre")
        btn_create = QPushButton("Créer")
        btn_update = QPushButton("Mettre à jour")
        self.readlist_id = QLineEdit()
        self.readlist_id.setPlaceholderText("ID readlist pour update")
        row.addWidget(QLabel("Bibliothèque"))
        row.addWidget(self.readlist_library_combo)
        row.addWidget(self.readlist_search)
        row.addWidget(btn_load)
        row.addWidget(btn_use_selected)
        row.addWidget(btn_use_explorer)
        row.addWidget(btn_book_readlists)
        row.addWidget(btn_create)
        row.addWidget(btn_update)
        layout.addLayout(row)

        self.readlist_technical_toggle = QCheckBox("Afficher les identifiants techniques — avancé")
        self.readlist_technical_panel = QWidget()
        readlist_technical_layout = QHBoxLayout(self.readlist_technical_panel)
        readlist_technical_layout.setContentsMargins(0, 0, 0, 0)
        readlist_technical_layout.addWidget(QLabel("ID de la readlist ouverte"))
        readlist_technical_layout.addWidget(self.readlist_id, 1)
        self.readlist_technical_panel.setVisible(False)
        layout.addWidget(self.readlist_technical_toggle)
        layout.addWidget(self.readlist_technical_panel)

        subtabs = QTabWidget()
        split = QSplitter(Qt.Horizontal)
        self.readlists_table = QTableWidget()
        self._register_table(self.readlists_table, "readlists.list", default_hidden=["ID"])
        split.addWidget(self.readlists_table)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        fields = QGroupBox("Champs readlist")
        ff = QFormLayout(fields)
        self.readlist_name = QLineEdit()
        self.readlist_summary = QTextEdit()
        self.readlist_summary.setMaximumHeight(80)
        self.readlist_book_ids = QTextEdit()
        self.readlist_book_ids.setPlaceholderText("Un bookId par ligne, dans l'ordre de lecture")
        ff.addRow("Nom", self.readlist_name)
        ff.addRow("Résumé", self.readlist_summary)
        right_layout.addWidget(fields)
        self.readlist_member_ids_toggle = QCheckBox("Afficher/modifier les IDs des tomes — avancé")
        self.readlist_member_ids_panel = QWidget()
        readlist_member_ids_layout = QVBoxLayout(self.readlist_member_ids_panel)
        readlist_member_ids_layout.setContentsMargins(0, 0, 0, 0)
        readlist_member_ids_layout.addWidget(QLabel("Un identifiant de tome par ligne, dans l'ordre de lecture"))
        readlist_member_ids_layout.addWidget(self.readlist_book_ids)
        self.readlist_member_ids_panel.setVisible(False)
        right_layout.addWidget(self.readlist_member_ids_toggle)
        right_layout.addWidget(self.readlist_member_ids_panel)
        book_row = QHBoxLayout()
        book_row.addWidget(QLabel("Livres affichés clairement"))
        btn_rl_up = QPushButton("Monter")
        btn_rl_down = QPushButton("Descendre")
        book_row.addWidget(btn_rl_up)
        book_row.addWidget(btn_rl_down)
        book_row.addStretch(1)
        right_layout.addLayout(book_row)
        self.readlist_books_table = QTableWidget()
        self._register_table(self.readlist_books_table, "readlists.books", default_hidden=["ID"])
        right_layout.addWidget(self.readlist_books_table, 1)
        self.readlist_payload_preview = QTextEdit()
        self.readlist_payload_preview.setReadOnly(True)
        self.readlist_payload_preview.setMaximumHeight(150)
        right_layout.addWidget(self.readlist_payload_preview)
        right_layout.addWidget(QLabel("Readlists associées au livre sélectionné"))
        self.book_readlists_table = QTableWidget()
        self._register_table(self.book_readlists_table, "readlists.book_links", default_hidden=["ID"])
        self._ensure_min_table_visible_rows(self.book_readlists_table)
        right_layout.addWidget(self.book_readlists_table)
        split.addWidget(right)
        split.setSizes([650, 950])
        subtabs.addTab(split, "Par readlist")

        by_book = QWidget()
        by_book_layout = QVBoxLayout(by_book)
        readlist_source_toolbar = QHBoxLayout()
        self.readlist_series_search = QLineEdit()
        self.readlist_series_search.setPlaceholderText("Filtrer séries...")
        btn_load_readlist_series = QPushButton("Charger séries")
        btn_load_books_for_series = QPushButton("Charger tomes de la série")
        btn_add_books = QPushButton("Ajouter tomes sélectionnés → readlist")
        btn_add_books_save = QPushButton("Ajouter tomes + enregistrer")
        btn_add_books_to_targets = QPushButton("Ajouter tomes aux readlists cochées")
        readlist_source_toolbar.addWidget(self.readlist_series_search, 1)
        readlist_source_toolbar.addWidget(btn_load_readlist_series)
        readlist_source_toolbar.addWidget(btn_load_books_for_series)
        readlist_source_toolbar.addWidget(btn_add_books)
        readlist_source_toolbar.addWidget(btn_add_books_save)
        readlist_source_toolbar.addWidget(btn_add_books_to_targets)
        by_book_layout.addLayout(readlist_source_toolbar)
        book_split = QSplitter(Qt.Horizontal)
        left_books = QWidget()
        left_books_layout = QVBoxLayout(left_books)
        left_books_layout.addWidget(QLabel("Séries de la bibliothèque"))
        self.readlist_library_series_table = QTableWidget()
        self._register_table(self.readlist_library_series_table, "readlists.library_series", default_hidden=["ID"])
        left_books_layout.addWidget(self.readlist_library_series_table, 1)
        center_books = QWidget()
        center_books_layout = QVBoxLayout(center_books)
        center_books_layout.addWidget(QLabel("Tomes de la série sélectionnée"))
        self.readlist_library_books_table = QTableWidget()
        self._register_table(self.readlist_library_books_table, "readlists.library_books", default_hidden=["ID"])
        center_books_layout.addWidget(self.readlist_library_books_table, 1)
        target_readlists = QWidget()
        target_readlists_layout = QVBoxLayout(target_readlists)
        target_readlists_layout.addWidget(QLabel("Readlists existantes (cibles)"))
        readlist_target_create_row = QHBoxLayout()
        self.readlist_target_new_name = QLineEdit()
        self.readlist_target_new_name.setPlaceholderText("Nouvelle readlist")
        btn_create_readlist_target = QPushButton("Créer")
        readlist_target_create_row.addWidget(self.readlist_target_new_name, 1)
        readlist_target_create_row.addWidget(btn_create_readlist_target)
        target_readlists_layout.addLayout(readlist_target_create_row)
        self.readlist_target_readlists_table = QTableWidget()
        self._register_table(self.readlist_target_readlists_table, "readlists.by_book_targets", default_hidden=["ID"])
        target_readlists_layout.addWidget(self.readlist_target_readlists_table, 1)
        right_books = QWidget()
        right_books_layout = QVBoxLayout(right_books)
        right_books_layout.addWidget(QLabel("Readlists du tome sélectionné"))
        self.readlist_book_links_table = QTableWidget()
        self._register_table(self.readlist_book_links_table, "readlists.by_book_links", default_hidden=["ID"])
        right_books_layout.addWidget(self.readlist_book_links_table, 1)
        book_split.addWidget(left_books)
        book_split.addWidget(center_books)
        book_split.addWidget(target_readlists)
        book_split.addWidget(right_books)
        book_split.setSizes([500, 650, 450, 400])
        by_book_layout.addWidget(book_split, 1)
        subtabs.addTab(by_book, "Par série / tome")

        completeness = QWidget()
        completeness_layout = QVBoxLayout(completeness)
        completeness_toolbar = QHBoxLayout()
        self.readlist_completeness_search = QLineEdit()
        self.readlist_completeness_search.setPlaceholderText("Filtrer readlists...")
        self.readlist_completeness_ignore_single = QCheckBox("Ignorer séries avec un seul tome")
        self.readlist_completeness_ignore_single.setChecked(True)
        self.readlist_completeness_show_complete = QCheckBox("Afficher complètes")
        btn_analyze_completeness = QPushButton("Analyser")
        completeness_toolbar.addWidget(self.readlist_completeness_search, 1)
        completeness_toolbar.addWidget(self.readlist_completeness_ignore_single)
        completeness_toolbar.addWidget(self.readlist_completeness_show_complete)
        completeness_toolbar.addWidget(btn_analyze_completeness)
        completeness_layout.addLayout(completeness_toolbar)
        completeness_split = QSplitter(Qt.Vertical)
        self.readlist_completeness_table = QTableWidget()
        self._register_table(self.readlist_completeness_table, "readlists.completeness", default_hidden=["Readlist ID", "Série ID"])
        completeness_split.addWidget(self.readlist_completeness_table)
        self.readlist_completeness_detail = QTextEdit()
        self.readlist_completeness_detail.setReadOnly(True)
        self.readlist_completeness_detail.setMaximumHeight(220)
        completeness_split.addWidget(self.readlist_completeness_detail)
        completeness_split.setSizes([700, 220])
        completeness_layout.addWidget(completeness_split, 1)
        subtabs.addTab(completeness, "Complétude série")

        bulk = QWidget()
        bulk_layout = QVBoxLayout(bulk)
        bulk_toolbar = QHBoxLayout()
        self.readlist_bulk_book_search = QLineEdit()
        self.readlist_bulk_book_search.setPlaceholderText("Filtrer tomes...")
        self.readlist_bulk_without_readlist = QCheckBox("Sans readlist")
        btn_bulk_reload_books = QPushButton("Recharger tomes")
        btn_bulk_add_books = QPushButton("Ajouter sélection → readlist")
        btn_bulk_save_books = QPushButton("Ajouter sélection + enregistrer")
        btn_bulk_add_books_targets = QPushButton("Ajouter aux readlists cochées")
        btn_bulk_remove_books_targets = QPushButton("Retirer des readlists cochées")
        bulk_toolbar.addWidget(QLabel("Sélection multiple de tomes pour alimenter la readlist ouverte"))
        bulk_toolbar.addWidget(self.readlist_bulk_book_search, 1)
        bulk_toolbar.addWidget(self.readlist_bulk_without_readlist)
        bulk_toolbar.addStretch(1)
        bulk_toolbar.addWidget(btn_bulk_reload_books)
        bulk_toolbar.addWidget(btn_bulk_add_books)
        bulk_toolbar.addWidget(btn_bulk_save_books)
        bulk_toolbar.addWidget(btn_bulk_add_books_targets)
        bulk_toolbar.addWidget(btn_bulk_remove_books_targets)
        bulk_layout.addLayout(bulk_toolbar)
        readlist_bulk_split = QSplitter(Qt.Horizontal)
        readlist_bulk_left = QWidget()
        readlist_bulk_left_layout = QVBoxLayout(readlist_bulk_left)
        readlist_bulk_left_layout.addWidget(QLabel("Tomes"))
        self.readlist_bulk_books_table = QTableWidget()
        self._register_table(self.readlist_bulk_books_table, "readlists.bulk_books", default_hidden=["ID"])
        readlist_bulk_left_layout.addWidget(self.readlist_bulk_books_table, 1)
        readlist_bulk_right = QWidget()
        readlist_bulk_right_layout = QVBoxLayout(readlist_bulk_right)
        readlist_bulk_right_layout.addWidget(QLabel("Readlists existantes (cibles)"))
        readlist_bulk_create_row = QHBoxLayout()
        self.readlist_bulk_new_name = QLineEdit()
        self.readlist_bulk_new_name.setPlaceholderText("Nouvelle readlist")
        btn_create_readlist_bulk = QPushButton("Créer")
        readlist_bulk_create_row.addWidget(self.readlist_bulk_new_name, 1)
        readlist_bulk_create_row.addWidget(btn_create_readlist_bulk)
        readlist_bulk_right_layout.addLayout(readlist_bulk_create_row)
        self.readlist_bulk_target_readlists_table = QTableWidget()
        self._register_table(self.readlist_bulk_target_readlists_table, "readlists.bulk_targets", default_hidden=["ID"])
        readlist_bulk_right_layout.addWidget(self.readlist_bulk_target_readlists_table, 1)
        readlist_bulk_split.addWidget(readlist_bulk_left)
        readlist_bulk_split.addWidget(readlist_bulk_right)
        readlist_bulk_split.setSizes([900, 500])
        bulk_layout.addWidget(readlist_bulk_split, 1)
        subtabs.addTab(bulk, "Bulk")

        layout.addWidget(subtabs, 1)

        btn_load.clicked.connect(self.load_readlists)
        self.readlist_technical_toggle.toggled.connect(self.readlist_technical_panel.setVisible)
        self.readlist_member_ids_toggle.toggled.connect(self.readlist_member_ids_panel.setVisible)
        btn_use_selected.clicked.connect(self.use_selected_readlist)
        btn_use_explorer.clicked.connect(self.add_selected_book_to_readlist_form)
        btn_book_readlists.clicked.connect(self.load_readlists_for_selected_book)
        btn_rl_up.clicked.connect(lambda: self.move_readlist_member(-1))
        btn_rl_down.clicked.connect(lambda: self.move_readlist_member(1))
        btn_create.clicked.connect(self.create_readlist)
        btn_update.clicked.connect(self.update_readlist)
        self.readlists_table.itemDoubleClicked.connect(lambda *_: self.use_selected_readlist())
        self.readlists_table.itemSelectionChanged.connect(self.use_selected_readlist)
        self.readlist_library_combo.currentIndexChanged.connect(lambda *_: self.load_readlists())
        self.readlist_library_combo.currentIndexChanged.connect(lambda *_: self.load_readlist_library_series())
        self.readlist_search.returnPressed.connect(self.load_readlists)
        btn_load_readlist_series.clicked.connect(self.load_readlist_library_series)
        btn_load_books_for_series.clicked.connect(self.load_readlist_books_for_selected_series)
        self.readlist_series_search.returnPressed.connect(self.load_readlist_library_series)
        self.readlist_library_series_table.itemSelectionChanged.connect(self.load_readlist_books_for_selected_series)
        self.readlist_library_books_table.itemSelectionChanged.connect(self.load_readlists_for_selected_book)
        self.readlist_library_books_table.itemDoubleClicked.connect(lambda *_: self.add_readlist_book_selection_to_form())
        btn_add_books.clicked.connect(self.add_readlist_book_selection_to_form)
        btn_add_books_save.clicked.connect(self.add_readlist_book_selection_and_update)
        btn_add_books_to_targets.clicked.connect(self.add_selected_books_to_selected_readlists)
        self.readlist_target_readlists_table.itemDoubleClicked.connect(lambda *_: self.add_selected_books_to_selected_readlists())
        btn_create_readlist_target.clicked.connect(lambda: self.create_readlist_from_book_selection(self.readlist_target_new_name, self.readlist_library_books_table))
        btn_analyze_completeness.clicked.connect(self.analyze_readlist_series_completeness)
        self.readlist_completeness_search.returnPressed.connect(self.analyze_readlist_series_completeness)
        self.readlist_completeness_table.itemSelectionChanged.connect(self.update_readlist_completeness_detail)
        btn_bulk_reload_books.clicked.connect(self.load_readlist_books_for_selected_series)
        self.readlist_bulk_book_search.returnPressed.connect(self.load_readlist_books_for_selected_series)
        self.readlist_bulk_without_readlist.stateChanged.connect(lambda *_: self.load_readlist_books_for_selected_series())
        btn_bulk_add_books.clicked.connect(self.add_readlist_bulk_book_selection_to_form)
        btn_bulk_save_books.clicked.connect(self.add_readlist_bulk_book_selection_and_update)
        btn_bulk_add_books_targets.clicked.connect(self.add_readlist_bulk_books_to_selected_readlists)
        btn_bulk_remove_books_targets.clicked.connect(self.remove_readlist_bulk_books_from_selected_readlists)
        self.readlist_bulk_target_readlists_table.itemDoubleClicked.connect(lambda *_: self.add_readlist_bulk_books_to_selected_readlists())
        btn_create_readlist_bulk.clicked.connect(lambda: self.create_readlist_from_book_selection(self.readlist_bulk_new_name, self.readlist_bulk_books_table))
        self._add_main_tab(tab, "Readlists")

    def _build_posters_tab(self) -> None:
        tab = QWidget()
        self.poster_tab = tab
        layout = QVBoxLayout(tab)
        intro = QLabel(
            "Choisissez une cible, vérifiez ses couvertures existantes, puis ajoutez ou sélectionnez une image. "
            "Le mode simulation bloque tous les uploads."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        target_box = QGroupBox("1. Choisir la cible")
        form = QGridLayout(target_box)
        self.poster_library_combo = self._make_library_combo("posters")
        self.poster_type = QComboBox()
        self.poster_type.addItems(["series", "book", "collection", "readlist"])
        self.poster_id = QLineEdit()
        self.poster_url = QLineEdit()
        self.poster_url.setPlaceholderText("URL image optionnelle")
        btn_use_selection = QPushButton("Utiliser sélection")
        btn_show_current = QPushButton("Voir couverture")
        btn_list = QPushButton("Lister posters")
        btn_add_local = QPushButton("Ajouter image locale")
        btn_add_url = QPushButton("Ajouter depuis URL")
        self.poster_select_id = QLineEdit()
        self.poster_select_id.setPlaceholderText("thumbnailId à sélectionner")
        btn_select = QPushButton("Marquer sélectionné")
        form.addWidget(QLabel("Bibliothèque"), 0, 0)
        form.addWidget(self.poster_library_combo, 0, 1)
        form.addWidget(QLabel("Type"), 0, 2)
        form.addWidget(self.poster_type, 0, 3)
        form.addWidget(QLabel("ID"), 0, 4)
        form.addWidget(self.poster_id, 0, 5)
        form.addWidget(btn_use_selection, 0, 6)
        layout.addWidget(target_box)

        actions_box = QGroupBox("2. Vérifier ou ajouter une couverture")
        actions = QGridLayout(actions_box)
        actions.addWidget(btn_show_current, 0, 0)
        actions.addWidget(btn_list, 0, 1)
        actions.addWidget(btn_add_local, 0, 2)
        actions.addWidget(self.poster_url, 1, 0, 1, 2)
        actions.addWidget(btn_add_url, 1, 2)
        layout.addWidget(actions_box)

        select_box = QGroupBox("3. Sélectionner une couverture existante")
        select_layout = QHBoxLayout(select_box)
        select_layout.addWidget(self.poster_select_id, 1)
        select_layout.addWidget(btn_select)
        layout.addWidget(select_box)
        self.poster_status_label = QLabel("Aucune cible active.")
        self.poster_status_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.poster_status_label)
        split = QSplitter(Qt.Horizontal)
        self.poster_table = QTableWidget()
        self._register_table(self.poster_table, "posters.list", default_hidden=["ID"])
        self.poster_preview = QLabel("Aucune couverture")
        self.poster_preview.setAlignment(Qt.AlignCenter)
        split.addWidget(self.poster_table)
        split.addWidget(self.poster_preview)
        split.setSizes([1000, 350])
        layout.addWidget(split, 1)
        btn_use_selection.clicked.connect(self.use_selected_for_poster)
        btn_show_current.clicked.connect(lambda: self._show_cover(self.poster_type.currentText(), self.poster_id.text().strip(), self.poster_preview))
        btn_list.clicked.connect(self.list_posters)
        btn_add_local.clicked.connect(self.add_local_poster)
        btn_add_url.clicked.connect(self.add_url_poster)
        btn_select.clicked.connect(self.select_poster)
        self.poster_id.textChanged.connect(self._update_poster_status)
        self.poster_type.currentTextChanged.connect(self._update_poster_status)
        self._add_main_tab(tab, "Couvertures")

    def _build_csv_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        intro = QLabel(
            "Chargez d'abord le fichier pour identifier son format et prévisualiser les actions. "
            "Aucune application n'est possible tant que cette étape n'est pas valide."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        file_box = QGroupBox("1. Choisir et prévisualiser le fichier")
        file_layout = QHBoxLayout(file_box)
        self.csv_path = QLineEdit()
        self.csv_path.setPlaceholderText("Chemin du fichier CSV")
        btn_browse = QPushButton("Choisir CSV")
        btn_load = QPushButton("Charger et prévisualiser")
        btn_validate_comicinfo = QPushButton("Valider CSV ComicInfo")
        file_layout.addWidget(self.csv_path, 1)
        file_layout.addWidget(btn_browse)
        file_layout.addWidget(btn_load)
        file_layout.addWidget(btn_validate_comicinfo)
        layout.addWidget(file_box)

        apply_box = QGroupBox("2. Vérifier puis appliquer")
        apply_layout = QHBoxLayout(apply_box)
        self.csv_status_label = QLabel("Aucun fichier prévisualisé.")
        self.csv_status_label.setStyleSheet("font-weight: 600;")
        btn_apply = QPushButton("Appliquer les actions prévisualisées")
        btn_apply.setEnabled(False)
        self.csv_apply_button = btn_apply
        apply_layout.addWidget(self.csv_status_label, 1)
        apply_layout.addWidget(btn_apply)
        layout.addWidget(apply_box)

        templates_box = QGroupBox("Modèles et outils de préparation")
        templates_layout = QHBoxLayout(templates_box)
        btn_export_director = QPushButton("Exporter modèle directeur")
        btn_export_specialized = QPushButton("Exporter modèles spécialisés")
        btn_export_comicinfo = QPushButton("Exporter modèle ComicInfo")
        templates_layout.addWidget(btn_export_director)
        templates_layout.addWidget(btn_export_specialized)
        templates_layout.addWidget(btn_export_comicinfo)
        templates_layout.addStretch(1)
        layout.addWidget(templates_box)

        self.csv_preview = QTextEdit()
        self.csv_preview.setReadOnly(True)
        self.csv_preview.setPlaceholderText("Le rapport de prévisualisation apparaîtra ici.")
        layout.addWidget(self.csv_preview, 1)
        btn_browse.clicked.connect(self.browse_csv)
        btn_load.clicked.connect(self.load_csv_preview)
        btn_validate_comicinfo.clicked.connect(self.validate_comicinfo_csv)
        btn_apply.clicked.connect(self.apply_csv_actions)
        self.csv_path.textChanged.connect(self._invalidate_csv_preview)
        btn_export_director.clicked.connect(self.export_director_template)
        btn_export_specialized.clicked.connect(self.export_specialized_templates)
        btn_export_comicinfo.clicked.connect(self.export_comicinfo_director_template)
        self._add_main_tab(tab, "CSV / Bulk")

    def _build_komf_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self._add_source_workflow_header(
            layout,
            "Komf",
            "Utilisez Komf pour rechercher une série puis lancer son identification avec le fournisseur choisi.",
        )
        row = QHBoxLayout()
        self.komf_library_combo = self._make_library_combo("komf")
        self.komf_search_name = QLineEdit()
        self.komf_search_name.setPlaceholderText("Nom à chercher")
        self.komf_library_id = QLineEdit()
        self.komf_library_id.setPlaceholderText("libraryId optionnel")
        self.komf_series_id = QLineEdit()
        self.komf_series_id.setPlaceholderText("seriesId optionnel")
        btn_use_lib = QPushButton("Utiliser biblio")
        btn_search = QPushButton("Recherche Komf")
        row.addWidget(QLabel("Bibliothèque"))
        row.addWidget(self.komf_library_combo)
        row.addWidget(self.komf_search_name, 2)
        row.addWidget(self.komf_library_id, 1)
        row.addWidget(self.komf_series_id, 1)
        row.addWidget(btn_use_lib)
        row.addWidget(btn_search)
        layout.addLayout(row)
        row2 = QHBoxLayout()
        self.komf_provider = QLineEdit()
        self.komf_provider.setPlaceholderText("provider")
        self.komf_provider_id = QLineEdit()
        self.komf_provider_id.setPlaceholderText("providerSeriesId")
        btn_identify = QPushButton("Identifier série")
        row2.addWidget(self.komf_provider)
        row2.addWidget(self.komf_provider_id)
        row2.addWidget(btn_identify)
        layout.addLayout(row2)
        self.komf_output = QTextEdit()
        self.komf_output.setReadOnly(True)
        layout.addWidget(self.komf_output, 1)
        btn_use_lib.clicked.connect(lambda: self.komf_library_id.setText(self._library_id("komf")))
        btn_search.clicked.connect(self.search_komf)
        btn_identify.clicked.connect(self.identify_komf)
        self._add_main_tab(tab, "Komf")

    def _build_bedetheque_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        content.setMinimumHeight(1220)
        layout = QVBoxLayout(content)
        self._add_source_workflow_header(
            layout,
            "Bedetheque",
            "Sélectionnez une série Komga, comparez la fiche et les albums Bedetheque, puis prévisualisez les champs retenus.",
        )
        scroll.setWidget(content)
        tab_layout.addWidget(scroll, 1)

        source_row = QHBoxLayout()
        self.bdt_csv_only = QCheckBox("CSV uniquement (aucune recherche web)")
        self.bdt_csv_path = QLineEdit()
        self.bdt_csv_path.setPlaceholderText("Chemin vers bedetheque.csv")
        btn_browse_csv = QPushButton("Choisir CSV")
        btn_test_source = QPushButton("Tester la source")
        source_row.addWidget(QLabel("Source Bedetheque"))
        source_row.addWidget(self.bdt_csv_only)
        source_row.addWidget(self.bdt_csv_path, 1)
        source_row.addWidget(btn_browse_csv)
        source_row.addWidget(btn_test_source)
        layout.addLayout(source_row)

        top = QHBoxLayout()
        self.bdt_library_combo = self._make_library_combo("bedetheque")
        self.bdt_komga_search = QLineEdit()
        self.bdt_komga_search.setPlaceholderText("Filtrer séries Komga...")
        btn_load_komga = QPushButton("Charger séries Komga")
        btn_queue_selected = QPushButton("Ajouter sélection à la file")
        btn_open_queue = QPushButton("Ouvrir file Bedetheque")
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.bdt_library_combo, 2)
        top.addWidget(self.bdt_komga_search, 2)
        top.addStretch(1)
        top.addWidget(btn_load_komga)
        top.addWidget(btn_queue_selected)
        top.addWidget(btn_open_queue)
        layout.addLayout(top)
        layout.addLayout(self._make_source_series_filters_row("bdt", self.load_bedetheque_komga_series, fixed_link_labels=["Bedetheque"]))

        workflow_tabs = QTabWidget()
        workflow_tabs.setMinimumHeight(920)

        # Série ---------------------------------------------------------
        serie_tab = QWidget()
        serie_layout = QVBoxLayout(serie_tab)
        serie_split = QSplitter(Qt.Vertical)
        serie_top_split = QSplitter(Qt.Horizontal)

        komga_series_box = QGroupBox("Komga — séries")
        komga_series_layout = QVBoxLayout(komga_series_box)
        self.bdt_komga_series_table = QTableWidget()
        self._register_table(self.bdt_komga_series_table, "bedetheque.komga_series", default_hidden=["ID"])
        self._register_series_table_rows(self.bdt_komga_series_table, "bdt_komga_series_rows")
        self.bdt_komga_series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bdt_komga_series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        komga_series_layout.addWidget(self.bdt_komga_series_table, 1)
        serie_top_split.addWidget(komga_series_box)

        bdt_results_box = QGroupBox("Bedetheque — recherche série")
        bdt_results_layout = QVBoxLayout(bdt_results_box)
        search_row = QHBoxLayout()
        self.bdt_query = QLineEdit()
        self.bdt_query.setPlaceholderText("Recherche Bedetheque, ex: Thorgal")
        btn_from_komga = QPushButton("Titre Komga → recherche")
        btn_search = QPushButton("Rechercher")
        btn_scrape_series = QPushButton("Scraper série sélectionnée")
        search_row.addWidget(self.bdt_query, 3)
        search_row.addWidget(btn_from_komga)
        search_row.addWidget(btn_search)
        search_row.addWidget(btn_scrape_series)
        bdt_results_layout.addLayout(search_row)
        self.bdt_results_table = QTableWidget()
        self._register_table(self.bdt_results_table, "bedetheque.results", default_hidden=["URL"])
        bdt_results_split = QSplitter(Qt.Vertical)
        bdt_results_split.addWidget(self.bdt_results_table)
        bdt_results_split.addWidget(self._make_selection_detail_panel("bedetheque.result", "Détails résultat Bedetheque"))
        bdt_results_split.setSizes([420, 180])
        bdt_results_layout.addWidget(bdt_results_split, 1)
        serie_top_split.addWidget(bdt_results_box)
        serie_top_split.setSizes([760, 860])
        serie_split.addWidget(serie_top_split)

        serie_diff_box = QGroupBox("Comparaison série")
        serie_diff_layout = QVBoxLayout(serie_diff_box)
        serie_buttons = QHBoxLayout()
        btn_preview_series = QPushButton("Prévisualiser série")
        btn_apply_series = QPushButton("Appliquer série")
        btn_fit_series_columns = QPushButton("Ajuster colonnes")
        btn_compact_series_rows = QPushButton("Lignes compactes")
        btn_fit_series_rows = QPushButton("Lignes auto")
        serie_buttons.addWidget(btn_preview_series)
        serie_buttons.addWidget(btn_apply_series)
        serie_buttons.addWidget(btn_fit_series_columns)
        serie_buttons.addWidget(btn_compact_series_rows)
        serie_buttons.addWidget(btn_fit_series_rows)
        serie_buttons.addStretch(1)
        serie_diff_layout.addLayout(serie_buttons)
        serie_diff_split = QSplitter(Qt.Vertical)
        self.bdt_series_metadata_table = QTableWidget()
        self._register_table(self.bdt_series_metadata_table, "bedetheque.series_diff")
        self._init_metadata_table(self.bdt_series_metadata_table)
        serie_diff_split.addWidget(self.bdt_series_metadata_table)
        self.bdt_series_preview = QTextEdit()
        self.bdt_series_preview.setReadOnly(True)
        self.bdt_series_preview.setLineWrapMode(QTextEdit.WidgetWidth)
        self.bdt_series_preview.setStyleSheet("font-family: monospace;")
        self.bdt_series_preview.setMinimumHeight(180)
        serie_diff_split.addWidget(self.bdt_series_preview)
        serie_diff_split.setSizes([300, 220])
        serie_diff_layout.addWidget(serie_diff_split, 1)
        serie_split.addWidget(serie_diff_box)
        serie_split.setSizes([520, 420])
        serie_layout.addWidget(serie_split, 1)
        workflow_tabs.addTab(serie_tab, "Série")

        # Tomes ---------------------------------------------------------
        books_tab = QWidget()
        books_layout = QVBoxLayout(books_tab)
        books_split = QSplitter(Qt.Vertical)
        books_top_split = QSplitter(Qt.Horizontal)

        books_box = QGroupBox("Komga — tomes / livres de la série")
        books_box_layout = QVBoxLayout(books_box)
        self.bdt_komga_books_table = QTableWidget()
        self._register_table(self.bdt_komga_books_table, "bedetheque.komga_books", default_hidden=["ID"])
        self.bdt_komga_books_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bdt_komga_books_table.setSelectionMode(QAbstractItemView.SingleSelection)
        books_box_layout.addWidget(self.bdt_komga_books_table, 1)
        books_top_split.addWidget(books_box)

        albums_box = QGroupBox("Bedetheque — albums scrapés")
        albums_layout = QVBoxLayout(albums_box)
        album_actions = QHBoxLayout()
        btn_scrape_album = QPushButton("Scraper album sélectionné")
        btn_scrape_all = QPushButton("Scraper tous albums")
        album_actions.addWidget(btn_scrape_album)
        album_actions.addWidget(btn_scrape_all)
        album_actions.addStretch(1)
        albums_layout.addLayout(album_actions)
        self.bdt_albums_status = QLabel("")
        self.bdt_albums_status.setWordWrap(True)
        albums_layout.addWidget(self.bdt_albums_status)
        self.bdt_albums_table = QTableWidget()
        self._register_table(self.bdt_albums_table, "bedetheque.albums", default_hidden=["URL"])
        bdt_albums_split = QSplitter(Qt.Vertical)
        bdt_albums_split.addWidget(self.bdt_albums_table)
        bdt_albums_split.addWidget(self._make_selection_detail_panel("bedetheque.album", "Détails album Bedetheque"))
        bdt_albums_split.setSizes([360, 180])
        albums_layout.addWidget(bdt_albums_split, 1)
        books_top_split.addWidget(albums_box)
        books_top_split.setSizes([780, 840])
        books_split.addWidget(books_top_split)

        matching_box = QGroupBox("Matching tomes")
        matching_layout = QVBoxLayout(matching_box)
        match_buttons = QHBoxLayout()
        btn_match = QPushButton("Auto-match tomes")
        btn_preview_book = QPushButton("Prévisualiser tome sélectionné")
        btn_apply_book = QPushButton("Appliquer tome(s) sélectionné(s)")
        match_buttons.addWidget(btn_match)
        match_buttons.addWidget(btn_preview_book)
        match_buttons.addWidget(btn_apply_book)
        match_buttons.addStretch(1)
        matching_layout.addLayout(match_buttons)
        lower_split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Matching Komga ↔ Bedetheque — sélectionne une ou plusieurs lignes"))
        self.bdt_match_table = QTableWidget()
        self._register_table(self.bdt_match_table, "bedetheque.tome_matching", default_hidden=["Book ID"])
        left_layout.addWidget(self.bdt_match_table, 1)
        lower_split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        diff_header = QHBoxLayout()
        diff_header.addWidget(QLabel("Diff métadonnées du tome sélectionné"))
        diff_header.addStretch(1)
        btn_open_book_preview = QPushButton("Ouvrir aperçu")
        diff_header.addWidget(btn_open_book_preview)
        right_layout.addLayout(diff_header)
        right_split = QSplitter(Qt.Vertical)
        self.bdt_book_metadata_table = QTableWidget()
        self._register_table(self.bdt_book_metadata_table, "bedetheque.book_diff")
        self._init_metadata_table(self.bdt_book_metadata_table)
        right_split.addWidget(self.bdt_book_metadata_table)
        self.bdt_book_preview = QTextEdit()
        self.bdt_book_preview.setReadOnly(True)
        self.bdt_book_preview.setLineWrapMode(QTextEdit.WidgetWidth)
        self.bdt_book_preview.setStyleSheet("font-family: monospace;")
        self.bdt_book_preview.setMinimumHeight(240)
        right_split.addWidget(self.bdt_book_preview)
        right_split.setSizes([220, 360])
        right_layout.addWidget(right_split, 1)
        lower_split.addWidget(right)
        lower_split.setSizes([760, 860])
        matching_layout.addWidget(lower_split, 1)
        books_split.addWidget(matching_box)
        books_split.setSizes([360, 560])
        books_layout.addWidget(books_split, 1)
        workflow_tabs.addTab(books_tab, "Tomes")

        # Batch / rapports ---------------------------------------------
        batch_tab = QWidget()
        batch_layout = QVBoxLayout(batch_tab)
        batch_help = QTextEdit()
        batch_help.setReadOnly(True)
        batch_help.setPlainText(
            "Batch Bedetheque\n\n"
            "Les actions batch restent déclenchées depuis l'onglet Explorateur ou la file Bedetheque.\n"
            "Les comptes rendus détaillés sont affichés en popup, écrits dans les logs, et exportés en CSV dans le dossier de backup de session.\n\n"
            "Garde-fous conservés : sélection obligatoire, simulation respectée, backup avant PATCH réel."
        )
        batch_layout.addWidget(batch_help, 1)
        workflow_tabs.addTab(batch_tab, "Traitement en masse / rapports")

        # Debug ---------------------------------------------------------
        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        self.bdt_raw = QTextEdit()
        self.bdt_raw.setReadOnly(True)
        raw_layout.addWidget(self.bdt_raw, 1)
        workflow_tabs.addTab(raw_tab, "Données techniques")

        layout.addWidget(workflow_tabs, 1)

        # Hidden compatibility fields used by existing selection/apply helpers.
        self.bdt_target_type = QComboBox()
        self.bdt_target_type.addItems(["series", "book"])
        self.bdt_target_id = QLineEdit()
        self.bdt_album_number = QLineEdit()

        btn_load_komga.clicked.connect(self.load_bedetheque_komga_series)
        self.bdt_library_combo.currentIndexChanged.connect(lambda *_: self.load_bedetheque_komga_series())
        self.bdt_csv_only.toggled.connect(self.on_bedetheque_source_changed)
        btn_browse_csv.clicked.connect(self.choose_bedetheque_csv)
        btn_test_source.clicked.connect(self.test_bedetheque_source)
        btn_queue_selected.clicked.connect(self.add_selected_bedetheque_series_to_queue)
        btn_open_queue.clicked.connect(self.open_bedetheque_queue_dialog)
        self.bdt_komga_search.returnPressed.connect(self.load_bedetheque_komga_series)
        self.bdt_komga_series_table.itemSelectionChanged.connect(self.on_bedetheque_komga_series_selected)
        self.bdt_komga_books_table.itemSelectionChanged.connect(self.on_bedetheque_komga_book_selected)
        btn_from_komga.clicked.connect(self.use_selected_for_bedetheque)
        btn_search.clicked.connect(self.search_bedetheque)
        self.bdt_query.returnPressed.connect(self.search_bedetheque)
        self.bdt_results_table.itemSelectionChanged.connect(self.on_bedetheque_result_selected)
        self.bdt_results_table.itemDoubleClicked.connect(lambda *_: self.scrape_selected_bedetheque_series())
        btn_scrape_series.clicked.connect(self.scrape_selected_bedetheque_series)
        self.bdt_albums_table.itemSelectionChanged.connect(self.on_bedetheque_album_selected)
        self.bdt_albums_table.itemDoubleClicked.connect(lambda *_: self.scrape_selected_bedetheque_album())
        btn_scrape_album.clicked.connect(self.scrape_selected_bedetheque_album)
        btn_scrape_all.clicked.connect(self.scrape_all_bedetheque_albums)
        btn_match.clicked.connect(self.match_bedetheque_tomes)
        self.bdt_match_table.itemSelectionChanged.connect(self.on_bedetheque_match_selected)
        btn_preview_series.clicked.connect(self.preview_bedetheque_series)
        btn_apply_series.clicked.connect(self.apply_bedetheque_series)
        btn_fit_series_columns.clicked.connect(lambda: self._fit_metadata_table_columns(self.bdt_series_metadata_table))
        btn_compact_series_rows.clicked.connect(lambda: self._compact_metadata_table(self.bdt_series_metadata_table))
        btn_fit_series_rows.clicked.connect(lambda: self._fit_metadata_table_rows(self.bdt_series_metadata_table))
        btn_preview_book.clicked.connect(self.preview_bedetheque_book)
        btn_apply_book.clicked.connect(self.apply_bedetheque_book)
        btn_open_book_preview.clicked.connect(self.open_bedetheque_book_preview_popup)
        self._add_main_tab(tab, "Bedetheque")

    def choose_bedetheque_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choisir le CSV Bedetheque",
            self.bdt_csv_path.text().strip(),
            "CSV (*.csv);;Tous fichiers (*)",
        )
        if path:
            self.bdt_csv_path.setText(path)
            self.bdt_csv_only.setChecked(True)

    def on_bedetheque_source_changed(self, *_: Any) -> None:
        csv_mode = self.bdt_csv_only.isChecked()
        self.bdt_csv_path.setEnabled(csv_mode)

    def test_bedetheque_source(self) -> None:
        self.run_worker(
            "Test source Bedetheque",
            lambda: self.bedetheque_client().test(),
            lambda result: self.log(f"✅ {result}"),
        )

    def _build_mangabaka_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        content.setMinimumHeight(1020)
        layout = QVBoxLayout(content)
        self._add_source_workflow_header(
            layout,
            "MangaBaka",
            "Sélectionnez une série manga, chargez le meilleur candidat, contrôlez les différences puis appliquez uniquement les champs choisis.",
        )
        scroll.setWidget(content)
        tab_layout.addWidget(scroll, 1)

        top = QHBoxLayout()
        self.mbk_library_combo = self._make_library_combo("mangabaka")
        self.mbk_komga_search = QLineEdit()
        self.mbk_komga_search.setPlaceholderText("Filtrer séries Komga...")
        btn_load_komga = QPushButton("Charger séries Komga")
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.mbk_library_combo, 2)
        top.addWidget(self.mbk_komga_search, 2)
        top.addStretch(1)
        top.addWidget(btn_load_komga)
        layout.addLayout(top)
        layout.addLayout(self._make_source_series_filters_row("mbk", self.load_mangabaka_komga_series, fixed_link_labels=["MangaBaka"]))

        workflow_tabs = QTabWidget()
        workflow_tabs.setMinimumHeight(860)

        serie_tab = QWidget()
        serie_layout = QVBoxLayout(serie_tab)
        serie_split = QSplitter(Qt.Vertical)
        top_split = QSplitter(Qt.Horizontal)

        komga_box = QGroupBox("Komga — séries")
        komga_layout = QVBoxLayout(komga_box)
        self.mbk_komga_series_table = QTableWidget()
        self._register_table(self.mbk_komga_series_table, "mangabaka.komga_series", default_hidden=["ID"])
        self._register_series_table_rows(self.mbk_komga_series_table, "mbk_komga_series_rows")
        self.mbk_komga_series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mbk_komga_series_table.setSelectionMode(QAbstractItemView.SingleSelection)
        komga_layout.addWidget(self.mbk_komga_series_table, 1)
        top_split.addWidget(komga_box)

        mbk_box = QGroupBox("MangaBaka — recherche API")
        mbk_layout = QVBoxLayout(mbk_box)
        search_row = QHBoxLayout()
        self.mbk_query = QLineEdit()
        self.mbk_query.setPlaceholderText("Recherche MangaBaka, ex: One Piece")
        btn_from_komga = QPushButton("Titre Komga → recherche")
        btn_search = QPushButton("Rechercher")
        btn_fetch = QPushButton("Charger résultat sélectionné")
        btn_cover = QPushButton("Cover → onglet Couvertures")
        self.mbk_filter_manga_only = QCheckBox("type=manga uniquement")
        self.mbk_filter_manga_only.setChecked(True)
        search_row.addWidget(self.mbk_query, 3)
        search_row.addWidget(self.mbk_filter_manga_only)
        search_row.addWidget(btn_from_komga)
        search_row.addWidget(btn_search)
        search_row.addWidget(btn_fetch)
        search_row.addWidget(btn_cover)
        mbk_layout.addLayout(search_row)
        self.mbk_results_table = QTableWidget()
        self._register_table(self.mbk_results_table, "mangabaka.results", default_hidden=["ID", "URL"])
        mbk_layout.addWidget(QLabel("Résultats MangaBaka — aucun résultat n'est appliqué automatiquement"))
        mbk_results_split = QSplitter(Qt.Vertical)
        mbk_results_split.addWidget(self.mbk_results_table)
        mbk_results_split.addWidget(self._make_selection_detail_panel("mangabaka.result", "Détails résultat MangaBaka"))
        mbk_results_split.setSizes([420, 180])
        mbk_layout.addWidget(mbk_results_split, 1)
        top_split.addWidget(mbk_box)
        top_split.setSizes([720, 900])
        serie_split.addWidget(top_split)

        diff_box = QGroupBox("Comparaison série")
        diff_layout = QVBoxLayout(diff_box)
        buttons = QHBoxLayout()
        btn_preview = QPushButton("Prévisualiser série")
        btn_apply = QPushButton("Appliquer série")
        btn_fit_columns = QPushButton("Ajuster colonnes")
        btn_compact_rows = QPushButton("Lignes compactes")
        btn_fit_rows = QPushButton("Lignes auto")
        buttons.addWidget(btn_preview)
        buttons.addWidget(btn_apply)
        buttons.addWidget(btn_fit_columns)
        buttons.addWidget(btn_compact_rows)
        buttons.addWidget(btn_fit_rows)
        buttons.addStretch(1)
        diff_layout.addLayout(buttons)
        diff_split = QSplitter(Qt.Vertical)
        self.mbk_series_metadata_table = QTableWidget()
        self._register_table(self.mbk_series_metadata_table, "mangabaka.series_diff")
        self._init_metadata_table(self.mbk_series_metadata_table)
        diff_split.addWidget(self.mbk_series_metadata_table)
        self.mbk_series_preview = QTextEdit()
        self.mbk_series_preview.setReadOnly(True)
        self.mbk_series_preview.setLineWrapMode(QTextEdit.WidgetWidth)
        self.mbk_series_preview.setStyleSheet("font-family: monospace;")
        self.mbk_series_preview.setMinimumHeight(200)
        diff_split.addWidget(self.mbk_series_preview)
        diff_split.setSizes([320, 240])
        diff_layout.addWidget(diff_split, 1)
        serie_split.addWidget(diff_box)
        serie_split.setSizes([520, 420])
        serie_layout.addWidget(serie_split, 1)
        workflow_tabs.addTab(serie_tab, "Série")

        books_tab = QWidget()
        books_layout = QVBoxLayout(books_tab)
        books_split = QSplitter(Qt.Horizontal)
        mbk_books_box = QGroupBox("Komga — tomes de la série sélectionnée")
        mbk_books_layout = QVBoxLayout(mbk_books_box)
        self.mbk_komga_books_table = QTableWidget()
        self._register_table(self.mbk_komga_books_table, "mangabaka.komga_books", default_hidden=["ID"])
        self.mbk_komga_books_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mbk_komga_books_table.setSelectionMode(QAbstractItemView.SingleSelection)
        mbk_books_layout.addWidget(self.mbk_komga_books_table, 1)
        books_split.addWidget(mbk_books_box)

        mbk_books_help = QTextEdit()
        mbk_books_help.setReadOnly(True)
        mbk_books_help.setLineWrapMode(QTextEdit.WidgetWidth)
        mbk_books_help.setPlainText(
            "Tomes MangaBaka\n\n"
            "Le client MangaBaka actuel expose la recherche et la fiche série, mais pas de route tome/volume exploitable. "
            "Je n'invente donc pas un matching tome faux.\n\n"
            "Ce sous-onglet charge les tomes Komga pour garder le même découpage Série / Tomes. "
            "Dès qu'une route MangaBaka volume/chapitre est disponible, on pourra brancher ici le même flux que Bedetheque ou ComicVine : "
            "liste source, auto-match prudent, validation utilisateur, prévisualisation, puis application."
        )
        books_split.addWidget(mbk_books_help)
        books_split.setSizes([760, 860])
        books_layout.addWidget(books_split, 1)
        workflow_tabs.addTab(books_tab, "Tomes")

        batch_tab = QWidget()
        batch_layout = QVBoxLayout(batch_tab)
        batch_help = QTextEdit()
        batch_help.setReadOnly(True)
        batch_help.setPlainText(
            "Batch MangaBaka\n\n"
            "Les actions batch restent déclenchées depuis l'onglet Explorateur.\n"
            "Les comptes rendus détaillés sont affichés en popup, écrits dans les logs, et exportés en CSV dans le dossier de backup de session.\n\n"
            "Garde-fous conservés : sélection obligatoire, type=manga par défaut, simulation respectée, backup avant PATCH réel."
        )
        batch_layout.addWidget(batch_help, 1)
        workflow_tabs.addTab(batch_tab, "Traitement en masse / rapports")

        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        self.mbk_raw = QTextEdit()
        self.mbk_raw.setReadOnly(True)
        raw_layout.addWidget(self.mbk_raw, 1)
        workflow_tabs.addTab(raw_tab, "Données techniques")

        layout.addWidget(workflow_tabs, 1)

        self.mbk_target_id = QLineEdit()
        self.mbk_target_id.setVisible(False)

        btn_load_komga.clicked.connect(self.load_mangabaka_komga_series)
        self.mbk_library_combo.currentIndexChanged.connect(lambda *_: self.load_mangabaka_komga_series())
        self.mbk_komga_search.returnPressed.connect(self.load_mangabaka_komga_series)
        self.mbk_komga_series_table.itemSelectionChanged.connect(self.on_mangabaka_komga_series_selected)
        btn_from_komga.clicked.connect(self.use_selected_for_mangabaka)
        btn_search.clicked.connect(self.search_mangabaka)
        self.mbk_query.returnPressed.connect(self.search_mangabaka)
        btn_fetch.clicked.connect(self.fetch_selected_mangabaka_series)
        self.mbk_results_table.itemSelectionChanged.connect(self.on_mangabaka_result_selected)
        self.mbk_results_table.itemDoubleClicked.connect(lambda *_: self.fetch_selected_mangabaka_series())
        btn_preview.clicked.connect(self.preview_mangabaka_series)
        btn_apply.clicked.connect(self.apply_mangabaka_series)
        btn_fit_columns.clicked.connect(lambda: self._fit_metadata_table_columns(self.mbk_series_metadata_table))
        btn_compact_rows.clicked.connect(lambda: self._compact_metadata_table(self.mbk_series_metadata_table))
        btn_fit_rows.clicked.connect(lambda: self._fit_metadata_table_rows(self.mbk_series_metadata_table))
        btn_cover.clicked.connect(self.send_mangabaka_cover_to_posters)
        self._add_main_tab(tab, "MangaBaka")

    def _build_manga_news_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        content.setMinimumHeight(1080)
        layout = QVBoxLayout(content)
        self._add_source_workflow_header(
            layout,
            "Manga News",
            "Utilisez Manga News pour les résumés, les volumes et les informations de parution après comparaison avec Komga.",
        )
        scroll.setWidget(content)
        tab_layout.addWidget(scroll, 1)

        top = QHBoxLayout()
        self.mn_library_combo = self._make_library_combo("manga_news")
        self.mn_komga_search = QLineEdit()
        self.mn_komga_search.setPlaceholderText("Filtrer séries Komga...")
        btn_load_komga = QPushButton("Charger séries Komga")
        btn_from_komga = QPushButton("Utiliser sélection explorateur")
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.mn_library_combo, 2)
        top.addWidget(self.mn_komga_search, 2)
        top.addWidget(btn_load_komga)
        top.addWidget(btn_from_komga)
        layout.addLayout(top)
        layout.addLayout(self._make_source_series_filters_row("mn", self.load_manga_news_komga_series, fixed_link_labels=["Manga-News"]))

        main_split = QSplitter(Qt.Horizontal)
        main_split.setChildrenCollapsible(False)
        main_split.setMinimumHeight(360)

        left_box = QGroupBox("Komga — séries")
        left_layout = QVBoxLayout(left_box)
        self.mn_komga_series_table = QTableWidget()
        self._register_table(self.mn_komga_series_table, "manga_news.komga_series", default_hidden=["ID"])
        self._register_series_table_rows(self.mn_komga_series_table, "mn_komga_series_rows")
        left_layout.addWidget(self.mn_komga_series_table, 1)
        main_split.addWidget(left_box)

        right_box = QGroupBox("Manga News — recherche API perso")
        right_layout = QVBoxLayout(right_box)
        search_row = QHBoxLayout()
        self.mn_query = QLineEdit()
        self.mn_query.setPlaceholderText("Recherche Manga News, ex: One Piece")
        self.mn_filter_manga_only = QCheckBox("media_kind=manga uniquement")
        self.mn_filter_manga_only.setChecked(True)
        btn_search = QPushButton("Rechercher")
        btn_fetch = QPushButton("Charger résultat")
        search_row.addWidget(self.mn_query, 2)
        search_row.addWidget(self.mn_filter_manga_only)
        search_row.addWidget(btn_search)
        search_row.addWidget(btn_fetch)
        right_layout.addLayout(search_row)
        mn_results_split = QSplitter(Qt.Vertical)
        self.mn_results_table = QTableWidget()
        self._register_table(self.mn_results_table, "manga_news.results", default_hidden=["Slug", "URL"])
        mn_results_split.addWidget(self.mn_results_table)
        mn_results_split.addWidget(self._make_selection_detail_panel("manga_news.result", "Détails résultat Manga News"))
        mn_results_split.setSizes([520, 220])
        right_layout.addWidget(mn_results_split, 1)
        main_split.addWidget(right_box)
        main_split.setSizes([620, 900])
        layout.addWidget(main_split, 1)

        workflow_tabs = QTabWidget()
        workflow_tabs.setMinimumHeight(760)
        serie_tab = QWidget()
        serie_layout = QVBoxLayout(serie_tab)
        actions = QHBoxLayout()
        btn_preview = QPushButton("Prévisualiser")
        btn_apply = QPushButton("Appliquer série")
        btn_fit_columns = QPushButton("Ajuster colonnes")
        btn_compact_rows = QPushButton("Lignes compactes")
        btn_fit_rows = QPushButton("Lignes auto")
        actions.addWidget(btn_preview)
        actions.addWidget(btn_apply)
        actions.addWidget(btn_fit_columns)
        actions.addWidget(btn_compact_rows)
        actions.addWidget(btn_fit_rows)
        actions.addStretch(1)
        serie_layout.addLayout(actions)
        serie_split = QSplitter(Qt.Vertical)
        self.mn_series_metadata_table = QTableWidget()
        self._register_table(self.mn_series_metadata_table, "manga_news.series_diff")
        self._fill_manga_news_metadata_table({}, {})
        self.mn_series_preview = QTextEdit()
        self.mn_series_preview.setReadOnly(True)
        serie_split.addWidget(self.mn_series_metadata_table)
        serie_split.addWidget(self.mn_series_preview)
        serie_split.setSizes([520, 420])
        serie_layout.addWidget(serie_split, 1)
        workflow_tabs.addTab(serie_tab, "Série")

        books_tab = QWidget()
        books_layout = QVBoxLayout(books_tab)
        books_split = QSplitter(Qt.Horizontal)
        mn_books_left = QWidget()
        mn_books_left_layout = QVBoxLayout(mn_books_left)
        mn_books_left_layout.addWidget(QLabel("Tomes Komga — sélectionne le tome à enrichir"))
        self.mn_komga_books_table = QTableWidget()
        self._register_table(self.mn_komga_books_table, "manga_news.komga_books", default_hidden=["ID"])
        self.mn_komga_books_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mn_komga_books_table.setSelectionMode(QAbstractItemView.SingleSelection)
        mn_books_left_layout.addWidget(self.mn_komga_books_table, 1)
        books_split.addWidget(mn_books_left)

        mn_books_right = QWidget()
        mn_books_right_layout = QVBoxLayout(mn_books_right)
        mn_volume_row = QHBoxLayout()
        self.mn_volume_url = QLineEdit()
        self.mn_volume_url.setPlaceholderText("URL volume Manga News, ex: https://www.manga-news.com/...")
        btn_mn_load_volume = QPushButton("Charger volume")
        btn_mn_load_volume_number = QPushButton("Charger par numéro")
        btn_mn_preview_book = QPushButton("Prévisualiser tome")
        btn_mn_apply_book = QPushButton("Appliquer tome")
        mn_volume_row.addWidget(self.mn_volume_url, 1)
        mn_volume_row.addWidget(btn_mn_load_volume)
        mn_volume_row.addWidget(btn_mn_load_volume_number)
        mn_volume_row.addWidget(btn_mn_preview_book)
        mn_volume_row.addWidget(btn_mn_apply_book)
        mn_books_right_layout.addLayout(mn_volume_row)
        self.mn_book_metadata_table = QTableWidget()
        self._register_table(self.mn_book_metadata_table, "manga_news.book_diff")
        self._init_metadata_table(self.mn_book_metadata_table)
        self.mn_book_preview = QTextEdit()
        self.mn_book_preview.setReadOnly(True)
        self.mn_book_preview.setLineWrapMode(QTextEdit.WidgetWidth)
        self.mn_book_preview.setStyleSheet("font-family: monospace;")
        mn_book_split = QSplitter(Qt.Vertical)
        mn_book_split.addWidget(self.mn_book_metadata_table)
        mn_book_split.addWidget(self.mn_book_preview)
        mn_book_split.setSizes([360, 320])
        mn_books_right_layout.addWidget(mn_book_split, 1)
        books_split.addWidget(mn_books_right)
        books_split.setSizes([680, 980])
        books_layout.addWidget(books_split, 1)
        workflow_tabs.addTab(books_tab, "Tomes")

        batch_tab = QWidget()
        batch_layout = QVBoxLayout(batch_tab)
        batch_help = QTextEdit()
        batch_help.setReadOnly(True)
        batch_help.setPlainText(
            "Batch Manga News\n\n"
            "Les actions batch restent déclenchées depuis l'onglet Explorateur.\n"
            "Source pensée surtout pour compléter les summary des séries Komga.\n"
            "Garde-fous : sélection obligatoire, recherche media_kind=manga, score titre >= seuil prudent, simulation respectée, backup avant PATCH réel."
        )
        batch_layout.addWidget(batch_help, 1)
        workflow_tabs.addTab(batch_tab, "Traitement en masse / rapports")

        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        self.mn_raw = QTextEdit()
        self.mn_raw.setReadOnly(True)
        raw_layout.addWidget(self.mn_raw, 1)
        workflow_tabs.addTab(raw_tab, "Données techniques")

        layout.addWidget(workflow_tabs, 1)

        self.mn_target_id = QLineEdit()
        self.mn_target_id.setVisible(False)

        btn_load_komga.clicked.connect(self.load_manga_news_komga_series)
        self.mn_library_combo.currentIndexChanged.connect(lambda *_: self.load_manga_news_komga_series())
        self.mn_komga_search.returnPressed.connect(self.load_manga_news_komga_series)
        self.mn_komga_series_table.itemSelectionChanged.connect(self.on_manga_news_komga_series_selected)
        btn_from_komga.clicked.connect(self.use_selected_for_manga_news)
        btn_search.clicked.connect(self.search_manga_news)
        self.mn_query.returnPressed.connect(self.search_manga_news)
        btn_fetch.clicked.connect(self.fetch_selected_manga_news_series)
        self.mn_results_table.itemSelectionChanged.connect(self.on_manga_news_result_selected)
        self.mn_results_table.itemDoubleClicked.connect(lambda *_: self.fetch_selected_manga_news_series())
        btn_preview.clicked.connect(self.preview_manga_news_series)
        btn_apply.clicked.connect(self.apply_manga_news_series)
        btn_fit_columns.clicked.connect(lambda: self._fit_metadata_table_columns(self.mn_series_metadata_table))
        btn_compact_rows.clicked.connect(lambda: self._compact_metadata_table(self.mn_series_metadata_table))
        btn_fit_rows.clicked.connect(lambda: self._fit_metadata_table_rows(self.mn_series_metadata_table))
        btn_mn_load_volume.clicked.connect(self.load_manga_news_volume_by_url)
        btn_mn_load_volume_number.clicked.connect(self.load_manga_news_volume_by_number)
        btn_mn_preview_book.clicked.connect(self.preview_manga_news_book)
        btn_mn_apply_book.clicked.connect(self.apply_manga_news_book)
        self.mn_komga_books_table.itemSelectionChanged.connect(self.on_manga_news_komga_book_selected)
        self._add_main_tab(tab, "Manga News")

    def _build_next_releases_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        content.setMinimumHeight(980)
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        tab_layout.addWidget(scroll, 1)

        info = QLabel(
            "Recherche des prochaines sorties via Manga News ou MangaBaka. "
            "MangaBaka est strict : aucun tag n'est proposé si la fiche ne fournit pas une prochaine sortie datée. "
            f"Le tag Komga écrit est unique et technique : {NEXT_RELEASE_TAG_PREFIX}<tome>-<jj.mm.aaaa>."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        controls = QGridLayout()
        self.nr_library_combo = self._make_library_combo("next_releases")
        self.nr_source_combo = QComboBox()
        self.nr_source_combo.addItem("Manga News", "manga_news")
        self.nr_source_combo.addItem("MangaBaka", "mangabaka")
        self.nr_search = QLineEdit()
        self.nr_search.setPlaceholderText("Filtrer séries Komga...")
        self.nr_only_not_ended = QCheckBox("Statut non terminé uniquement")
        self.nr_only_not_ended.setChecked(True)
        self.nr_require_source_link = QCheckBox("Exiger un lien source")
        self.nr_require_source_link.setChecked(True)
        self.nr_require_manga_news_link = self.nr_require_source_link
        self.nr_show_only_changes = QCheckBox("Afficher seulement modifications trouvées")
        self.nr_show_only_changes.setToolTip("Masquer les lignes sans prochaine sortie et celles déjà à jour")
        btn_load = QPushButton("Charger séries")
        btn_scan_selected = QPushButton("Scanner sélection")
        btn_scan_all = QPushButton("Scanner toutes non terminées")
        btn_apply_selected = QPushButton("Appliquer tags sélectionnés")
        btn_apply_all = QPushButton("Appliquer tous tags trouvés")
        self.nr_scan_selected_button = btn_scan_selected
        self.nr_apply_selected_button = btn_apply_selected
        self.nr_apply_all_button = btn_apply_all
        btn_scan_selected.setEnabled(False)
        btn_apply_selected.setEnabled(False)
        btn_apply_all.setEnabled(False)
        controls.addWidget(QLabel("Bibliothèque"), 0, 0)
        controls.addWidget(self.nr_library_combo, 0, 1, 1, 2)
        controls.addWidget(QLabel("Source"), 0, 3)
        controls.addWidget(self.nr_source_combo, 0, 4)
        controls.addWidget(self.nr_search, 0, 5, 1, 2)
        controls.addWidget(btn_load, 0, 7)
        controls.addWidget(self.nr_only_not_ended, 1, 0, 1, 2)
        controls.addWidget(self.nr_require_source_link, 1, 2, 1, 2)
        controls.addWidget(btn_scan_selected, 1, 4)
        controls.addWidget(btn_scan_all, 1, 5)
        controls.addWidget(btn_apply_selected, 1, 6)
        controls.addWidget(btn_apply_all, 1, 7)
        controls.addWidget(self.nr_show_only_changes, 2, 0, 1, 3)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Vertical)
        series_box = QGroupBox("Séries candidates")
        series_layout = QVBoxLayout(series_box)
        self.nr_scope_label = QLabel("Aucune série chargée.")
        self.nr_scope_label.setStyleSheet("font-weight: 600;")
        series_layout.addWidget(self.nr_scope_label)
        self.nr_series_table = QTableWidget()
        self._register_table(self.nr_series_table, "next_releases.series", default_hidden=["ID", "Library"])
        self._register_series_table_rows(self.nr_series_table, "nr_series_rows")
        self.nr_series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.nr_series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        series_layout.addWidget(self.nr_series_table, 1)
        splitter.addWidget(series_box)

        results_box = QGroupBox("Résultats et tags à écrire")
        results_layout = QVBoxLayout(results_box)
        self.nr_result_scope_label = QLabel("Aucun scan effectué.")
        self.nr_result_scope_label.setStyleSheet("font-weight: 600;")
        results_layout.addWidget(self.nr_result_scope_label)
        self.nr_results_table = QTableWidget()
        self._register_table(self.nr_results_table, "next_releases.results", default_hidden=["Series ID", "URL"])
        self.nr_results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.nr_results_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        results_layout.addWidget(self.nr_results_table, 1)
        self.nr_detail = QTextEdit()
        self.nr_detail.setReadOnly(True)
        self.nr_detail.setLineWrapMode(QTextEdit.WidgetWidth)
        self.nr_detail.setStyleSheet("font-family: monospace;")
        results_layout.addWidget(self.nr_detail)
        splitter.addWidget(results_box)
        splitter.setSizes([420, 560])
        layout.addWidget(splitter, 1)

        btn_load.clicked.connect(self.load_next_release_series)
        self.nr_library_combo.currentIndexChanged.connect(lambda *_: self.load_next_release_series())
        self.nr_source_combo.currentIndexChanged.connect(lambda *_: self._apply_next_release_series_filters())
        self.nr_search.returnPressed.connect(self.load_next_release_series)
        self.nr_only_not_ended.stateChanged.connect(lambda *_: self._apply_next_release_series_filters())
        self.nr_require_source_link.stateChanged.connect(lambda *_: self._apply_next_release_series_filters())
        self.nr_show_only_changes.stateChanged.connect(lambda *_: self._refresh_next_release_results_table())
        btn_scan_selected.clicked.connect(self.scan_next_releases_selected)
        btn_scan_all.clicked.connect(self.scan_next_releases_all)
        btn_apply_selected.clicked.connect(self.apply_next_release_tags_selected)
        btn_apply_all.clicked.connect(self.apply_next_release_tags_all)
        self.nr_series_table.itemSelectionChanged.connect(self._update_next_release_scope_labels)
        self.nr_results_table.itemSelectionChanged.connect(self.on_next_release_result_selected)
        self.nr_results_table.itemSelectionChanged.connect(self._update_next_release_scope_labels)
        self._add_main_tab(tab, "Prochaines sorties")

    def _build_comicvine_tab(self) -> None:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        content.setMinimumHeight(1220)
        layout = QVBoxLayout(content)
        self._add_source_workflow_header(
            layout,
            "ComicVine",
            "Sélectionnez un volume ComicVine, vérifiez les numéros correspondants et prévisualisez les métadonnées ou couvertures.",
        )
        scroll.setWidget(content)
        tab_layout.addWidget(scroll, 1)

        top = QHBoxLayout()
        self.cv_library_combo = self._make_library_combo("comicvine")
        self.cv_komga_search = QLineEdit()
        self.cv_komga_search.setPlaceholderText("Filtrer séries Komga...")
        btn_load_komga = QPushButton("Charger séries Komga")
        btn_from_komga = QPushButton("Utiliser sélection explorateur")
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.cv_library_combo, 2)
        top.addWidget(self.cv_komga_search, 2)
        top.addWidget(btn_load_komga)
        top.addWidget(btn_from_komga)
        layout.addLayout(top)
        layout.addLayout(self._make_source_series_filters_row("cv", self.load_comicvine_komga_series, fixed_link_labels=["ComicVine"]))

        main_split = QSplitter(Qt.Horizontal)
        main_split.setChildrenCollapsible(False)
        main_split.setMinimumHeight(380)

        left_box = QGroupBox("Komga — séries")
        left_box.setMinimumHeight(350)
        left_layout = QVBoxLayout(left_box)
        self.cv_komga_series_table = QTableWidget()
        self._register_table(self.cv_komga_series_table, "comicvine.komga_series", default_hidden=["ID"])
        self._register_series_table_rows(self.cv_komga_series_table, "cv_komga_series_rows")
        self.cv_komga_series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cv_komga_series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.cv_komga_series_table.setMinimumHeight(280)
        left_layout.addWidget(self.cv_komga_series_table, 1)
        main_split.addWidget(left_box)

        right_box = QGroupBox("ComicVine — recherche API")
        right_box.setMinimumHeight(350)
        right_layout = QVBoxLayout(right_box)
        search_row = QHBoxLayout()
        self.cv_query = QLineEdit()
        self.cv_query.setPlaceholderText("Recherche ComicVine, ex: Batman")
        btn_search = QPushButton("Rechercher")
        btn_fetch = QPushButton("Charger résultat")
        btn_cover = QPushButton("Cover → onglet Couvertures")
        search_row.addWidget(self.cv_query, 2)
        search_row.addWidget(btn_search)
        search_row.addWidget(btn_fetch)
        search_row.addWidget(btn_cover)
        right_layout.addLayout(search_row)
        cv_results_split = QSplitter(Qt.Horizontal)
        cv_results_split.setChildrenCollapsible(False)
        self.cv_results_table = QTableWidget()
        self._register_table(self.cv_results_table, "comicvine.results", default_hidden=["ID", "URL"])
        self.cv_results_table.setMinimumHeight(260)
        cv_results_split.addWidget(self.cv_results_table)
        cv_result_detail = self._make_selection_detail_panel("comicvine.result", "Détails résultat ComicVine")
        cv_result_detail.setMinimumWidth(420)
        cv_result_detail.setMinimumHeight(260)
        cv_results_split.addWidget(cv_result_detail)
        cv_results_split.setSizes([760, 520])
        right_layout.addWidget(cv_results_split, 1)
        main_split.addWidget(right_box)
        main_split.setSizes([620, 900])
        layout.addWidget(main_split)

        workflow_tabs = QTabWidget()
        workflow_tabs.setMinimumHeight(760)
        serie_tab = QWidget()
        serie_layout = QVBoxLayout(serie_tab)
        actions = QHBoxLayout()
        btn_preview = QPushButton("Prévisualiser")
        btn_apply = QPushButton("Appliquer série")
        btn_fit_columns = QPushButton("Ajuster colonnes")
        btn_compact_rows = QPushButton("Lignes compactes")
        btn_fit_rows = QPushButton("Lignes auto")
        actions.addWidget(btn_preview)
        actions.addWidget(btn_apply)
        actions.addWidget(btn_fit_columns)
        actions.addWidget(btn_compact_rows)
        actions.addWidget(btn_fit_rows)
        actions.addStretch(1)
        serie_layout.addLayout(actions)
        serie_split = QSplitter(Qt.Vertical)
        self.cv_series_metadata_table = QTableWidget()
        self._register_table(self.cv_series_metadata_table, "comicvine.series_diff")
        self._fill_comicvine_metadata_table({}, {})
        self.cv_series_preview = QTextEdit()
        self.cv_series_preview.setReadOnly(True)
        serie_split.addWidget(self.cv_series_metadata_table)
        serie_split.addWidget(self.cv_series_preview)
        serie_split.setSizes([520, 420])
        serie_layout.addWidget(serie_split, 1)
        workflow_tabs.addTab(serie_tab, "Série")

        books_tab = QWidget()
        books_layout = QVBoxLayout(books_tab)
        books_split = QSplitter(Qt.Vertical)
        books_top_split = QSplitter(Qt.Horizontal)

        cv_books_box = QGroupBox("Komga — tomes de la série sélectionnée")
        cv_books_layout = QVBoxLayout(cv_books_box)
        self.cv_komga_books_table = QTableWidget()
        self._register_table(self.cv_komga_books_table, "comicvine.komga_books", default_hidden=["ID"])
        self.cv_komga_books_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cv_komga_books_table.setSelectionMode(QAbstractItemView.SingleSelection)
        cv_books_layout.addWidget(self.cv_komga_books_table, 1)
        books_top_split.addWidget(cv_books_box)

        cv_issues_box = QGroupBox("ComicVine — issues du volume chargé")
        cv_issues_layout = QVBoxLayout(cv_issues_box)
        issue_actions = QHBoxLayout()
        btn_cv_load_issues = QPushButton("Charger issues du volume")
        btn_cv_load_issue = QPushButton("Charger issue sélectionnée")
        issue_actions.addWidget(btn_cv_load_issues)
        issue_actions.addWidget(btn_cv_load_issue)
        issue_actions.addStretch(1)
        cv_issues_layout.addLayout(issue_actions)
        self.cv_issues_table = QTableWidget()
        self._register_table(self.cv_issues_table, "comicvine.issues", default_hidden=["ID", "URL"])
        cv_issues_split = QSplitter(Qt.Vertical)
        cv_issues_split.addWidget(self.cv_issues_table)
        cv_issues_split.addWidget(self._make_selection_detail_panel("comicvine.issue", "Détails issue ComicVine"))
        cv_issues_split.setSizes([360, 180])
        cv_issues_layout.addWidget(cv_issues_split, 1)
        books_top_split.addWidget(cv_issues_box)
        books_top_split.setSizes([780, 840])
        books_split.addWidget(books_top_split)

        cv_match_box = QGroupBox("Matching tomes Komga ↔ issues ComicVine")
        cv_match_layout = QVBoxLayout(cv_match_box)
        cv_match_buttons = QHBoxLayout()
        btn_cv_match = QPushButton("Auto-match tomes")
        btn_cv_preview_book = QPushButton("Prévisualiser tome sélectionné")
        btn_cv_apply_book = QPushButton("Appliquer tome(s) sélectionné(s)")
        btn_cv_apply_all_matched = QPushButton("Appliquer tous les tomes matchés")
        cv_match_buttons.addWidget(btn_cv_match)
        cv_match_buttons.addWidget(btn_cv_preview_book)
        cv_match_buttons.addWidget(btn_cv_apply_book)
        cv_match_buttons.addWidget(btn_cv_apply_all_matched)
        cv_match_buttons.addStretch(1)
        cv_match_layout.addLayout(cv_match_buttons)
        cv_lower_split = QSplitter(Qt.Horizontal)
        cv_left = QWidget()
        cv_left_layout = QVBoxLayout(cv_left)
        cv_left_layout.addWidget(QLabel("Matching — sélectionne une ligne, corrige manuellement si besoin, puis prévisualise."))
        self.cv_book_match_table = QTableWidget()
        self._register_table(self.cv_book_match_table, "comicvine.book_matching", default_hidden=["Book ID", "Issue ID"])
        cv_left_layout.addWidget(self.cv_book_match_table, 1)
        cv_lower_split.addWidget(cv_left)
        cv_right = QWidget()
        cv_right_layout = QVBoxLayout(cv_right)
        cv_right_layout.addWidget(QLabel("Diff métadonnées du tome sélectionné"))
        cv_right_split = QSplitter(Qt.Vertical)
        self.cv_book_metadata_table = QTableWidget()
        self._register_table(self.cv_book_metadata_table, "comicvine.book_diff")
        self._init_metadata_table(self.cv_book_metadata_table)
        cv_right_split.addWidget(self.cv_book_metadata_table)
        self.cv_book_preview = QTextEdit()
        self.cv_book_preview.setReadOnly(True)
        self.cv_book_preview.setLineWrapMode(QTextEdit.WidgetWidth)
        self.cv_book_preview.setStyleSheet("font-family: monospace;")
        cv_right_split.addWidget(self.cv_book_preview)
        cv_right_split.setSizes([240, 320])
        cv_right_layout.addWidget(cv_right_split, 1)
        cv_lower_split.addWidget(cv_right)
        cv_lower_split.setSizes([760, 860])
        cv_match_layout.addWidget(cv_lower_split, 1)
        books_split.addWidget(cv_match_box)
        books_split.setSizes([360, 560])
        books_layout.addWidget(books_split, 1)
        workflow_tabs.addTab(books_tab, "Tomes")

        batch_tab = QWidget()
        batch_layout = QVBoxLayout(batch_tab)
        batch_help = QTextEdit()
        batch_help.setReadOnly(True)
        batch_help.setPlainText(
            "Batch ComicVine\n\n"
            "ComicVine est surtout utile pour title/titleSort, résumé, publisher, auteurs, cover, lien et totalBookCount.\n"
            "Le statut n'est pas inventé si l'API ne le fournit pas clairement.\n"
            "Garde-fous : sélection obligatoire, score titre visible, simulation respectée, backup avant PATCH réel."
        )
        batch_layout.addWidget(batch_help, 1)
        workflow_tabs.addTab(batch_tab, "Traitement en masse / rapports")

        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        self.cv_raw = QTextEdit()
        self.cv_raw.setReadOnly(True)
        raw_layout.addWidget(self.cv_raw, 1)
        workflow_tabs.addTab(raw_tab, "Données techniques")

        layout.addWidget(workflow_tabs)

        self.cv_target_id = QLineEdit()
        self.cv_target_id.setVisible(False)

        btn_load_komga.clicked.connect(self.load_comicvine_komga_series)
        self.cv_library_combo.currentIndexChanged.connect(lambda *_: self.load_comicvine_komga_series())
        self.cv_komga_search.returnPressed.connect(self.load_comicvine_komga_series)
        self.cv_komga_series_table.itemSelectionChanged.connect(self.on_comicvine_komga_series_selected)
        btn_from_komga.clicked.connect(self.use_selected_for_comicvine)
        btn_search.clicked.connect(self.search_comicvine)
        self.cv_query.returnPressed.connect(self.search_comicvine)
        btn_fetch.clicked.connect(self.fetch_selected_comicvine_series)
        self.cv_results_table.itemSelectionChanged.connect(self.on_comicvine_result_selected)
        self.cv_results_table.itemDoubleClicked.connect(lambda *_: self.fetch_selected_comicvine_series())
        btn_preview.clicked.connect(self.preview_comicvine_series)
        btn_apply.clicked.connect(self.apply_comicvine_series)
        btn_fit_columns.clicked.connect(lambda: self._fit_metadata_table_columns(self.cv_series_metadata_table))
        btn_compact_rows.clicked.connect(lambda: self._compact_metadata_table(self.cv_series_metadata_table))
        btn_fit_rows.clicked.connect(lambda: self._fit_metadata_table_rows(self.cv_series_metadata_table))
        btn_cover.clicked.connect(self.send_comicvine_cover_to_posters)
        btn_cv_load_issues.clicked.connect(self.load_comicvine_issues_for_current_volume)
        btn_cv_load_issue.clicked.connect(self.fetch_selected_comicvine_issue)
        btn_cv_match.clicked.connect(self.match_comicvine_tomes)
        btn_cv_preview_book.clicked.connect(self.preview_comicvine_book)
        btn_cv_apply_book.clicked.connect(self.apply_comicvine_book)
        btn_cv_apply_all_matched.clicked.connect(self.apply_all_matched_comicvine_books)
        self.cv_issues_table.itemSelectionChanged.connect(self.on_comicvine_issue_selected)
        self.cv_issues_table.itemDoubleClicked.connect(lambda *_: self.fetch_selected_comicvine_issue())
        self.cv_book_match_table.itemSelectionChanged.connect(self.on_comicvine_book_match_selected)
        self._add_main_tab(tab, "ComicVine")



    # ------------------------------------------------------------------
    # Santé
    # ------------------------------------------------------------------
    def _build_health_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title = QLabel("Santé de la bibliothèque")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        intro = QLabel(
            "Cette vue rassemble l'état de connexion, le dernier audit et les erreurs de la session. "
            "Elle ne lance aucune correction automatiquement."
        )
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        cards = QHBoxLayout()
        connection_box = QGroupBox("Connexion")
        connection_layout = QVBoxLayout(connection_box)
        self.health_connection_label = QLabel("Komga non validé")
        self.health_connection_label.setWordWrap(True)
        btn_health_connection = QPushButton("Vérifier la connexion")
        btn_health_connection.clicked.connect(lambda: self._set_current_tab_by_title("Connexion"))
        connection_layout.addWidget(self.health_connection_label)
        connection_layout.addWidget(btn_health_connection)
        cards.addWidget(connection_box, 1)

        audit_box = QGroupBox("Qualité des données")
        audit_layout = QVBoxLayout(audit_box)
        self.health_audit_label = QLabel("Aucun audit disponible")
        self.health_audit_label.setWordWrap(True)
        btn_health_audit = QPushButton("Lancer ou actualiser l'audit")
        btn_health_audit.clicked.connect(lambda: self._set_current_tab_by_title("Audit"))
        audit_layout.addWidget(self.health_audit_label)
        audit_layout.addWidget(btn_health_audit)
        cards.addWidget(audit_box, 1)

        operations_box = QGroupBox("Opérations de la session")
        operations_layout = QVBoxLayout(operations_box)
        self.health_operations_label = QLabel("Aucune erreur enregistrée")
        self.health_operations_label.setWordWrap(True)
        btn_health_operations = QPushButton("Voir le centre des opérations")
        btn_health_operations.clicked.connect(lambda: self._set_current_tab_by_title("Opérations"))
        operations_layout.addWidget(self.health_operations_label)
        operations_layout.addWidget(btn_health_operations)
        cards.addWidget(operations_box, 1)
        layout.addLayout(cards)

        findings_box = QGroupBox("Problèmes détectés et parcours de correction")
        findings_layout = QVBoxLayout(findings_box)
        self.health_findings_table = QTableWidget()
        self.health_findings_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.health_findings_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._register_table(self.health_findings_table, "health.findings")
        findings_layout.addWidget(self.health_findings_table, 1)
        actions = QHBoxLayout()
        self.health_fix_button = QPushButton("Ouvrir le parcours de correction")
        self.health_fix_button.setEnabled(False)
        btn_health_full_audit = QPushButton("Voir le détail de l'audit")
        self.health_fix_button.clicked.connect(self.open_selected_health_action)
        btn_health_full_audit.clicked.connect(lambda: self._set_current_tab_by_title("Audit"))
        actions.addWidget(self.health_fix_button)
        actions.addWidget(btn_health_full_audit)
        actions.addStretch(1)
        findings_layout.addLayout(actions)
        layout.addWidget(findings_box, 1)

        self.health_findings_table.itemSelectionChanged.connect(self._update_health_action)
        self.health_tab_index = self._add_main_tab(tab, "Santé")
        self._refresh_health_dashboard()

    def _refresh_health_dashboard(self) -> None:
        if not hasattr(self, "health_findings_table"):
            return
        validated = bool(getattr(self, "_komga_connection_validated", False))
        self.health_connection_label.setText(
            "Connexion Komga validée pour cette session."
            if validated
            else "Connexion Komga non validée ou à revérifier."
        )
        findings = [row for row in self.audit_rows if int(row.get("count") or 0) > 0]
        total = sum(int(row.get("count") or 0) for row in findings)
        self.health_audit_label.setText(
            "Aucun audit disponible."
            if not self.audit_rows
            else ("Aucune anomalie détectée." if not findings else f"{total} anomalie(s) dans {len(findings)} catégorie(s).")
        )
        errors = [task for task in self.operation_history if task.get("status") == "Erreur"]
        running = [task for task in self.operation_history if task.get("status") == "En cours"]
        self.health_operations_label.setText(
            f"{len(running)} en cours, {len(errors)} en erreur."
            if running or errors
            else "Aucune erreur enregistrée pendant cette session."
        )
        rows = [
            [
                row.get("type", ""),
                row.get("count", 0),
                row.get("severity", ""),
                self._audit_action_target(str(row.get("type") or "")) or "À examiner",
            ]
            for row in findings
        ]
        self._set_table(
            self.health_findings_table,
            ["Problème", "Nombre", "Priorité", "Correction"],
            rows,
            row_data=findings,
            selection_mode=QAbstractItemView.SingleSelection,
        )
        self.health_fix_button.setEnabled(False)

    def _update_health_action(self) -> None:
        row = self._selected_row_data(self.health_findings_table)
        target = self._audit_action_target(str(row.get("type") or "")) if isinstance(row, dict) else ""
        self.health_fix_button.setEnabled(bool(target))
        self.health_fix_button.setText(
            f"Ouvrir : {target}" if target else "Ouvrir le parcours de correction"
        )

    def open_selected_health_action(self) -> None:
        row = self._selected_row_data(self.health_findings_table)
        if not isinstance(row, dict):
            return
        target = self._audit_action_target(str(row.get("type") or ""))
        if target:
            self._set_current_tab_by_title(target)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def _build_audit_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        intro = QLabel(
            "Analysez la bibliothèque, sélectionnez un problème, puis ouvrez directement le parcours adapté pour le corriger."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        top = QHBoxLayout()
        self.audit_library_combo = self._make_library_combo("audit")
        self.audit_include_books = QCheckBox("Inclure tomes")
        self.audit_include_books.setToolTip("Plus lent : charge les tomes de la bibliothèque pour repérer les résumés/ISBN manquants.")
        btn_run = QPushButton("Analyser")
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.audit_library_combo)
        top.addWidget(self.audit_include_books)
        top.addStretch(1)
        top.addWidget(btn_run)
        layout.addLayout(top)

        audit_actions = QHBoxLayout()
        self.audit_summary_label = QLabel("Aucun audit lancé.")
        self.audit_summary_label.setStyleSheet("font-weight: 600;")
        self.audit_action_button = QPushButton("Ouvrir le parcours de correction")
        self.audit_action_button.setEnabled(False)
        audit_actions.addWidget(self.audit_summary_label, 1)
        audit_actions.addWidget(self.audit_action_button)
        layout.addLayout(audit_actions)

        split = QSplitter(Qt.Vertical)
        self.audit_table = QTableWidget()
        self._register_table(self.audit_table, "audit.rows")
        split.addWidget(self.audit_table)
        self.audit_detail = QTextEdit()
        self.audit_detail.setReadOnly(True)
        self.audit_detail.setLineWrapMode(QTextEdit.WidgetWidth)
        self.audit_detail.setStyleSheet("font-family: monospace;")
        split.addWidget(self.audit_detail)
        split.setSizes([520, 240])
        layout.addWidget(split, 1)

        btn_run.clicked.connect(self.run_library_audit)
        self.audit_table.itemSelectionChanged.connect(self.show_library_audit_detail)
        self.audit_action_button.clicked.connect(self.open_selected_audit_action)
        self._add_main_tab(tab, "Audit")

    def run_library_audit(self) -> None:
        lib_id = self._library_id("audit")
        include_books = self.audit_include_books.isChecked()

        def do_audit() -> List[Dict[str, Any]]:
            api = self.komga_api()
            series_rows = api.series(library_id=lib_id or None, page_size=200)
            collections = api.collections()
            readlists = api.readlists()
            rows: List[Dict[str, Any]] = []

            collection_series_ids: set[str] = set()
            for collection in collections:
                series_ids = collection.raw.get("seriesIds") if isinstance(collection.raw, dict) else []
                if isinstance(series_ids, list):
                    collection_series_ids.update(str(value) for value in series_ids if str(value).strip())

            def series_examples(candidates: List[Any]) -> str:
                return "\n".join(f"- {getattr(row, 'title', '')} ({getattr(row, 'id', '')})" for row in candidates[:30])

            missing_summary = [row for row in series_rows if not str((getattr(row, "metadata", {}) or {}).get("summary") or "").strip()]
            rows.append({"type": "Séries sans résumé", "count": len(missing_summary), "severity": "moyen", "detail": series_examples(missing_summary)})

            missing_genres = [
                row for row in series_rows
                if not ((getattr(row, "metadata", {}) or {}).get("genres") or (getattr(row, "metadata", {}) or {}).get("tags") or [])
            ]
            rows.append({"type": "Séries sans genre/tag", "count": len(missing_genres), "severity": "moyen", "detail": series_examples(missing_genres)})

            no_collection = [row for row in series_rows if getattr(row, "id", "") not in collection_series_ids]
            rows.append({"type": "Séries sans collection", "count": len(no_collection), "severity": "fort", "detail": series_examples(no_collection)})

            empty_collections = [
                row for row in collections
                if lib_id in {"", str((row.raw.get("library") or {}).get("id") or row.raw.get("libraryId") or "")}
                and not (row.raw.get("seriesIds") or [])
            ]
            rows.append({
                "type": "Collections vides",
                "count": len(empty_collections),
                "severity": "faible",
                "detail": "\n".join(f"- {row.name} ({row.id})" for row in empty_collections[:30]),
            })

            empty_readlists = [row for row in readlists if not (row.raw.get("bookIds") or [])]
            rows.append({
                "type": "Readlists vides",
                "count": len(empty_readlists),
                "severity": "faible",
                "detail": "\n".join(f"- {row.name} ({row.id})" for row in empty_readlists[:30]),
            })

            if include_books:
                books = api.books(library_id=lib_id or None, page_size=500)
                no_book_summary = [row for row in books if not str((getattr(row, "metadata", {}) or {}).get("summary") or "").strip()]
                no_isbn = [row for row in books if not str((getattr(row, "metadata", {}) or {}).get("isbn") or "").strip()]
                rows.append({"type": "Tomes sans résumé", "count": len(no_book_summary), "severity": "moyen", "detail": "\n".join(f"- {row.title} ({row.id})" for row in no_book_summary[:30])})
                rows.append({"type": "Tomes sans ISBN", "count": len(no_isbn), "severity": "faible", "detail": "\n".join(f"- {row.title} ({row.id})" for row in no_isbn[:30])})
            return rows

        def done(rows: List[Dict[str, Any]]) -> None:
            self.audit_rows = list(rows)
            visible = [row for row in rows if int(row.get("count") or 0) > 0]
            self._set_table(
                self.audit_table,
                ["Contrôle", "Nombre", "Priorité"],
                [[row["type"], row["count"], row["severity"]] for row in visible],
                row_data=visible,
            )
            total_findings = sum(int(row.get("count") or 0) for row in visible)
            self.audit_summary_label.setText(
                "Aucune anomalie détectée."
                if not visible
                else f"{total_findings} anomalie(s) répartie(s) dans {len(visible)} catégorie(s)."
            )
            self.audit_action_button.setEnabled(False)
            self.audit_detail.setPlainText("Aucune anomalie détectée." if not visible else "Sélectionne une ligne pour voir les exemples.")
            self.log(f"✅ Audit bibliothèque : {len(visible)} contrôle(s) avec résultat")
            self._refresh_health_dashboard()

        self.run_worker("Audit bibliothèque", do_audit, done)

    def show_library_audit_detail(self) -> None:
        row = self._selected_row_data(self.audit_table) if hasattr(self, "audit_table") else None
        if not isinstance(row, dict):
            self.audit_action_button.setEnabled(False)
            return
        target = self._audit_action_target(str(row.get("type") or ""))
        self.audit_action_button.setEnabled(bool(target))
        if target:
            self.audit_action_button.setText(f"Ouvrir : {target}")
        self.audit_detail.setPlainText(
            f"{row.get('type', '')}\n"
            f"Nombre : {row.get('count', 0)}\n"
            f"Priorité : {row.get('severity', '')}\n\n"
            f"{row.get('detail', '') or 'Pas d’exemple disponible.'}"
        )

    @staticmethod
    def _audit_action_target(finding_type: str) -> str:
        normalized = str(finding_type or "").casefold()
        if "collection" in normalized:
            return "Collections"
        if "readlist" in normalized:
            return "Readlists"
        if "genre" in normalized or "tag" in normalized:
            return "Genres Kora"
        if "résumé" in normalized or "isbn" in normalized:
            return "Enrichissement"
        return ""

    def open_selected_audit_action(self) -> None:
        row = self._selected_row_data(self.audit_table) if hasattr(self, "audit_table") else None
        if not isinstance(row, dict):
            return
        target = self._audit_action_target(str(row.get("type") or ""))
        if target:
            self._set_current_tab_by_title(target)

    # ------------------------------------------------------------------
    # Suivi sorties
    # ------------------------------------------------------------------
    def _build_release_tracking_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title_row = QHBoxLayout()
        title = QLabel("Suivre l'avancement des séries")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        btn_rt_explorer = QPushButton("Choisir dans l'Explorateur")
        btn_rt_explorer.clicked.connect(lambda: self._set_current_tab_by_title("Explorateur"))
        title_row.addWidget(title, 1)
        title_row.addWidget(btn_rt_explorer)
        layout.addLayout(title_row)

        intro = QLabel(
            "Suivi contrôlé des sorties : ce workflow utilise uniquement les liens déjà présents, soit sur la sélection, "
            "soit sur toutes les séries chargées avec un lien Manga News ou ComicVine. Il ne modifie que status et totalBookCount, "
            "après validation utilisateur."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        steps = QGroupBox("Parcours recommandé")
        steps_layout = QHBoxLayout(steps)
        for step in (
            "1. Charger et filtrer",
            "2. Choisir la portée",
            "3. Analyser les sources",
            "4. Valider les propositions",
            "5. Appliquer ou exporter",
        ):
            label = QLabel(step)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("padding: 5px; border: 1px solid #555; border-radius: 3px;")
            steps_layout.addWidget(label, 1)
        layout.addWidget(steps)

        top = QGridLayout()
        self.rt_source_mode = QComboBox()
        self.rt_source_mode.addItem("Auto contrôlé", "auto")
        self.rt_source_mode.addItem("Bedetheque uniquement", "bedetheque")
        self.rt_source_mode.addItem("MangaBaka uniquement", "mangabaka")
        self.rt_source_mode.addItem("Manga News uniquement", "manga_news")
        self.rt_source_mode.addItem("ComicVine uniquement", "comicvine")
        self.rt_scope_mode = QComboBox()
        self.rt_scope_mode.setToolTip(
            "La portée globale traite uniquement les séries actuellement chargées et filtrées "
            "qui possèdent déjà un lien exploitable pour la source choisie."
        )
        self._refresh_release_tracking_scope_options()

        self.rt_filter = QComboBox()
        self.rt_filter.addItem("Tous", "all")
        self.rt_filter.addItem("Changements seulement", "changes")
        self.rt_filter.addItem("Risque faible", "Faible")
        self.rt_filter.addItem("Risque moyen", "Moyen")
        self.rt_filter.addItem("Risque fort", "Fort")
        self.rt_filter.addItem("Erreurs", "Erreur")
        self.rt_filter.addItem("Ignorés", "Ignoré")
        self.rt_filter.addItem("Statuts modifiés", "status_changes")
        self.rt_filter.addItem("Tomes changés", "count_changes")
        self.rt_only_changes = QCheckBox("Afficher seulement les changements")
        self.rt_hide_ignored = QCheckBox("Masquer ignorés")
        self.rt_hide_ignored.setChecked(True)
        self.rt_hide_ignored.setToolTip("Masque les lignes ignorées dans la table de validation. Les lignes restent exportées dans le CSV.")
        self.rt_selected_label = QLabel("0 série sélectionnée")

        top.addWidget(QLabel("Source"), 0, 0)
        top.addWidget(self.rt_source_mode, 0, 1)
        top.addWidget(QLabel("Portée"), 0, 2)
        top.addWidget(self.rt_scope_mode, 0, 3)
        top.addWidget(QLabel("Filtre résultats"), 0, 4)
        top.addWidget(self.rt_filter, 0, 5)
        top.addWidget(self.rt_only_changes, 0, 6)
        top.addWidget(self.rt_hide_ignored, 0, 7)
        top.addWidget(self.rt_selected_label, 0, 8)
        layout.addLayout(top)

        main_splitter = QSplitter(Qt.Vertical)

        series_box = QGroupBox("Séries à suivre — sélectionne ici avec Ctrl/Shift")
        series_layout = QVBoxLayout(series_box)
        series_controls = QGridLayout()
        self.rt_library_combo = self._make_library_combo("release_tracking")
        self.rt_search_series_text = QLineEdit()
        self.rt_search_series_text.setPlaceholderText("Filtrer séries Komga...")
        self.rt_filter_empty_summary = QCheckBox("Résumé vide")
        self.rt_filter_empty_summary.setToolTip("Afficher uniquement les séries dont le résumé est vide")
        self.rt_filter_language = QComboBox()
        self.rt_filter_language.setMinimumWidth(80)
        self.rt_filter_language.addItem("Toutes", "")
        self.rt_filter_language.addItem("FR", "fr")
        self.rt_filter_language.addItem("EN", "en")
        self.rt_filter_status = QComboBox()
        self.rt_filter_status.setMinimumWidth(105)
        self.rt_filter_status.addItem("Tous", "ALL")
        for status in SERIES_STATUS_VALUES:
            self.rt_filter_status.addItem(status, status)
        self.rt_filter_status.addItem("VIDE", "VIDE")
        self.rt_filter_link_label = QComboBox()
        self.rt_filter_link_label.setMinimumWidth(160)
        self.rt_filter_link_label.addItem("Tous", "ALL")
        self.rt_filter_link_label.addItem("Sans lien", "__NO_LINK__")
        btn_rt_load_libs = QPushButton("Charger bibliothèques")
        btn_rt_load_series = QPushButton("Charger séries")

        series_controls.addWidget(QLabel("Bibliothèque"), 0, 0)
        series_controls.addWidget(self.rt_library_combo, 0, 1, 1, 2)
        series_controls.addWidget(self.rt_search_series_text, 0, 3, 1, 2)
        series_controls.addWidget(btn_rt_load_libs, 0, 5)
        series_controls.addWidget(btn_rt_load_series, 0, 6)
        series_controls.addWidget(QLabel("Filtres"), 1, 0)
        series_controls.addWidget(self.rt_filter_empty_summary, 1, 1)
        series_controls.addWidget(QLabel("Langue"), 1, 2)
        series_controls.addWidget(self.rt_filter_language, 1, 3)
        series_controls.addWidget(QLabel("Statut"), 1, 4)
        series_controls.addWidget(self.rt_filter_status, 1, 5)
        series_controls.addWidget(QLabel("Liens"), 1, 6)
        series_controls.addWidget(self.rt_filter_link_label, 1, 7)
        series_layout.addLayout(series_controls)

        self.rt_series_table = QTableWidget()
        self._register_table(self.rt_series_table, "release_tracking.series", default_hidden=["ID", "Library"])
        self._register_series_table_rows(self.rt_series_table, "rt_series_rows")
        self.rt_series_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rt_series_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        series_layout.addWidget(self.rt_series_table, 1)
        main_splitter.addWidget(series_box)

        results_box = QGroupBox("Validation status / totalBookCount")
        results_layout = QVBoxLayout(results_box)
        action_row = QHBoxLayout()
        self.rt_scan_button = QPushButton("Analyser la portée choisie")
        btn_check_low = QPushButton("Tout cocher faible")
        btn_uncheck = QPushButton("Tout décocher")
        self.rt_apply_button = QPushButton("Appliquer les champs cochés")
        btn_export = QPushButton("Exporter CSV")
        action_row.addWidget(self.rt_scan_button)
        action_row.addWidget(btn_check_low)
        action_row.addWidget(btn_uncheck)
        action_row.addWidget(self.rt_apply_button)
        action_row.addWidget(btn_export)
        action_row.addStretch(1)
        results_layout.addLayout(action_row)

        self.rt_workflow_status_label = QLabel("Chargez les séries, puis choisissez la source et la portée à analyser.")
        self.rt_workflow_status_label.setWordWrap(True)
        self.rt_workflow_status_label.setStyleSheet("font-weight: 600;")
        results_layout.addWidget(self.rt_workflow_status_label)

        result_splitter = QSplitter(Qt.Vertical)
        self.rt_table = QTableWidget()
        self.rt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rt_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._register_table(self.rt_table, "release_tracking.rows", default_hidden=["Series ID", "Lien source", "Payload JSON", "Erreur"])
        result_splitter.addWidget(self.rt_table)

        self.rt_detail = QTextEdit()
        self.rt_detail.setReadOnly(True)
        self.rt_detail.setLineWrapMode(QTextEdit.WidgetWidth)
        self.rt_detail.setStyleSheet("font-family: monospace;")
        self.rt_detail.setMinimumHeight(180)
        result_splitter.addWidget(self.rt_detail)
        result_splitter.setSizes([520, 240])
        results_layout.addWidget(result_splitter, 1)
        main_splitter.addWidget(results_box)
        main_splitter.setSizes([360, 620])
        layout.addWidget(main_splitter, 1)

        btn_rt_load_libs.clicked.connect(self.load_libraries)
        btn_rt_load_series.clicked.connect(self.load_release_tracking_series)
        self.rt_library_combo.currentIndexChanged.connect(lambda *_: self.load_release_tracking_series())
        self.rt_search_series_text.returnPressed.connect(self.load_release_tracking_series)
        self.rt_filter_empty_summary.stateChanged.connect(lambda *_: self._apply_release_tracking_series_filters())
        self.rt_filter_language.currentIndexChanged.connect(lambda *_: self._apply_release_tracking_series_filters())
        self.rt_filter_status.currentIndexChanged.connect(lambda *_: self._apply_release_tracking_series_filters())
        self.rt_filter_link_label.currentIndexChanged.connect(lambda *_: self._apply_release_tracking_series_filters())
        self.rt_series_table.itemSelectionChanged.connect(self._update_release_tracking_selection_label)
        self.rt_source_mode.currentIndexChanged.connect(self._on_release_tracking_source_changed)
        self.rt_scope_mode.currentIndexChanged.connect(self._on_release_tracking_scope_changed)

        self.rt_scan_button.clicked.connect(self.scan_release_tracking)
        btn_check_low.clicked.connect(self.release_tracking_check_low_risk)
        btn_uncheck.clicked.connect(self.release_tracking_uncheck_all)
        self.rt_apply_button.clicked.connect(self.apply_release_tracking_checked)
        btn_export.clicked.connect(self.export_release_tracking_csv)
        self.rt_filter.currentIndexChanged.connect(self.populate_release_tracking_table)
        self.rt_only_changes.stateChanged.connect(self.populate_release_tracking_table)
        self.rt_hide_ignored.stateChanged.connect(self.populate_release_tracking_table)
        self.rt_table.itemSelectionChanged.connect(self.show_release_tracking_detail)
        self.rt_apply_button.setEnabled(False)
        self._update_release_tracking_action_state()
        self._add_main_tab(tab, "Suivi sorties")

    def load_release_tracking_series(self) -> None:
        if not hasattr(self, "rt_series_table"):
            return
        lib_id = self._library_id("release_tracking")
        search = self.rt_search_series_text.text().strip()
        generation = self._next_series_load_generation("release_tracking")
        self.rt_workflow_status_label.setText(
            "Chargement des séries depuis Komga… Les filtres seront appliqués localement à la réception."
        )

        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("release_tracking", generation):
                return
            self.rt_series_unfiltered_rows = self._filter_global_series_visibility(list(rows or []))
            self._refresh_release_tracking_link_filter_options(self.rt_series_unfiltered_rows)
            self._apply_release_tracking_series_filters()
            counts = self.rt_filter_counts
            self.log(
                f"✅ Suivi sorties : {counts.get('received', 0)} reçue(s), "
                f"{counts.get('visible', 0)} affichée(s) après filtres locaux"
            )

        self.run_worker("Suivi sorties — chargement séries", lambda: self.komga_api().series(lib_id, search=search, page_size=200), done)

    def _apply_release_tracking_series_filters(self) -> None:
        if not hasattr(self, "rt_series_table"):
            return
        rows = list(self.rt_series_unfiltered_rows or [])
        received = len(rows)
        active_filters: List[str] = []
        hidden_summary = hidden_language = hidden_status = hidden_link = 0
        if self.rt_filter_empty_summary.isChecked():
            before = len(rows)
            rows = [x for x in rows if is_blank_metadata_value((getattr(x, "metadata", {}) or {}).get("summary"))]
            hidden_summary = before - len(rows)
            active_filters.append("résumé vide")
        language_filter = self.rt_filter_language.currentData() or ""
        if language_filter:
            before = len(rows)
            rows = [x for x in rows if metadata_language_matches((getattr(x, "metadata", {}) or {}).get("language"), language_filter)]
            hidden_language = before - len(rows)
            active_filters.append(f"langue {str(language_filter).upper()}")
        status_filter = self.rt_filter_status.currentData() or "ALL"
        if normalized_status_code(status_filter) != "ALL":
            before = len(rows)
            rows = [x for x in rows if metadata_status_matches((getattr(x, "metadata", {}) or {}).get("status"), status_filter)]
            hidden_status = before - len(rows)
            active_filters.append(f"statut {normalized_status_code(status_filter)}")
        link_filter = self.rt_filter_link_label.currentData() or "ALL"
        if normalized_link_label(link_filter) != "all":
            before = len(rows)
            rows = [x for x in rows if metadata_link_label_matches((getattr(x, "metadata", {}) or {}).get("links"), link_filter)]
            hidden_link = before - len(rows)
            display = "sans lien" if normalized_link_label(link_filter) == "__no_link__" else str(link_filter)
            active_filters.append(f"liens {display}")
        self.rt_filter_counts = {
            "received": received,
            "visible": len(rows),
            "hidden_summary": hidden_summary,
            "hidden_language": hidden_language,
            "hidden_status": hidden_status,
            "hidden_link": hidden_link,
        }
        self.rt_series_rows = rows
        self.release_tracking_rows = []
        self.release_tracking_last_csv_path = ""
        self._set_table(
            self.rt_series_table,
            self._series_table_headers(include_library=True),
            [self._series_table_row(x, include_library=True) for x in rows],
            selection_mode=QAbstractItemView.ExtendedSelection,
        )
        self.populate_release_tracking_table()
        self._update_release_tracking_selection_label()
        filters_text = ", ".join(active_filters) if active_filters else "aucun filtre actif"
        self.rt_workflow_status_label.setText(
            f"{received} reçue(s) de Komga → {len(rows)} affichée(s). {filters_text}."
        )

    def _selected_release_tracking_series_rows(self) -> List[Any]:
        table = getattr(self, "rt_series_table", None)
        if table is None:
            return []
        rows = self._selected_row_indexes(table)
        return [self.rt_series_rows[i] for i in rows if 0 <= i < len(self.rt_series_rows)]

    def _manga_news_linked_release_tracking_series_rows(self) -> List[Any]:
        return [series for series in self.rt_series_rows if any(self._manga_news_link_for_series(series))]

    def _mangabaka_linked_release_tracking_series_rows(self) -> List[Any]:
        return [series for series in self.rt_series_rows if any(self._mangabaka_link_for_series(series))]

    def _bedetheque_linked_release_tracking_series_rows(self) -> List[Any]:
        return [
            series for series in self.rt_series_rows
            if self._pick_supported_update_link(series, "bedetheque")[0] == "bedetheque"
        ]

    def _comicvine_linked_release_tracking_series_rows(self) -> List[Any]:
        return [series for series in self.rt_series_rows if any(self._comicvine_link_for_series(series))]

    @staticmethod
    def _release_tracking_source_label(source: str) -> str:
        return {
            "auto": "source exploitable",
            "bedetheque": "Bedetheque",
            "mangabaka": "MangaBaka",
            "manga_news": "Manga News",
            "comicvine": "ComicVine",
        }.get(str(source or ""), str(source or "source"))

    def _refresh_release_tracking_scope_options(self) -> None:
        if not hasattr(self, "rt_scope_mode"):
            return
        previous = str(self.rt_scope_mode.currentData() or "selected")
        source = str(self.rt_source_mode.currentData() or "auto") if hasattr(self, "rt_source_mode") else "auto"
        label = self._release_tracking_source_label(source)
        self.rt_scope_mode.blockSignals(True)
        self.rt_scope_mode.clear()
        self.rt_scope_mode.addItem("Sélection uniquement", "selected")
        self.rt_scope_mode.addItem(f"Toutes avec lien {label}", "all_source_linked")
        index = self.rt_scope_mode.findData(previous)
        if index < 0 and previous in {"all_manga_news_linked", "all_comicvine_linked"}:
            index = self.rt_scope_mode.findData("all_source_linked")
        self.rt_scope_mode.setCurrentIndex(index if index >= 0 else 0)
        self.rt_scope_mode.blockSignals(False)

    def _on_release_tracking_source_changed(self) -> None:
        self._refresh_release_tracking_scope_options()
        self._update_release_tracking_selection_label()

    def _on_release_tracking_scope_changed(self) -> None:
        self._update_release_tracking_selection_label()

    def _release_tracking_linked_rows_for_source(self, source_mode: str) -> List[Any]:
        source = str(source_mode or "auto")
        if source == "manga_news":
            return self._manga_news_linked_release_tracking_series_rows()
        if source == "mangabaka":
            return self._mangabaka_linked_release_tracking_series_rows()
        if source == "comicvine":
            return self._comicvine_linked_release_tracking_series_rows()
        if source == "bedetheque":
            return self._bedetheque_linked_release_tracking_series_rows()
        return [
            series for series in self.rt_series_rows
            if self._release_tracking_provider_links(series)
        ]

    def _is_release_tracking_ignored_row(self, row: Dict[str, Any]) -> bool:
        return row.get("risk") == "Ignoré" or str(row.get("operation_status", "")).startswith("Ignoré")

    def _update_release_tracking_selection_label(self) -> None:
        if not hasattr(self, "rt_selected_label"):
            return
        selected = len(self._selected_release_tracking_series_rows()) if hasattr(self, "rt_series_table") else len(self.release_tracking_series_rows)
        source_mode = str(self.rt_source_mode.currentData() or "auto") if hasattr(self, "rt_source_mode") else "auto"
        source_label = self._release_tracking_source_label(source_mode)
        linked_source = len(self._release_tracking_linked_rows_for_source(source_mode)) if hasattr(self, "rt_series_rows") else 0
        total_results = len(self.release_tracking_rows)
        visible_results = len([row for row in self.release_tracking_rows if self._release_tracking_row_matches_filter(row)]) if self.release_tracking_rows else 0
        ignored_total = len([row for row in self.release_tracking_rows if self._is_release_tracking_ignored_row(row)]) if self.release_tracking_rows else 0
        ignored_visible = len([row for row in self.release_tracking_rows if self._is_release_tracking_ignored_row(row) and self._release_tracking_row_matches_filter(row)]) if self.release_tracking_rows else 0
        ignored_hidden = max(0, ignored_total - ignored_visible)
        extra = f" — ignorés masqués : {ignored_hidden}" if ignored_hidden else ""
        self.rt_selected_label.setText(
            f"{selected} sélectionnée(s), {linked_source} lien {source_label}, "
            f"{visible_results}/{total_results} résultat(s){extra}"
        )
        self._update_release_tracking_action_state()

    def _update_release_tracking_action_state(self) -> None:
        if not hasattr(self, "rt_scan_button"):
            return
        scope_mode = str(self.rt_scope_mode.currentData() or "selected") if hasattr(self, "rt_scope_mode") else "selected"
        source_mode = str(self.rt_source_mode.currentData() or "auto") if hasattr(self, "rt_source_mode") else "auto"
        selected = len(self._selected_release_tracking_series_rows()) if hasattr(self, "rt_series_table") else 0
        linked = len(self._release_tracking_linked_rows_for_source(source_mode)) if hasattr(self, "rt_series_rows") else 0
        available = selected if scope_mode == "selected" else linked
        self.rt_scan_button.setEnabled(available > 0)
        if hasattr(self, "rt_apply_button"):
            self.rt_apply_button.setEnabled(bool(self.release_tracking_rows))
        if hasattr(self, "rt_workflow_status_label") and not self.release_tracking_rows:
            received = int(self.rt_filter_counts.get("received", len(self.rt_series_unfiltered_rows)) or 0)
            visible = int(self.rt_filter_counts.get("visible", len(self.rt_series_rows)) or 0)
            if available:
                self.rt_workflow_status_label.setText(
                    f"{received} reçue(s) de Komga → {visible} affichée(s). "
                    f"Prêt à analyser {available} série(s) avec {self._release_tracking_source_label(source_mode)}."
                )
            elif received and not visible:
                self.rt_workflow_status_label.setText(
                    f"{received} série(s) reçue(s) de Komga, mais aucune ne correspond aux filtres actifs."
                )
            elif visible:
                self.rt_workflow_status_label.setText(
                    f"{received} reçue(s) de Komga → {visible} affichée(s), mais aucune ne possède "
                    f"un lien {self._release_tracking_source_label(source_mode)} exploitable pour cette portée."
                )
            else:
                self.rt_workflow_status_label.setText(
                    "Aucune série reçue de Komga. Vérifiez la bibliothèque et la recherche, puis rechargez."
                )

    def import_release_tracking_from_explorer(self) -> None:
        rows = self._selected_explorer_series_rows()
        self.release_tracking_series_rows = rows
        self.rt_selected_label.setText(f"{len(rows)} série(s) sélectionnée(s)")
        if not rows:
            QMessageBox.warning(self, "Suivi sorties", "Sélectionne une ou plusieurs séries dans l'Explorateur. Le suivi ne traite jamais toute la bibliothèque implicitement.")
        else:
            self.log(f"Suivi sorties : {len(rows)} série(s) importée(s) depuis l'Explorateur")

    def _release_tracking_provider_links(self, series: Any) -> Dict[str, Dict[str, str]]:
        links: Dict[str, Dict[str, str]] = {}
        for provider in ("bedetheque", "mangabaka"):
            found_provider, entry, _reason = self._pick_supported_update_link(series, provider)
            if found_provider == provider and entry:
                links[provider] = entry
        manga_news_slug, manga_news_url = self._manga_news_link_for_series(series)
        if manga_news_slug or manga_news_url:
            links["manga_news"] = {
                "label": "Manga News",
                "url": manga_news_url,
                "slug": manga_news_slug,
            }
        comicvine_volume_id, comicvine_url = self._comicvine_link_for_series(series)
        if comicvine_volume_id or comicvine_url:
            links["comicvine"] = {
                "label": "ComicVine",
                "url": comicvine_url,
                "volume_id": comicvine_volume_id,
            }
        return links

    def _release_tracking_load_provider_metadata(
        self,
        provider: str,
        link_entry: Dict[str, str],
        bdt_client: BedethequeClient,
        mbk_client: MangaBakaClient,
        mn_client: MangaNewsClient,
        cv_client: ComicVineClient,
    ) -> Dict[str, Any]:
        url = (link_entry or {}).get("url", "")
        if provider == "bedetheque":
            candidate = bdt_client.scrape_series(url)
            metadata, notes = self._enrich_update_with_link_series_metadata("bedetheque", candidate, candidate.series_metadata)
            albums = getattr(candidate, "raw", {}).get("albums", []) if candidate is not None else []
            raw_count = len(albums) if isinstance(albums, list) else 0
            filtered_count = bedetheque_main_album_count(albums)
            return {
                "provider": "bedetheque",
                "title": getattr(candidate, "series_title", "") or metadata.get("title", ""),
                "url": url,
                "metadata": metadata,
                "notes": notes,
                "raw_count": raw_count,
                "filtered_count": filtered_count,
            }
        if provider == "mangabaka":
            series_id = extract_mangabaka_series_id_from_url(url)
            if not series_id:
                raise ValueError("ID série MangaBaka introuvable dans le lien")
            candidate = mbk_client.get_series(series_id)
            metadata, notes = self._enrich_update_with_link_series_metadata("mangabaka", candidate, candidate.series_metadata)
            return {
                "provider": "mangabaka",
                "title": getattr(candidate, "title", "") or metadata.get("title", ""),
                "url": url,
                "metadata": metadata,
                "notes": notes,
                "raw_count": metadata.get("totalBookCount", ""),
                "filtered_count": metadata.get("totalBookCount", ""),
            }
        if provider == "manga_news":
            slug = str((link_entry or {}).get("slug") or "").strip()
            if slug:
                candidate = mn_client.get_series(slug)
            elif url:
                candidate = mn_client.get_series_by_url(url)
            else:
                raise ValueError("Slug/URL Manga News introuvable dans le lien")
            metadata = dict(candidate.series_metadata or {})
            source_url = url or candidate.source_url
            return {
                "provider": "manga_news",
                "title": candidate.title or metadata.get("title", ""),
                "url": source_url,
                "metadata": metadata,
                "notes": ["fiche chargée directement depuis le lien Manga News existant"],
                "raw_count": metadata.get("totalBookCount", ""),
                "filtered_count": metadata.get("totalBookCount", ""),
            }
        if provider == "comicvine":
            volume_id = str((link_entry or {}).get("volume_id") or "").strip() or extract_comicvine_volume_id_from_url(url)
            if not volume_id:
                raise ValueError("ID volume ComicVine introuvable dans le lien")
            candidate = cv_client.get_volume(volume_id)
            metadata, notes = self._enrich_update_with_link_series_metadata("comicvine", candidate, candidate.series_metadata)
            return {
                "provider": "comicvine",
                "title": getattr(candidate, "title", "") or metadata.get("title", ""),
                "url": url or getattr(candidate, "source_url", ""),
                "metadata": metadata,
                "notes": notes,
                "raw_count": metadata.get("totalBookCount", ""),
                "filtered_count": metadata.get("totalBookCount", ""),
            }
        raise ValueError(f"Provider non supporté : {provider}")

    def _release_tracking_source_for_series(
        self,
        series: Any,
        current: Dict[str, Any],
        source_mode: str,
        bdt_client: BedethequeClient,
        mbk_client: MangaBakaClient,
        mn_client: MangaNewsClient,
        cv_client: ComicVineClient,
    ) -> Dict[str, Any]:
        links = self._release_tracking_provider_links(series)
        if source_mode in {"bedetheque", "mangabaka", "manga_news", "comicvine"}:
            if source_mode not in links:
                return {"error": f"aucun lien {source_mode} exploitable"}
            provider_data = self._release_tracking_load_provider_metadata(
                source_mode,
                links[source_mode],
                bdt_client,
                mbk_client,
                mn_client,
                cv_client,
            )
            metadata = dict(provider_data.get("metadata") or {})
            return {
                "source_label": source_mode,
                "status_provider": source_mode if metadata.get("status") else "",
                "count_provider": source_mode if metadata.get("totalBookCount") else "",
                "status_source": metadata.get("status", ""),
                "count_source": metadata.get("totalBookCount", ""),
                "source_url": provider_data.get("url", ""),
                "source_title": provider_data.get("title", ""),
                "raw_count": provider_data.get("raw_count", ""),
                "filtered_count": provider_data.get("filtered_count", ""),
                "notes": provider_data.get("notes", []),
            }

        # Auto contrôlé : FR + Bedetheque => Bedetheque pour le count. Si le
        # status Bedetheque manque, MangaBaka peut compléter le status.
        language = normalized_language_code((current or {}).get("language") or getattr(series, "metadata", {}).get("language", ""))
        notes: List[str] = [f"mode auto contrôlé, langue={language or '<vide>'}"]
        bdt_data: Optional[Dict[str, Any]] = None
        mbk_data: Optional[Dict[str, Any]] = None
        if "bedetheque" in links and (language == "fr" or "mangabaka" not in links):
            bdt_data = self._release_tracking_load_provider_metadata("bedetheque", links["bedetheque"], bdt_client, mbk_client, mn_client, cv_client)
        if "mangabaka" in links and (language != "fr" or bdt_data is None):
            mbk_data = self._release_tracking_load_provider_metadata("mangabaka", links["mangabaka"], bdt_client, mbk_client, mn_client, cv_client)
        if bdt_data is not None and not (bdt_data.get("metadata") or {}).get("status") and "mangabaka" in links:
            try:
                mbk_data = mbk_data or self._release_tracking_load_provider_metadata("mangabaka", links["mangabaka"], bdt_client, mbk_client, mn_client, cv_client)
                notes.append("status complété via MangaBaka car Bedetheque ne le fournit pas")
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                notes.append(f"status MangaBaka indisponible: {exc}")
        if bdt_data is None and mbk_data is None:
            return {"error": "aucun lien Bedetheque ou MangaBaka exploitable"}

        count_data = bdt_data or mbk_data
        status_data = bdt_data if bdt_data and (bdt_data.get("metadata") or {}).get("status") else (mbk_data or bdt_data)
        count_meta = (count_data.get("metadata") if count_data else {}) or {}
        status_meta = (status_data.get("metadata") if status_data else {}) or {}
        notes.extend(count_data.get("notes", []) if count_data else [])
        if status_data and status_data is not count_data:
            notes.extend(status_data.get("notes", []))
        source_parts = []
        if count_data:
            source_parts.append(f"count={count_data.get('provider')}")
        if status_data:
            source_parts.append(f"status={status_data.get('provider')}")
        return {
            "source_label": "auto: " + ", ".join(source_parts),
            "status_provider": status_data.get("provider", "") if status_data and status_meta.get("status") else "",
            "count_provider": count_data.get("provider", "") if count_data and count_meta.get("totalBookCount") else "",
            "status_source": status_meta.get("status", ""),
            "count_source": count_meta.get("totalBookCount", ""),
            "source_url": (count_data or status_data or {}).get("url", ""),
            "source_title": (count_data or status_data or {}).get("title", ""),
            "raw_count": (count_data or {}).get("raw_count", ""),
            "filtered_count": (count_data or {}).get("filtered_count", ""),
            "notes": notes,
        }

    def _release_tracking_row_from_sources(self, index: int, series: Any, current: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        status_decision = quality_release_tracking_status_decision(current.get("status", ""), source.get("status_source", ""))
        count_decision = quality_release_tracking_total_decision(current.get("totalBookCount", ""), source.get("count_source", ""))
        risk = quality_combine_release_tracking_risk(status_decision.get("risk"), count_decision.get("risk"))
        payload: Dict[str, Any] = {}
        if status_decision.get("proposed"):
            payload["status"] = status_decision.get("proposed")
        if count_decision.get("proposed") is not None:
            payload["totalBookCount"] = count_decision.get("proposed")
        notes = list(source.get("notes") or [])
        if source.get("raw_count") not in {"", None} and source.get("filtered_count") not in {"", None} and str(source.get("raw_count")) != str(source.get("filtered_count")):
            notes.append(f"count Bedetheque brut={source.get('raw_count')} filtré={source.get('filtered_count')}")
            if risk == "Faible":
                risk = "Moyen"
        selected_status = bool(status_decision.get("selected")) and risk == "Faible"
        selected_count = bool(count_decision.get("selected")) and risk == "Faible"
        return {
            "index": index,
            "series_id": getattr(series, "id", ""),
            "title": getattr(series, "title", ""),
            "source": source.get("source_label", ""),
            "source_url": source.get("source_url", ""),
            "source_title": source.get("source_title", ""),
            "risk": risk,
            "operation_status": "À valider" if payload else "Ignoré : aucun changement exploitable",
            "apply_status": selected_status,
            "apply_totalBookCount": selected_count,
            "current_status": status_decision.get("current") or "",
            "source_status": status_decision.get("source") or "",
            "proposed_status": status_decision.get("proposed") or "",
            "status_action": status_decision.get("action", ""),
            "status_risk": status_decision.get("risk", ""),
            "current_totalBookCount": "" if count_decision.get("current") is None else str(count_decision.get("current")),
            "source_totalBookCount": "" if count_decision.get("source") is None else str(count_decision.get("source")),
            "proposed_totalBookCount": "" if count_decision.get("proposed") is None else str(count_decision.get("proposed")),
            "totalBookCount_action": count_decision.get("action", ""),
            "totalBookCount_risk": count_decision.get("risk", ""),
            "raw_totalBookCount": source.get("raw_count", ""),
            "filtered_totalBookCount": source.get("filtered_count", ""),
            "notes": " | ".join(str(x) for x in notes if str(x).strip()),
            "error": "",
            "payload_json": json_text(payload, indent=0) if payload else "",
            "__current_metadata": current,
            "__payload": payload,
        }

    def scan_release_tracking(self) -> None:
        scope_mode = str(self.rt_scope_mode.currentData() or "selected")
        source_mode = str(self.rt_source_mode.currentData() or "auto")
        if scope_mode in {"all_source_linked", "all_manga_news_linked", "all_comicvine_linked"}:
            if scope_mode == "all_manga_news_linked":
                source_mode = "manga_news"
            elif scope_mode == "all_comicvine_linked":
                source_mode = "comicvine"
            selected_series = self._release_tracking_linked_rows_for_source(source_mode)
        else:
            selected_series = self._selected_release_tracking_series_rows()
        self.release_tracking_series_rows = list(selected_series)
        if not selected_series:
            if scope_mode in {"all_source_linked", "all_manga_news_linked", "all_comicvine_linked"}:
                message = f"Aucune série actuellement chargée et filtrée ne possède de lien {self._release_tracking_source_label(source_mode)} exploitable."
            else:
                message = "Sélectionne une ou plusieurs séries dans l'onglet Suivi sorties."
            QMessageBox.warning(self, "Suivi sorties", message)
            return
        if source_mode == "manga_news" and not self.manga_news_enabled.isChecked():
            QMessageBox.warning(self, "Suivi sorties", "Le module Manga News est désactivé dans l'onglet Connexion.")
            return
        if source_mode == "comicvine" and not self.comicvine_enabled.isChecked():
            QMessageBox.warning(self, "Suivi sorties", "Le module ComicVine est désactivé dans l'onglet Connexion.")
            return
        if source_mode == "comicvine" and not self.comicvine_api_key.text().strip():
            QMessageBox.warning(self, "Suivi sorties", "Clé API ComicVine absente.")
            return
        if self.bdt_csv_only.isChecked() and source_mode == "auto":
            source_mode = "bedetheque"
            self.log("ℹ️ CSV uniquement actif : le suivi sorties utilise exclusivement le CSV Bedetheque.")
        total = len(selected_series)
        if hasattr(self, "rt_workflow_status_label"):
            self.rt_workflow_status_label.setText(
                f"Analyse en cours : {total} série(s), source {self._release_tracking_source_label(source_mode)}."
            )
        if scope_mode in {"all_source_linked", "all_manga_news_linked", "all_comicvine_linked"}:
            self.log(f"ℹ️ Suivi sorties : rafraîchissement {self._release_tracking_source_label(source_mode)} de {total} série(s) chargée(s) avec lien.")
        self._set_auto_match_progress(f"Suivi sorties — scan {source_mode} — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def do_scan() -> Dict[str, Any]:
            api = self.komga_api()
            bdt_client = self.bedetheque_client() if source_mode in {"auto", "bedetheque"} else None
            mbk_client = self.mangabaka_client() if source_mode in {"auto", "mangabaka"} else None
            mn_client = self.manga_news_client() if source_mode == "manga_news" else None
            cv_client = self.comicvine_client() if source_mode == "comicvine" else None
            rows: List[Dict[str, Any]] = []
            for index, series in enumerate(selected_series, start=1):
                title = getattr(series, "title", "")
                self._emit_auto_match_progress(progress, "Suivi sorties", index - 1, total, f"scan {index}/{total} — {title}")
                row_base = {
                    "index": index,
                    "series_id": getattr(series, "id", ""),
                    "title": title,
                    "source": source_mode,
                    "source_url": "",
                    "source_title": "",
                    "risk": "Erreur",
                    "operation_status": "Erreur",
                    "apply_status": False,
                    "apply_totalBookCount": False,
                    "current_status": "",
                    "source_status": "",
                    "proposed_status": "",
                    "status_action": "",
                    "status_risk": "",
                    "current_totalBookCount": "",
                    "source_totalBookCount": "",
                    "proposed_totalBookCount": "",
                    "totalBookCount_action": "",
                    "totalBookCount_risk": "",
                    "raw_totalBookCount": "",
                    "filtered_totalBookCount": "",
                    "notes": "",
                    "error": "",
                    "payload_json": "",
                    "__current_metadata": {},
                    "__payload": {},
                }
                try:
                    if source_mode in {"manga_news", "mangabaka", "comicvine"}:
                        self.enrichment_history.record_search(source_mode, getattr(series, "id", ""), title)
                    current = self._fetch_current_metadata("series", series.id)
                    source = self._release_tracking_source_for_series(
                        series,
                        current,
                        source_mode,
                        bdt_client,
                        mbk_client,
                        mn_client,
                        cv_client,
                    )
                    if source.get("error"):
                        row_base.update({
                            "risk": "Ignoré",
                            "operation_status": "Ignoré",
                            "error": source.get("error", ""),
                            "current_status": quality_normalize_series_status_for_tracking(current.get("status", "")),
                            "current_totalBookCount": one_line(current.get("totalBookCount", "")),
                            "__current_metadata": current,
                        })
                        rows.append(row_base)
                    else:
                        rows.append(self._release_tracking_row_from_sources(index, series, current, source))
                except ExternalSourceBlocked:
                    raise
                except Exception as exc:
                    row_base["error"] = str(exc)
                    rows.append(row_base)
                self._emit_auto_match_progress(progress, "Suivi sorties", index, total, f"scan {index}/{total} — {title}")
            csv_path = self.backup.export_csv(f"release_tracking_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", self._release_tracking_public_rows(rows))
            return {"rows": rows, "csv_path": csv_path, "source_mode": source_mode}

        def done(result: Dict[str, Any]) -> None:
            self.release_tracking_rows = result.get("rows") or []
            self.release_tracking_last_csv_path = result.get("csv_path", "")
            if hasattr(self, "rt_hide_ignored"):
                self.rt_hide_ignored.setChecked(True)
            self.populate_release_tracking_table()
            summary = self._status_counts_line(self.release_tracking_rows, status_key="operation_status")
            self.rt_detail.setPlainText(f"Scan terminé. CSV : {self.release_tracking_last_csv_path}\n{summary}")
            if hasattr(self, "rt_workflow_status_label"):
                self.rt_workflow_status_label.setText(
                    f"Analyse terminée : {summary}. Vérifiez les cases avant d'appliquer."
                )
            self._set_auto_match_progress(f"Suivi sorties — scan terminé — {summary}", total, total)

        self.run_worker("Suivi sorties — scan", do_scan, done)

    def _release_tracking_public_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        public: List[Dict[str, Any]] = []
        for row in rows or []:
            public.append({k: v for k, v in row.items() if not str(k).startswith("__")})
        return public

    def _release_tracking_headers(self) -> List[str]:
        return [
            "Appliquer status", "Appliquer tomes", "Risque", "Série", "Source",
            "Status Komga", "Status source", "Status proposé", "Action status",
            "Tomes Komga", "Tomes source", "Tomes proposé", "Action tomes",
            "BDT brut", "BDT filtré", "Statut opération", "Notes", "Erreur", "Series ID", "Lien source", "Payload JSON",
        ]

    def _release_tracking_row_matches_filter(self, row: Dict[str, Any]) -> bool:
        filt = str(self.rt_filter.currentData() or "all") if hasattr(self, "rt_filter") else "all"
        hide_ignored = self.rt_hide_ignored.isChecked() if hasattr(self, "rt_hide_ignored") else False
        if hide_ignored and filt != "Ignoré" and self._is_release_tracking_ignored_row(row):
            return False
        if self.rt_only_changes.isChecked() if hasattr(self, "rt_only_changes") else False:
            if not row.get("proposed_status") and not row.get("proposed_totalBookCount"):
                return False
        if filt == "all":
            return True
        if filt == "changes":
            return bool(row.get("proposed_status") or row.get("proposed_totalBookCount"))
        if filt == "status_changes":
            return bool(row.get("proposed_status"))
        if filt == "count_changes":
            return bool(row.get("proposed_totalBookCount"))
        if filt in {"Faible", "Moyen", "Fort", "Erreur", "Ignoré"}:
            return row.get("risk") == filt or str(row.get("operation_status", "")).startswith(filt)
        return True

    def populate_release_tracking_table(self) -> None:
        if not hasattr(self, "rt_table"):
            return
        headers = self._release_tracking_headers()
        visible_rows = [row for row in self.release_tracking_rows if self._release_tracking_row_matches_filter(row)]
        self.rt_table.setColumnCount(len(headers))
        self.rt_table.setHorizontalHeaderLabels(headers)
        self.rt_table.setRowCount(len(visible_rows))
        self.rt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rt_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for table_row, row in enumerate(visible_rows):
            original_index = self.release_tracking_rows.index(row)
            status_item = QTableWidgetItem("")
            status_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            status_item.setCheckState(Qt.Checked if row.get("apply_status") else Qt.Unchecked)
            status_item.setData(Qt.UserRole, original_index)
            count_item = QTableWidgetItem("")
            count_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            count_item.setCheckState(Qt.Checked if row.get("apply_totalBookCount") else Qt.Unchecked)
            count_item.setData(Qt.UserRole, original_index)
            self.rt_table.setItem(table_row, 0, status_item)
            self.rt_table.setItem(table_row, 1, count_item)
            values = [
                row.get("risk", ""), row.get("title", ""), row.get("source", ""),
                row.get("current_status", ""), row.get("source_status", ""), row.get("proposed_status", ""), row.get("status_action", ""),
                row.get("current_totalBookCount", ""), row.get("source_totalBookCount", ""), row.get("proposed_totalBookCount", ""), row.get("totalBookCount_action", ""),
                row.get("raw_totalBookCount", ""), row.get("filtered_totalBookCount", ""), row.get("operation_status", ""),
                row.get("notes", ""), row.get("error", ""), row.get("series_id", ""), row.get("source_url", ""), row.get("payload_json", ""),
            ]
            for offset, value in enumerate(values, start=2):
                item = QTableWidgetItem(str(value or ""))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip(str(value or ""))
                item.setData(Qt.UserRole, original_index)
                self.rt_table.setItem(table_row, offset, item)
        self._configure_resizable_table(self.rt_table)
        self._ensure_generic_table_context_menu(self.rt_table)
        self._update_release_tracking_selection_label()

    def _sync_release_tracking_checks_from_table(self) -> None:
        if not hasattr(self, "rt_table"):
            return
        for table_row in range(self.rt_table.rowCount()):
            item = self.rt_table.item(table_row, 0)
            if item is None:
                continue
            index = item.data(Qt.UserRole)
            if not isinstance(index, int) or not (0 <= index < len(self.release_tracking_rows)):
                continue
            self.release_tracking_rows[index]["apply_status"] = item.checkState() == Qt.Checked
            count_item = self.rt_table.item(table_row, 1)
            self.release_tracking_rows[index]["apply_totalBookCount"] = bool(count_item and count_item.checkState() == Qt.Checked)

    def release_tracking_check_low_risk(self) -> None:
        for row in self.release_tracking_rows:
            row["apply_status"] = row.get("status_risk") == "Faible" and bool(row.get("proposed_status"))
            row["apply_totalBookCount"] = row.get("totalBookCount_risk") == "Faible" and bool(row.get("proposed_totalBookCount"))
        self.populate_release_tracking_table()

    def release_tracking_uncheck_all(self) -> None:
        for row in self.release_tracking_rows:
            row["apply_status"] = False
            row["apply_totalBookCount"] = False
        self.populate_release_tracking_table()

    def show_release_tracking_detail(self) -> None:
        rows = self._selected_row_indexes(self.rt_table) if hasattr(self, "rt_table") else []
        if not rows:
            return
        item = self.rt_table.item(rows[0], 0)
        index = item.data(Qt.UserRole) if item else None
        if not isinstance(index, int) or not (0 <= index < len(self.release_tracking_rows)):
            return
        public_row = {k: v for k, v in self.release_tracking_rows[index].items() if not str(k).startswith("__")}
        self.rt_detail.setPlainText(json_text(public_row))

    def export_release_tracking_csv(self) -> None:
        self._sync_release_tracking_checks_from_table()
        path = self.backup.export_csv(f"release_tracking_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", self._release_tracking_public_rows(self.release_tracking_rows))
        self.release_tracking_last_csv_path = path
        self.rt_detail.setPlainText(f"CSV exporté : {path}")
        self.log(f"✅ Suivi sorties CSV exporté : {path}")

    def apply_release_tracking_checked(self) -> None:
        self._sync_release_tracking_checks_from_table()
        rows_to_apply = []
        for row in self.release_tracking_rows:
            payload: Dict[str, Any] = {}
            if row.get("apply_status") and row.get("proposed_status"):
                payload["status"] = row.get("proposed_status")
            if row.get("apply_totalBookCount") and row.get("proposed_totalBookCount"):
                try:
                    payload["totalBookCount"] = int(row.get("proposed_totalBookCount"))
                except (TypeError, ValueError):
                    pass
            if payload:
                rows_to_apply.append((row, payload))
        if not rows_to_apply:
            QMessageBox.information(self, "Suivi sorties", "Aucune modification cochée.")
            return
        simulation = self.simulation_enabled()
        if not simulation:
            answer = QMessageBox.question(
                self,
                "Suivi sorties — écriture réelle",
                f"Appliquer {len(rows_to_apply)} modification(s) status/totalBookCount ?\nBackup + audit seront créés avant chaque PATCH.",
            )
            if answer != QMessageBox.Yes:
                return
        total = len(rows_to_apply)
        if hasattr(self, "rt_workflow_status_label"):
            mode = "simulation" if simulation else "écriture réelle"
            self.rt_workflow_status_label.setText(f"Application en cours : {total} modification(s), mode {mode}.")
        self._set_auto_match_progress("Suivi sorties — application", 0, total)
        progress = self._auto_match_progress_callback()

        def do_apply() -> Dict[str, Any]:
            api = self.komga_api()
            result_rows: List[Dict[str, Any]] = []
            for index, (row, payload) in enumerate(rows_to_apply, start=1):
                title = row.get("title", "")
                self._emit_auto_match_progress(progress, "Suivi sorties", index - 1, total, f"application {index}/{total} — {title}")
                result = dict(row)
                result["applied_payload_json"] = json_text(payload, indent=0)
                try:
                    if simulation:
                        result["operation_status"] = "OK simulation"
                    else:
                        self._write_metadata_update(api, "series", str(row.get("series_id") or ""), payload, row.get("__current_metadata") or {}, source="release_tracking", note="Suivi sorties")
                        result["operation_status"] = "OK appliqué"
                except Exception as exc:
                    result["operation_status"] = "Erreur"
                    result["error"] = str(exc)
                result_rows.append(result)
                self._emit_auto_match_progress(progress, "Suivi sorties", index, total, f"application {index}/{total} — {title}")
            csv_path = self.backup.export_csv(f"release_tracking_apply_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", self._release_tracking_public_rows(result_rows))
            return {"rows": result_rows, "csv_path": csv_path, "simulation": simulation}

        def done(result: Dict[str, Any]) -> None:
            rows_by_id = {row.get("series_id"): row for row in result.get("rows") or []}
            for row in self.release_tracking_rows:
                updated = rows_by_id.get(row.get("series_id"))
                if updated:
                    row["operation_status"] = updated.get("operation_status", row.get("operation_status", ""))
                    row["error"] = updated.get("error", row.get("error", ""))
            self.populate_release_tracking_table()
            summary = self._status_counts_line(result.get("rows") or [], status_key="operation_status")
            self.release_tracking_last_csv_path = result.get("csv_path", "")
            self.rt_detail.setPlainText(f"Application terminée. CSV : {self.release_tracking_last_csv_path}\n{summary}")
            if hasattr(self, "rt_workflow_status_label"):
                self.rt_workflow_status_label.setText(f"Application terminée : {summary}.")
            self._set_auto_match_progress(f"Suivi sorties — application terminée — {summary}", total, total)

        self.run_worker("Suivi sorties — application", do_apply, done)

    def _build_operations_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title_row = QHBoxLayout()
        title = QLabel("Centre des opérations")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        btn_logs = QPushButton("Ouvrir les journaux")
        btn_rollback = QPushButton("Ouvrir les restaurations")
        btn_logs.clicked.connect(lambda: self._set_current_tab_by_title("Logs / Backups"))
        btn_rollback.clicked.connect(lambda: self._set_current_tab_by_title("Rollback"))
        title_row.addWidget(title, 1)
        title_row.addWidget(btn_logs)
        title_row.addWidget(btn_rollback)
        layout.addLayout(title_row)

        intro = QLabel(
            "Toutes les tâches lancées pendant cette session apparaissent ici. Les relances sont proposées uniquement "
            "pour les lectures sans écriture ; une opération d'écriture en erreur doit être contrôlée avant toute nouvelle tentative."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        actions = QHBoxLayout()
        self.operations_retry_button = QPushButton("Relancer la lecture sélectionnée")
        self.operations_clear_button = QPushButton("Effacer l'historique terminé")
        self.operations_retry_button.setEnabled(False)
        actions.addWidget(self.operations_retry_button)
        actions.addWidget(self.operations_clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        splitter = QSplitter(Qt.Vertical)
        self.operations_table = QTableWidget()
        self.operations_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.operations_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.operations_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._register_table(self.operations_table, "operations.history")
        splitter.addWidget(self.operations_table)

        self.operations_detail = QTextEdit()
        self.operations_detail.setReadOnly(True)
        self.operations_detail.setLineWrapMode(QTextEdit.WidgetWidth)
        self.operations_detail.setMinimumHeight(180)
        splitter.addWidget(self.operations_detail)
        splitter.setSizes([520, 220])
        layout.addWidget(splitter, 1)

        self.operations_retry_button.clicked.connect(self.retry_selected_operation)
        self.operations_clear_button.clicked.connect(self.clear_finished_operations)
        self.operations_table.itemSelectionChanged.connect(self.show_selected_operation)
        self._populate_operations_table()
        self._add_main_tab(tab, "Opérations")

    def _populate_operations_table(self) -> None:
        table = getattr(self, "operations_table", None)
        if table is None:
            return
        headers = ["État", "Opération", "Démarrage", "Durée", "Résumé", "Relance"]
        sorting = table.isSortingEnabled()
        table.setSortingEnabled(False)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setRowCount(len(self.operation_history))
        for row_index, task in enumerate(self.operation_history):
            started = task.get("started_at")
            started_text = started.strftime("%H:%M:%S") if isinstance(started, datetime) else ""
            duration = task.get("duration_ms")
            duration_text = "En cours" if duration is None else f"{float(duration) / 1000:.2f} s"
            values = [
                task.get("status", ""),
                task.get("label", ""),
                started_text,
                duration_text,
                task.get("summary", ""),
                "Oui" if task.get("retryable") else "Non",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, str(task.get("id") or ""))
                table.setItem(row_index, column, item)
        self._configure_resizable_table(table)
        table.setSortingEnabled(sorting or True)
        self._ensure_generic_table_context_menu(table)
        self._update_operation_actions()

    def _selected_operation_task(self) -> Optional[Dict[str, Any]]:
        table = getattr(self, "operations_table", None)
        if table is None:
            return None
        row = self._selected_row_index(table)
        item = table.item(row, 0) if row >= 0 else None
        task_id = str(item.data(Qt.UserRole) or "") if item is not None else ""
        return self._operation_task(task_id)

    def _update_operation_actions(self) -> None:
        task = self._selected_operation_task()
        can_retry = bool(
            task
            and task.get("status") == "Erreur"
            and task.get("retryable")
            and callable(task.get("fn"))
        )
        if hasattr(self, "operations_retry_button"):
            self.operations_retry_button.setEnabled(can_retry)

    def show_selected_operation(self) -> None:
        task = self._selected_operation_task()
        self._update_operation_actions()
        if task is None:
            return
        started = task.get("started_at")
        public = {
            "opération": task.get("label", ""),
            "état": task.get("status", ""),
            "démarrage": started.isoformat(timespec="seconds") if isinstance(started, datetime) else "",
            "durée_ms": task.get("duration_ms"),
            "résumé": task.get("summary", ""),
            "relance_sans_écriture": bool(task.get("retryable")),
            "détail": task.get("detail", ""),
        }
        self.operations_detail.setPlainText(json_text(public))

    def retry_selected_operation(self) -> None:
        task = self._selected_operation_task()
        if not task or task.get("status") != "Erreur" or not task.get("retryable"):
            QMessageBox.information(
                self,
                "Centre des opérations",
                "Cette opération ne peut pas être relancée automatiquement.",
            )
            return
        fn = task.get("fn")
        done = task.get("done")
        if not callable(fn):
            return
        if QMessageBox.question(
            self,
            "Relancer la lecture",
            f"Relancer « {task.get('label', '')} » ?\n\nAucune relance automatique n'est proposée pour les écritures.",
        ) != QMessageBox.Yes:
            return
        self.run_worker(f"Relance — {task.get('label', 'opération')}", fn, done if callable(done) else None)

    def clear_finished_operations(self) -> None:
        self.operation_history = [
            task for task in self.operation_history if task.get("status") == "En cours"
        ]
        self.operations_detail.clear()
        self._populate_operations_table()

    def open_latest_rollback(self) -> None:
        self._set_current_tab_by_title("Rollback")
        self.refresh_rollback_records()
        records = list(getattr(self, "rollback_records", []))
        if not records:
            QMessageBox.information(self, "Restaurations", "Aucun snapshot de restauration disponible.")
            return
        latest = max(
            records,
            key=lambda record: (str(record.get("timestamp") or ""), str(record.get("path") or "")),
        )
        expected_path = str(latest.get("path") or "")
        for row in range(self.rollback_table.rowCount()):
            path_item = self.rollback_table.item(row, 5)
            if path_item is not None and path_item.text() == expected_path:
                self.rollback_table.selectRow(row)
                self.rollback_table.scrollToItem(path_item)
                break

    def _build_rollback_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        title = QLabel("Restaurer une modification")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        intro = QLabel(
            "Chaque écriture réalisée par l'outil crée un snapshot. Sélectionnez-en un, comparez-le à l'état actuel, "
            "puis restaurez uniquement les champs qui ont réellement changé."
        )
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        steps = QGroupBox("Parcours sécurisé")
        steps_layout = QHBoxLayout(steps)
        for step in (
            "1. Actualiser les snapshots",
            "2. Sélectionner une cible",
            "3. Prévisualiser les différences",
            "4. Restaurer si nécessaire",
        ):
            label = QLabel(step)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("padding: 5px; border: 1px solid #555; border-radius: 3px;")
            steps_layout.addWidget(label, 1)
        layout.addWidget(steps)

        controls = QHBoxLayout()
        self.rollback_filter = QLineEdit()
        self.rollback_filter.setPlaceholderText("Filtrer session, type, ID, note…")
        self.rollback_type_filter = QComboBox()
        self.rollback_type_filter.addItem("Tous types", "")
        self.rollback_type_filter.addItem("Séries", "series")
        self.rollback_type_filter.addItem("Tomes/livres", "book")
        btn_refresh = QPushButton("Rafraîchir")
        self.rollback_preview_button = QPushButton("Prévisualiser les différences")
        self.rollback_apply_button = QPushButton("Restaurer le snapshot sélectionné")
        self.rollback_open_file_button = QPushButton("Ouvrir le JSON")
        btn_open_folder = QPushButton("Ouvrir dossier backups")
        controls.addWidget(QLabel("Recherche"))
        controls.addWidget(self.rollback_filter, 1)
        controls.addWidget(QLabel("Type"))
        controls.addWidget(self.rollback_type_filter)
        controls.addWidget(btn_refresh)
        controls.addWidget(self.rollback_preview_button)
        controls.addWidget(self.rollback_apply_button)
        controls.addWidget(self.rollback_open_file_button)
        controls.addWidget(btn_open_folder)
        layout.addLayout(controls)

        self.rollback_status_label = QLabel("Actualisez la liste pour afficher les snapshots disponibles.")
        self.rollback_status_label.setWordWrap(True)
        self.rollback_status_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.rollback_status_label)

        splitter = QSplitter(Qt.Vertical)
        self.rollback_table = QTableWidget()
        self.rollback_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rollback_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rollback_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._register_table(self.rollback_table, "rollback.records", default_hidden=["Chemin"])
        splitter.addWidget(self.rollback_table)

        self.rollback_preview = QTextEdit()
        self.rollback_preview.setReadOnly(True)
        self.rollback_preview.setLineWrapMode(QTextEdit.WidgetWidth)
        self.rollback_preview.setStyleSheet("font-family: monospace;")
        self.rollback_preview.setMinimumHeight(220)
        splitter.addWidget(self.rollback_preview)
        splitter.setSizes([460, 300])
        layout.addWidget(splitter, 1)

        self.rollback_records: List[Dict[str, Any]] = []
        self.rollback_visible_records: List[Dict[str, Any]] = []

        btn_refresh.clicked.connect(self.refresh_rollback_records)
        self.rollback_preview_button.clicked.connect(self.preview_selected_rollback)
        self.rollback_apply_button.clicked.connect(self.apply_selected_rollback)
        self.rollback_open_file_button.clicked.connect(self.open_selected_rollback_json)
        btn_open_folder.clicked.connect(self.open_backup_root_folder)
        self.rollback_filter.textChanged.connect(self._populate_rollback_table)
        self.rollback_type_filter.currentIndexChanged.connect(self._populate_rollback_table)
        self.rollback_table.itemSelectionChanged.connect(self._show_selected_rollback_snapshot)
        self._update_rollback_action_state()

        self._add_main_tab(tab, "Rollback")

    def refresh_rollback_records(self) -> None:
        root = self.backup_root.text().strip() if hasattr(self, "backup_root") else self.config.backup_root
        if not root:
            root = self.config.backup_root
        self.rollback_records = list_rollback_records(root)
        self._populate_rollback_table()
        self.rollback_preview.setPlainText(f"{len(self.rollback_records)} snapshot(s) rollback trouvé(s) dans {os.path.abspath(root)}")
        self.rollback_status_label.setText(
            f"{len(self.rollback_records)} snapshot(s) disponible(s). Sélectionnez une ligne pour continuer."
        )

    def _populate_rollback_table(self) -> None:
        if not hasattr(self, "rollback_table"):
            return
        text_filter = self.rollback_filter.text().strip().casefold() if hasattr(self, "rollback_filter") else ""
        type_filter = self.rollback_type_filter.currentData() if hasattr(self, "rollback_type_filter") else ""
        visible: List[Dict[str, Any]] = []
        for record in getattr(self, "rollback_records", []):
            if type_filter and record.get("target_type") != type_filter:
                continue
            haystack = " ".join(str(record.get(key) or "") for key in ("session", "timestamp", "target_type", "target_id", "note", "path")).casefold()
            if text_filter and text_filter not in haystack:
                continue
            visible.append(record)
        self.rollback_visible_records = visible
        rows = [[
            rec.get("session", ""),
            rec.get("timestamp", ""),
            rec.get("target_type", ""),
            rec.get("target_id", ""),
            rec.get("note", ""),
            rec.get("path", ""),
        ] for rec in visible]
        self._set_table(self.rollback_table, ["Session", "Timestamp", "Type", "ID", "Note", "Chemin"], rows, selection_mode=QAbstractItemView.SingleSelection)
        self.rollback_table.setSortingEnabled(True)
        self._update_rollback_action_state()

    def _selected_rollback_record(self) -> Optional[Dict[str, Any]]:
        row = self._selected_row_index(self.rollback_table)
        if row < 0:
            return None
        path_item = self.rollback_table.item(row, 5)
        visible_path = path_item.text() if path_item is not None else ""
        for record in getattr(self, "rollback_visible_records", []):
            if str(record.get("path") or "") == visible_path:
                return record
        return None

    def _update_rollback_action_state(self) -> None:
        selected = self._selected_rollback_record() is not None if hasattr(self, "rollback_table") else False
        for name in ("rollback_preview_button", "rollback_apply_button", "rollback_open_file_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(selected)

    def _show_selected_rollback_snapshot(self) -> None:
        record = self._selected_rollback_record()
        if not record:
            self._update_rollback_action_state()
            return
        self._update_rollback_action_state()
        if hasattr(self, "rollback_status_label"):
            self.rollback_status_label.setText(
                f"Snapshot sélectionné : {record.get('target_type', '')}:{record.get('target_id', '')}. "
                "Prévisualisez les différences avant toute restauration."
            )
        try:
            snapshot = load_rollback_snapshot(str(record.get("abs_path") or ""))
        except Exception as exc:
            self.rollback_preview.setPlainText(f"Erreur lecture rollback JSON : {exc}")
            return
        self.rollback_preview.setPlainText(json_text({"record": record, "snapshot": snapshot}))

    def open_selected_rollback_json(self) -> None:
        record = self._selected_rollback_record()
        if not record:
            QMessageBox.information(self, "Rollback", "Aucun rollback sélectionné.")
            return
        path = str(record.get("abs_path") or "")
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Rollback", "Fichier rollback introuvable.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_backup_root_folder(self) -> None:
        root = self.backup_root.text().strip() if hasattr(self, "backup_root") else self.config.backup_root
        if root and os.path.isdir(root):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(root)))
        else:
            QMessageBox.warning(self, "Backups", "Dossier backups introuvable.")

    def _rollback_payload_from_snapshot(self, target_type: str, snapshot: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        fields = self._fields_for_target(target_type)
        snapshot = snapshot or {}
        current = current or {}
        for field in fields:
            if target_type == "book" and field == "isbn":
                continue
            if field.endswith("Lock") and field not in snapshot:
                continue
            old_present = field in snapshot
            current_present = field in current
            if not old_present and not current_present:
                continue
            old_value = snapshot.get(field, "")
            current_value = current.get(field, "")
            old_norm = normalize_metadata_payload_value(field, old_value)
            current_norm = normalize_metadata_payload_value(field, current_value)
            if one_line(old_norm) == one_line(current_norm):
                continue
            if is_blank_metadata_value(old_value):
                payload[field] = None
            else:
                payload[field] = old_norm
        return self._normalize_payload_for_target(target_type, payload)

    def _rollback_preview_text(self, record: Dict[str, Any], snapshot: Dict[str, Any], current: Dict[str, Any], payload: Dict[str, Any]) -> str:
        lines = [
            "Rollback assisté",
            f"Session : {record.get('session', '')}",
            f"Snapshot : {record.get('path', '')}",
            f"Cible : {record.get('target_type', '')}:{record.get('target_id', '')}",
            f"Simulation : {self.simulation_enabled()}",
            "",
            "PRÉVISUALISATION AVANT / APRÈS",
            "Champs qui seraient restaurés :",
        ]
        if not payload:
            lines.append("  Aucun changement détecté.")
        for field, value in payload.items():
            lines.append(f"- {metadata_field_label(field)} [{field}]")
            lines.append(f"  Avant : {one_line((current or {}).get(field, ''))}")
            lines.append(f"  Après : {one_line(value)}")
        lines.extend(["", "Données techniques de restauration :", json_text(payload), "", "Snapshot complet :", json_text(snapshot)])
        return "\n".join(lines)

    def preview_selected_rollback(self) -> None:
        record = self._selected_rollback_record()
        if not record:
            QMessageBox.information(self, "Rollback", "Aucun rollback sélectionné.")
            return
        target_type = str(record.get("target_type") or "").strip()
        target_id = str(record.get("target_id") or "").strip()
        if target_type not in {"series", "book"} or not target_id:
            QMessageBox.warning(self, "Rollback", "Rollback invalide : type ou ID manquant.")
            return

        def do_preview() -> Dict[str, Any]:
            snapshot = load_rollback_snapshot(str(record.get("abs_path") or ""))
            current = self._fetch_current_metadata(target_type, target_id)
            payload = self._rollback_payload_from_snapshot(target_type, snapshot, current)
            return {"record": record, "snapshot": snapshot, "current": current, "payload": payload}

        def done(data: Dict[str, Any]) -> None:
            self.rollback_preview.setPlainText(self._rollback_preview_text(data["record"], data["snapshot"], data["current"], data["payload"]))
            if hasattr(self, "rollback_status_label"):
                count = len(data["payload"])
                self.rollback_status_label.setText(
                    f"Prévisualisation terminée : {count} champ(s) seraient restauré(s)."
                )

        self.run_worker("Prévisualisation rollback", do_preview, done)

    def apply_selected_rollback(self) -> None:
        record = self._selected_rollback_record()
        if not record:
            QMessageBox.information(self, "Rollback", "Aucun rollback sélectionné.")
            return
        target_type = str(record.get("target_type") or "").strip()
        target_id = str(record.get("target_id") or "").strip()
        if target_type not in {"series", "book"} or not target_id:
            QMessageBox.warning(self, "Rollback", "Rollback invalide : type ou ID manquant.")
            return

        if self.simulation_enabled():
            self.preview_selected_rollback()
            self.log("Simulation active : rollback non appliqué.")
            return

        message = (
            "Appliquer ce rollback ?\n\n"
            f"Cible : {target_type}:{target_id}\n"
            f"Snapshot : {record.get('path', '')}\n\n"
            "L'opération écrira dans Komga. Un nouvel audit et un nouveau snapshot rollback seront créés avant écriture."
        )
        if QMessageBox.question(self, "Confirmer rollback", message) != QMessageBox.Yes:
            return

        def do_apply() -> Dict[str, Any]:
            api = self.komga_api()
            snapshot = load_rollback_snapshot(str(record.get("abs_path") or ""))
            current = self._fetch_current_metadata(target_type, target_id)
            payload = self._rollback_payload_from_snapshot(target_type, snapshot, current)
            response = self._write_metadata_update(api, target_type, target_id, payload, current, source="rollback", note=f"Rollback depuis {record.get('path', '')}")
            return {"record": record, "snapshot": snapshot, "current": current, "payload": payload, "response": response}

        def done(data: Dict[str, Any]) -> None:
            self.rollback_preview.setPlainText(json_text({
                "applied": True,
                "record": data["record"],
                "payload": data["payload"],
                "response": data["response"],
            }))
            self.log(f"✅ Rollback appliqué sur {target_type}:{target_id}")
            self.refresh_rollback_records()
            if hasattr(self, "rollback_status_label"):
                self.rollback_status_label.setText(
                    f"Restauration terminée pour {target_type}:{target_id}. Un nouveau snapshot de sécurité a été créé."
                )

        self.run_worker("Application rollback", do_apply, done)

    def _build_logs_tab(self) -> None:
        self.tab_logs = QWidget()
        layout = QVBoxLayout(self.tab_logs)
        title_row = QHBoxLayout()
        title = QLabel("Journal d'activité et sauvegardes")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        btn_logs_rollback = QPushButton("Consulter les restaurations")
        btn_logs_backups = QPushButton("Ouvrir le dossier de la session")
        btn_logs_rollback.clicked.connect(lambda: self._set_current_tab_by_title("Rollback"))
        btn_logs_backups.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.backup.session_dir)))
        title_row.addWidget(title, 1)
        title_row.addWidget(btn_logs_rollback)
        title_row.addWidget(btn_logs_backups)
        layout.addLayout(title_row)
        intro = QLabel(
            "Ce journal explique les opérations de la session. Les rapports détaillés et snapshots associés restent "
            "dans le dossier local de sauvegarde."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)
        self._add_main_tab(self.tab_logs, "Logs / Backups")

    # ------------------------------------------------------------------
    # Config/connection
    # ------------------------------------------------------------------
    def _config_to_ui(self) -> None:
        self.komga_url.setText(self.config.komga.url)
        self.auth_mode.setCurrentText(self.config.komga.auth_mode)
        self.api_key.setText(self.config.komga.api_key)
        self.username.setText(self.config.komga.username)
        self.password.setText(self.config.komga.password)
        self.timeout_seconds.setValue(int(self.config.komga.timeout_seconds))
        self.komf_url.setText(self.config.komf.url)
        self.komf_enabled.setChecked(bool(self.config.komf.enabled))
        self.komf_timeout_seconds.setValue(int(self.config.komf.timeout_seconds))
        self.bdt_csv_only.setChecked(self.config.bedetheque.mode == "csv")
        self.bdt_csv_path.setText(self.config.bedetheque.csv_path)
        self.on_bedetheque_source_changed()
        self.mangabaka_base_url.setText(self.config.mangabaka.url)
        self.mangabaka_enabled.setChecked(bool(self.config.mangabaka.enabled))
        self.mangabaka_timeout_seconds.setValue(int(self.config.mangabaka.timeout_seconds))
        self.mangabaka_cache_enabled.setChecked(bool(self.config.mangabaka.cache_enabled))
        self.mangabaka_cache_dir.setText(self.config.mangabaka.cache_dir)
        self.manga_news_base_url.setText(self.config.manga_news.url)
        self.manga_news_enabled.setChecked(bool(self.config.manga_news.enabled))
        self.manga_news_timeout_seconds.setValue(int(self.config.manga_news.timeout_seconds))
        self.manga_news_token.setText(self.config.manga_news.token)
        self.manga_news_cache_enabled.setChecked(bool(self.config.manga_news.cache_enabled))
        self.manga_news_cache_dir.setText(self.config.manga_news.cache_dir)
        self.comicvine_base_url.setText(self.config.comicvine.url)
        self.comicvine_enabled.setChecked(bool(self.config.comicvine.enabled))
        self.comicvine_timeout_seconds.setValue(int(self.config.comicvine.timeout_seconds))
        self.comicvine_api_key.setText(self.config.comicvine.api_key)
        self.comicvine_cache_enabled.setChecked(bool(self.config.comicvine.cache_enabled))
        self.comicvine_cache_dir.setText(self.config.comicvine.cache_dir)
        self.matching_title_score_min.setValue(float(self.config.matching.title_score_min))
        self.matching_loaded_title_score_min.setValue(float(self.config.matching.loaded_title_score_min))
        self.matching_exact_title_score_min.setValue(float(self.config.matching.exact_title_score_min))
        self.matching_tome_pair_score_min.setValue(float(self.config.matching.tome_pair_score_min))
        self.matching_tome_min_books.setValue(int(self.config.matching.tome_match_min_books))
        self.matching_tome_min_ratio.setValue(float(self.config.matching.tome_match_min_ratio))
        self.matching_tome_min_avg_score.setValue(float(self.config.matching.tome_match_min_avg_score))
        self.matching_max_bedetheque_candidates.setValue(int(self.config.matching.max_bedetheque_candidates))
        ui = self.config.ui if isinstance(getattr(self.config, "ui", None), dict) else {}
        if hasattr(self, "show_chap_scan_series"):
            self.show_chap_scan_series.setChecked(bool(ui.get("show_chap_scan_series", False)))
        if hasattr(self, "diagnostic_requests_enabled"):
            self.diagnostic_requests_enabled.setChecked(bool(ui.get("diagnostic_requests", True)))
            self._set_diagnostics_enabled_cached(self.diagnostic_requests_enabled.isChecked())
        series_fields = ui.get("series_table_fields")
        if not isinstance(series_fields, list):
            series_fields = DEFAULT_SERIES_TABLE_FIELDS
        for field, checkbox in getattr(self, "series_table_field_checks", {}).items():
            checkbox.setChecked(field in series_fields)
        book_fields = ui.get("book_table_fields")
        if not isinstance(book_fields, list):
            book_fields = DEFAULT_BOOK_TABLE_FIELDS
        for field, checkbox in getattr(self, "book_table_field_checks", {}).items():
            checkbox.setChecked(field in book_fields)
        self.simulation_check.setChecked(bool(self.config.simulation))
        self.backup_root.setText(self.config.backup_root)

    def _ui_to_config(self) -> AppConfig:
        ui_config = getattr(self.config, "ui", {}) if isinstance(getattr(self.config, "ui", {}), dict) else {}
        ui_config = dict(ui_config)
        if hasattr(self, "show_chap_scan_series"):
            ui_config["show_chap_scan_series"] = self.show_chap_scan_series.isChecked()
        if hasattr(self, "diagnostic_requests_enabled"):
            ui_config["diagnostic_requests"] = self.diagnostic_requests_enabled.isChecked()
        if hasattr(self, "series_table_field_checks"):
            ui_config["series_table_fields"] = [
                field
                for field, _label in SERIES_TABLE_FIELD_OPTIONS
                if self.series_table_field_checks[field].isChecked()
            ]
        if hasattr(self, "book_table_field_checks"):
            ui_config["book_table_fields"] = [
                field
                for field, _label in BOOK_TABLE_FIELD_OPTIONS
                if self.book_table_field_checks[field].isChecked()
            ]

        data = {
            "komga": {
                "url": self.komga_url.text().strip(),
                "auth_mode": self.auth_mode.currentText(),
                "api_key": self.api_key.text().strip(),
                "username": self.username.text().strip(),
                "password": self.password.text(),
                "timeout_seconds": self.timeout_seconds.value(),
            },
            "komf": {
                "url": self.komf_url.text().strip(),
                "enabled": self.komf_enabled.isChecked(),
                "timeout_seconds": self.komf_timeout_seconds.value(),
            },
            "bedetheque": {
                "mode": "csv" if self.bdt_csv_only.isChecked() else "web",
                "csv_path": self.bdt_csv_path.text().strip(),
            },
            "mangabaka": {
                "url": self.mangabaka_base_url.text().strip() or DEFAULT_API_BASE_URL,
                "enabled": self.mangabaka_enabled.isChecked(),
                "timeout_seconds": self.mangabaka_timeout_seconds.value(),
                "cache_enabled": self.mangabaka_cache_enabled.isChecked(),
                "cache_dir": self.mangabaka_cache_dir.text().strip() or ".komga_db_tool_cache/mangabaka",
            },
            "manga_news": {
                "url": self.manga_news_base_url.text().strip() or DEFAULT_MANGA_NEWS_API_BASE_URL,
                "enabled": self.manga_news_enabled.isChecked(),
                "timeout_seconds": self.manga_news_timeout_seconds.value(),
                "token": self.manga_news_token.text().strip(),
                "cache_enabled": self.manga_news_cache_enabled.isChecked(),
                "cache_dir": self.manga_news_cache_dir.text().strip() or ".komga_db_tool_cache/manga_news",
            },
            "comicvine": {
                "url": self.comicvine_base_url.text().strip() or DEFAULT_COMICVINE_API_BASE_URL,
                "enabled": self.comicvine_enabled.isChecked(),
                "timeout_seconds": self.comicvine_timeout_seconds.value(),
                "api_key": self.comicvine_api_key.text().strip(),
                "cache_enabled": self.comicvine_cache_enabled.isChecked(),
                "cache_dir": self.comicvine_cache_dir.text().strip() or ".komga_db_tool_cache/comicvine",
            },
            "matching": {
                "title_score_min": self.matching_title_score_min.value(),
                "loaded_title_score_min": self.matching_loaded_title_score_min.value(),
                "exact_title_score_min": self.matching_exact_title_score_min.value(),
                "tome_pair_score_min": self.matching_tome_pair_score_min.value(),
                "tome_match_min_books": self.matching_tome_min_books.value(),
                "tome_match_min_ratio": self.matching_tome_min_ratio.value(),
                "tome_match_min_avg_score": self.matching_tome_min_avg_score.value(),
                "max_bedetheque_candidates": self.matching_max_bedetheque_candidates.value(),
            },
            "simulation": self.simulation_check.isChecked(),
            "backup_root": self.backup_root.text().strip() or "_komga_db_tool_backups",
            "ui": ui_config,
        }
        return AppConfig.from_dict(data)

    def save_config_action(self) -> None:
        self.config = self._ui_to_config()
        save_config(self.config, self.config_path)
        self._komga_connection_validated = False
        self._refresh_context_header()
        if os.path.abspath(self.config.backup_root) != os.path.abspath(self.backup.root):
            self.backup = BackupManager(self.config.backup_root)
        self.log(f"✅ Réglages sauvegardés : {self.config_path} ; secrets dans le coffre système")
        self._refresh_loaded_tables_for_display_fields()
        QTimer.singleShot(0, self.test_komga)

    def test_komga(self) -> None:
        reason = self._komga_credentials_missing_reason()
        if reason:
            self.log(f"⚠️ Test Komga impossible : {reason}.")
            self.tabs.setCurrentIndex(0)
            return

        def done(result: Any) -> None:
            self._komga_connection_validated = True
            self._refresh_context_header()
            self.log(f"✅ {result}")
            self.load_libraries()

        self.run_worker("Test Komga", lambda: self.komga_api().test(), done)

    def test_komf(self) -> None:
        self.run_worker("Test Komf", lambda: self.komf_api().test(), lambda r: self.log(f"✅ {r}"))

    def test_mangabaka(self) -> None:
        self.run_worker("Test MangaBaka", lambda: self.mangabaka_client().test(), lambda r: self.log(f"✅ {r}"))

    def test_manga_news(self) -> None:
        self.run_worker("Test Manga News", lambda: self.manga_news_client().test(), lambda r: self.log(f"✅ {r}"))

    def test_comicvine(self) -> None:
        if not self.comicvine_api_key.text().strip():
            QMessageBox.warning(self, "ComicVine", "Clé API ComicVine absente")
            return
        self.run_worker("Test ComicVine", lambda: self.comicvine_client().test(), lambda r: self.log(f"✅ {r}"))

    def load_libraries(self) -> None:
        def done(rows: List[Any]) -> None:
            self._komga_connection_validated = True
            self.libraries = rows
            self._populate_library_combos()
            self._refresh_context_header()
            self.log(f"✅ {len(rows)} bibliothèques chargées dans tous les onglets")
            if self.tabs.currentIndex() == getattr(self, "kora_tab_index", -1):
                QTimer.singleShot(
                    0,
                    lambda: self._on_top_level_tab_changed(self.kora_tab_index),
                )
        self.run_worker("Chargement bibliothèques", lambda: self.komga_api().libraries(), done)

    # ------------------------------------------------------------------
    # Explorer
    # ------------------------------------------------------------------
    def show_explorer_series_context_menu(self, point: Any) -> None:
        row = self.series_table.rowAt(point.y())
        if row < 0 or row >= len(self.series_rows):
            return
        clicked_item = self.series_table.item(row, max(0, self.series_table.columnAt(point.x())))
        if clicked_item is None or not clicked_item.isSelected():
            self.series_table.selectRow(row)
        series = self.series_rows[row]

        col = self.series_table.columnAt(point.x())
        item = self.series_table.item(row, col) if col >= 0 else None

        menu = QMenu(self)
        action_copy = menu.addAction("Copier") if item is not None else None
        action_edit = menu.addAction("Modifier…") if item is not None else None
        if item is not None:
            menu.addSeparator()
        action_bedetheque = menu.addAction("Bedetheque")
        action_mangabaka = menu.addAction("MangaBaka")
        action_manga_news = menu.addAction("Manga News")
        action_comicvine = menu.addAction("ComicVine")
        menu.addSeparator()
        action_summary_from_tome1 = menu.addAction("Remplir summary série depuis le tome 1")
        action_ignore = menu.addAction("Ignorer la série partout")
        selected_action = menu.exec(self.series_table.viewport().mapToGlobal(point))

        if item is not None and selected_action == action_copy:
            self._copy_table_cell(item)
        elif item is not None and selected_action == action_edit:
            self._edit_table_cell_dialog(self.series_table, item)
        elif selected_action == action_bedetheque:
            self.open_explorer_series_in_bedetheque(row)
        elif selected_action == action_mangabaka:
            self.open_explorer_series_in_mangabaka(row)
        elif selected_action == action_manga_news:
            self.open_explorer_series_in_manga_news(row)
        elif selected_action == action_comicvine:
            self.open_explorer_series_in_comicvine(row)
        elif selected_action == action_summary_from_tome1:
            self.fill_selected_series_summary_from_first_book(self.series_table)
        elif selected_action == action_ignore:
            self._ignore_selected_series_from_table(self.series_table)

    def _explorer_series_at_row(self, row: int) -> Optional[Any]:
        if row < 0 or row >= len(self.series_rows):
            return None
        return self.series_rows[row]

    def open_explorer_series_in_bedetheque(self, row: Optional[int] = None) -> None:
        if row is None:
            row = self._selected_row_index(self.series_table)
        series = self._explorer_series_at_row(row)
        if series is None:
            QMessageBox.warning(self, "Bedetheque", "Aucune série Komga sélectionnée dans l'explorateur")
            return

        self._set_current_tab_by_title("Bedetheque")
        self._set_library_combo("bedetheque", getattr(series, "library_id", ""))
        self.bdt_context_generation += 1
        generation = self.bdt_context_generation
        self.bdt_target_type.setCurrentText("series")
        self.bdt_target_id.setText(series.id)
        self.bdt_query.setText(clean_search_title(series.title))
        self.bdt_album_number.setText("")
        self._select_bedetheque_komga_series_row_by_id(series.id)
        self._clear_bedetheque_comparison_views(
            f"Série Komga préchargée depuis l'explorateur : {series.title}\n"
            "Recherche Bedetheque en cours…"
        )

        lib_id = getattr(series, "library_id", "") or self._library_id("bedetheque") or self._library_id("explorer")

        def done(rows: List[Any]) -> None:
            if generation != self.bdt_context_generation:
                return
            self.bdt_komga_book_rows = rows
            self._set_table(
                self.bdt_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(x) for x in rows],
                stretch_from=1,
            )
            self.log(f"✅ Bedetheque : série préchargée depuis l'explorateur — {series.title}")
            self.search_bedetheque()

        self.run_worker("Préchargement Bedetheque depuis l'explorateur", lambda: self.komga_api().books(lib_id, series.id), done)

    def open_explorer_series_in_mangabaka(self, row: Optional[int] = None) -> None:
        if row is None:
            row = self._selected_row_index(self.series_table)
        series = self._explorer_series_at_row(row)
        if series is None:
            QMessageBox.warning(self, "MangaBaka", "Aucune série Komga sélectionnée dans l'explorateur")
            return

        self._set_current_tab_by_title("MangaBaka")
        self._set_library_combo("mangabaka", getattr(series, "library_id", ""))
        self.mbk_context_generation += 1
        self.mbk_target_id.setText(series.id)
        self.mbk_query.setText(clean_search_title(series.title))
        self._select_mangabaka_komga_series_row_by_id(series.id)
        self._clear_mangabaka_views(
            f"Série Komga préchargée depuis l'explorateur : {series.title}\n"
            "Recherche MangaBaka en cours…"
        )
        self.search_mangabaka()

    def open_explorer_series_in_manga_news(self, row: Optional[int] = None) -> None:
        if row is None:
            row = self._selected_row_index(self.series_table)
        series = self._explorer_series_at_row(row)
        if series is None:
            QMessageBox.warning(self, "Manga News", "Aucune série Komga sélectionnée dans l'explorateur")
            return

        self._set_current_tab_by_title("Manga News")
        self._set_library_combo("manga_news", getattr(series, "library_id", ""))
        self.mn_context_generation += 1
        self.mn_target_id.setText(series.id)
        self.mn_query.setText(clean_search_title(series.title))
        self._select_manga_news_komga_series_row_by_id(series.id)
        slug, url = self._manga_news_link_for_series(series)
        if slug or url:
            self._clear_manga_news_views(
                f"Série Komga préchargée depuis l'explorateur : {series.title}\n"
                "Lien Manga News existant détecté. Chargement direct…"
            )
            self.fetch_manga_news_series_direct(slug=slug, url=url)
        else:
            self._clear_manga_news_views(
                f"Série Komga préchargée depuis l'explorateur : {series.title}\n"
                "Recherche Manga News en cours…"
            )
            self.search_manga_news()

    def open_explorer_series_in_comicvine(self, row: Optional[int] = None) -> None:
        if row is None:
            row = self._selected_row_index(self.series_table)
        series = self._explorer_series_at_row(row)
        if series is None:
            QMessageBox.warning(self, "ComicVine", "Aucune série Komga sélectionnée dans l'explorateur")
            return

        self._set_current_tab_by_title("ComicVine")
        self._set_library_combo("comicvine", getattr(series, "library_id", ""))
        self.cv_context_generation += 1
        self.cv_target_id.setText(series.id)
        self.cv_query.setText(clean_search_title(series.title))
        self._select_comicvine_komga_series_row_by_id(series.id)
        volume_id, url = self._comicvine_link_for_series(series)
        if volume_id:
            self._clear_comicvine_views(
                f"Série Komga préchargée depuis l'explorateur : {series.title}\n"
                "Lien ComicVine existant détecté. Chargement direct…"
            )
            self.fetch_comicvine_series_direct(volume_id=volume_id, url=url)
        else:
            self._clear_comicvine_views(
                f"Série Komga préchargée depuis l'explorateur : {series.title}\n"
                "Recherche ComicVine en cours…"
            )
            self.search_comicvine()

    def _manga_news_link_for_series(self, series: Any) -> tuple[str, str]:
        """Return (slug, url) from an existing Komga Manga-News link when available."""
        metadata = getattr(series, "metadata", {}) if isinstance(getattr(series, "metadata", {}), dict) else {}
        fallback_url = ""
        for entry in metadata_link_entries(metadata.get("links")):
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            label_norm = normalized_link_label(entry.get("label"))
            url_label = normalized_link_label(_link_label_from_url(url))
            slug = extract_manga_news_series_slug_from_url(url)
            if slug:
                return slug, url
            if (label_norm == "manga_news" or url_label == "manga_news") and not fallback_url:
                fallback_url = url
        return "", fallback_url

    def _mangabaka_link_for_series(self, series: Any) -> tuple[str, str]:
        """Return (series_id, url) from an existing Komga MangaBaka link when available."""
        metadata = getattr(series, "metadata", {}) if isinstance(getattr(series, "metadata", {}), dict) else {}
        fallback_url = ""
        for entry in metadata_link_entries(metadata.get("links")):
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            label_norm = normalized_link_label(entry.get("label"))
            url_label = normalized_link_label(_link_label_from_url(url))
            series_id = extract_mangabaka_series_id_from_url(url)
            if series_id:
                return series_id, url
            if (label_norm == "mangabaka" or url_label == "mangabaka") and not fallback_url:
                fallback_url = url
        return "", fallback_url

    def _comicvine_link_for_series(self, series: Any) -> tuple[str, str]:
        """Return (volume_id, url) from an existing Komga ComicVine link when available."""
        metadata = getattr(series, "metadata", {}) if isinstance(getattr(series, "metadata", {}), dict) else {}
        for entry in metadata_link_entries(metadata.get("links")):
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            label_norm = normalized_link_label(entry.get("label"))
            url_label = normalized_link_label(_link_label_from_url(url))
            volume_id = extract_comicvine_volume_id_from_url(url)
            if volume_id or label_norm == "comicvine" or url_label == "comicvine":
                return volume_id, url
        return "", ""

    def _selected_explorer_series_rows(self) -> List[Any]:
        rows = self._selected_row_indexes(self.series_table)
        return [self.series_rows[row] for row in rows if 0 <= row < len(self.series_rows)]

    def _preferred_link_update_provider(self) -> str:
        value = ""
        combo = getattr(self, "filter_series_link_label", None)
        if combo is not None:
            value = str(combo.currentData() or "")
        norm = normalized_link_label(value)
        return norm if norm in SUPPORTED_LINK_UPDATE_PROVIDERS else ""

    def _pick_supported_update_link(self, series: Any, preferred_provider: str = "") -> tuple[str, Dict[str, str], str]:
        metadata = getattr(series, "metadata", {}) or {}
        entries = metadata_link_entries(metadata.get("links"))
        by_provider: Dict[str, List[Dict[str, str]]] = {"bedetheque": [], "mangabaka": [], "comicvine": []}
        for entry in entries:
            label_norm = normalized_link_label(entry.get("label"))
            url = entry.get("url", "")
            url_label = normalized_link_label(_link_label_from_url(url))
            if label_norm == "bedetheque" or url_label == "bedetheque" or is_bedetheque_series_url(url):
                by_provider["bedetheque"].append(entry)
            elif label_norm == "mangabaka" or url_label == "mangabaka" or extract_mangabaka_series_id_from_url(url):
                by_provider["mangabaka"].append(entry)
            elif label_norm == "comicvine" or url_label == "comicvine" or extract_comicvine_volume_id_from_url(url):
                by_provider["comicvine"].append(entry)

        providers = [preferred_provider] if preferred_provider else LINK_UPDATE_PROVIDER_PRIORITY
        for provider in providers:
            if provider == "bedetheque":
                for entry in by_provider["bedetheque"]:
                    if is_bedetheque_series_url(entry.get("url", "")):
                        return provider, entry, ""
                if by_provider["bedetheque"]:
                    return "", by_provider["bedetheque"][0], "lien Bedetheque non reconnu comme URL série"
            elif provider == "mangabaka":
                for entry in by_provider["mangabaka"]:
                    if extract_mangabaka_series_id_from_url(entry.get("url", "")):
                        return provider, entry, ""
                if by_provider["mangabaka"]:
                    return "", by_provider["mangabaka"][0], "lien MangaBaka sans ID série exploitable"
            elif provider == "comicvine":
                for entry in by_provider["comicvine"]:
                    if extract_comicvine_volume_id_from_url(entry.get("url", "")):
                        return provider, entry, ""
                if by_provider["comicvine"]:
                    return "", by_provider["comicvine"][0], "lien ComicVine sans ID volume exploitable"
        if preferred_provider:
            return "", {}, f"aucun lien {preferred_provider} exploitable"
        return "", {}, "aucun lien Bedetheque, MangaBaka ou ComicVine exploitable"

    def _update_with_link_provider_choice(self, selected_count: int, simulation: bool) -> Optional[tuple[str, str]]:
        """Ask explicitly which provider strategy should be used for Update with link."""
        filter_provider = self._preferred_link_update_provider()
        dialog = QDialog(self)
        dialog.setWindowTitle("Update with link — source")
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            f"Mettre à jour {selected_count} série(s) sélectionnée(s) depuis leurs liens existants.\n"
            "Aucune recherche / auto-match ne sera lancée : seuls les links déjà stockés sont utilisés.\n"
            f"Mode actuel : {'SIMULATION — aucune écriture' if simulation else 'ÉCRITURE RÉELLE — backup avant PATCH'}."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        combo = QComboBox()
        combo.addItem("Auto prudent : Bedetheque puis MangaBaka en fallback", "auto")
        combo.addItem(
            f"Source du filtre Links actuel ({filter_provider or 'aucune source supportée'})",
            "filter",
        )
        combo.addItem("Bedetheque uniquement", "bedetheque")
        combo.addItem("MangaBaka uniquement", "mangabaka")
        combo.addItem("ComicVine uniquement", "comicvine")
        form.addRow("Source", combo)
        layout.addLayout(form)

        note = QLabel(
            "Règles : séries sélectionnées uniquement, payload normalisé, summaries prudents, "
            "audit/backup avant écriture réelle."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return None
        mode = str(combo.currentData() or "auto")
        if mode == "filter":
            if not filter_provider:
                QMessageBox.warning(
                    self,
                    "Update with link",
                    "Le filtre Links actuel ne correspond pas à une source supportée. Choisis Bedetheque, MangaBaka, ComicVine ou Auto prudent.",
                )
                return None
            return filter_provider, f"filtre Links actuel : {filter_provider}"
        if mode in SUPPORTED_LINK_UPDATE_PROVIDERS:
            return mode, f"{mode} uniquement"
        return "", "auto prudent : Bedetheque puis MangaBaka"

    def _status_counts_line(self, rows: List[Dict[str, Any]], status_key: str = "status") -> str:
        counts: Dict[str, int] = {}
        for row in rows or []:
            status = str(row.get(status_key) or row.get("operation_status") or row.get("status") or "Sans statut")
            counts[status] = counts.get(status, 0) + 1
        if not counts:
            return "aucun item"
        return ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))

    def _new_series_refresh_report_fields(self) -> Dict[str, str]:
        """Fields always present in series refresh/batch reports.

        These make the domain status/totalBookCount visible even when there is
        no PATCH payload. Do not confuse operation_status with Komga's series
        metadata field named status.
        """
        return {
            "operation_status": "",
            "current_series_status": "",
            "source_series_status": "",
            "proposed_series_status": "",
            "series_status_action": "",
            "current_totalBookCount": "",
            "source_totalBookCount": "",
            "proposed_totalBookCount": "",
            "totalBookCount_action": "",
            "source_fields": "",
            "source_notes": "",
        }

    def _fill_series_refresh_report_fields(
        self,
        report: Dict[str, Any],
        current: Dict[str, Any],
        source_metadata: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> None:
        status_report = quality_metadata_field_update_report("status", current, source_metadata, payload)
        total_report = quality_metadata_field_update_report("totalBookCount", current, source_metadata, payload)
        report["current_series_status"] = status_report["current"]
        report["source_series_status"] = status_report["source"]
        report["proposed_series_status"] = status_report["proposed"]
        report["series_status_action"] = status_report["action"]
        report["current_totalBookCount"] = total_report["current"]
        report["source_totalBookCount"] = total_report["source"]
        report["proposed_totalBookCount"] = total_report["proposed"]
        report["totalBookCount_action"] = total_report["action"]

    def _series_refresh_detail_suffix(self, row: Dict[str, Any]) -> str:
        status_part = (
            f" | status série: {row.get('current_series_status') or '<vide>'}"
            f" → {row.get('source_series_status') or '<indisponible>'}"
            f" ({row.get('series_status_action') or 'non évalué'})"
        )
        total_part = (
            f" | totalBookCount: {row.get('current_totalBookCount') or '<vide>'}"
            f" → {row.get('source_totalBookCount') or '<indisponible>'}"
            f" ({row.get('totalBookCount_action') or 'non évalué'})"
        )
        return status_part + total_part

    def update_selected_series_with_existing_links(self) -> None:
        selected_series = self._selected_explorer_series_rows()
        if not selected_series:
            QMessageBox.warning(
                self,
                "Update with link",
                "Sélectionne une ou plusieurs séries dans l'explorateur. Le batch ne traite jamais toute la bibliothèque sans sélection.",
            )
            return
        simulation = self.simulation_enabled()
        provider_choice = self._update_with_link_provider_choice(len(selected_series), simulation)
        if provider_choice is None:
            return
        preferred_provider, provider_text = provider_choice
        total = len(selected_series)
        self._set_auto_match_progress(f"Update with link — {provider_text} — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def done(result: Dict[str, Any]) -> None:
            self._show_update_with_link_report(result)
            summary = self._status_counts_line(result.get("rows") or [], status_key="operation_status")
            self._set_auto_match_progress(f"Update with link terminé — {summary}", total, total)

        self.run_worker(
            "Update with link",
            lambda: self._run_update_with_link(selected_series, simulation, preferred_provider, progress, provider_text=provider_text),
            done,
        )

    def _enrich_update_with_link_series_metadata(self, provider: str, candidate: Any, metadata: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
        """Add provider-specific critical fields before Update with link builds its payload."""
        out = dict(metadata or {})
        notes: List[str] = []
        if provider == "bedetheque":
            albums = getattr(candidate, "raw", {}).get("albums", []) if candidate is not None else []
            album_count = bedetheque_main_album_count(albums)
            if album_count and not out.get("totalBookCount"):
                out["totalBookCount"] = album_count
                notes.append(f"totalBookCount calculé depuis les albums Bedetheque: {album_count}")
            elif album_count:
                notes.append(f"totalBookCount source Bedetheque: {out.get('totalBookCount')}")
            else:
                notes.append("totalBookCount non fourni/calculable par Bedetheque")
            if not out.get("status"):
                notes.append("status non fourni par Bedetheque")
        elif provider == "mangabaka":
            if not out.get("totalBookCount"):
                notes.append("totalBookCount non fourni par MangaBaka")
            if not out.get("status"):
                notes.append("status non fourni par MangaBaka")
        elif provider == "comicvine":
            if not out.get("totalBookCount"):
                notes.append("totalBookCount non fourni par ComicVine")
            if not out.get("status"):
                notes.append("status non fourni par ComicVine")
        return out, notes


    def _run_update_with_link(
        self,
        selected_series: List[Any],
        simulation: bool,
        preferred_provider: str = "",
        progress: Optional[Callable[[str, int, int], None]] = None,
        provider_text: str = "",
    ) -> Dict[str, Any]:
        api = self.komga_api()
        bdt_client = self.bedetheque_client()
        mbk_client = self.mangabaka_client()
        cv_client = self.comicvine_client()
        rows: List[Dict[str, Any]] = []
        total = len(selected_series)

        for index, series in enumerate(selected_series, start=1):
            title = getattr(series, "title", "")
            self._emit_auto_match_progress(progress, "Update with link", index - 1, total, f"{index}/{total} — {title}")
            report: Dict[str, Any] = {
                "index": index,
                "series_id": getattr(series, "id", ""),
                "komga_title": title,
                "provider": "",
                "link_label": "",
                "link_url": "",
                "loaded_title": "",
                "payload_fields": "",
                "payload_json": "",
                "operation_status": "",
                "current_series_status": "",
                "source_series_status": "",
                "proposed_series_status": "",
                "series_status_action": "",
                "current_totalBookCount": "",
                "source_totalBookCount": "",
                "proposed_totalBookCount": "",
                "totalBookCount_action": "",
                "error": "",
            }
            try:
                provider, link_entry, reason = self._pick_supported_update_link(series, preferred_provider)
                if not provider:
                    report["operation_status"] = "Ignoré : lien non supporté"
                    report["error"] = reason
                    if link_entry:
                        report["link_label"] = link_entry.get("label", "")
                        report["link_url"] = link_entry.get("url", "")
                else:
                    url = link_entry.get("url", "")
                    report["provider"] = provider
                    report["link_label"] = link_entry.get("label", "")
                    report["link_url"] = url
                    if provider == "bedetheque":
                        candidate = bdt_client.scrape_series(url)
                        loaded_title = candidate.series_title
                        candidate_metadata = candidate.series_metadata
                        backup_payload = {"current": {}, "bedetheque": BedethequeClient.candidate_to_dict(candidate), "payload": {}}
                    elif provider == "mangabaka":
                        series_id = extract_mangabaka_series_id_from_url(url)
                        if not series_id:
                            raise ValueError("ID série MangaBaka introuvable dans le lien")
                        candidate = mbk_client.get_series(series_id)
                        loaded_title = candidate.title
                        candidate_metadata = candidate.series_metadata
                        backup_payload = {"current": {}, "mangabaka": MangaBakaClient.candidate_to_dict(candidate), "payload": {}}
                    elif provider == "comicvine":
                        volume_id = extract_comicvine_volume_id_from_url(url)
                        if not volume_id:
                            raise ValueError("ID volume ComicVine introuvable dans le lien")
                        candidate = cv_client.get_volume(volume_id)
                        loaded_title = candidate.title
                        candidate_metadata = candidate.series_metadata
                        backup_payload = {"current": {}, "comicvine": ComicVineClient.candidate_to_dict(candidate), "payload": {}}
                    else:
                        raise ValueError(f"Provider non supporté : {provider}")

                    report["loaded_title"] = loaded_title or ""
                    candidate_metadata, source_notes = self._enrich_update_with_link_series_metadata(provider, candidate, candidate_metadata)
                    report["source_fields"] = "; ".join(sorted(candidate_metadata.keys()))
                    report["source_notes"] = " | ".join(source_notes)
                    current = self._fetch_current_metadata("series", series.id)
                    payload = self._payload_from_metadata_maps(current, candidate_metadata, SERIES_METADATA_FIELDS, target_type="series")
                    report["payload_fields"] = "; ".join(payload.keys())
                    report["payload_json"] = json_text(payload, indent=0) if payload else ""
                    status_report = quality_metadata_field_update_report("status", current, candidate_metadata, payload)
                    total_report = quality_metadata_field_update_report("totalBookCount", current, candidate_metadata, payload)
                    report["current_series_status"] = status_report["current"]
                    report["source_series_status"] = status_report["source"]
                    report["proposed_series_status"] = status_report["proposed"]
                    report["series_status_action"] = status_report["action"]
                    report["current_totalBookCount"] = total_report["current"]
                    report["source_totalBookCount"] = total_report["source"]
                    report["proposed_totalBookCount"] = total_report["proposed"]
                    report["totalBookCount_action"] = total_report["action"]

                    if not payload:
                        report["operation_status"] = "OK : aucun changement"
                    elif simulation:
                        report["operation_status"] = "OK simulation"
                    else:
                        backup_payload["current"] = current
                        backup_payload["payload"] = payload
                        self.backup.save_json(
                            "operation",
                            "series",
                            series.id,
                            backup_payload,
                            f"avant PATCH update with link {provider}",
                        )
                        self._write_metadata_update(api, "series", series.id, payload, current, source=f"update_with_link:{provider}", note="Update with link")
                        report["operation_status"] = "OK appliqué"
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                report["operation_status"] = "Erreur"
                report["error"] = str(exc)
            rows.append(report)
            self._emit_auto_match_progress(
                progress,
                "Update with link",
                index,
                total,
                f"{index}/{total} — {title} — {report.get('operation_status') or 'traité'}",
            )

        csv_name = f"update_with_link_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = self.backup.export_csv(csv_name, rows)
        return {
            "rows": rows,
            "csv_path": csv_path,
            "simulation": simulation,
            "preferred_provider": preferred_provider,
            "provider_text": provider_text or (preferred_provider or "auto prudent : Bedetheque puis MangaBaka"),
        }

    def _show_update_with_link_report(self, result: Dict[str, Any]) -> None:
        rows = result.get("rows") or []
        csv_path = result.get("csv_path", "")
        counts: Dict[str, int] = {}
        for row in rows:
            operation_status = str(row.get("operation_status") or row.get("status") or "Sans statut")
            counts[operation_status] = counts.get(operation_status, 0) + 1

        lines = [
            "Compte rendu Update with link",
            f"Source : {result.get('provider_text') or result.get('preferred_provider') or 'auto prudent : Bedetheque puis MangaBaka'}",
            f"Simulation : {result.get('simulation')}",
            f"CSV : {csv_path}",
            "",
            "Synthèse :",
        ]
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
        lines.append("Détail :")
        for row in rows:
            detail = f"{row.get('index')}. {row.get('komga_title')} → {row.get('operation_status') or row.get('status')}"
            if row.get("provider"):
                detail += f" | provider: {row.get('provider')}"
            if row.get("link_label") or row.get("link_url"):
                detail += f" | link: {row.get('link_label')} {row.get('link_url')}"
            if row.get("loaded_title"):
                detail += f" | chargé: {row.get('loaded_title')}"
            detail += (
                f" | status série: {row.get('current_series_status') or '<vide>'}"
                f" → {row.get('source_series_status') or '<indisponible>'}"
                f" ({row.get('series_status_action') or 'non évalué'})"
            )
            detail += (
                f" | totalBookCount: {row.get('current_totalBookCount') or '<vide>'}"
                f" → {row.get('source_totalBookCount') or '<indisponible>'}"
                f" ({row.get('totalBookCount_action') or 'non évalué'})"
            )
            if row.get("payload_fields"):
                detail += f" | champs: {row.get('payload_fields')}"
            if row.get("source_notes"):
                detail += f" | source: {row.get('source_notes')}"
            if row.get("error"):
                detail += f" | erreur: {row.get('error')}"
            lines.append(detail)

        text = "\n".join(lines)
        self._show_structured_report_dialog(
            "Compte rendu Update with link",
            text,
            rows,
            csv_path,
            columns=[
                ("index", "#"),
                ("komga_title", "Série Komga"),
                ("operation_status", "Statut opération"),
                ("provider", "Source"),
                ("current_series_status", "Status Komga"),
                ("source_series_status", "Status source"),
                ("proposed_series_status", "Status proposé"),
                ("series_status_action", "Action status"),
                ("current_totalBookCount", "Tomes Komga"),
                ("source_totalBookCount", "Tomes source"),
                ("proposed_totalBookCount", "Tomes proposés"),
                ("totalBookCount_action", "Action tomes"),
                ("payload_fields", "Champs"),
                ("source_notes", "Notes source"),
                ("error", "Erreur"),
            ],
            secondary_filter_keys=["provider", "series_status_action", "totalBookCount_action"],
            status_filter_key="operation_status",
        )

    def _bedetheque_auto_match_status(self, query: str, results: List[BedethequeSearchResult]) -> tuple[Optional[BedethequeSearchResult], str, float, int]:
        series_results = [r for r in results if r.kind == "serie"]
        if not series_results:
            return None, "Échec : aucun résultat série", 0.0, 0

        clean_query = clean_search_title(query)

        if len(series_results) == 1:
            result = series_results[0]
            score = title_similarity(clean_query, clean_search_title(result.title))
            if score < self._matching_title_score_min():
                return None, "Échec : score titre insuffisant", score, len(series_results)
            return result, "Candidat unique", score, len(series_results)

        # Plusieurs résultats Bedetheque ne signifient pas toujours ambiguïté.
        # Cas fréquent : la recherche renvoie un titre exact plus des titres
        # contenant le même mot-clé (ex. "Fawcett" + "Bulletman (Fawcett - 1941)").
        # On accepte uniquement s'il existe un seul titre strictement équivalent
        # après normalisation. Sinon on laisse le départage par tomes décider.
        exact_matches: List[tuple[BedethequeSearchResult, float]] = []
        best_score = 0.0
        for result in series_results:
            score = title_similarity(clean_query, clean_search_title(result.title))
            best_score = max(best_score, score)
            if score >= self._matching_exact_title_score_min():
                exact_matches.append((result, score))

        if len(exact_matches) == 1:
            result, score = exact_matches[0]
            return result, "Candidat titre exact unique parmi plusieurs résultats", score, len(series_results)
        if len(exact_matches) > 1:
            return None, "Échec : plusieurs titres exacts", 1.0, len(series_results)

        return None, "Échec : plusieurs résultats série", best_score, len(series_results)

    def _score_bedetheque_tome_candidate(self, komga_books: List[Any], bedetheque_albums: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Score one Bedetheque series candidate against Komga book titles.

        This is deliberately stricter than the manual album matcher: exact volume
        numbers alone are not enough. A book counts as matched only when the
        album title is also very close.
        """
        books: List[Dict[str, str]] = []
        for book in komga_books or []:
            meta = getattr(book, "metadata", {}) or {}
            title = clean_search_title(meta.get("title") or getattr(book, "title", ""))
            number = normalize_volume_number(meta.get("number") or meta.get("numberSort") or getattr(book, "number", ""))
            if title:
                books.append({"title": title, "number": number})

        albums: List[Dict[str, str]] = []
        for album in bedetheque_albums or []:
            title = clean_search_title(album.get("title", ""))
            number = normalize_volume_number(album.get("number", ""))
            if title:
                albums.append({"title": title, "number": number})

        total_books = len(books)
        if total_books < self._matching_tome_min_books():
            return {
                "valid": False,
                "matched_count": 0,
                "total_books": total_books,
                "ratio": 0.0,
                "avg_score": 0.0,
                "detail": f"tomes Komga insuffisants ({total_books})",
            }
        if not albums:
            return {
                "valid": False,
                "matched_count": 0,
                "total_books": total_books,
                "ratio": 0.0,
                "avg_score": 0.0,
                "detail": "aucun album Bedetheque exploitable",
            }

        used_album_indexes: set[int] = set()
        matched_scores: List[float] = []
        examples: List[str] = []

        for book in books:
            best_index = -1
            best_score = 0.0
            best_album: Dict[str, str] = {}
            for album_index, album in enumerate(albums):
                if album_index in used_album_indexes:
                    continue
                title_score = title_similarity(book["title"], album["title"])
                number_match = bool(book.get("number") and album.get("number") and book["number"] == album["number"])
                pair_score = 0.0
                if title_score >= 0.88:
                    pair_score = title_score
                if number_match and title_score >= 0.75:
                    pair_score = max(pair_score, min(1.0, title_score + 0.05))
                if pair_score > best_score:
                    best_score = pair_score
                    best_index = album_index
                    best_album = album
            if best_index >= 0 and best_score >= self._matching_tome_pair_score_min():
                used_album_indexes.add(best_index)
                matched_scores.append(best_score)
                if len(examples) < 3:
                    examples.append(f"{book['title']} ≈ {best_album.get('title', '')} ({best_score:.3f})")

        matched_count = len(matched_scores)
        ratio = matched_count / total_books if total_books else 0.0
        avg_score = sum(matched_scores) / matched_count if matched_scores else 0.0
        valid = (
            matched_count >= self._matching_tome_min_books()
            and ratio >= self._matching_tome_min_ratio()
            and avg_score >= self._matching_tome_min_avg_score()
        )
        detail = f"{matched_count}/{total_books} tomes, ratio {ratio:.2f}, score moyen {avg_score:.3f}"
        if examples:
            detail += " — " + " ; ".join(examples)
        return {
            "valid": valid,
            "matched_count": matched_count,
            "total_books": total_books,
            "ratio": ratio,
            "avg_score": avg_score,
            "detail": detail,
        }

    def _bedetheque_auto_match_by_tomes(
        self,
        query: str,
        series: Any,
        series_results: List[BedethequeSearchResult],
        client: BedethequeClient,
        api: KomgaApi,
    ) -> tuple[Optional[BedethequeSearchResult], Optional[BedethequeCandidate], Dict[str, Any]]:
        info: Dict[str, Any] = {
            "status": "Échec : tomes insuffisants",
            "strategy": "tomes",
            "match_score": 0.0,
            "matched_count": 0,
            "total_books": 0,
            "ratio": 0.0,
            "avg_score": 0.0,
            "detail": "",
            "scraped_title": "",
            "scraped_score": 0.0,
            "scraped_candidates": 0,
        }
        if not series_results:
            info["status"] = "Échec : aucun résultat série"
            return None, None, info

        komga_books = api.books(getattr(series, "library_id", "") or None, getattr(series, "id", "") or None)
        if len(komga_books) < self._matching_tome_min_books():
            info["total_books"] = len(komga_books)
            info["detail"] = f"tomes Komga insuffisants ({len(komga_books)})"
            return None, None, info

        ranked: List[Dict[str, Any]] = []
        for result in series_results[:self._matching_max_bedetheque_candidates()]:
            try:
                candidate = client.scrape_series(result.url)
                albums = candidate.raw.get("albums", []) if isinstance(candidate.raw, dict) else []
                tome_score = self._score_bedetheque_tome_candidate(komga_books, albums)
                search_score = title_similarity(clean_search_title(query), clean_search_title(result.title))
                scraped_score = title_similarity(clean_search_title(query), clean_search_title(candidate.series_title or result.title))
                ranked.append({
                    "result": result,
                    "candidate": candidate,
                    "search_score": search_score,
                    "scraped_score": scraped_score,
                    **tome_score,
                })
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                ranked.append({
                    "result": result,
                    "candidate": None,
                    "search_score": title_similarity(clean_search_title(query), clean_search_title(result.title)),
                    "scraped_score": 0.0,
                    "valid": False,
                    "matched_count": 0,
                    "total_books": len(komga_books),
                    "ratio": 0.0,
                    "avg_score": 0.0,
                    "detail": f"erreur scrape candidat: {exc}",
                })

        info["scraped_candidates"] = len(ranked)
        if not ranked:
            info["status"] = "Échec : aucun candidat scrapé"
            return None, None, info

        ranked.sort(
            key=lambda row: (
                bool(row.get("valid")),
                int(row.get("matched_count") or 0),
                float(row.get("ratio") or 0.0),
                float(row.get("avg_score") or 0.0),
                float(row.get("search_score") or 0.0),
            ),
            reverse=True,
        )
        valid = [row for row in ranked if row.get("valid") and row.get("candidate") is not None]
        top = ranked[0]
        info.update({
            "match_score": float(top.get("search_score") or 0.0),
            "matched_count": int(top.get("matched_count") or 0),
            "total_books": int(top.get("total_books") or 0),
            "ratio": float(top.get("ratio") or 0.0),
            "avg_score": float(top.get("avg_score") or 0.0),
            "detail": str(top.get("detail") or ""),
            "scraped_title": getattr(top.get("candidate"), "series_title", "") if top.get("candidate") else "",
            "scraped_score": float(top.get("scraped_score") or 0.0),
        })
        if not valid:
            info["status"] = "Échec : aucun candidat dominant par tomes"
            return None, None, info

        top_valid = valid[0]
        if len(valid) > 1:
            second = valid[1]
            top_count = int(top_valid.get("matched_count") or 0)
            second_count = int(second.get("matched_count") or 0)
            top_avg = float(top_valid.get("avg_score") or 0.0)
            second_avg = float(second.get("avg_score") or 0.0)
            clearly_ahead = top_count >= second_count + 2 or (top_count > second_count and top_avg >= second_avg + 0.05)
            if not clearly_ahead:
                info.update({
                    "status": "Échec : plusieurs candidats plausibles par tomes",
                    "match_score": float(top_valid.get("search_score") or 0.0),
                    "matched_count": top_count,
                    "total_books": int(top_valid.get("total_books") or 0),
                    "ratio": float(top_valid.get("ratio") or 0.0),
                    "avg_score": top_avg,
                    "detail": f"top: {top_valid.get('detail', '')} | second: {second.get('result').title if second.get('result') else ''} — {second.get('detail', '')}",
                    "scraped_title": getattr(top_valid.get("candidate"), "series_title", "") if top_valid.get("candidate") else "",
                    "scraped_score": float(top_valid.get("scraped_score") or 0.0),
                })
                return None, None, info

        result = top_valid["result"]
        candidate = top_valid["candidate"]
        info.update({
            "status": "Candidat par tomes",
            "match_score": float(top_valid.get("search_score") or 0.0),
            "matched_count": int(top_valid.get("matched_count") or 0),
            "total_books": int(top_valid.get("total_books") or 0),
            "ratio": float(top_valid.get("ratio") or 0.0),
            "avg_score": float(top_valid.get("avg_score") or 0.0),
            "detail": str(top_valid.get("detail") or ""),
            "scraped_title": getattr(candidate, "series_title", "") if candidate else "",
            "scraped_score": float(top_valid.get("scraped_score") or 0.0),
        })
        return result, candidate, info

    def auto_match_bedetheque_prudent_from_explorer(self) -> None:
        selected_series = self._selected_explorer_series_rows()
        if not selected_series:
            QMessageBox.warning(
                self,
                "Auto-match Bedetheque",
                "Sélectionne une ou plusieurs séries dans l'explorateur. Le batch ne traite jamais toute la bibliothèque sans sélection.",
            )
            return
        simulation = self.simulation_enabled()
        answer = QMessageBox.question(
            self,
            "Auto-match Bedetheque prudent",
            f"Traiter {len(selected_series)} série(s) sélectionnée(s) avec Bedetheque ?\n\n"
            f"Règles : {self._matching_rules_summary()}.\n"
            f"Mode actuel : {'SIMULATION — aucune écriture' if simulation else 'ÉCRITURE RÉELLE — backup avant PATCH'}.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        total = len(selected_series)
        self._set_auto_match_progress("Auto-match Bedetheque prudent — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def done(result: Dict[str, Any]) -> None:
            self._show_auto_match_bedetheque_report(result)
            self._set_auto_match_progress("Auto-match Bedetheque prudent — terminé", total, total)

        self.run_worker(
            "Auto-match Bedetheque prudent",
            lambda: self._run_auto_match_bedetheque_prudent(selected_series, simulation, progress),
            done,
        )

    def _run_auto_match_bedetheque_prudent(
        self,
        selected_series: List[Any],
        simulation: bool,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        client = self.bedetheque_client()
        api = self.komga_api()
        rows: List[Dict[str, Any]] = []
        total = len(selected_series)

        for index, series in enumerate(selected_series, start=1):
            title = getattr(series, "title", "")
            query = clean_search_title(title)
            self.enrichment_history.record_search("bedetheque", getattr(series, "id", ""), title)
            self._emit_auto_match_progress(progress, "Auto-match Bedetheque prudent", index - 1, total, f"{index}/{total} — {title}")
            report: Dict[str, Any] = {
                "index": index,
                "series_id": getattr(series, "id", ""),
                "komga_title": title,
                "query": query,
                "search_query_used": "",
                "search_attempts": "",
                **self._new_series_refresh_report_fields(),
                "result_count": 0,
                "matched_title": "",
                "matched_url": "",
                "match_score": "",
                "scraped_title": "",
                "scraped_score": "",
                "match_strategy": "",
                "tome_match_count": "",
                "tome_match_ratio": "",
                "tome_match_avg": "",
                "tome_match_detail": "",
                "payload_fields": "",
                "payload_json": "",
                "error": "",
            }
            try:
                if not query:
                    report["operation_status"] = "Échec : requête vide"
                else:
                    search_result = self._search_bedetheque_with_fallback(query, client)
                    results = search_result.get("rows") or []
                    report["search_query_used"] = str(search_result.get("used_query") or query)
                    report["search_attempts"] = self._format_search_attempts(search_result.get("attempts") or [])
                    series_results = [r for r in results if r.kind == "serie"]
                    result, status, score, result_count = self._bedetheque_auto_match_status(query, results)
                    candidate: Optional[BedethequeCandidate] = None
                    report["result_count"] = result_count
                    report["match_score"] = f"{score:.3f}" if score else ""

                    if result is not None:
                        report["match_strategy"] = "titre exact unique" if result_count > 1 else "titre unique"
                        candidate = client.scrape_series(result.url)
                        report["scraped_title"] = candidate.series_title
                        scraped_score = title_similarity(query, clean_search_title(candidate.series_title or result.title))
                        report["scraped_score"] = f"{scraped_score:.3f}"
                        # Le résultat de recherche a déjà passé la règle stricte :
                        # exactement 1 résultat série + score titre >= 0.90.
                        # Certains titres scrapés Bedetheque sont bruités ou moins propres ;
                        # on ne rejette pas un match unique fiable uniquement pour ça.
                        if max(score, scraped_score) < self._matching_loaded_title_score_min():
                            result = None
                            candidate = None
                            report["operation_status"] = "Échec : score titre insuffisant après scrape"
                    else:
                        tome_result, tome_candidate, tome_info = self._bedetheque_auto_match_by_tomes(query, series, series_results, client, api)
                        report["match_strategy"] = tome_info.get("strategy", "tomes")
                        report["match_score"] = f"{float(tome_info.get('match_score') or 0.0):.3f}" if tome_info.get("match_score") else report["match_score"]
                        report["scraped_title"] = tome_info.get("scraped_title", "")
                        report["scraped_score"] = f"{float(tome_info.get('scraped_score') or 0.0):.3f}" if tome_info.get("scraped_score") else ""
                        report["tome_match_count"] = str(tome_info.get("matched_count", ""))
                        report["tome_match_ratio"] = f"{float(tome_info.get('ratio') or 0.0):.2f}"
                        report["tome_match_avg"] = f"{float(tome_info.get('avg_score') or 0.0):.3f}"
                        report["tome_match_detail"] = tome_info.get("detail", "")
                        if tome_result is None or tome_candidate is None:
                            report["operation_status"] = tome_info.get("status") or status
                        else:
                            result = tome_result
                            candidate = tome_candidate

                    if result is not None and candidate is not None:
                        report["matched_title"] = result.title
                        report["matched_url"] = result.url
                        current = self._fetch_current_metadata("series", series.id)
                        source_metadata, source_notes = self._enrich_update_with_link_series_metadata("bedetheque", candidate, candidate.series_metadata)
                        report["source_fields"] = "; ".join(sorted(source_metadata.keys()))
                        report["source_notes"] = " | ".join(source_notes)
                        payload = self._payload_from_metadata_maps(current, source_metadata, SERIES_METADATA_FIELDS, target_type="series")
                        report["payload_fields"] = "; ".join(payload.keys())
                        report["payload_json"] = json_text(payload, indent=0) if payload else ""
                        self._fill_series_refresh_report_fields(report, current, source_metadata, payload)

                        if not payload:
                            report["operation_status"] = "OK : aucun changement"
                        elif simulation:
                            report["operation_status"] = "OK simulation"
                        else:
                            self.backup.save_json(
                                "operation",
                                "series",
                                series.id,
                                {"current": current, "bedetheque": BedethequeClient.candidate_to_dict(candidate), "payload": payload, "source_metadata": source_metadata},
                                "avant PATCH auto-match Bedetheque prudent",
                            )
                            self._write_metadata_update(api, "series", series.id, payload, current, source="auto_match_bedetheque", note="Auto-match Bedetheque prudent")
                            report["operation_status"] = "OK appliqué"
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                report["operation_status"] = "Erreur"
                report["error"] = str(exc)
            rows.append(report)
            self._emit_auto_match_progress(
                progress,
                "Auto-match Bedetheque prudent",
                index,
                total,
                f"{index}/{total} — {title} — {report.get('operation_status') or 'traité'}",
            )

        csv_name = f"auto_match_bedetheque_prudent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = self.backup.export_csv(csv_name, rows)
        return {"rows": rows, "csv_path": csv_path, "simulation": simulation}

    def _show_auto_match_bedetheque_report(self, result: Dict[str, Any]) -> None:
        rows = result.get("rows") or []
        csv_path = result.get("csv_path", "")
        counts: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("operation_status") or row.get("status") or "Sans statut")
            counts[status] = counts.get(status, 0) + 1

        lines = [
            "Compte rendu auto-match Bedetheque prudent",
            f"Mode : {'simulation' if result.get('simulation') else 'écriture réelle'}",
            f"CSV : {csv_path}",
            "",
            "Synthèse :",
        ]
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
        lines.append("Détail :")
        for row in rows:
            detail = f"{row.get('index')}. {row.get('komga_title')} → {row.get('operation_status') or row.get('status')}"
            if row.get("search_query_used") and row.get("search_query_used") != row.get("query"):
                detail += f" | requête utilisée: {row.get('search_query_used')}"
            if row.get("matched_title"):
                detail += f" | match: {row.get('matched_title')} ({row.get('match_score')})"
            if row.get("scraped_title"):
                detail += f" | scrapé: {row.get('scraped_title')} ({row.get('scraped_score')})"
            if row.get("match_strategy"):
                detail += f" | stratégie: {row.get('match_strategy')}"
            if row.get("tome_match_count"):
                detail += f" | tomes: {row.get('tome_match_count')} ratio {row.get('tome_match_ratio')} score {row.get('tome_match_avg')}"
            if row.get("tome_match_detail"):
                detail += f" | détail tomes: {row.get('tome_match_detail')}"
            detail += self._series_refresh_detail_suffix(row)
            if row.get("payload_fields"):
                detail += f" | champs: {row.get('payload_fields')}"
            if row.get("source_notes"):
                detail += f" | source: {row.get('source_notes')}"
            if row.get("error"):
                detail += f" | erreur: {row.get('error')}"
            lines.append(detail)

        text = "\n".join(lines)
        self._show_structured_report_dialog(
            "Compte rendu auto-match Bedetheque prudent",
            text,
            rows,
            csv_path,
            columns=[
                ("index", "#"),
                ("komga_title", "Série Komga"),
                ("operation_status", "Statut opération"),
                ("search_query_used", "Requête"),
                ("result_count", "Résultats"),
                ("match_strategy", "Stratégie"),
                ("matched_title", "Match"),
                ("match_score", "Score"),
                ("scraped_title", "Scrapé"),
                ("current_series_status", "Status Komga"),
                ("source_series_status", "Status source"),
                ("proposed_series_status", "Status proposé"),
                ("series_status_action", "Action status"),
                ("current_totalBookCount", "Tomes Komga"),
                ("source_totalBookCount", "Tomes source"),
                ("proposed_totalBookCount", "Tomes proposés"),
                ("totalBookCount_action", "Action tomes"),
                ("tome_match_count", "Tomes"),
                ("tome_match_ratio", "Ratio"),
                ("tome_match_avg", "Score tomes"),
                ("payload_fields", "Champs"),
                ("error", "Erreur"),
            ],
            secondary_filter_keys=["match_strategy", "series_status_action", "totalBookCount_action"],
            status_filter_key="operation_status",
        )

    def _dedupe_manga_news_results(self, results: List[MangaNewsSearchResult]) -> List[MangaNewsSearchResult]:
        """Collapse duplicate Manga News search rows without hiding real ambiguity."""
        deduped: List[MangaNewsSearchResult] = []
        seen: set[str] = set()
        for result in results:
            if (result.kind or "series") != "series":
                continue
            key = result.slug or result.url or clean_title_for_compare(result.title)
            key = str(key or "").casefold().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

    def _manga_news_auto_match_status(self, query: str, results: List[MangaNewsSearchResult]) -> tuple[Optional[MangaNewsSearchResult], str, float, int]:
        series_results = self._dedupe_manga_news_results(results)
        result_count = len(series_results)
        if result_count == 0:
            return None, "Échec : aucun résultat", 0.0, 0

        scored = [(title_similarity(query, clean_search_title(result.title)), result) for result in series_results]
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_result = scored[0]
        min_score = self._matching_title_score_min()
        strong = [(score, result) for score, result in scored if score >= min_score]

        if len(strong) == 1:
            return best_result, "OK : match prudent unique après scoring", best_score, result_count
        if len(strong) > 1:
            return None, "Échec : plusieurs résultats solides", best_score, result_count
        if result_count != 1:
            return None, "Échec : plusieurs résultats", best_score, result_count
        if best_score < min_score:
            return None, "Échec : score titre insuffisant", best_score, result_count
        return best_result, "OK : match unique prudent", best_score, result_count

    def auto_match_manga_news_prudent_from_explorer(self) -> None:
        selected_series = self._selected_explorer_series_rows()
        if not selected_series:
            QMessageBox.warning(
                self,
                "Auto-match Manga News",
                "Sélectionne une ou plusieurs séries dans l'explorateur. Le batch ne traite jamais toute la bibliothèque sans sélection.",
            )
            return
        if not self.manga_news_enabled.isChecked():
            QMessageBox.warning(self, "Auto-match Manga News", "Module Manga News désactivé dans l'onglet Connexion")
            return
        simulation = self.simulation_enabled()
        answer = QMessageBox.question(
            self,
            "Auto-match Manga News prudent",
            f"Traiter {len(selected_series)} série(s) sélectionnée(s) avec Manga News ?\n\n"
            f"Règles : {self._matching_rules_summary()}.\n"
            "Source surtout utile pour compléter les summary.\n"
            f"Mode actuel : {'SIMULATION — aucune écriture' if simulation else 'ÉCRITURE RÉELLE — backup avant PATCH'}.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        total = len(selected_series)
        self._set_auto_match_progress("Auto-match Manga News prudent — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def done(result: Dict[str, Any]) -> None:
            self._show_auto_match_manga_news_report(result)
            self._set_auto_match_progress("Auto-match Manga News prudent — terminé", total, total)

        self.run_worker(
            "Auto-match Manga News prudent",
            lambda: self._run_auto_match_manga_news_prudent(selected_series, simulation, progress),
            done,
        )

    def _run_auto_match_manga_news_prudent(
        self,
        selected_series: List[Any],
        simulation: bool,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        client = self.manga_news_client()
        api = self.komga_api()
        rows: List[Dict[str, Any]] = []
        total = len(selected_series)

        for index, series in enumerate(selected_series, start=1):
            title = getattr(series, "title", "")
            query = clean_search_title(title)
            self.enrichment_history.record_search("manga_news", getattr(series, "id", ""), title)
            self._emit_auto_match_progress(progress, "Auto-match Manga News prudent", index - 1, total, f"{index}/{total} — {title}")
            report: Dict[str, Any] = {
                "index": index,
                "series_id": getattr(series, "id", ""),
                "komga_title": title,
                "query": query,
                "search_query_used": "",
                "search_attempts": "",
                **self._new_series_refresh_report_fields(),
                "result_count": 0,
                "matched_slug": "",
                "matched_title": "",
                "matched_media_kind": "",
                "matched_url": "",
                "match_score": "",
                "loaded_title": "",
                "loaded_score": "",
                "payload_fields": "",
                "payload_json": "",
                "error": "",
            }
            try:
                if not query:
                    report["operation_status"] = "Échec : requête vide"
                else:
                    direct_slug, direct_url = self._manga_news_link_for_series(series)
                    score = 0.0
                    result = None
                    if direct_slug or direct_url:
                        report["search_query_used"] = "<lien Manga-News existant>"
                        report["search_attempts"] = "chargement direct"
                        report["result_count"] = 1
                        if direct_slug:
                            candidate = client.get_series(direct_slug)
                            report["matched_slug"] = direct_slug
                        else:
                            candidate = client.get_series_by_url(direct_url)
                        report["matched_url"] = direct_url or candidate.source_url
                        report["matched_media_kind"] = "manga"
                        report["matched_title"] = candidate.title
                        report["match_score"] = "direct-link"
                        loaded_score = title_similarity(query, clean_search_title(candidate.title or title))
                    else:
                        search_result = self._search_manga_news_with_fallback(query, True, client)
                        results = search_result.get("rows") or []
                        report["search_query_used"] = str(search_result.get("used_query") or query)
                        report["search_attempts"] = self._format_search_attempts(search_result.get("attempts") or [])
                        if search_result.get("error") and not results:
                            report["operation_status"] = "Erreur recherche Manga News"
                            report["error"] = str(search_result.get("error") or "")
                            rows.append(report)
                            self._emit_auto_match_progress(
                                progress,
                                "Auto-match Manga News prudent",
                                index,
                                total,
                                f"{index}/{total} — {title} — {report.get('operation_status')}",
                            )
                            continue
                        result, status, score, result_count = self._manga_news_auto_match_status(query, results)
                        report["result_count"] = result_count
                        report["match_score"] = f"{score:.3f}" if score else ""
                        if result is None:
                            report["operation_status"] = status
                            rows.append(report)
                            self._emit_auto_match_progress(
                                progress,
                                "Auto-match Manga News prudent",
                                index,
                                total,
                                f"{index}/{total} — {title} — {report.get('operation_status')}",
                            )
                            continue
                        report["matched_slug"] = result.slug
                        report["matched_title"] = result.title
                        report["matched_media_kind"] = result.media_kind
                        report["matched_url"] = result.url
                        candidate = client.get_series(result.slug)
                        loaded_score = title_similarity(query, clean_search_title(candidate.title or result.title))
                    report["loaded_title"] = candidate.title
                    report["loaded_score"] = f"{loaded_score:.3f}"
                    if not (direct_slug or direct_url) and max(score, loaded_score) < self._matching_loaded_title_score_min():
                        report["operation_status"] = "Échec : score titre insuffisant après chargement"
                    else:
                        current = self._fetch_current_metadata("series", series.id)
                        source_metadata = dict(candidate.series_metadata or {})
                        report["source_fields"] = "; ".join(sorted(source_metadata.keys()))
                        report["source_notes"] = "lien direct Manga News" if (direct_slug or direct_url) else "summary prioritaire depuis Manga News ; champs avancés visibles mais batch conservateur"
                        payload = self._manga_news_payload_from_metadata_maps(current, source_metadata)
                        report["payload_fields"] = "; ".join(payload.keys())
                        report["payload_json"] = json_text(payload, indent=0) if payload else ""
                        self._fill_series_refresh_report_fields(report, current, source_metadata, payload)

                        if not payload:
                            report["operation_status"] = "OK : aucun changement"
                        elif simulation:
                            report["operation_status"] = "OK simulation"
                        else:
                            self.backup.save_json(
                                "operation",
                                "series",
                                series.id,
                                {"current": current, "manga_news": MangaNewsClient.candidate_to_dict(candidate), "payload": payload, "source_metadata": source_metadata},
                                "avant PATCH auto-match Manga News prudent",
                            )
                            self._write_metadata_update(api, "series", series.id, payload, current, source="auto_match_manga_news", note="Auto-match Manga News prudent")
                            report["operation_status"] = "OK appliqué"
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                report["operation_status"] = "Erreur"
                report["error"] = str(exc)
            rows.append(report)
            self._emit_auto_match_progress(
                progress,
                "Auto-match Manga News prudent",
                index,
                total,
                f"{index}/{total} — {title} — {report.get('operation_status') or 'traité'}",
            )

        csv_name = f"auto_match_manga_news_prudent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = self.backup.export_csv(csv_name, rows)
        return {"rows": rows, "csv_path": csv_path, "simulation": simulation}

    def _show_auto_match_manga_news_report(self, result: Dict[str, Any]) -> None:
        rows = result.get("rows") or []
        csv_path = result.get("csv_path", "")
        counts: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("operation_status") or row.get("status") or "Sans statut")
            counts[status] = counts.get(status, 0) + 1

        lines = [
            "Compte rendu auto-match Manga News prudent",
            f"Mode : {'simulation' if result.get('simulation') else 'écriture réelle'}",
            f"CSV : {csv_path}",
            "",
            "Synthèse :",
        ]
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
        lines.append("Détail :")
        for row in rows:
            detail = f"{row.get('index')}. {row.get('komga_title')} → {row.get('operation_status') or row.get('status')}"
            if row.get("search_query_used") and row.get("search_query_used") != row.get("query"):
                detail += f" | requête utilisée: {row.get('search_query_used')}"
            if row.get("matched_title"):
                detail += f" | match: {row.get('matched_title')} [{row.get('matched_media_kind')}] ({row.get('match_score')})"
            if row.get("loaded_title"):
                detail += f" | chargé: {row.get('loaded_title')} ({row.get('loaded_score')})"
            detail += self._series_refresh_detail_suffix(row)
            if row.get("payload_fields"):
                detail += f" | champs: {row.get('payload_fields')}"
            if row.get("source_notes"):
                detail += f" | source: {row.get('source_notes')}"
            if row.get("error"):
                detail += f" | erreur: {row.get('error')}"
            lines.append(detail)

        text = "\n".join(lines)
        self._show_structured_report_dialog(
            "Compte rendu auto-match Manga News prudent",
            text,
            rows,
            csv_path,
            columns=[
                ("index", "#"),
                ("komga_title", "Série Komga"),
                ("operation_status", "Statut opération"),
                ("search_query_used", "Requête"),
                ("result_count", "Résultats"),
                ("matched_media_kind", "Media"),
                ("matched_title", "Match"),
                ("match_score", "Score"),
                ("loaded_title", "Chargé"),
                ("loaded_score", "Score chargé"),
                ("current_series_status", "Status Komga"),
                ("source_series_status", "Status source"),
                ("proposed_series_status", "Status proposé"),
                ("series_status_action", "Action status"),
                ("current_totalBookCount", "Tomes Komga"),
                ("source_totalBookCount", "Tomes source"),
                ("proposed_totalBookCount", "Tomes proposés"),
                ("totalBookCount_action", "Action tomes"),
                ("payload_fields", "Champs"),
                ("error", "Erreur"),
            ],
            secondary_filter_keys=["matched_media_kind", "series_status_action", "totalBookCount_action"],
            status_filter_key="operation_status",
        )


    def _mangabaka_auto_match_status(self, query: str, results: List[MangaBakaSearchResult]) -> tuple[Optional[MangaBakaSearchResult], str, float, int]:
        manga_results = [r for r in results if str(r.type or "").casefold() == "manga"]
        if not manga_results:
            return None, "Échec : aucun résultat manga", 0.0, 0
        if len(manga_results) != 1:
            return None, "Échec : plusieurs résultats manga", 0.0, len(manga_results)
        result = manga_results[0]
        score = title_similarity(clean_search_title(query), clean_search_title(result.title))
        if score < self._matching_title_score_min():
            return None, "Échec : score titre insuffisant", score, len(manga_results)
        return result, "Candidat unique", score, len(manga_results)

    def auto_match_mangabaka_prudent_from_explorer(self) -> None:
        selected_series = self._selected_explorer_series_rows()
        if not selected_series:
            QMessageBox.warning(
                self,
                "Auto-match MangaBaka",
                "Sélectionne une ou plusieurs séries dans l'explorateur. Le batch ne traite jamais toute la bibliothèque sans sélection.",
            )
            return
        if not self.mangabaka_enabled.isChecked():
            QMessageBox.warning(self, "Auto-match MangaBaka", "Module MangaBaka désactivé dans l'onglet Connexion")
            return
        simulation = self.simulation_enabled()
        answer = QMessageBox.question(
            self,
            "Auto-match MangaBaka prudent",
            f"Traiter {len(selected_series)} série(s) sélectionnée(s) avec MangaBaka ?\n\n"
            f"Règles : {self._matching_rules_summary()}.\n"
            f"Mode actuel : {'SIMULATION — aucune écriture' if simulation else 'ÉCRITURE RÉELLE — backup avant PATCH'}.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        total = len(selected_series)
        self._set_auto_match_progress("Auto-match MangaBaka prudent — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def done(result: Dict[str, Any]) -> None:
            self._show_auto_match_mangabaka_report(result)
            self._set_auto_match_progress("Auto-match MangaBaka prudent — terminé", total, total)

        self.run_worker(
            "Auto-match MangaBaka prudent",
            lambda: self._run_auto_match_mangabaka_prudent(selected_series, simulation, progress),
            done,
        )

    def _run_auto_match_mangabaka_prudent(
        self,
        selected_series: List[Any],
        simulation: bool,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        client = self.mangabaka_client()
        api = self.komga_api()
        rows: List[Dict[str, Any]] = []
        total = len(selected_series)

        for index, series in enumerate(selected_series, start=1):
            title = getattr(series, "title", "")
            query = clean_search_title(title)
            self.enrichment_history.record_search("mangabaka", getattr(series, "id", ""), title)
            self._emit_auto_match_progress(progress, "Auto-match MangaBaka prudent", index - 1, total, f"{index}/{total} — {title}")
            report: Dict[str, Any] = {
                "index": index,
                "series_id": getattr(series, "id", ""),
                "komga_title": title,
                "query": query,
                "search_query_used": "",
                "search_attempts": "",
                **self._new_series_refresh_report_fields(),
                "result_count": 0,
                "matched_id": "",
                "matched_title": "",
                "matched_type": "",
                "matched_url": "",
                "match_score": "",
                "loaded_title": "",
                "loaded_score": "",
                "payload_fields": "",
                "payload_json": "",
                "error": "",
            }
            try:
                if not query:
                    report["operation_status"] = "Échec : requête vide"
                else:
                    search_result = self._search_mangabaka_with_fallback(query, True, client)
                    results = search_result.get("rows") or []
                    report["search_query_used"] = str(search_result.get("used_query") or query)
                    report["search_attempts"] = self._format_search_attempts(search_result.get("attempts") or [])
                    result, status, score, result_count = self._mangabaka_auto_match_status(query, results)
                    report["result_count"] = result_count
                    report["match_score"] = f"{score:.3f}" if score else ""
                    if result is None:
                        report["operation_status"] = status
                    else:
                        report["matched_id"] = result.id
                        report["matched_title"] = result.title
                        report["matched_type"] = result.type
                        report["matched_url"] = result.source_url
                        candidate = client.get_series(result.id)
                        report["loaded_title"] = candidate.title
                        loaded_score = title_similarity(query, clean_search_title(candidate.title or result.title))
                        report["loaded_score"] = f"{loaded_score:.3f}"
                        # Le résultat de recherche a déjà passé la règle stricte :
                        # exactement 1 résultat type=manga + score titre >= 0.90.
                        # Certains titres chargés peuvent être moins propres ; on garde le
                        # match unique fiable si au moins un des deux scores reste valide.
                        if max(score, loaded_score) < self._matching_loaded_title_score_min():
                            report["operation_status"] = "Échec : score titre insuffisant après chargement"
                        else:
                            current = self._fetch_current_metadata("series", series.id)
                            source_metadata, source_notes = self._enrich_update_with_link_series_metadata("mangabaka", candidate, candidate.series_metadata)
                            report["source_fields"] = "; ".join(sorted(source_metadata.keys()))
                            report["source_notes"] = " | ".join(source_notes)
                            payload = self._payload_from_metadata_maps(current, source_metadata, SERIES_METADATA_FIELDS, target_type="series")
                            report["payload_fields"] = "; ".join(payload.keys())
                            report["payload_json"] = json_text(payload, indent=0) if payload else ""
                            self._fill_series_refresh_report_fields(report, current, source_metadata, payload)

                            if not payload:
                                report["operation_status"] = "OK : aucun changement"
                            elif simulation:
                                report["operation_status"] = "OK simulation"
                            else:
                                self.backup.save_json(
                                    "operation",
                                    "series",
                                    series.id,
                                    {"current": current, "mangabaka": MangaBakaClient.candidate_to_dict(candidate), "payload": payload, "source_metadata": source_metadata},
                                    "avant PATCH auto-match MangaBaka prudent",
                                )
                                self._write_metadata_update(api, "series", series.id, payload, current, source="auto_match_mangabaka", note="Auto-match MangaBaka prudent")
                                report["operation_status"] = "OK appliqué"
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                report["operation_status"] = "Erreur"
                report["error"] = str(exc)
            rows.append(report)
            self._emit_auto_match_progress(
                progress,
                "Auto-match MangaBaka prudent",
                index,
                total,
                f"{index}/{total} — {title} — {report.get('operation_status') or 'traité'}",
            )

        csv_name = f"auto_match_mangabaka_prudent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = self.backup.export_csv(csv_name, rows)
        return {"rows": rows, "csv_path": csv_path, "simulation": simulation}

    def _show_auto_match_mangabaka_report(self, result: Dict[str, Any]) -> None:
        rows = result.get("rows") or []
        csv_path = result.get("csv_path", "")
        counts: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("operation_status") or row.get("status") or "Sans statut")
            counts[status] = counts.get(status, 0) + 1

        lines = [
            "Compte rendu auto-match MangaBaka prudent",
            f"Mode : {'simulation' if result.get('simulation') else 'écriture réelle'}",
            f"CSV : {csv_path}",
            "",
            "Synthèse :",
        ]
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
        lines.append("Détail :")
        for row in rows:
            detail = f"{row.get('index')}. {row.get('komga_title')} → {row.get('operation_status') or row.get('status')}"
            if row.get("search_query_used") and row.get("search_query_used") != row.get("query"):
                detail += f" | requête utilisée: {row.get('search_query_used')}"
            if row.get("matched_title"):
                detail += f" | match: {row.get('matched_title')} [{row.get('matched_type')}] ({row.get('match_score')})"
            if row.get("loaded_title"):
                detail += f" | chargé: {row.get('loaded_title')} ({row.get('loaded_score')})"
            detail += self._series_refresh_detail_suffix(row)
            if row.get("payload_fields"):
                detail += f" | champs: {row.get('payload_fields')}"
            if row.get("source_notes"):
                detail += f" | source: {row.get('source_notes')}"
            if row.get("error"):
                detail += f" | erreur: {row.get('error')}"
            lines.append(detail)

        text = "\n".join(lines)
        self._show_structured_report_dialog(
            "Compte rendu auto-match MangaBaka prudent",
            text,
            rows,
            csv_path,
            columns=[
                ("index", "#"),
                ("komga_title", "Série Komga"),
                ("operation_status", "Statut opération"),
                ("search_query_used", "Requête"),
                ("result_count", "Résultats"),
                ("matched_type", "Type"),
                ("matched_title", "Match"),
                ("match_score", "Score"),
                ("loaded_title", "Chargé"),
                ("loaded_score", "Score chargé"),
                ("current_series_status", "Status Komga"),
                ("source_series_status", "Status source"),
                ("proposed_series_status", "Status proposé"),
                ("series_status_action", "Action status"),
                ("current_totalBookCount", "Tomes Komga"),
                ("source_totalBookCount", "Tomes source"),
                ("proposed_totalBookCount", "Tomes proposés"),
                ("totalBookCount_action", "Action tomes"),
                ("payload_fields", "Champs"),
                ("error", "Erreur"),
            ],
            secondary_filter_keys=["matched_type", "series_status_action", "totalBookCount_action"],
            status_filter_key="operation_status",
        )

    def load_series(self) -> None:
        lib_id = self._library_id("explorer")
        search = self.search_series_text.text().strip()
        generation = self._next_series_load_generation("explorer")
        language_filter = self.filter_series_language.currentData() if hasattr(self, "filter_series_language") else ""
        status_filter = self.filter_series_status.currentData() if hasattr(self, "filter_series_status") else "ALL"
        link_filter = self.filter_series_link_label.currentData() if hasattr(self, "filter_series_link_label") else "ALL"
        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("explorer", generation):
                return
            rows = self._filter_global_series_visibility(rows)
            self._refresh_explorer_link_filter_options(rows)
            link_filter_current = self.filter_series_link_label.currentData() if hasattr(self, "filter_series_link_label") else link_filter
            active_filters: List[str] = []
            if self.filter_series_empty_summary.isChecked():
                rows = [x for x in rows if is_blank_metadata_value(x.metadata.get("summary"))]
                active_filters.append("summary vide")
            if language_filter:
                rows = [x for x in rows if metadata_language_matches(x.metadata.get("language"), language_filter)]
                active_filters.append(f"langue={str(language_filter).upper()}")
            if status_filter and normalized_status_code(status_filter) != "ALL":
                rows = [x for x in rows if metadata_status_matches(x.metadata.get("status"), status_filter)]
                active_filters.append(f"status={normalized_status_code(status_filter)}")
            if link_filter_current and normalized_link_label(link_filter_current) != "all":
                rows = [x for x in rows if metadata_link_label_matches(x.metadata.get("links"), link_filter_current)]
                display = "SANS LINK" if normalized_link_label(link_filter_current) == "__no_link__" else str(link_filter_current)
                active_filters.append(f"links={display}")
            self.series_rows = rows
            self._set_table(
                self.series_table,
                self._series_table_headers(include_library=True),
                [self._series_table_row(x, include_library=True) for x in rows],
                selection_mode=QAbstractItemView.ExtendedSelection,
                row_data=rows,
            )
            suffix = f" — {', '.join(active_filters)}" if active_filters else ""
            self.log(f"✅ {len(rows)} séries chargées{suffix}")
        self.run_worker("Chargement séries", lambda: self.komga_api().series(lib_id, search=search), done)

    def load_books(self, series_id: Optional[str] = None, library_id: Optional[str] = None) -> None:
        lib_id = library_id or self._library_id("explorer")
        series_id = series_id if series_id is not None else self._selected_id_from_table(self.series_table)
        search = self.search_books_text.text().strip()
        generation = self._next_series_load_generation("explorer_books")
        self.book_rows = []
        self._set_table(
            self.books_table,
            self._book_table_headers(include_series=True, include_library=True),
            [],
            row_data=[],
        )
        self.explorer_book_target_id = ""
        self.explorer_book_detail = {}
        self._set_detail_table(self.explorer_book_details_table, {})
        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("explorer_books", generation):
                return
            if series_id and series_id != self._selected_id_from_table(self.series_table):
                return
            self.book_rows = rows
            self._set_table(
                self.books_table,
                self._book_table_headers(include_series=True, include_library=True),
                [self._book_table_row(x, include_series=True, include_library=True) for x in rows],
                row_data=rows,
            )
            if rows:
                self._reset_table_to_first_row(self.books_table)
                self.on_book_selected()
            else:
                self.explorer_book_target_id = ""
                self.explorer_book_detail = {}
                self._set_detail_table(self.explorer_book_details_table, {})
            target = f" pour {series_id}" if series_id else ""
            self.log(f"✅ {len(rows)} livres chargés{target}")
        self.run_worker(
            "Chargement livres",
            lambda: self.komga_api().books(
                lib_id,
                series_id or None,
                search=search,
                page_size=200,
                direct_series_only=bool(series_id),
                timeout=min(int(self.timeout_seconds.value()), 12),
            ),
            done,
        )

    def export_book_inventory(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter l'inventaire des livres",
            "komga_book_inventory.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        library_id = self._library_id("explorer")
        library_names = {
            str(getattr(item, "id", "")): str(getattr(item, "name", ""))
            for item in self.libraries
        }

        def work() -> List[Dict[str, Any]]:
            books = self.komga_api().books(library_id=library_id, page_size=1000)
            return [
                book_inventory_row(book, library_names.get(str(book.library_id), ""))
                for book in books
            ]

        def done(rows: List[Dict[str, Any]]) -> None:
            write_csv(path, rows, BOOK_INVENTORY_COLUMNS)
            self.log(f"✅ Inventaire livres exporté : {len(rows)} ligne(s) — {path}")

        self.run_worker("Export inventaire livres", work, done)

    def on_series_selected(self) -> None:
        selected_count = len(self._selected_row_indexes(self.series_table))
        if hasattr(self, "explorer_selection_label"):
            self.explorer_selection_label.setText(
                "Aucune série sélectionnée"
                if selected_count == 0
                else f"{selected_count} série(s) sélectionnée(s) — vérifiez le mode avant de lancer une action"
            )
        for button in getattr(self, "explorer_action_buttons", []):
            button.setEnabled(selected_count > 0)
        sid = self._selected_id_from_table(self.series_table)
        if sid:
            self._set_metadata_target_type("series")
            self.meta_target_id.setText(sid)
            self.poster_type.setCurrentText("series")
            self.poster_id.setText(sid)
            if hasattr(self, "komf_series_id"):
                self.komf_series_id.setText(sid)
                self.komf_library_id.setText(self._library_id("explorer"))
            self.bdt_target_type.setCurrentText("series")
            self.bdt_target_id.setText(sid)
            selected_data = self._selected_row_data(self.series_table)
            series = selected_data if self._record_id(selected_data) == sid else None
            if series is None:
                series = next((item for item in self.series_rows if self._record_id(item) == sid), None)
            if series is not None:
                self._set_context_selection(series=series)
                self.bdt_query.setText(clean_search_title(series.title))
                if hasattr(self, "mbk_query"):
                    self.mbk_query.setText(clean_search_title(series.title))
                    self.mbk_target_id.setText(sid)
                self.explorer_current_target_type = "series"
                self.explorer_current_target_id = sid
                self.explorer_current_detail = series.raw or {}
                self.explorer_series_target_id = sid
                self.explorer_series_detail = series.raw or {}
                self.explorer_book_target_id = ""
                self.explorer_book_detail = {}
                self._set_detail_table(self.explorer_series_details_table, self.explorer_series_detail)
                self._set_detail_table(self.explorer_book_details_table, {})
            self._show_cover("series", sid, self.explorer_cover)
            self.load_books(series_id=sid, library_id=self._library_id("explorer"))

    def on_book_selected(self) -> None:
        bid = self._selected_id_from_table(self.books_table)
        if bid:
            self._set_metadata_target_type("book")
            self.meta_target_id.setText(bid)
            self.poster_type.setCurrentText("book")
            self.poster_id.setText(bid)
            self.bdt_target_type.setCurrentText("book")
            self.bdt_target_id.setText(bid)
            selected_data = self._selected_row_data(self.books_table)
            book = selected_data if self._record_id(selected_data) == bid else None
            if book is None:
                book = next((item for item in self.book_rows if self._record_id(item) == bid), None)
            if book is not None:
                self._set_context_selection(book=book)
                self.bdt_album_number.setText(str(book.number or ""))
                self.bdt_query.setText(clean_search_title(book.title))
                self.explorer_current_target_type = "book"
                self.explorer_current_target_id = bid
                self.explorer_current_detail = book.raw or {}
                self.explorer_book_target_id = bid
                self.explorer_book_detail = book.raw or {}
                self._set_detail_table(self.explorer_book_details_table, self.explorer_book_detail)

    def _explorer_detail_context(self, target: str = "current") -> tuple[str, str, Dict[str, Any], QTableWidget]:
        target = str(target or "current")
        if target == "series":
            return "series", self.explorer_series_target_id, self.explorer_series_detail or {}, self.explorer_series_details_table
        if target == "book":
            return "book", self.explorer_book_target_id, self.explorer_book_detail or {}, self.explorer_book_details_table
        table = self.explorer_book_details_table if self.explorer_current_target_type == "book" else self.explorer_series_details_table
        return self.explorer_current_target_type, self.explorer_current_target_id, self.explorer_current_detail or {}, table

    def _explorer_detail_value(self, target: str, *keys: str) -> str:
        _target_type, _target_id, data, _table = self._explorer_detail_context(target)
        flat: Dict[str, Any] = {}
        for key, value in data.items():
            if key == "metadata" and isinstance(value, dict):
                for meta_key, meta_value in value.items():
                    flat.setdefault(meta_key, meta_value)
            else:
                flat.setdefault(key, value)
        for key in keys:
            value = flat.get(key)
            if value:
                return str(value)
        return ""

    def copy_explorer_detail_id(self, target: str = "current") -> None:
        QApplication.clipboard().setText(self._explorer_detail_value(target, "id", "bookId", "seriesId"))

    def _explorer_detail_url(self, target: str = "current") -> str:
        _target_type, _target_id, data, _table = self._explorer_detail_context(target)
        links = self._explorer_detail_value(target, "url", "source_url")
        if not links:
            raw_links = data.get("metadata", {}).get("links") if isinstance(data.get("metadata"), dict) else None
            if isinstance(raw_links, list) and raw_links:
                first = raw_links[0]
                if isinstance(first, dict):
                    links = str(first.get("url") or "")
                else:
                    links = str(first)
        return links

    def copy_explorer_detail_url(self, target: str = "current") -> None:
        links = self._explorer_detail_url(target)
        QApplication.clipboard().setText(links)

    def open_explorer_detail_url(self, target: str = "current") -> None:
        url = self._explorer_detail_url(target).strip()
        if not url:
            QMessageBox.information(self, "URL", "Aucune URL disponible pour cette sélection.")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        QDesktopServices.openUrl(QUrl(url))

    def show_explorer_detail_json(self, target: str = "current") -> None:
        target_type, _target_id, data, _table = self._explorer_detail_context(target)
        label = "série" if target_type == "series" else "tome" if target_type == "book" else "sélection"
        self._show_text_popup(f"Détails {label} Explorateur — JSON", json_text(data or {}))

    def open_explorer_metadata_editor(self, target: str = "current") -> None:
        target_type, target_id, detail, detail_table = self._explorer_detail_context(target)
        if not target_id:
            QMessageBox.information(self, "Métadonnées", "Sélectionne d'abord une série ou un tome.")
            return
        if target_type not in {"series", "book"}:
            QMessageBox.warning(self, "Métadonnées", "Type de sélection inconnu.")
            return
        raw_metadata = (detail or {}).get("metadata")
        current = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        dialog = TypedMetadataDialog(target_type, current, self)
        if dialog.exec() != QDialog.Accepted:
            return
        payload = dialog.payload
        endpoint = f"PATCH /api/v1/{'series' if target_type == 'series' else 'books'}/{target_id}/metadata"
        preview = self._format_diff(current, payload, endpoint)
        if self.simulation_enabled():
            self._show_text_popup("Simulation — modification métadonnées", preview, (1000, 720))
            self.log("Simulation active : aucune écriture depuis l'éditeur Explorateur")
            return

        def work() -> Dict[str, Any]:
            api = self.komga_api()
            fresh = self._fetch_current_metadata(target_type, target_id)
            response = self._write_metadata_update(
                api,
                target_type,
                target_id,
                payload,
                fresh,
                source="explorer_typed_editor",
                note="Éditeur typé Explorateur",
                allow_all_languages=True,
                preserve_empty_strings=True,
            )
            return {"metadata": self._fetch_current_metadata(target_type, target_id), "response": response}

        def done(result: Dict[str, Any]) -> None:
            metadata = result.get("metadata") if isinstance(result, dict) else {}
            if isinstance(metadata, dict):
                detail["metadata"] = metadata
                if target_type == "series":
                    self.explorer_series_detail = detail
                elif target_type == "book":
                    self.explorer_book_detail = detail
                self.explorer_current_detail = detail
                self.explorer_current_target_type = target_type
                self.explorer_current_target_id = target_id
                self._set_detail_table(detail_table, detail)
            self.log(f"✅ Métadonnées appliquées sur {target_type}:{target_id}")
            if target_type == "series":
                self.load_series()
            else:
                self.load_books()

        self.run_worker("Application métadonnées Explorateur", work, done)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    def _metadata_target_type(self) -> str:
        return str(self.meta_target_type.currentData() or "series")

    def _set_metadata_target_type(self, target_type: str) -> None:
        index = self.meta_target_type.findData(target_type)
        if index >= 0:
            self.meta_target_type.setCurrentIndex(index)

    def _metadata_operation_signature(self, target_type: str, target_id: str, payload: Dict[str, Any]) -> str:
        return json.dumps(
            {
                "target_type": target_type,
                "target_id": target_id,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    def _invalidate_metadata_preview(self) -> None:
        self._metadata_preview_signature = None
        if hasattr(self, "meta_apply_button"):
            self.meta_apply_button.setEnabled(False)
        if hasattr(self, "meta_workflow_status_label"):
            self.meta_workflow_status_label.setText(
                "Modifications non prévisualisées. Vérifiez les champs puis lancez la prévisualisation."
            )

    def _fields_for_target(self, target_type: str) -> List[str]:
        return SERIES_METADATA_FIELDS if target_type == "series" else BOOK_METADATA_FIELDS

    def use_selected_for_metadata(self) -> None:
        bid = self._selected_id_from_table(self.books_table)
        sid = self._selected_id_from_table(self.series_table)
        if bid:
            self._set_metadata_target_type("book")
            self.meta_target_id.setText(bid)
        elif sid:
            self._set_metadata_target_type("series")
            self.meta_target_id.setText(sid)
        self._select_metadata_target_by_id(self.meta_target_id.text().strip())
        self.load_current_metadata()

    def on_metadata_target_type_changed(self, *_: Any) -> None:
        self._invalidate_metadata_preview()
        self.current_metadata = {}
        self._fill_metadata_table(
            self.meta_table,
            {},
            {},
            self._fields_for_target(self._metadata_target_type()),
        )
        self.load_metadata_targets()

    def load_metadata_targets(self) -> None:
        library_id = self._library_id("metadata")
        target_type = self._metadata_target_type()
        generation = self._next_series_load_generation("metadata_targets")
        self._invalidate_metadata_preview()
        self.meta_workflow_status_label.setText("Chargement des cibles disponibles…")
        self.meta_target_combo.blockSignals(True)
        self.meta_target_combo.clear()
        self.meta_target_combo.addItem("Chargement...", "")
        self.meta_target_combo.blockSignals(False)

        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("metadata_targets", generation):
                return
            if target_type == "series":
                rows = self._filter_global_series_visibility(rows)
            self.meta_target_rows = rows
            self.meta_target_combo.blockSignals(True)
            self.meta_target_combo.clear()
            self.meta_target_combo.addItem("Sélectionner une cible...", "")
            for row in rows:
                suffix = f" — tome {row.number}" if target_type == "book" and getattr(row, "number", "") else ""
                self.meta_target_combo.addItem(f"{row.title}{suffix}", row.id)
            self.meta_target_combo.blockSignals(False)
            self.meta_preview.setPlainText(f"{len(rows)} cible(s) {target_type} chargée(s).")
            self.meta_workflow_status_label.setText(
                f"{len(rows)} cible(s) disponible(s). Sélectionnez celle à modifier."
            )

        api_call = (
            (lambda: self.komga_api().series(library_id))
            if target_type == "series"
            else (lambda: self.komga_api().books(library_id=library_id, page_size=200))
        )
        self.run_worker(f"Chargement cibles métadonnées ({target_type})", api_call, done)

    def on_metadata_target_selected(self, index: int) -> None:
        target_id = str(self.meta_target_combo.itemData(index) or "") if index >= 0 else ""
        self.meta_target_id.setText(target_id)
        if target_id:
            selected = next((row for row in self.meta_target_rows if str(getattr(row, "id", "")) == target_id), None)
            if selected is not None:
                if self._metadata_target_type() == "series":
                    self._set_context_selection(series=selected)
                else:
                    self._set_context_selection(book=selected)
            self.load_current_metadata()

    def _select_metadata_target_by_id(self, target_id: str) -> bool:
        index = self.meta_target_combo.findData(target_id)
        if index < 0:
            return False
        self.meta_target_combo.setCurrentIndex(index)
        return True

    def _fetch_current_metadata(self, target_type: str, target_id: str) -> Dict[str, Any]:
        api = self.komga_api()
        if target_type == "series":
            raw = api.get_series(target_id)
        elif target_type == "book":
            raw = api.get_book(target_id)
        else:
            raise ValueError("Type metadata non supporté")
        return raw.get("metadata") if isinstance(raw.get("metadata"), dict) else raw

    def load_current_metadata(self) -> None:
        target_type = self._metadata_target_type()
        target_id = self.meta_target_id.text().strip()
        if not target_id:
            QMessageBox.warning(self, "ID", "Aucun ID cible")
            return
        self._invalidate_metadata_preview()
        self.meta_workflow_status_label.setText("Chargement des métadonnées actuelles…")
        def done(data: Dict[str, Any]) -> None:
            self.current_metadata = data
            self._fill_metadata_table(self.meta_table, data, {}, self._fields_for_target(target_type))
            self.meta_preview.setPlainText("Métadonnées chargées. Modifie la colonne Nouveau et coche Inclure.")
            self.meta_workflow_status_label.setText(
                "Métadonnées chargées. Choisissez les champs à inclure, puis prévisualisez."
            )
            self.backup.save_json("session", target_type, target_id, data, "chargement metadata")
        self.run_worker("Chargement metadata", lambda: self._fetch_current_metadata(target_type, target_id), done)

    def _metadata_payload(self) -> Dict[str, Any]:
        payload = self._payload_from_metadata_table(self.meta_table)
        extra = self._json_from_text(self.meta_extra_json)
        payload.update(extra)
        return payload

    def simulate_metadata(self) -> None:
        target_type = self._metadata_target_type()
        target_id = self.meta_target_id.text().strip()
        if not target_id:
            self._invalidate_metadata_preview()
            QMessageBox.warning(self, "Métadonnées", "Choisissez une cible avant de prévisualiser.")
            return
        try:
            payload = self._normalize_payload_for_target(target_type, self._metadata_payload())
        except Exception as exc:
            self._invalidate_metadata_preview()
            QMessageBox.warning(self, "Payload invalide", str(exc))
            return
        endpoint = f"PATCH /api/v1/{'series' if target_type == 'series' else 'books'}/{target_id}/metadata"
        self.meta_preview.setPlainText(self._format_diff(self.current_metadata, payload, endpoint))
        if not payload:
            self._invalidate_metadata_preview()
            self.meta_workflow_status_label.setText("Aucun champ n'est inclus dans l'opération.")
            return
        self._metadata_preview_signature = self._metadata_operation_signature(target_type, target_id, payload)
        self.meta_apply_button.setEnabled(True)
        self.meta_workflow_status_label.setText(
            f"Prévisualisation prête : {len(payload)} champ(s). Relisez la comparaison avant l'application."
        )

    def apply_metadata(self) -> None:
        target_type = self._metadata_target_type()
        target_id = self.meta_target_id.text().strip()
        try:
            payload = self._normalize_payload_for_target(target_type, self._metadata_payload())
        except Exception as exc:
            self._invalidate_metadata_preview()
            QMessageBox.warning(self, "Payload invalide", str(exc))
            return
        if not target_id or not payload:
            QMessageBox.warning(self, "Payload", "ID ou payload vide")
            return
        signature = self._metadata_operation_signature(target_type, target_id, payload)
        if signature != self._metadata_preview_signature:
            self.simulate_metadata()
            QMessageBox.information(
                self,
                "Prévisualisation requise",
                "La cible ou les champs ont changé. Une nouvelle prévisualisation vient d'être préparée ; "
                "relisez-la avant d'appliquer.",
            )
            return
        if self.simulation_enabled():
            self.simulate_metadata()
            self.log("Simulation active : aucune écriture metadata")
            self.meta_workflow_status_label.setText("Simulation terminée : aucune écriture n'a été effectuée.")
            return

        confirmation = (
            "Appliquer les changements prévisualisés ?\n\n"
            f"Cible : {target_type}:{target_id}\n"
            f"Champs : {len(payload)}\n\n"
            "Une sauvegarde et un audit seront créés avant l'écriture."
        )
        if QMessageBox.question(self, "Confirmer l'écriture", confirmation) != QMessageBox.Yes:
            return

        self.meta_apply_button.setEnabled(False)
        self.meta_workflow_status_label.setText("Application en cours…")

        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata(target_type, target_id)
            self.backup.save_json("operation", target_type, target_id, current, "avant PATCH metadata")
            return self._write_metadata_update(api, target_type, target_id, payload, current, source="manual_metadata", note="Application manuelle metadata")

        def done(result: Any) -> None:
            self.meta_preview.setPlainText(json_text({"applied": True, "payload": payload, "response": result}))
            self.log(f"✅ Metadata appliquée sur {target_type}:{target_id}")
            self._metadata_preview_signature = None
            self.meta_apply_button.setEnabled(False)
            self.meta_workflow_status_label.setText(
                "Modification appliquée. Une sauvegarde est disponible dans Historique et restauration."
            )
        self.run_worker("Application metadata", do_apply, done)

    # ------------------------------------------------------------------
    # Collections
    # ------------------------------------------------------------------
    def _confirm_membership_bulk_action(
        self,
        *,
        member_label: str,
        member_count: int,
        target_label: str,
        target_count: int,
        action: str,
    ) -> bool:
        if self.simulation_enabled():
            return True
        message = (
            f"Confirmer l'action en masse ?\n\n"
            f"Action : {action}\n"
            f"{member_label} : {member_count}\n"
            f"{target_label} : {target_count}\n\n"
            "Chaque ressource modifiée sera sauvegardée avant l'écriture."
        )
        return QMessageBox.question(self, "Confirmer l'action en masse", message) == QMessageBox.Yes

    def load_collections(self) -> None:
        lib_id = self._library_id("collections")
        search = self.collection_search.text().strip().lower()
        generation = self._next_series_load_generation("collections")
        self._next_series_load_generation("collection_detail")
        def do_load() -> List[CollectionItem]:
            api = self.komga_api()
            rows = api.collections()
            if search:
                rows = [r for r in rows if search in r.name.lower()]
            if lib_id:
                series_ids = {s.id for s in api.series(lib_id)}
                rows = [r for r in rows if set(r.raw.get("seriesIds") or []) & series_ids]
            return rows
        def done(rows: List[CollectionItem]) -> None:
            if not self._is_current_series_load_generation("collections", generation):
                return
            self.collection_rows = rows
            table_rows = [[x.id, x.name, len(x.raw.get("seriesIds") or [])] for x in rows]
            self._set_table(self.collections_table, ["ID", "Nom", "Séries"], table_rows)
            if hasattr(self, "collection_target_collections_table"):
                self._set_table(
                    self.collection_target_collections_table,
                    ["ID", "Nom", "Séries"],
                    table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
            if hasattr(self, "collection_bulk_target_collections_table"):
                self._set_table(
                    self.collection_bulk_target_collections_table,
                    ["ID", "Nom", "Séries"],
                    table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
            if hasattr(self, "collection_suggestion_target_collections_table"):
                self._set_collection_suggestion_target_rows(rows)
            self.log(f"✅ {len(rows)} collections")
            if rows:
                self._reset_table_to_first_row(self.collections_table)
            else:
                self._fill_collection_form({}, [])
        self.run_worker("Chargement collections", do_load, done)

    def _set_collection_suggestion_target_rows(self, rows: List[CollectionItem]) -> None:
        previous_id = self._selected_id_from_table(self.collection_suggestion_target_collections_table)
        search_widget = getattr(self, "collection_suggestion_collection_search", None)
        search = search_widget.text().strip().casefold() if search_widget is not None else ""
        visible = [row for row in rows if not search or search in row.name.casefold()]
        table_rows = [[row.id, row.name, len(row.raw.get("seriesIds") or [])] for row in visible]
        self._set_table(
            self.collection_suggestion_target_collections_table,
            ["ID", "Nom", "Séries"],
            table_rows,
            selection_mode=QAbstractItemView.SingleSelection,
            row_data=visible,
        )
        restored = False
        if previous_id:
            for index, row in enumerate(visible):
                if row.id == previous_id:
                    self.collection_suggestion_target_collections_table.selectRow(index)
                    restored = True
                    break
        if visible and not restored:
            self._reset_table_to_first_row(self.collection_suggestion_target_collections_table)

    def load_collection_suggestion_targets(self) -> None:
        lib_id = self._library_id("collections")
        generation = self._next_series_load_generation("collection_suggestion_targets")

        def do_load() -> List[CollectionItem]:
            api = self.komga_api()
            rows = api.collections()
            if lib_id:
                series_ids = {s.id for s in api.series(lib_id)}
                rows = [row for row in rows if set(row.raw.get("seriesIds") or []) & series_ids]
            return rows

        def done(rows: List[CollectionItem]) -> None:
            if not self._is_current_series_load_generation("collection_suggestion_targets", generation):
                return
            self.collection_rows = rows
            self._set_collection_suggestion_target_rows(rows)
            self.log(f"✅ {len(rows)} collection(s) disponibles pour suggestions")

        self.run_worker("Chargement collections suggestions", do_load, done)

    def _fill_collection_form(self, data: Dict[str, Any], members: List[Dict[str, Any]]) -> None:
        self.collection_id.setText(safe_str(data.get("id")))
        self.collection_name.setText(safe_str(data.get("name")))
        self.collection_summary.setPlainText(safe_str(data.get("summary")))
        ids = data.get("seriesIds") if isinstance(data.get("seriesIds"), list) else [m.get("id") for m in members]
        self.collection_series_ids.setPlainText(id_lines(ids))
        self.collection_member_rows = list(members)
        self._set_table(
            self.collection_members_table,
            self._series_table_headers(),
            [self._series_table_row(member) for member in members],
        )
        self.collection_payload_preview.setPlainText(json_text(self._collection_payload_from_form()))

    def use_selected_collection(self) -> None:
        cid = self._selected_id_from_table(self.collections_table)
        if not cid:
            return
        generation = self._next_series_load_generation("collection_detail")
        def do_load() -> Dict[str, Any]:
            api = self.komga_api()
            data = api.get_collection(cid)
            members = api.collection_series(cid)
            return {"data": data, "members": members}
        def done(bundle: Dict[str, Any]) -> None:
            if not self._is_current_series_load_generation("collection_detail", generation):
                return
            self._fill_collection_form(bundle["data"], bundle["members"])
            self.backup.save_json("session", "collection", cid, bundle, "chargement collection")
        self.run_worker("Détail collection", do_load, done)

    def add_selected_series_to_collection_form(self) -> None:
        sid = self._selected_id_from_table(self.series_table)
        if not sid:
            QMessageBox.warning(self, "Série", "Aucune série sélectionnée dans l'explorateur")
            return
        self._add_series_ids_to_collection_form([sid])

    def load_collection_library_series(self) -> None:
        lib_id = self._library_id("collections")
        search_widget = getattr(self, "collection_series_search", None)
        search = search_widget.text().strip() if search_widget is not None else ""
        bulk_search_widget = getattr(self, "collection_bulk_series_search", None)
        bulk_search = bulk_search_widget.text().strip() if bulk_search_widget is not None else search
        without_collection = (
            self.collection_series_without_collection.isChecked()
            if hasattr(self, "collection_series_without_collection")
            else False
        )
        bulk_without_collection = (
            self.collection_bulk_without_collection.isChecked()
            if hasattr(self, "collection_bulk_without_collection")
            else False
        )
        generation = self._next_series_load_generation("collections_library_series")
        def do_load() -> Dict[str, Any]:
            api = self.komga_api()
            rows = self._filter_global_series_visibility(api.series(lib_id or None, search=search))
            bulk_rows = rows if bulk_search == search else self._filter_global_series_visibility(api.series(lib_id or None, search=bulk_search))
            if without_collection or bulk_without_collection:
                linked_series_ids: set[str] = set()
                for collection in api.collections():
                    for series_id in collection.raw.get("seriesIds") or []:
                        series_id = safe_str(series_id)
                        if series_id:
                            linked_series_ids.add(series_id)
                if without_collection:
                    rows = [row for row in rows if self._record_id(row) not in linked_series_ids]
                if bulk_without_collection:
                    bulk_rows = [row for row in bulk_rows if self._record_id(row) not in linked_series_ids]
            return {"rows": rows, "bulk_rows": bulk_rows, "without_collection": without_collection, "bulk_without_collection": bulk_without_collection}
        def done(result: Dict[str, Any]) -> None:
            if not self._is_current_series_load_generation("collections_library_series", generation):
                return
            rows = list(result.get("rows") or [])
            bulk_rows = list(result.get("bulk_rows") or rows)
            self.collection_library_series_rows = rows
            self.collection_bulk_series_rows = bulk_rows
            headers = self._series_table_headers()
            table_rows = [self._series_table_row(row) for row in rows]
            bulk_table_rows = [self._series_table_row(row) for row in bulk_rows]
            if hasattr(self, "collection_library_series_table"):
                self._set_table(self.collection_library_series_table, headers, table_rows)
                self._reset_table_to_first_row(self.collection_library_series_table, select=bool(rows))
            if hasattr(self, "collection_bulk_series_table"):
                self._set_table(
                    self.collection_bulk_series_table,
                    headers,
                    bulk_table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
            self.log(f"✅ {len(rows)} séries disponibles pour collections")
        self.run_worker("Chargement séries pour collections", do_load, done)

    def _add_series_ids_to_collection_form(self, series_ids: List[str]) -> int:
        ids = ids_from_text(self.collection_series_ids.toPlainText())
        added = 0
        for sid in series_ids:
            if sid and sid not in ids:
                ids.append(sid)
                added += 1
        self.collection_series_ids.setPlainText("\n".join(ids))
        self.collection_payload_preview.setPlainText(json_text(self._collection_payload_from_form()))
        self.log(f"✅ {added} série(s) ajoutée(s) au formulaire collection")
        return added

    def add_collection_series_selection_to_form(self) -> None:
        ids = self._selected_ids_from_table(self.collection_library_series_table)
        if not ids:
            QMessageBox.warning(self, "Collection", "Aucune série sélectionnée")
            return
        self._add_series_ids_to_collection_form(ids)

    def add_collection_bulk_series_selection_to_form(self) -> None:
        ids = self._selected_ids_from_table(self.collection_bulk_series_table)
        if not ids:
            QMessageBox.warning(self, "Collection", "Aucune série sélectionnée")
            return
        self._add_series_ids_to_collection_form(ids)

    def add_collection_series_selection_and_update(self) -> None:
        if not self.collection_id.text().strip():
            QMessageBox.warning(self, "Collection", "Ouvre d'abord une collection")
            return
        if self._add_series_ids_to_collection_form(self._selected_ids_from_table(self.collection_library_series_table)):
            self.update_collection()

    def add_collection_bulk_series_selection_and_update(self) -> None:
        if not self.collection_id.text().strip():
            QMessageBox.warning(self, "Collection", "Ouvre d'abord une collection")
            return
        if self._add_series_ids_to_collection_form(self._selected_ids_from_table(self.collection_bulk_series_table)):
            self.update_collection()

    def create_collection_from_series_selection(self, name_edit: QLineEdit, source_table: QTableWidget) -> None:
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Collection", "Nom de collection vide")
            return
        series_ids = ids_from_text(id_lines(self._selected_ids_from_table(source_table)))
        payload = {
            "name": name,
            "ordered": False,
            "summary": "",
            "seriesIds": series_ids,
        }
        if self.simulation_enabled():
            self.log(f"Simulation active : collection non créée ({name})")
            return
        def done(_result: Any) -> None:
            self.log(f"✅ Collection créée : {name} ({len(series_ids)} série(s))")
            name_edit.clear()
            self.load_collections()
        self.run_worker("Création collection", lambda: self.komga_api().create_collection(payload), done)

    def _book_path_candidates(self, book: Any) -> List[str]:
        raw = book if isinstance(book, dict) else getattr(book, "raw", {})
        raw = raw if isinstance(raw, dict) else {}
        paths: List[str] = []

        def add(value: Any) -> None:
            text = safe_str(value).strip()
            if text and text not in paths:
                paths.append(text)

        for key in ("url", "path", "filePath", "file", "filename"):
            add(raw.get(key))
        media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
        for key in ("url", "path", "filePath", "file", "filename"):
            add(media.get(key))
        files = media.get("files") if isinstance(media.get("files"), list) else []
        for file_info in files:
            if isinstance(file_info, dict):
                for key in ("url", "path", "filePath", "file", "filename"):
                    add(file_info.get(key))
        return paths

    @staticmethod
    def _normalized_path_text(path: str, *, ignore_case: bool) -> str:
        text = safe_str(path).replace("\\", "/")
        while "//" in text:
            text = text.replace("//", "/")
        return text.casefold() if ignore_case else text

    @staticmethod
    def _path_segments(path: str, *, ignore_case: bool) -> List[str]:
        text = MainWindow._normalized_path_text(path, ignore_case=ignore_case)
        return [part for part in text.split("/") if part]

    @staticmethod
    def _path_segments_original(path: str) -> List[str]:
        text = MainWindow._normalized_path_text(path, ignore_case=False)
        return [part for part in text.split("/") if part]

    def _path_matches_collection_suggestion(self, path: str, query: str, mode: str, *, ignore_case: bool) -> bool:
        haystack = self._normalized_path_text(path, ignore_case=ignore_case)
        needle = self._normalized_path_text(query, ignore_case=ignore_case).strip("/")
        if not needle:
            return True
        if mode == "starts":
            return haystack.startswith(self._normalized_path_text(query, ignore_case=ignore_case))
        if mode == "segment":
            return needle in self._path_segments(path, ignore_case=ignore_case)
        return needle in haystack

    def _collection_suggestion_group_key(self, path: str, query: str, group_mode: str, *, ignore_case: bool) -> tuple[str, str]:
        normalized = self._normalized_path_text(path, ignore_case=False)
        parts = [part for part in normalized.split("/") if part]
        if group_mode == "anchor":
            original_parts = self._path_segments_original(path)
            compare_parts = self._path_segments(path, ignore_case=ignore_case)
            query_parts = self._path_segments(query, ignore_case=ignore_case)
            if not query_parts:
                name = query.strip().strip("/\\") or "Suggestion collection"
                return ("__single__", name)
            for index in range(0, max(0, len(compare_parts) - len(query_parts) + 1)):
                if compare_parts[index:index + len(query_parts)] == query_parts:
                    end = index + len(query_parts)
                    key_parts = original_parts[:end]
                    return ("/".join(key_parts), original_parts[end - 1])
            needle = query_parts[-1]
            for index, part in enumerate(compare_parts):
                if needle and needle in part:
                    key_parts = original_parts[:index + 1]
                    return ("/".join(key_parts), original_parts[index])
        if group_mode == "single":
            name = query.strip().strip("/\\") or "Suggestion collection"
            return ("__single__", name)
        if not parts:
            return ("", "Chemin inconnu")
        if group_mode == "folder":
            folder = parts[-2] if len(parts) >= 2 else parts[0]
            key = "/".join(parts[:-1]) if len(parts) >= 2 else folder
            return (key, folder)
        file_parent_parts = parts[:-1]
        parent_parts = file_parent_parts[:-1] if len(file_parent_parts) >= 2 else file_parent_parts
        if not parent_parts:
            folder = file_parent_parts[-1] if file_parent_parts else parts[0]
            return (folder, folder)
        return ("/".join(parent_parts), parent_parts[-1])

    @staticmethod
    def _collection_suggestion_folder_parts(path: str) -> List[str]:
        parts = MainWindow._path_segments_original(path)
        if not parts:
            return []
        last = parts[-1]
        if "." in last:
            return parts[:-1]
        return parts

    @classmethod
    def _collection_suggestion_seed_keys_for_path(cls, path: str) -> List[str]:
        folder_parts = cls._collection_suggestion_folder_parts(path)
        keys: List[str] = []
        if len(folder_parts) >= 2:
            key = "/".join(folder_parts)
            if key:
                keys.append(key)
        return keys

    @classmethod
    def _collection_suggestion_best_seed_key(cls, path: str, seed_keys: set[str]) -> str:
        folder_parts = cls._collection_suggestion_folder_parts(path)
        candidates = ["/".join(folder_parts[:size]) for size in range(len(folder_parts), 1, -1)]
        for candidate in candidates:
            if candidate in seed_keys:
                return candidate
        return ""

    @staticmethod
    def _collection_suggestion_name_tokens(name: str) -> set[str]:
        ignored = {
            "comic", "comics", "collection", "collections", "univers", "universe",
            "serie", "series", "série", "séries", "label", "ere", "ère",
            "super", "heros", "héros", "super-heros", "super-héros",
        }
        tokens: set[str] = set()
        for token in re.split(r"[^0-9a-zA-ZÀ-ÿ]+", safe_str(name).casefold()):
            token = token.strip()
            if len(token) >= 3 and token not in ignored:
                tokens.add(token)
        return tokens

    @staticmethod
    def _collection_suggestion_generic_segment(segment: str) -> bool:
        normalized = safe_str(segment).casefold().strip()
        return normalized in {
            "comics", "comic", "super-héros", "super-heros", "super héros", "super heros",
            "dc", "marvel", "manga", "mangas", "bd", "bds", "books", "livres",
        }

    @classmethod
    def _collection_suggestion_anchor_key_for_path(cls, path: str, tokens: set[str]) -> str:
        if not tokens:
            return ""
        parts = cls._collection_suggestion_folder_parts(path)
        if not parts:
            return ""
        folded = [part.casefold() for part in parts]
        for index in range(len(parts) - 1, -1, -1):
            if folded[index] in tokens:
                return "/".join(parts[:index + 1])
        for index in range(len(parts) - 1, -1, -1):
            if any(token in folded[index] for token in tokens):
                return "/".join(parts[:index + 1])
        return ""

    @classmethod
    def _collection_suggestion_common_seed_keys(cls, paths: List[str]) -> set[str]:
        folders = [cls._collection_suggestion_folder_parts(path) for path in paths]
        folders = [parts for parts in folders if parts]
        if not folders:
            return set()
        prefix = list(folders[0])
        for parts in folders[1:]:
            size = 0
            for left, right in zip(prefix, parts):
                if left.casefold() != right.casefold():
                    break
                size += 1
            prefix = prefix[:size]
            if not prefix:
                break
        if len(prefix) < 4 or cls._collection_suggestion_generic_segment(prefix[-1]):
            return set()
        return {"/".join(prefix)}

    @staticmethod
    def _collection_suggestion_book_series(book: Any) -> tuple[str, str]:
        if isinstance(book, dict):
            series = book.get("series") if isinstance(book.get("series"), dict) else {}
            metadata = series.get("metadata") if isinstance(series.get("metadata"), dict) else {}
            series_id = safe_str(book.get("seriesId") or series.get("id"))
            title = safe_str(metadata.get("title") or series.get("name") or series.get("title") or series_id)
            return series_id, title
        raw = getattr(book, "raw", {}) if isinstance(getattr(book, "raw", {}), dict) else {}
        series_id = safe_str(getattr(book, "series_id", "") or raw.get("seriesId") or (raw.get("series") or {}).get("id"))
        series_title = safe_str(raw.get("seriesTitle") or series_id)
        series = raw.get("series") if isinstance(raw.get("series"), dict) else {}
        metadata = series.get("metadata") if isinstance(series.get("metadata"), dict) else {}
        series_title = safe_str(series_title or metadata.get("title") or series.get("name") or series.get("title") or series_id)
        return series_id, series_title

    @staticmethod
    def _collection_suggestion_rule_label(match_mode: str, group_mode: str, query: str) -> str:
        match_labels = {
            "contains": "chemin contient",
            "starts": "chemin commence par",
            "segment": "segment exact",
        }
        group_labels = {
            "anchor": "dossier recherché",
            "single": "une collection",
            "parent": "parent commun",
            "folder": "dossier final",
        }
        needle = query.strip() or "*"
        return f"{group_labels.get(group_mode, group_mode)} / {match_labels.get(match_mode, match_mode)} {needle}"

    @staticmethod
    def _collection_name_score(suggestion_name: str, collection_name: str) -> int:
        suggestion = safe_str(suggestion_name).casefold().strip()
        collection = safe_str(collection_name).casefold().strip()
        if not suggestion or not collection:
            return 0
        score = 0
        if suggestion == collection:
            score += 100
        elif suggestion in collection or collection in suggestion:
            score += 60
        suggestion_tokens = {token for token in re.split(r"[^0-9a-zA-ZÀ-ÿ]+", suggestion) if token}
        collection_tokens = {token for token in re.split(r"[^0-9a-zA-ZÀ-ÿ]+", collection) if token}
        if suggestion_tokens and collection_tokens:
            shared = suggestion_tokens & collection_tokens
            score += int((len(shared) / max(len(suggestion_tokens), 1)) * 40)
        return min(score, 140)

    def _collection_suggestion_candidates(
        self,
        row: Dict[str, Any],
        collections: List[CollectionItem],
    ) -> List[Dict[str, Any]]:
        suggestion_ids = {safe_str(series_id) for series_id in row.get("series_ids") or [] if safe_str(series_id)}
        total = len(suggestion_ids)
        candidates: List[Dict[str, Any]] = []
        for collection in collections:
            collection_ids = {
                safe_str(series_id)
                for series_id in (collection.raw.get("seriesIds") or [])
                if safe_str(series_id)
            }
            present_ids = suggestion_ids & collection_ids
            missing_ids = suggestion_ids - collection_ids
            name_score = self._collection_name_score(safe_str(row.get("name")), collection.name)
            if not present_ids and name_score <= 0:
                continue
            present = len(present_ids)
            missing = len(missing_ids)
            coverage = int((present / total) * 100) if total else 0
            score = (present * 1000) + (coverage * 5) + name_score
            candidates.append({
                "id": collection.id,
                "name": collection.name,
                "series_ids": list(collection_ids),
                "present_ids": sorted(present_ids),
                "missing_ids": sorted(missing_ids),
                "present": present,
                "missing": missing,
                "coverage": coverage,
                "name_score": name_score,
                "score": score,
            })
        candidates.sort(key=lambda item: (-int(item.get("score", 0)), int(item.get("missing", 0)), safe_str(item.get("name")).casefold()))
        for index, candidate in enumerate(candidates):
            candidate["recommended"] = index == 0
        return candidates[:80]

    def analyze_collection_path_suggestions(self) -> None:
        lib_id = self._library_id("collections")
        target_id = self._selected_id_from_table(self.collection_suggestion_target_collections_table)
        query = self.collection_suggestion_path_search.text().strip() if hasattr(self, "collection_suggestion_path_search") else ""
        source_mode = self.collection_suggestion_source_mode.currentData() if hasattr(self, "collection_suggestion_source_mode") else "combined"
        match_mode = self.collection_suggestion_match_mode.currentData() if hasattr(self, "collection_suggestion_match_mode") else "contains"
        group_mode = self.collection_suggestion_group_mode.currentData() if hasattr(self, "collection_suggestion_group_mode") else "anchor"
        ignore_case = self.collection_suggestion_ignore_case.isChecked() if hasattr(self, "collection_suggestion_ignore_case") else True
        one_per_series = self.collection_suggestion_one_per_series.isChecked() if hasattr(self, "collection_suggestion_one_per_series") else True
        if not target_id:
            QMessageBox.warning(self, "Suggestions collections", "Sélectionne d'abord une collection cible dans la liste de gauche.")
            return
        if str(source_mode) == "manual" and not query:
            QMessageBox.warning(self, "Suggestions collections", "Saisis un chemin ou un dossier à rechercher.")
            return
        generation = self._next_series_load_generation("collection_suggestions")
        self.collection_suggestion_rows = []
        if hasattr(self, "collection_suggestion_table"):
            self._set_table(
                self.collection_suggestion_table,
                ["Proposition", "Source", "Séries à ajouter", "Tomes", "Collection cible", "Déjà dedans", "À ajouter", "Action", "Series IDs"],
                [],
                selection_mode=QAbstractItemView.SingleSelection,
            )
        if hasattr(self, "collection_suggestion_detail"):
            self.collection_suggestion_detail.setPlainText("Analyse des suggestions en cours...")

        def do_load() -> Dict[str, Any]:
            api = self.komga_api()
            current = api.get_collection(target_id)
            target_name = safe_str(current.get("name")) or target_id
            target_series_ids = ids_from_text(id_lines(current.get("seriesIds") if isinstance(current.get("seriesIds"), list) else []))
            if not target_series_ids:
                members = api.collection_series(target_id)
                target_series_ids = [safe_str(row.get("id")) for row in members if safe_str(row.get("id"))]
            if lib_id:
                library_series_ids = {safe_str(series.id) for series in api.series(lib_id)}
                target_series_ids = [series_id for series_id in target_series_ids if series_id in library_series_ids]
            target_series_set = set(target_series_ids)
            books = api.books(library_id=lib_id or None, page_size=1000)
            groups: Dict[str, Dict[str, Any]] = {}
            seed_keys: set[str] = set()
            seed_paths: List[str] = []
            seed_path_count = 0
            target_tokens = self._collection_suggestion_name_tokens(target_name)
            for book in books:
                series_id, _series_title = self._collection_suggestion_book_series(book)
                if series_id not in target_series_set:
                    continue
                for path in self._book_path_candidates(book):
                    seed_path_count += 1
                    seed_paths.append(path)
                    anchor_key = self._collection_suggestion_anchor_key_for_path(path, target_tokens)
                    if anchor_key:
                        seed_keys.add(anchor_key)
            if not seed_keys:
                seed_keys.update(self._collection_suggestion_common_seed_keys(seed_paths))
            path_count = 0
            matched_book_count = 0
            rule_label = self._collection_suggestion_rule_label(str(match_mode), str(group_mode), query)
            source_label = {
                "collection": "chemins déjà présents dans la collection",
                "manual": "recherche chemin saisie",
                "combined": "recherche chemin saisie" if query else "chemins déjà présents dans la collection",
            }.get(str(source_mode), str(source_mode))
            require_seed = str(source_mode) == "collection" or (str(source_mode) == "combined" and not query)
            require_query = str(source_mode) == "manual" or (str(source_mode) == "combined" and bool(query))
            for book in books:
                series_id, series_title = self._collection_suggestion_book_series(book)
                if not series_id:
                    continue
                if series_id in target_series_set:
                    continue
                book_paths = self._book_path_candidates(book)
                path_count += len(book_paths)
                matched_for_series_in_group: set[str] = set()
                for path in book_paths:
                    matched_seed_key = self._collection_suggestion_best_seed_key(path, seed_keys) if seed_keys else ""
                    if require_seed and not matched_seed_key:
                        continue
                    if require_query and not self._path_matches_collection_suggestion(path, query, str(match_mode), ignore_case=ignore_case):
                        continue
                    if query:
                        key, name = self._collection_suggestion_group_key(path, query, str(group_mode), ignore_case=ignore_case)
                    elif matched_seed_key:
                        key = matched_seed_key
                        name = matched_seed_key.split("/")[-1]
                    else:
                        key, name = self._collection_suggestion_group_key(path, query, str(group_mode), ignore_case=ignore_case)
                    if one_per_series and key in matched_for_series_in_group:
                        continue
                    matched_for_series_in_group.add(key)
                    matched_book_count += 1
                    group = groups.setdefault(key, {
                        "name": name,
                        "key": key,
                        "rule": f"{source_label} / {rule_label}",
                        "target_id": target_id,
                        "target_name": target_name,
                        "target_series_count": len(target_series_set),
                        "series_ids": [],
                        "series_titles": {},
                        "paths": [],
                        "book_count": 0,
                    })
                    if series_id not in group["series_ids"]:
                        group["series_ids"].append(series_id)
                    group["series_titles"][series_id] = series_title
                    group["book_count"] += 1
                    if len(group["paths"]) < 80:
                        group["paths"].append(path)
            rows = sorted(groups.values(), key=lambda row: (-len(row["series_ids"]), str(row["name"]).casefold()))
            for row in rows:
                row["recommended_collection"] = {"id": target_id, "name": target_name}
                row["recommended_present"] = len(target_series_set)
                row["recommended_missing"] = len(row.get("series_ids") or [])
                row["recommended_action"] = "Ajouter à la collection"
            return {
                "rows": rows,
                "books": len(books),
                "paths": path_count,
                "matched_books": matched_book_count,
                "seed_paths": seed_path_count,
                "seed_keys": len(seed_keys),
                "target_name": target_name,
                "target_series": len(target_series_set),
            }

        def done(result: Dict[str, Any]) -> None:
            if not self._is_current_series_load_generation("collection_suggestions", generation):
                return
            rows = list(result.get("rows") or [])
            self.collection_suggestion_rows = rows
            table_rows = [
                [
                    row.get("name", ""),
                    row.get("rule", ""),
                    len(row.get("series_ids") or []),
                    row.get("book_count", 0),
                    safe_str((row.get("recommended_collection") or {}).get("name")) or "Aucune",
                    row.get("recommended_present", 0),
                    row.get("recommended_missing", 0),
                    row.get("recommended_action", ""),
                    " | ".join(row.get("series_ids") or []),
                ]
                for row in rows
            ]
            self._set_table(
                self.collection_suggestion_table,
                ["Proposition", "Source", "Séries à ajouter", "Tomes", "Collection cible", "Déjà dedans", "À ajouter", "Action", "Series IDs"],
                table_rows,
                selection_mode=QAbstractItemView.SingleSelection,
                row_data=rows,
            )
            self.log(
                f"✅ Suggestions pour {result.get('target_name', '')} : {len(rows)} groupe(s), "
                f"{result.get('matched_books', 0)} tome(s) candidat(s), {result.get('paths', 0)} chemin(s) inspecté(s), "
                f"{result.get('seed_keys', 0)} dossier(s) de référence"
            )
            if rows:
                self.collection_suggestion_table.selectRow(0)
            else:
                self.collection_suggestion_detail.setPlainText(
                    f"Aucune suggestion pour {result.get('target_name', 'la collection sélectionnée')}."
                )

        self.run_worker("Suggestions collections", do_load, done)

    def _selected_collection_suggestion(self) -> Dict[str, Any]:
        row = self._selected_row_data(self.collection_suggestion_table) if hasattr(self, "collection_suggestion_table") else None
        return row if isinstance(row, dict) else {}

    def _selected_collection_suggestion_target(self) -> Dict[str, Any]:
        row = self._selected_row_data(self.collection_suggestion_target_collections_table) if hasattr(self, "collection_suggestion_target_collections_table") else None
        if isinstance(row, dict):
            return row
        if isinstance(row, CollectionItem):
            return {"id": row.id, "name": row.name, "seriesIds": row.raw.get("seriesIds") or []}
        return {}

    def update_collection_suggestion_detail(self) -> None:
        row = self._selected_collection_suggestion()
        if not row:
            if hasattr(self, "collection_suggestion_detail"):
                target = self._selected_collection_suggestion_target()
                if target:
                    self.collection_suggestion_detail.setPlainText(
                        f"Collection sélectionnée : {target.get('name', '')}\n"
                        "Clique sur Analyser pour chercher des séries à ajouter."
                    )
                else:
                    self.collection_suggestion_detail.setPlainText(
                        "Sélectionne une collection à gauche, puis clique sur Analyser."
                    )
            return
        suggested_name = safe_str(row.get("name"))
        if hasattr(self, "collection_suggestion_name"):
            self.collection_suggestion_name.setText(suggested_name)
        self._update_collection_suggestion_detail_text(row)

    def update_collection_suggestion_target_detail(self) -> None:
        row = self._selected_collection_suggestion()
        if row:
            self._update_collection_suggestion_detail_text(row)

    def _update_collection_suggestion_detail_text(self, row: Dict[str, Any]) -> None:
        if not hasattr(self, "collection_suggestion_detail"):
            return
        suggested_name = safe_str(row.get("name"))
        titles = row.get("series_titles") if isinstance(row.get("series_titles"), dict) else {}
        series_ids = [safe_str(series_id) for series_id in row.get("series_ids") or [] if safe_str(series_id)]
        target_name = safe_str(row.get("target_name") or (row.get("recommended_collection") or {}).get("name"))
        target_count = int(row.get("target_series_count", 0) or row.get("recommended_present", 0) or 0)
        target_summary = (
            f"{target_name} : {target_count} série(s) déjà dedans, "
            f"{len(series_ids)} série(s) proposée(s) en plus"
        )
        series_lines: List[str] = []
        for series_id in series_ids[:200]:
            series_lines.append(f"- {titles.get(series_id, series_id)} ({series_id}) : à ajouter")
        if len(series_ids) > 200:
            series_lines.append(f"- ... {len(series_ids) - 200} autre(s) série(s)")
        path_lines = [f"- {path}" for path in (row.get("paths") or [])[:60]]
        if len(row.get("paths") or []) > 60:
            path_lines.append(f"- ... {len(row.get('paths') or []) - 60} autre(s) chemin(s)")
        lines = [
            f"Suggestion : ajouter {len(series_ids)} série(s) à {target_name}",
            f"Groupe proposé : {suggested_name}",
            f"Source : {row.get('rule', '')}",
            f"Dossier / motif : {row.get('key', '')}",
            f"Cible analysée : {target_summary}",
            f"Tomes correspondants : {row.get('book_count', 0)}",
            f"Action recommandée : {row.get('recommended_action', '')}",
            "",
            "Séries à ajouter :",
            *(series_lines or ["- Aucune"]),
            "",
            "Chemins exemples :",
            *(path_lines or ["- Aucun chemin disponible"]),
        ]
        self.collection_suggestion_detail.setPlainText("\n".join(lines))

    def create_collection_from_selected_suggestion(self) -> None:
        row = self._selected_collection_suggestion()
        if not row:
            QMessageBox.warning(self, "Collection", "Aucune suggestion sélectionnée")
            return
        name = self.collection_suggestion_name.text().strip() if hasattr(self, "collection_suggestion_name") else ""
        if not name:
            name = safe_str(row.get("name")) or "Suggestion collection"
        series_ids = ids_from_text(id_lines(row.get("series_ids") or []))
        payload = {"name": name, "ordered": False, "summary": "", "seriesIds": series_ids}
        if self.simulation_enabled():
            self.log(f"Simulation active : collection suggérée non créée ({name})")
            return
        def done(_result: Any) -> None:
            self.log(f"✅ Collection suggérée créée : {name} ({len(series_ids)} série(s))")
            self.load_collections()
            self.analyze_collection_path_suggestions()
        self.run_worker("Création collection suggérée", lambda: self.komga_api().create_collection(payload), done)

    def add_selected_collection_suggestion_to_target(self) -> None:
        row = self._selected_collection_suggestion()
        cid = self._selected_id_from_table(self.collection_suggestion_target_collections_table)
        if not row:
            QMessageBox.warning(self, "Collection", "Aucune suggestion sélectionnée")
            return
        if not cid:
            QMessageBox.warning(self, "Collection", "Aucune collection cible sélectionnée")
            return
        series_ids = ids_from_text(id_lines(row.get("series_ids") or []))
        simulation = self.simulation_enabled()
        def do_update() -> Dict[str, Any]:
            api = self.komga_api()
            current = api.get_collection(cid)
            ids = ids_from_text(id_lines(current.get("seriesIds") if isinstance(current.get("seriesIds"), list) else []))
            if not ids:
                members = api.collection_series(cid)
                ids = [safe_str(x.get("id")) for x in members if safe_str(x.get("id"))]
            before = list(ids)
            for sid in series_ids:
                if sid and sid not in ids:
                    ids.append(sid)
            added = len(ids) - len(before)
            payload = {
                "name": safe_str(current.get("name")),
                "ordered": False,
                "summary": safe_str(current.get("summary")),
                "seriesIds": ids,
            }
            if added and not simulation:
                self.backup.save_json("operation", "collection", cid, current, "avant ajout suggestion collection")
                api.update_collection(cid, payload)
            return {"added": added, "name": safe_str(current.get("name"))}
        def done(result: Dict[str, Any]) -> None:
            self.log(f"✅ Suggestion ajoutée à collection : {result.get('added', 0)} série(s)")
            self.analyze_collection_path_suggestions()
            self.load_collections()
        self.run_worker("Ajout suggestion collection", do_update, done)

    def add_selected_series_to_selected_collections(self) -> None:
        series_ids = self._selected_ids_from_table(self.collection_library_series_table)
        collection_ids = self._selected_ids_from_table(self.collection_target_collections_table)
        if not series_ids:
            QMessageBox.warning(self, "Collection", "Aucune série sélectionnée")
            return
        if not collection_ids:
            QMessageBox.warning(self, "Collection", "Aucune collection cible sélectionnée")
            return
        if not self._confirm_membership_bulk_action(
            member_label="Séries sélectionnées",
            member_count=len(series_ids),
            target_label="Collections cibles",
            target_count=len(collection_ids),
            action="Ajouter les séries aux collections",
        ):
            return
        simulation = self.simulation_enabled()
        def do_update() -> Dict[str, Any]:
            api = self.komga_api()
            rows: List[Dict[str, Any]] = []
            for cid in collection_ids:
                current = api.get_collection(cid)
                ids = ids_from_text(id_lines(current.get("seriesIds") if isinstance(current.get("seriesIds"), list) else []))
                if not ids:
                    members = api.collection_series(cid)
                    ids = [safe_str(x.get("id")) for x in members if safe_str(x.get("id"))]
                before = list(ids)
                for sid in series_ids:
                    if sid and sid not in ids:
                        ids.append(sid)
                added = len(ids) - len(before)
                payload = {
                    "name": safe_str(current.get("name")),
                    "ordered": False,
                    "summary": safe_str(current.get("summary")),
                    "seriesIds": ids,
                }
                if added and not simulation:
                    self.backup.save_json("operation", "collection", cid, current, "avant ajout série(s) collection")
                    api.update_collection(cid, payload)
                rows.append({"id": cid, "name": safe_str(current.get("name")), "added": added, "simulation": simulation})
            return {"rows": rows}
        def done(result: Dict[str, Any]) -> None:
            rows = result.get("rows") or []
            added = sum(int(row.get("added") or 0) for row in rows)
            self.log(f"✅ Séries ajoutées aux collections : {added} ajout(s), {len(rows)} collection(s)")
            self.load_collections()
            self.load_collections_for_selected_series()
        self.run_worker("Ajout série(s) aux collections", do_update, done)

    def add_collection_bulk_series_to_selected_collections(self) -> None:
        self._update_bulk_series_collection_links(remove=False)

    def remove_collection_bulk_series_from_selected_collections(self) -> None:
        self._update_bulk_series_collection_links(remove=True)

    def _update_bulk_series_collection_links(self, *, remove: bool) -> None:
        series_ids = self._selected_ids_from_table(self.collection_bulk_series_table)
        collection_ids = self._selected_ids_from_table(self.collection_bulk_target_collections_table)
        if not series_ids:
            QMessageBox.warning(self, "Collection", "Aucune série sélectionnée")
            return
        if not collection_ids:
            QMessageBox.warning(self, "Collection", "Aucune collection cible sélectionnée")
            return
        if not self._confirm_membership_bulk_action(
            member_label="Séries sélectionnées",
            member_count=len(series_ids),
            target_label="Collections cibles",
            target_count=len(collection_ids),
            action="Retirer" if remove else "Ajouter",
        ):
            return
        simulation = self.simulation_enabled()
        selected = set(series_ids)
        def do_update() -> Dict[str, Any]:
            api = self.komga_api()
            rows: List[Dict[str, Any]] = []
            for cid in collection_ids:
                current = api.get_collection(cid)
                ids = ids_from_text(id_lines(current.get("seriesIds") if isinstance(current.get("seriesIds"), list) else []))
                if not ids:
                    members = api.collection_series(cid)
                    ids = [safe_str(x.get("id")) for x in members if safe_str(x.get("id"))]
                before = list(ids)
                if remove:
                    ids = [sid for sid in ids if sid not in selected]
                else:
                    for sid in series_ids:
                        if sid and sid not in ids:
                            ids.append(sid)
                changed = len(before) - len(ids) if remove else len(ids) - len(before)
                payload = {
                    "name": safe_str(current.get("name")),
                    "ordered": False,
                    "summary": safe_str(current.get("summary")),
                    "seriesIds": ids,
                }
                if changed and not simulation:
                    self.backup.save_json("operation", "collection", cid, current, "avant bulk collection")
                    api.update_collection(cid, payload)
                rows.append({"id": cid, "name": safe_str(current.get("name")), "changed": changed, "simulation": simulation})
            return {"rows": rows}
        def done(result: Dict[str, Any]) -> None:
            rows = result.get("rows") or []
            changed = sum(int(row.get("changed") or 0) for row in rows)
            action = "retirées des" if remove else "ajoutées aux"
            self.log(f"✅ Séries {action} collections : {changed} lien(s), {len(rows)} collection(s)")
            self.load_collections()
        self.run_worker("Bulk collections", do_update, done)

    def load_collections_for_selected_series(self) -> None:
        sid = (
            self._selected_id_from_table(getattr(self, "collection_library_series_table", self.series_table))
            or self._selected_id_from_table(self.series_table)
            or self._selected_id_from_table(self.bdt_komga_series_table)
        )
        if not sid:
            QMessageBox.warning(self, "Série", "Aucune série sélectionnée")
            return
        def done(rows: List[Dict[str, Any]]) -> None:
            table_rows = [[x.get("id", ""), x.get("name", "")] for x in rows]
            self._set_table(self.series_collections_table, ["ID", "Nom"], table_rows)
            if hasattr(self, "collection_series_collections_table"):
                self._set_table(self.collection_series_collections_table, ["ID", "Nom"], table_rows)
            self.log(f"✅ {len(rows)} collections associées à la série {sid}")
        self.run_worker("Collections de la série", lambda: self.komga_api().series_collections(sid), done)

    def move_collection_member(self, direction: int) -> None:
        ids = ids_from_text(self.collection_series_ids.toPlainText())
        row = self._selected_row_index(self.collection_members_table)
        if row < 0 or row >= len(ids):
            return
        new_row = max(0, min(len(ids) - 1, row + direction))
        if new_row == row:
            return
        ids[row], ids[new_row] = ids[new_row], ids[row]
        self.collection_series_ids.setPlainText("\n".join(ids))
        self.collection_payload_preview.setPlainText(json_text(self._collection_payload_from_form()))

    def _collection_payload_from_form(self) -> Dict[str, Any]:
        return {
            "name": self.collection_name.text().strip(),
            "ordered": False,
            "summary": self.collection_summary.toPlainText(),
            "seriesIds": ids_from_text(self.collection_series_ids.toPlainText()),
        }

    def create_collection(self) -> None:
        payload = self._collection_payload_from_form()
        self.collection_payload_preview.setPlainText(json_text(payload))
        if not str(payload.get("name") or "").strip():
            QMessageBox.warning(self, "Collection", "Le nom de la collection est obligatoire.")
            return
        if self.simulation_enabled():
            self.log("Simulation active : collection non créée")
            return
        if QMessageBox.question(
            self,
            "Créer la collection",
            f"Créer « {payload['name']} » avec {len(payload.get('seriesIds') or [])} série(s) ?",
        ) != QMessageBox.Yes:
            return
        self.run_worker("Création collection", lambda: self.komga_api().create_collection(payload), lambda r: self.log("✅ Collection créée"))

    def update_collection(self) -> None:
        cid = self.collection_id.text().strip()
        payload = self._collection_payload_from_form()
        self.collection_payload_preview.setPlainText(json_text(payload))
        if not cid:
            QMessageBox.warning(self, "Collection", "ID vide")
            return
        if self.simulation_enabled():
            self.log(f"Simulation active : collection {cid} non modifiée")
            return
        if QMessageBox.question(
            self,
            "Modifier la collection",
            f"Mettre à jour « {payload.get('name') or cid} » avec {len(payload.get('seriesIds') or [])} série(s) ?\n\n"
            "L'état actuel sera sauvegardé avant la modification.",
        ) != QMessageBox.Yes:
            return
        def do_update() -> Any:
            api = self.komga_api()
            current = api.get_collection(cid)
            self.backup.save_json("operation", "collection", cid, current, "avant PATCH collection")
            return api.update_collection(cid, payload)
        self.run_worker("Update collection", do_update, lambda r: self.log(f"✅ Collection modifiée : {cid}"))

    # ------------------------------------------------------------------
    # Readlists
    # ------------------------------------------------------------------
    def load_readlists(self) -> None:
        lib_id = self._library_id("readlists")
        search = self.readlist_search.text().strip().lower()
        generation = self._next_series_load_generation("readlists")
        self._next_series_load_generation("readlist_detail")
        def do_load() -> List[ReadlistItem]:
            api = self.komga_api()
            rows = api.readlists()
            if search:
                rows = [r for r in rows if search in r.name.lower()]
            if lib_id:
                book_ids = {b.id for b in api.books(lib_id)}
                rows = [r for r in rows if set(r.raw.get("bookIds") or []) & book_ids]
            return rows
        def done(rows: List[ReadlistItem]) -> None:
            if not self._is_current_series_load_generation("readlists", generation):
                return
            self.readlist_rows = rows
            table_rows = [[x.id, x.name, len(x.raw.get("bookIds") or [])] for x in rows]
            self._set_table(self.readlists_table, ["ID", "Nom", "Livres"], table_rows)
            if hasattr(self, "readlist_target_readlists_table"):
                self._set_table(
                    self.readlist_target_readlists_table,
                    ["ID", "Nom", "Livres"],
                    table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
            if hasattr(self, "readlist_bulk_target_readlists_table"):
                self._set_table(
                    self.readlist_bulk_target_readlists_table,
                    ["ID", "Nom", "Livres"],
                    table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
            self.log(f"✅ {len(rows)} readlists")
            if rows:
                self._reset_table_to_first_row(self.readlists_table)
            else:
                self._fill_readlist_form({}, [])
        self.run_worker("Chargement readlists", do_load, done)

    def _fill_readlist_form(self, data: Dict[str, Any], books: List[Dict[str, Any]]) -> None:
        self.readlist_id.setText(safe_str(data.get("id")))
        self.readlist_name.setText(safe_str(data.get("name")))
        self.readlist_summary.setPlainText(safe_str(data.get("summary")))
        ids = data.get("bookIds") if isinstance(data.get("bookIds"), list) else [b.get("id") for b in books]
        self.readlist_book_ids.setPlainText(id_lines(ids))
        self.readlist_book_rows = list(books)
        self._set_table(
            self.readlist_books_table,
            self._book_table_headers(include_series=True),
            [self._book_table_row(book, include_series=True) for book in books],
        )
        self.readlist_payload_preview.setPlainText(json_text(self._readlist_payload_from_form()))

    def use_selected_readlist(self) -> None:
        rid = self._selected_id_from_table(self.readlists_table)
        if not rid:
            return
        generation = self._next_series_load_generation("readlist_detail")
        def do_load() -> Dict[str, Any]:
            api = self.komga_api()
            data = api.get_readlist(rid)
            books = api.readlist_books(rid)
            return {"data": data, "books": books}
        def done(bundle: Dict[str, Any]) -> None:
            if not self._is_current_series_load_generation("readlist_detail", generation):
                return
            self._fill_readlist_form(bundle["data"], bundle["books"])
            self.backup.save_json("session", "readlist", rid, bundle, "chargement readlist")
        self.run_worker("Détail readlist", do_load, done)

    def add_selected_book_to_readlist_form(self) -> None:
        bid = self._selected_id_from_table(self.books_table)
        if not bid:
            QMessageBox.warning(self, "Livre", "Aucun livre sélectionné dans l'explorateur")
            return
        self._add_book_ids_to_readlist_form([bid])

    def load_readlist_library_series(self) -> None:
        lib_id = self._library_id("readlists")
        search_widget = getattr(self, "readlist_series_search", None)
        search = search_widget.text().strip() if search_widget is not None else ""
        generation = self._next_series_load_generation("readlists_library_series")
        def do_load() -> List[Any]:
            rows = self.komga_api().series(lib_id or None, search=search)
            return self._filter_global_series_visibility(rows)
        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("readlists_library_series", generation):
                return
            self.readlist_library_series_rows = rows
            if hasattr(self, "readlist_library_series_table"):
                self._set_table(self.readlist_library_series_table, self._series_table_headers(), [self._series_table_row(row) for row in rows])
                self._reset_table_to_first_row(self.readlist_library_series_table, select=bool(rows))
            self.log(f"✅ {len(rows)} séries disponibles pour readlists")
        self.run_worker("Chargement séries pour readlists", do_load, done)

    def load_readlist_books_for_selected_series(self) -> None:
        lib_id = self._library_id("readlists")
        sid = self._selected_id_from_table(getattr(self, "readlist_library_series_table", self.series_table)) or self._selected_id_from_table(self.series_table)
        if not sid:
            QMessageBox.warning(self, "Readlist", "Aucune série sélectionnée")
            return
        bulk_search_widget = getattr(self, "readlist_bulk_book_search", None)
        bulk_search = bulk_search_widget.text().strip().casefold() if bulk_search_widget is not None else ""
        bulk_without_readlist = (
            self.readlist_bulk_without_readlist.isChecked()
            if hasattr(self, "readlist_bulk_without_readlist")
            else False
        )
        generation = self._next_series_load_generation("readlists_library_books")
        def do_load() -> Dict[str, Any]:
            api = self.komga_api()
            rows = api.books(library_id=lib_id or None, series_id=sid, page_size=1000)
            bulk_rows = rows
            if bulk_search:
                bulk_rows = [
                    row for row in bulk_rows
                    if bulk_search in " ".join(str(value) for value in self._book_table_row(row, include_series=True)).casefold()
                ]
            if bulk_without_readlist:
                linked_book_ids: set[str] = set()
                for readlist in api.readlists():
                    for book_id in readlist.raw.get("bookIds") or []:
                        book_id = safe_str(book_id)
                        if book_id:
                            linked_book_ids.add(book_id)
                bulk_rows = [row for row in bulk_rows if self._record_id(row) not in linked_book_ids]
            return {"rows": rows, "bulk_rows": bulk_rows}
        def done(result: Dict[str, Any]) -> None:
            if not self._is_current_series_load_generation("readlists_library_books", generation):
                return
            rows = list(result.get("rows") or [])
            bulk_rows = list(result.get("bulk_rows") or rows)
            self.readlist_library_book_rows = rows
            self.readlist_bulk_book_rows = bulk_rows
            headers = self._book_table_headers(include_series=True)
            table_rows = [self._book_table_row(row, include_series=True) for row in rows]
            bulk_table_rows = [self._book_table_row(row, include_series=True) for row in bulk_rows]
            if hasattr(self, "readlist_library_books_table"):
                self._set_table(
                    self.readlist_library_books_table,
                    headers,
                    table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
                self._reset_table_to_first_row(self.readlist_library_books_table, select=bool(rows))
            if hasattr(self, "readlist_bulk_books_table"):
                self._set_table(
                    self.readlist_bulk_books_table,
                    headers,
                    bulk_table_rows,
                    selection_mode=QAbstractItemView.ExtendedSelection,
                )
            self.log(f"✅ {len(rows)} tome(s) disponibles pour readlists")
        self.run_worker("Chargement tomes pour readlists", do_load, done)

    def _add_book_ids_to_readlist_form(self, book_ids: List[str]) -> int:
        ids = ids_from_text(self.readlist_book_ids.toPlainText())
        added = 0
        for bid in book_ids:
            if bid and bid not in ids:
                ids.append(bid)
                added += 1
        self.readlist_book_ids.setPlainText("\n".join(ids))
        self.readlist_payload_preview.setPlainText(json_text(self._readlist_payload_from_form()))
        self.log(f"✅ {added} tome(s) ajouté(s) au formulaire readlist")
        return added

    def add_readlist_book_selection_to_form(self) -> None:
        ids = self._selected_ids_from_table(self.readlist_library_books_table)
        if not ids:
            QMessageBox.warning(self, "Readlist", "Aucun tome sélectionné")
            return
        self._add_book_ids_to_readlist_form(ids)

    def add_readlist_bulk_book_selection_to_form(self) -> None:
        ids = self._selected_ids_from_table(self.readlist_bulk_books_table)
        if not ids:
            QMessageBox.warning(self, "Readlist", "Aucun tome sélectionné")
            return
        self._add_book_ids_to_readlist_form(ids)

    def add_readlist_book_selection_and_update(self) -> None:
        if not self.readlist_id.text().strip():
            QMessageBox.warning(self, "Readlist", "Ouvre d'abord une readlist")
            return
        if self._add_book_ids_to_readlist_form(self._selected_ids_from_table(self.readlist_library_books_table)):
            self.update_readlist()

    def add_readlist_bulk_book_selection_and_update(self) -> None:
        if not self.readlist_id.text().strip():
            QMessageBox.warning(self, "Readlist", "Ouvre d'abord une readlist")
            return
        if self._add_book_ids_to_readlist_form(self._selected_ids_from_table(self.readlist_bulk_books_table)):
            self.update_readlist()

    def create_readlist_from_book_selection(self, name_edit: QLineEdit, source_table: QTableWidget) -> None:
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Readlist", "Nom de readlist vide")
            return
        book_ids = ids_from_text(id_lines(self._selected_ids_from_table(source_table)))
        payload = {
            "name": name,
            "ordered": True,
            "summary": "",
            "bookIds": book_ids,
        }
        if self.simulation_enabled():
            self.log(f"Simulation active : readlist non créée ({name})")
            return
        def done(_result: Any) -> None:
            self.log(f"✅ Readlist créée : {name} ({len(book_ids)} tome(s))")
            name_edit.clear()
            self.load_readlists()
        self.run_worker("Création readlist", lambda: self.komga_api().create_readlist(payload), done)

    def add_selected_books_to_selected_readlists(self) -> None:
        book_ids = self._selected_ids_from_table(self.readlist_library_books_table)
        readlist_ids = self._selected_ids_from_table(self.readlist_target_readlists_table)
        if not book_ids:
            QMessageBox.warning(self, "Readlist", "Aucun tome sélectionné")
            return
        if not readlist_ids:
            QMessageBox.warning(self, "Readlist", "Aucune readlist cible sélectionnée")
            return
        if not self._confirm_membership_bulk_action(
            member_label="Tomes sélectionnés",
            member_count=len(book_ids),
            target_label="Readlists cibles",
            target_count=len(readlist_ids),
            action="Ajouter les tomes aux readlists",
        ):
            return
        simulation = self.simulation_enabled()
        def do_update() -> Dict[str, Any]:
            api = self.komga_api()
            rows: List[Dict[str, Any]] = []
            for rid in readlist_ids:
                current = api.get_readlist(rid)
                ids = ids_from_text(id_lines(current.get("bookIds") if isinstance(current.get("bookIds"), list) else []))
                if not ids:
                    books = api.readlist_books(rid)
                    ids = [safe_str(x.get("id")) for x in books if safe_str(x.get("id"))]
                before = list(ids)
                for bid in book_ids:
                    if bid and bid not in ids:
                        ids.append(bid)
                added = len(ids) - len(before)
                payload = {
                    "name": safe_str(current.get("name")),
                    "ordered": True,
                    "summary": safe_str(current.get("summary")),
                    "bookIds": ids,
                }
                if added and not simulation:
                    self.backup.save_json("operation", "readlist", rid, current, "avant ajout tome(s) readlist")
                    api.update_readlist(rid, payload)
                rows.append({"id": rid, "name": safe_str(current.get("name")), "added": added, "simulation": simulation})
            return {"rows": rows}
        def done(result: Dict[str, Any]) -> None:
            rows = result.get("rows") or []
            added = sum(int(row.get("added") or 0) for row in rows)
            self.log(f"✅ Tomes ajoutés aux readlists : {added} ajout(s), {len(rows)} readlist(s)")
            self.load_readlists()
            self.load_readlists_for_selected_book()
        self.run_worker("Ajout tome(s) aux readlists", do_update, done)

    def add_readlist_bulk_books_to_selected_readlists(self) -> None:
        self._update_bulk_book_readlist_links(remove=False)

    def remove_readlist_bulk_books_from_selected_readlists(self) -> None:
        self._update_bulk_book_readlist_links(remove=True)

    def _update_bulk_book_readlist_links(self, *, remove: bool) -> None:
        book_ids = self._selected_ids_from_table(self.readlist_bulk_books_table)
        readlist_ids = self._selected_ids_from_table(self.readlist_bulk_target_readlists_table)
        if not book_ids:
            QMessageBox.warning(self, "Readlist", "Aucun tome sélectionné")
            return
        if not readlist_ids:
            QMessageBox.warning(self, "Readlist", "Aucune readlist cible sélectionnée")
            return
        if not self._confirm_membership_bulk_action(
            member_label="Tomes sélectionnés",
            member_count=len(book_ids),
            target_label="Readlists cibles",
            target_count=len(readlist_ids),
            action="Retirer" if remove else "Ajouter",
        ):
            return
        simulation = self.simulation_enabled()
        selected = set(book_ids)
        def do_update() -> Dict[str, Any]:
            api = self.komga_api()
            rows: List[Dict[str, Any]] = []
            for rid in readlist_ids:
                current = api.get_readlist(rid)
                ids = ids_from_text(id_lines(current.get("bookIds") if isinstance(current.get("bookIds"), list) else []))
                if not ids:
                    books = api.readlist_books(rid)
                    ids = [safe_str(x.get("id")) for x in books if safe_str(x.get("id"))]
                before = list(ids)
                if remove:
                    ids = [bid for bid in ids if bid not in selected]
                else:
                    for bid in book_ids:
                        if bid and bid not in ids:
                            ids.append(bid)
                changed = len(before) - len(ids) if remove else len(ids) - len(before)
                payload = {
                    "name": safe_str(current.get("name")),
                    "ordered": True,
                    "summary": safe_str(current.get("summary")),
                    "bookIds": ids,
                }
                if changed and not simulation:
                    self.backup.save_json("operation", "readlist", rid, current, "avant bulk readlist")
                    api.update_readlist(rid, payload)
                rows.append({"id": rid, "name": safe_str(current.get("name")), "changed": changed, "simulation": simulation})
            return {"rows": rows}
        def done(result: Dict[str, Any]) -> None:
            rows = result.get("rows") or []
            changed = sum(int(row.get("changed") or 0) for row in rows)
            action = "retirés des" if remove else "ajoutés aux"
            self.log(f"✅ Tomes {action} readlists : {changed} lien(s), {len(rows)} readlist(s)")
            self.load_readlists()
        self.run_worker("Bulk readlists", do_update, done)

    def _readlist_audit_book_series_id(self, book: Any) -> str:
        if isinstance(book, dict):
            series = book.get("series") if isinstance(book.get("series"), dict) else {}
            return safe_str(book.get("seriesId") or series.get("id"))
        return safe_str(getattr(book, "series_id", ""))

    def _readlist_audit_book_label(self, book: Any) -> str:
        metadata = self._book_metadata_map(book)
        if isinstance(book, dict):
            book_id = safe_str(book.get("id"))
            title = safe_str(metadata.get("title") or book.get("name"))
            number = safe_str(metadata.get("number") or metadata.get("numberSort"))
        else:
            book_id = safe_str(getattr(book, "id", ""))
            title = safe_str(getattr(book, "title", ""))
            number = safe_str(getattr(book, "number", ""))
        prefix = f"{number} - " if number else ""
        return f"{prefix}{title or book_id}".strip()

    def _readlist_audit_series_title(self, series_id: str, books: List[Any]) -> str:
        for book in books:
            if isinstance(book, dict):
                series = book.get("series") if isinstance(book.get("series"), dict) else {}
                metadata = series.get("metadata") if isinstance(series.get("metadata"), dict) else {}
                title = safe_str(metadata.get("title") or series.get("name") or series.get("title"))
                if title:
                    return title
        return series_id

    def analyze_readlist_series_completeness(self) -> None:
        lib_id = self._library_id("readlists")
        search = self.readlist_completeness_search.text().strip().lower() if hasattr(self, "readlist_completeness_search") else ""
        ignore_single = self.readlist_completeness_ignore_single.isChecked() if hasattr(self, "readlist_completeness_ignore_single") else True
        show_complete = self.readlist_completeness_show_complete.isChecked() if hasattr(self, "readlist_completeness_show_complete") else False
        generation = self._next_series_load_generation("readlist_completeness")

        def do_load() -> Dict[str, Any]:
            api = self.komga_api()
            readlists = api.readlists()
            if search:
                readlists = [row for row in readlists if search in row.name.lower()]
            library_book_ids: Optional[set[str]] = None
            if lib_id:
                library_book_ids = {safe_str(book.id) for book in api.books(lib_id, page_size=1000)}
                readlists = [
                    row for row in readlists
                    if not isinstance(row.raw.get("bookIds"), list)
                    or bool(set(safe_str(x) for x in row.raw.get("bookIds") or []) & library_book_ids)
                ]

            series_books_cache: Dict[str, List[Any]] = {}
            audit_rows: List[Dict[str, Any]] = []
            analyzed_readlists = 0
            for readlist in readlists:
                books = api.readlist_books(readlist.id)
                if library_book_ids is not None:
                    books = [book for book in books if self._record_id(book) in library_book_ids]
                if not books:
                    continue
                analyzed_readlists += 1
                by_series: Dict[str, List[Any]] = {}
                for book in books:
                    series_id = self._readlist_audit_book_series_id(book)
                    if series_id:
                        by_series.setdefault(series_id, []).append(book)
                for series_id, present_books in by_series.items():
                    if ignore_single and len(present_books) <= 1:
                        continue
                    if series_id not in series_books_cache:
                        series_books_cache[series_id] = api.books(library_id=lib_id or None, series_id=series_id, page_size=1000)
                    expected_books = series_books_cache[series_id] or list(present_books)
                    present_ids = {self._record_id(book) for book in present_books if self._record_id(book)}
                    missing_books = [book for book in expected_books if self._record_id(book) and self._record_id(book) not in present_ids]
                    if missing_books or show_complete:
                        audit_rows.append({
                            "readlist_id": readlist.id,
                            "readlist_name": readlist.name,
                            "series_id": series_id,
                            "series_title": self._readlist_audit_series_title(series_id, list(present_books) + list(expected_books)),
                            "present_count": len(present_books),
                            "total_count": len(expected_books),
                            "missing_count": len(missing_books),
                            "present_books": [self._readlist_audit_book_label(book) for book in present_books],
                            "missing_books": [self._readlist_audit_book_label(book) for book in missing_books],
                        })
            return {"rows": audit_rows, "readlists": analyzed_readlists}

        def done(result: Dict[str, Any]) -> None:
            if not self._is_current_series_load_generation("readlist_completeness", generation):
                return
            rows = list(result.get("rows") or [])
            table_rows = [
                [
                    row.get("readlist_id", ""),
                    row.get("readlist_name", ""),
                    row.get("series_id", ""),
                    row.get("series_title", ""),
                    row.get("present_count", 0),
                    row.get("total_count", 0),
                    row.get("missing_count", 0),
                    " | ".join(row.get("missing_books") or []),
                ]
                for row in rows
            ]
            self._set_table(
                self.readlist_completeness_table,
                ["Readlist ID", "Readlist", "Série ID", "Série", "Présents", "Total", "Manquants", "Tomes manquants"],
                table_rows,
                selection_mode=QAbstractItemView.SingleSelection,
                row_data=rows,
            )
            self.log(f"✅ Audit complétude readlists : {len(rows)} anomalie(s), {result.get('readlists', 0)} readlist(s) analysée(s)")
            if rows:
                self.readlist_completeness_table.selectRow(0)
            else:
                self.readlist_completeness_detail.setPlainText("Aucun tome manquant détecté.")

        self.run_worker("Audit complétude readlists", do_load, done)

    def update_readlist_completeness_detail(self) -> None:
        selected = self.readlist_completeness_table.selectedItems() if hasattr(self, "readlist_completeness_table") else []
        if not selected:
            return
        first = self.readlist_completeness_table.item(selected[0].row(), 0)
        row = first.data(Qt.UserRole) if first is not None else None
        if not isinstance(row, dict):
            return
        lines = [
            f"Readlist : {row.get('readlist_name', '')}",
            f"Série : {row.get('series_title', '')}",
            f"Présents : {row.get('present_count', 0)} / {row.get('total_count', 0)}",
            "",
            "Tomes manquants :",
            *[f"- {label}" for label in (row.get("missing_books") or ["Aucun"])],
            "",
            "Tomes présents :",
            *[f"- {label}" for label in (row.get("present_books") or [])],
        ]
        self.readlist_completeness_detail.setPlainText("\n".join(lines))

    def load_readlists_for_selected_book(self) -> None:
        bid = (
            self._selected_id_from_table(getattr(self, "readlist_library_books_table", self.books_table))
            or self._selected_id_from_table(self.books_table)
            or self._selected_id_from_table(self.bdt_komga_books_table)
        )
        if not bid:
            QMessageBox.warning(self, "Livre", "Aucun livre sélectionné")
            return
        def done(rows: List[Dict[str, Any]]) -> None:
            table_rows = [[x.get("id", ""), x.get("name", "")] for x in rows]
            self._set_table(self.book_readlists_table, ["ID", "Nom"], table_rows)
            if hasattr(self, "readlist_book_links_table"):
                self._set_table(self.readlist_book_links_table, ["ID", "Nom"], table_rows)
            self.log(f"✅ {len(rows)} readlists associées au livre {bid}")
        self.run_worker("Readlists du livre", lambda: self.komga_api().book_readlists(bid), done)

    def move_readlist_member(self, direction: int) -> None:
        ids = ids_from_text(self.readlist_book_ids.toPlainText())
        row = self._selected_row_index(self.readlist_books_table)
        if row < 0 or row >= len(ids):
            return
        new_row = max(0, min(len(ids) - 1, row + direction))
        if new_row == row:
            return
        ids[row], ids[new_row] = ids[new_row], ids[row]
        self.readlist_book_ids.setPlainText("\n".join(ids))
        self.readlist_payload_preview.setPlainText(json_text(self._readlist_payload_from_form()))

    def _readlist_payload_from_form(self) -> Dict[str, Any]:
        return {
            "name": self.readlist_name.text().strip(),
            "ordered": True,
            "summary": self.readlist_summary.toPlainText(),
            "bookIds": ids_from_text(self.readlist_book_ids.toPlainText()),
        }

    def create_readlist(self) -> None:
        payload = self._readlist_payload_from_form()
        self.readlist_payload_preview.setPlainText(json_text(payload))
        if not str(payload.get("name") or "").strip():
            QMessageBox.warning(self, "Readlist", "Le nom de la readlist est obligatoire.")
            return
        if self.simulation_enabled():
            self.log("Simulation active : readlist non créée")
            return
        if QMessageBox.question(
            self,
            "Créer la readlist",
            f"Créer « {payload['name']} » avec {len(payload.get('bookIds') or [])} tome(s) ?",
        ) != QMessageBox.Yes:
            return
        self.run_worker("Création readlist", lambda: self.komga_api().create_readlist(payload), lambda r: self.log("✅ Readlist créée"))

    def update_readlist(self) -> None:
        rid = self.readlist_id.text().strip()
        payload = self._readlist_payload_from_form()
        self.readlist_payload_preview.setPlainText(json_text(payload))
        if not rid:
            QMessageBox.warning(self, "Readlist", "ID vide")
            return
        if self.simulation_enabled():
            self.log(f"Simulation active : readlist {rid} non modifiée")
            return
        if QMessageBox.question(
            self,
            "Modifier la readlist",
            f"Mettre à jour « {payload.get('name') or rid} » avec {len(payload.get('bookIds') or [])} tome(s) ?\n\n"
            "L'état actuel sera sauvegardé avant la modification.",
        ) != QMessageBox.Yes:
            return
        def do_update() -> Any:
            api = self.komga_api()
            current = api.get_readlist(rid)
            self.backup.save_json("operation", "readlist", rid, current, "avant PATCH readlist")
            return api.update_readlist(rid, payload)
        self.run_worker("Update readlist", do_update, lambda r: self.log(f"✅ Readlist modifiée : {rid}"))

    # ------------------------------------------------------------------
    # Posters
    # ------------------------------------------------------------------
    def _update_poster_status(self, *_: Any) -> None:
        if not hasattr(self, "poster_status_label"):
            return
        typ = self.poster_type.currentText().strip()
        tid = self.poster_id.text().strip()
        self.poster_status_label.setText(
            f"Cible active : {typ} — {tid}" if typ and tid else "Aucune cible active. Utilisez une sélection ou renseignez un ID."
        )

    def _poster_target(self) -> tuple[str, str]:
        typ = self.poster_type.currentText().strip() if hasattr(self, "poster_type") else ""
        tid = self.poster_id.text().strip() if hasattr(self, "poster_id") else ""
        if typ not in {"series", "book", "collection", "readlist"}:
            QMessageBox.warning(self, "Couvertures", "Type de cible invalide.")
            return "", ""
        if not tid:
            QMessageBox.warning(self, "Couvertures", "Aucun ID cible. Utilise une sélection ou colle un ID.")
            return "", ""
        return typ, tid

    def _refresh_poster_target(self, typ: str, tid: str) -> None:
        if typ and tid:
            self._show_cover(typ, tid, self.poster_preview)
            self.list_posters()

    def use_selected_for_poster(self) -> None:
        bid = self._selected_id_from_table(self.books_table)
        sid = self._selected_id_from_table(self.series_table)
        cid = self._selected_id_from_table(self.collections_table)
        rid = self._selected_id_from_table(self.readlists_table)
        if bid:
            self.poster_type.setCurrentText("book"); self.poster_id.setText(bid)
        elif sid:
            self.poster_type.setCurrentText("series"); self.poster_id.setText(sid)
        elif cid:
            self.poster_type.setCurrentText("collection"); self.poster_id.setText(cid)
        elif rid:
            self.poster_type.setCurrentText("readlist"); self.poster_id.setText(rid)
        else:
            QMessageBox.warning(self, "Couvertures", "Aucune série, tome, collection ou readlist sélectionnée.")
            return
        self._refresh_poster_target(self.poster_type.currentText(), self.poster_id.text().strip())

    def list_posters(self) -> None:
        typ, tid = self._poster_target()
        if not typ or not tid:
            return
        def done(rows: List[Dict[str, Any]]) -> None:
            table_rows = [[x.get("id", ""), x.get("selected", ""), x.get("type", ""), x.get("width", ""), x.get("height", ""), x.get("mediaType", "")] for x in rows]
            self._set_table(self.poster_table, ["ID", "Selected", "Type", "W", "H", "Media"], table_rows, row_data=rows)
            self.poster_status_label.setText(f"{len(rows)} couverture(s) disponible(s) pour {typ}:{tid}.")
            self.log(f"✅ {len(rows)} poster(s) listés")
        self.run_worker("Liste posters", lambda: self.komga_api().list_thumbnails(typ, tid), done)

    def add_local_poster(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Image", "", "Images (*.jpg *.jpeg *.png *.webp *.gif);;Tous fichiers (*)")
        if not path:
            return
        typ, tid = self._poster_target()
        if not typ or not tid:
            return
        if self.simulation_enabled():
            self.log(f"Simulation active : poster non uploadé {path}")
            return
        if QMessageBox.question(
            self,
            "Ajouter une couverture",
            f"Uploader l'image locale sur {typ}:{tid} ?\n\n{path}",
        ) != QMessageBox.Yes:
            return
        def do_upload() -> Any:
            self.backup.save_json("operation", typ, tid, {"poster_upload": path}, "avant upload poster")
            return self.komga_api().add_thumbnail(typ, tid, path)
        def done(_result: Any) -> None:
            self.log(f"✅ Poster uploadé : {path}")
            self._refresh_poster_target(typ, tid)
        self.run_worker("Upload poster", do_upload, done)

    def add_url_poster(self) -> None:
        url = self.poster_url.text().strip()
        if not url:
            QMessageBox.warning(self, "URL", "URL vide")
            return
        typ, tid = self._poster_target()
        if not typ or not tid:
            return
        if self.simulation_enabled():
            self.log(f"Simulation active : URL poster non uploadée {url}")
            return
        if QMessageBox.question(
            self,
            "Ajouter une couverture",
            f"Télécharger puis uploader cette image sur {typ}:{tid} ?\n\n{url}",
        ) != QMessageBox.Yes:
            return
        def do_upload() -> Any:
            suffix = os.path.splitext(url.split("?", 1)[0])[1] or ".jpg"
            fd, tmp = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            try:
                req = urlrequest.Request(url, headers={"User-Agent": "komga-db-tool/0.2.0"})
                with urlrequest.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
                    f.write(resp.read())
                self.backup.save_json("operation", typ, tid, {"poster_upload_url": url}, "avant upload poster URL")
                return self.komga_api().add_thumbnail(typ, tid, tmp)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        def done(_result: Any) -> None:
            self.log("✅ Poster uploadé depuis URL")
            self._refresh_poster_target(typ, tid)
        self.run_worker("Upload poster URL", do_upload, done)

    def select_poster(self) -> None:
        typ, tid = self._poster_target()
        if not typ or not tid:
            return
        thumb = self.poster_select_id.text().strip() or self._selected_id_from_table(self.poster_table)
        if not thumb:
            QMessageBox.warning(self, "Poster", "Aucun thumbnailId")
            return
        if self.simulation_enabled():
            self.log(f"Simulation active : poster {thumb} non sélectionné")
            return
        if QMessageBox.question(
            self,
            "Sélectionner la couverture",
            f"Marquer la couverture {thumb} comme sélectionnée pour {typ}:{tid} ?",
        ) != QMessageBox.Yes:
            return
        def do_select() -> Any:
            current = self.komga_api().list_thumbnails(typ, tid)
            self.backup.save_json("operation", typ, tid, current, "avant sélection poster")
            return self.komga_api().select_thumbnail(typ, tid, thumb)
        def done(_result: Any) -> None:
            self.log(f"✅ Poster sélectionné : {thumb}")
            self._refresh_poster_target(typ, tid)
        self.run_worker("Sélection poster", do_select, done)

    # ------------------------------------------------------------------
    # CSV / bulk
    # ------------------------------------------------------------------
    def _invalidate_csv_preview(self) -> None:
        self.loaded_csv_actions = []
        if hasattr(self, "csv_apply_button"):
            self.csv_apply_button.setEnabled(False)
        if hasattr(self, "csv_status_label"):
            self.csv_status_label.setText("Fichier non prévisualisé ou chemin modifié.")

    def browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CSV", "", "CSV (*.csv);;Tous fichiers (*)")
        if path:
            self.csv_path.setText(path)

    def load_csv_preview(self) -> None:
        path = self.csv_path.text().strip()
        if not path:
            self._invalidate_csv_preview()
            QMessageBox.warning(self, "CSV", "Choisissez un fichier CSV avant la prévisualisation.")
            return
        try:
            rows = read_csv(path)
        except Exception as exc:
            self._invalidate_csv_preview()
            self.csv_preview.setPlainText(str(exc))
            QMessageBox.warning(self, "CSV invalide", str(exc))
            return
        if looks_like_comicinfo_director(rows):
            report = comicinfo_director_summary(rows)
            report["note"] = (
                "CSV directeur ComicInfo détecté. Cette prévisualisation vérifie le formalisme du tuto "
                "et montre les champs ComicInfo demandés ; l'onglet CSV / Bulk n'écrit pas directement dans les CBZ."
            )
            self.loaded_csv_actions = []
            self.csv_apply_button.setEnabled(False)
            self.csv_status_label.setText(
                "CSV ComicInfo détecté : validation disponible, mais aucune écriture directe dans les CBZ."
            )
            self.csv_preview.setPlainText(json_text(report))
            counts = report.get("counts", {})
            self.log(
                "CSV ComicInfo chargé : "
                f"{counts.get('rows', 0)} ligne(s), {counts.get('writable', 0)} compatible(s), "
                f"{counts.get('api_only', 0)} API-only, {counts.get('blocked', 0)} bloquée(s)"
            )
            return
        self.loaded_csv_actions = parse_director_actions(rows)
        preview = [asdict(a) for a in self.loaded_csv_actions[:200]]
        self.csv_preview.setPlainText(json_text({"count": len(self.loaded_csv_actions), "actions": preview}))
        self.csv_apply_button.setEnabled(bool(self.loaded_csv_actions))
        self.csv_status_label.setText(
            f"Prévisualisation prête : {len(self.loaded_csv_actions)} action(s)."
            if self.loaded_csv_actions
            else "Aucune action applicable trouvée dans ce fichier."
        )
        self.log(f"CSV chargé : {len(self.loaded_csv_actions)} action(s)")

    def validate_comicinfo_csv(self) -> None:
        path = self.csv_path.text().strip()
        if not path:
            QMessageBox.warning(self, "CSV ComicInfo", "Aucun CSV sélectionné.")
            return
        rows = read_csv(path)
        report = comicinfo_director_summary(rows)
        report["source"] = path
        report["columns_expected"] = COMICINFO_DIRECTOR_COLUMNS
        self.csv_preview.setPlainText(json_text(report))
        self.loaded_csv_actions = []
        self.csv_apply_button.setEnabled(False)
        counts = report.get("counts", {})
        self.csv_status_label.setText(
            f"Validation ComicInfo : {counts.get('rows', 0)} ligne(s), "
            f"{counts.get('errors', 0)} erreur(s). Aucune écriture directe."
        )
        self.log(
            "Validation CSV ComicInfo : "
            f"{counts.get('rows', 0)} ligne(s), {counts.get('writable', 0)} compatible(s), "
            f"{counts.get('skipped', 0)} ignorée(s), {counts.get('api_only', 0)} API-only, "
            f"{counts.get('blocked', 0)} bloquée(s), {counts.get('errors', 0)} erreur(s)"
        )

    def apply_csv_actions(self) -> None:
        if not self.loaded_csv_actions:
            self.load_csv_preview()
        if not self.loaded_csv_actions:
            QMessageBox.warning(self, "CSV", "Aucune action prévisualisée à appliquer.")
            return
        if self.simulation_enabled():
            self.log("Simulation active : CSV non appliqué")
            self.csv_status_label.setText(
                f"Simulation terminée : {len(self.loaded_csv_actions)} action(s), aucune écriture."
            )
            return
        action_count = len(self.loaded_csv_actions)
        if QMessageBox.question(
            self,
            "Confirmer l'application CSV",
            f"Appliquer {action_count} action(s) prévisualisée(s) ?\n\n"
            "Les ressources modifiées seront sauvegardées avant l'écriture.",
        ) != QMessageBox.Yes:
            return
        self.csv_apply_button.setEnabled(False)
        self.csv_status_label.setText(f"Application de {action_count} action(s) en cours…")

        def do_apply() -> Dict[str, Any]:
            api = self.komga_api()
            result = {"ok": 0, "skipped": 0, "errors": []}
            for action in self.loaded_csv_actions:
                try:
                    typ = action.target_type
                    op = action.operation
                    tid = action.target_id
                    payload = action.payload
                    if typ == "series" and op in {"update", "patch"}:
                        current = api.get_series(tid)
                        self.backup.save_json("operation", "series", tid, current, "avant CSV update series")
                        self._write_metadata_update(api, "series", tid, payload, current, source="csv", note="CSV update series")
                    elif typ in {"book", "books"} and op in {"update", "patch"}:
                        current = api.get_book(tid)
                        self.backup.save_json("operation", "book", tid, current, "avant CSV update book")
                        self._write_metadata_update(api, "book", tid, payload, current, source="csv", note="CSV update book")
                    elif typ == "collection" and op == "create":
                        api.create_collection(payload)
                    elif typ == "collection" and op in {"update", "patch"}:
                        current = api.get_collection(tid)
                        self.backup.save_json("operation", "collection", tid, current, "avant CSV update collection")
                        api.update_collection(tid, payload)
                    elif typ == "readlist" and op == "create":
                        api.create_readlist(payload)
                    elif typ == "readlist" and op in {"update", "patch"}:
                        current = api.get_readlist(tid)
                        self.backup.save_json("operation", "readlist", tid, current, "avant CSV update readlist")
                        api.update_readlist(tid, payload)
                    elif typ == "poster" and op in {"upload", "add"}:
                        poster_target_type = str(payload.get("target_type") or "").strip()
                        poster_path = str(payload.get("poster_path") or "").strip()
                        if not poster_target_type or not tid or not poster_path:
                            raise ValueError("Action poster incomplète : target_type/id/poster_path requis")
                        self.backup.save_json("operation", poster_target_type, tid, {"poster_upload": poster_path}, "avant CSV upload poster")
                        api.add_thumbnail(poster_target_type, tid, poster_path)
                    elif typ == "poster" and op in {"select", "use"}:
                        poster_target_type = str(payload.get("target_type") or "").strip()
                        thumbnail_id = str(payload.get("thumbnail_id") or "").strip()
                        if not poster_target_type or not tid or not thumbnail_id:
                            raise ValueError("Action poster incomplète : target_type/id/thumbnail_id requis")
                        current = api.list_thumbnails(poster_target_type, tid)
                        self.backup.save_json("operation", poster_target_type, tid, current, "avant CSV sélection poster")
                        api.select_thumbnail(poster_target_type, tid, thumbnail_id)
                    else:
                        result["skipped"] += 1
                        continue
                    result["ok"] += 1
                except Exception as exc:
                    result["errors"].append({"action": asdict(action), "error": str(exc)})
            return result

        def done(result: Dict[str, Any]) -> None:
            self.csv_preview.setPlainText(json_text(result))
            self.loaded_csv_actions = []
            self.csv_apply_button.setEnabled(False)
            self.csv_status_label.setText(
                f"Terminé : {result['ok']} appliquée(s), {result['skipped']} ignorée(s), "
                f"{len(result['errors'])} erreur(s)."
            )
            self.log(f"CSV appliqué : {result['ok']} OK, {result['skipped']} ignorées, {len(result['errors'])} erreurs")
        self.run_worker("Application CSV", do_apply, done)

    def export_director_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Modèle CSV directeur", "komga_director_template.csv", "CSV (*.csv)")
        if path:
            write_csv(path, [], DIRECTOR_COLUMNS)
            self.log(f"Modèle directeur exporté : {path}")

    def export_specialized_templates(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier modèles CSV")
        if not folder:
            return
        for name, columns in SPECIALIZED_COLUMNS.items():
            write_csv(os.path.join(folder, f"komga_{name}_template.csv"), [], columns)
        self.log(f"Modèles spécialisés exportés : {folder}")

    def export_comicinfo_director_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Modèle CSV ComicInfo", "komga_comicinfo_director_template.csv", "CSV (*.csv)")
        if path:
            write_csv(path, [], COMICINFO_DIRECTOR_COLUMNS)
            self.log(f"Modèle ComicInfo exporté : {path}")

    # ------------------------------------------------------------------
    # Komf
    # ------------------------------------------------------------------
    def search_komf(self) -> None:
        name = self.komf_search_name.text().strip()
        lib = self.komf_library_id.text().strip()
        sid = self.komf_series_id.text().strip()
        def done(result: Any) -> None:
            data, path = result
            self.komf_output.setPlainText(json_text({"endpoint": path, "response": data}))
            self.log("✅ Recherche Komf terminée")
        self.run_worker("Recherche Komf", lambda: self.komf_api().search(name, library_id=lib, series_id=sid), done)

    def identify_komf(self) -> None:
        lib = self.komf_library_id.text().strip()
        sid = self.komf_series_id.text().strip()
        provider = self.komf_provider.text().strip()
        provider_id = self.komf_provider_id.text().strip()
        if self.simulation_enabled():
            self.komf_output.setPlainText(json_text({
                "simulation": True,
                "payload": {"libraryId": lib, "seriesId": sid, "provider": provider, "providerSeriesId": provider_id},
            }))
            return
        self.run_worker("Identify Komf", lambda: self.komf_api().identify(lib, sid, provider, provider_id), lambda r: self.log("✅ Identify Komf terminé"))

    # ------------------------------------------------------------------
    # MangaBaka
    # ------------------------------------------------------------------
    def load_mangabaka_komga_series(self) -> None:
        lib_id = self._library_id("mangabaka")
        search = self.mbk_komga_search.text().strip()
        generation = self._next_series_load_generation("mangabaka")

        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("mangabaka", generation):
                return
            rows = self._filter_global_series_visibility(rows)
            self._source_series_unfiltered_rows["mbk"] = rows
            self._refresh_source_link_filter_options("mbk", rows)
            rows, active_filters = self._apply_source_series_filters("mbk", rows)
            self.mbk_komga_series_rows = rows
            self._set_table(
                self.mbk_komga_series_table,
                self._series_table_headers(include_history=True),
                self._series_table_rows_for_source("mbk", rows),
                stretch_from=1,
            )
            suffix = f" — {', '.join(active_filters)}" if active_filters else ""
            self.log(f"✅ MangaBaka : {len(rows)} séries Komga chargées{suffix}")

        self.run_worker("Chargement séries Komga pour MangaBaka", lambda: self.komga_api().series(lib_id, search=search, page_size=200), done)

    def _clear_mangabaka_views(self, message: str = "") -> None:
        self.mangabaka_results = []
        self.mangabaka_candidate = None
        self.mbk_komga_book_rows = []
        for table in (getattr(self, "mbk_results_table", None),):
            if table is not None:
                table.setRowCount(0)
        if hasattr(self, "mbk_komga_books_table"):
            self.mbk_komga_books_table.setRowCount(0)
        self._fill_series_preview_metadata_table(self.mbk_series_metadata_table, {}, {})
        self.mbk_series_preview.setPlainText(message or "Sélectionne une série Komga puis un résultat MangaBaka.")
        self.mbk_raw.setPlainText(message or "")
        self._set_selection_detail("mangabaka.result", "", {"info": message or "Aucun résultat MangaBaka sélectionné."}, "")

    def _select_mangabaka_komga_series_row_by_id(self, series_id: str) -> bool:
        if not series_id:
            return False
        for row, series in enumerate(self.mbk_komga_series_rows):
            if getattr(series, "id", "") == series_id:
                self.mbk_komga_series_table.blockSignals(True)
                self.mbk_komga_series_table.clearSelection()
                self.mbk_komga_series_table.selectRow(row)
                item = self.mbk_komga_series_table.item(row, 0)
                if item is not None:
                    self.mbk_komga_series_table.scrollToItem(item)
                self.mbk_komga_series_table.blockSignals(False)
                return True
        return False

    def on_mangabaka_komga_series_selected(self) -> None:
        row = self._selected_row_index(self.mbk_komga_series_table)
        if row < 0 or row >= len(self.mbk_komga_series_rows):
            return
        series = self.mbk_komga_series_rows[row]
        self._set_context_selection(series=series)
        self.mbk_context_generation += 1
        self.mbk_target_id.setText(series.id)
        self.mbk_query.setText(clean_search_title(series.title))
        self._clear_mangabaka_views(f"Série Komga sélectionnée : {series.title}\nRecherche MangaBaka en cours…")
        self.load_mangabaka_komga_books(series)
        self.search_mangabaka()

    def load_mangabaka_komga_books(self, series: Any = None) -> None:
        if not hasattr(self, "mbk_komga_books_table"):
            return
        if series is None:
            row = self._selected_row_index(self.mbk_komga_series_table)
            series = self.mbk_komga_series_rows[row] if 0 <= row < len(self.mbk_komga_series_rows) else None
        if series is None:
            return
        generation = self.mbk_context_generation
        lib_id = self._library_id("mangabaka") or getattr(series, "library_id", "")

        def done(rows: List[Any]) -> None:
            if generation != self.mbk_context_generation:
                return
            self.mbk_komga_book_rows = rows
            self._set_table(
                self.mbk_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(x) for x in rows],
                stretch_from=1,
            )
            self.log(f"✅ MangaBaka : {len(rows)} tomes Komga chargés pour {getattr(series, 'title', '')}")

        self.mbk_komga_book_rows = []
        self.mbk_komga_books_table.setRowCount(0)
        self.run_worker(
            "Chargement tomes Komga pour MangaBaka",
            lambda: self.komga_api().books(
                lib_id,
                getattr(series, "id", "") or None,
                page_size=200,
                direct_series_only=True,
                timeout=min(int(self.timeout_seconds.value()), 12),
            ),
            done,
        )

    def use_selected_for_mangabaka(self) -> None:
        row = self._selected_row_index(self.mbk_komga_series_table)
        if row >= 0 and row < len(self.mbk_komga_series_rows):
            self.mbk_target_id.setText(self.mbk_komga_series_rows[row].id)
            self.mbk_query.setText(clean_search_title(self.mbk_komga_series_rows[row].title))
            return
        sid = self._selected_id_from_table(self.series_table)
        if sid:
            row = self._selected_row_index(self.series_table)
            if row >= 0 and row < len(self.series_rows):
                series = self.series_rows[row]
                self.mbk_target_id.setText(sid)
                self.mbk_query.setText(clean_search_title(series.title))

    def search_mangabaka(self) -> None:
        raw_query = self.mbk_query.text().strip()
        query = clean_search_title(raw_query)
        if query != raw_query:
            self.mbk_query.setText(query)
        if not query:
            QMessageBox.warning(self, "MangaBaka", "Recherche vide")
            return
        if not self.mangabaka_enabled.isChecked():
            QMessageBox.warning(self, "MangaBaka", "Module MangaBaka désactivé dans l'onglet Connexion")
            return
        generation = self.mbk_context_generation
        manga_only = self.mbk_filter_manga_only.isChecked()
        self._record_enrichment_search("mbk", self._current_mangabaka_series_id())

        def done(search_result: Dict[str, Any]) -> None:
            if generation != self.mbk_context_generation:
                return
            rows = search_result.get("rows") or []
            ranked = ranked_title_results(query, rows)
            rows = [row for _, row in ranked]
            used_query = str(search_result.get("used_query") or query)
            attempts = search_result.get("attempts") or []
            self.mangabaka_results = rows
            self._set_table(self.mbk_results_table, ["ID", "Titre", "Score match", "Type", "Status", "Année", "Publisher", "Genres", "URL"], [
                [r.id, r.title, f"{score:.3f}", r.type, r.status, r.year, r.publisher, "; ".join(r.genres), r.source_url]
                for score, r in ranked
            ], stretch_from=1)
            if rows:
                self._reset_table_to_first_row(self.mbk_results_table)
                suffix = "" if used_query == query else f"\nVariante utilisée : {used_query}"
                self.mbk_raw.setPlainText(
                    f"{len(rows)} résultat(s), triés par score. La première ligne est sélectionnée et chargée automatiquement."
                    f"{suffix}\nEssais : {self._format_search_attempts(attempts)}"
                )
                self.log(f"MangaBaka recherche : {query} → {self._format_search_attempts(attempts)}")
                self.fetch_selected_mangabaka_series()
            else:
                self._reset_table_to_first_row(self.mbk_results_table)
                self.mbk_raw.setPlainText(f"0 résultat MangaBaka. Essais : {self._format_search_attempts(attempts)}")
                self.log(f"MangaBaka recherche sans résultat : {query} → {self._format_search_attempts(attempts)}")

        self.mbk_raw.setPlainText(f"Recherche MangaBaka en cours…\nRequête : {query}\nSi aucun résultat ne revient, le détail des essais sera affiché ici.")
        self._set_table(self.mbk_results_table, ["ID", "Titre", "Score match", "Type", "Status", "Année", "Publisher", "Genres", "URL"], [], stretch_from=1)
        self._reset_table_to_first_row(self.mbk_results_table)
        self.run_worker("Recherche MangaBaka", lambda: self._search_mangabaka_with_fallback(query, manga_only), done)

    def on_mangabaka_result_selected(self) -> None:
        row = self._selected_row_index(self.mbk_results_table)
        if row < 0 or row >= len(self.mangabaka_results):
            self._set_selection_detail("mangabaka.result", "", {"info": "Aucun résultat MangaBaka sélectionné."}, "")
            return
        result = self.mangabaka_results[row]
        data = {
            "source": "mangabaka",
            "id": result.id,
            "title": result.title,
            "type": result.type,
            "status": result.status,
            "year": result.year,
            "publisher": result.publisher,
            "genres": result.genres,
            "source_url": result.source_url,
            "cover_url": result.cover_url,
        }
        self._set_selection_detail("mangabaka.result", result.title, data, result.source_url)

    def fetch_selected_mangabaka_series(self) -> None:
        row = self._selected_row_index(self.mbk_results_table)
        if row < 0 or row >= len(self.mangabaka_results):
            QMessageBox.warning(self, "MangaBaka", "Aucun résultat MangaBaka sélectionné")
            return
        result = self.mangabaka_results[row]
        generation = self.mbk_context_generation

        def done(candidate: MangaBakaCandidate) -> None:
            if generation != self.mbk_context_generation:
                return
            self.mangabaka_candidate = candidate
            self.mbk_raw.setPlainText(json_text(MangaBakaClient.candidate_to_dict(candidate)))
            self.preview_mangabaka_series()
            self.log(f"✅ Série MangaBaka chargée : {candidate.title} — {candidate.series_id}")

        self.run_worker("Chargement série MangaBaka", lambda: self.mangabaka_client().get_series(result.id), done)

    def _current_mangabaka_series_id(self) -> str:
        explicit_id = self.mbk_target_id.text().strip()
        if explicit_id:
            return explicit_id
        row = self._selected_row_index(self.mbk_komga_series_table)
        if row >= 0 and row < len(self.mbk_komga_series_rows):
            return self.mbk_komga_series_rows[row].id
        return ""

    def preview_mangabaka_series(self) -> None:
        if not self.mangabaka_candidate:
            self.mbk_series_preview.setPlainText("Aucune série MangaBaka chargée.")
            return
        series_id = self._current_mangabaka_series_id()
        if not series_id:
            self.mbk_series_preview.setPlainText("Aucune série Komga sélectionnée.")
            return
        proposed = self.mangabaka_candidate.series_metadata

        def done(current: Dict[str, Any]) -> None:
            self._fill_series_preview_metadata_table(self.mbk_series_metadata_table, current, proposed)
            payload = self._payload_from_metadata_table(self.mbk_series_metadata_table)
            endpoint = f"PATCH /api/v1/series/{series_id}/metadata"
            self.mbk_series_preview.setPlainText(self._format_diff(current, payload, endpoint))

        self.run_worker("Preview MangaBaka série", lambda: self._fetch_current_series_preview_metadata(series_id), done)

    def apply_mangabaka_series(self) -> None:
        series_id = self._current_mangabaka_series_id()
        payload = self._payload_from_metadata_table(self.mbk_series_metadata_table)
        if not series_id or not payload:
            QMessageBox.warning(self, "MangaBaka", "Série Komga ou payload vide")
            return
        if self.simulation_enabled():
            self.preview_mangabaka_series()
            self.log("Simulation active : aucune écriture série MangaBaka")
            return
        if not self._confirm_source_write(
            source_name="MangaBaka",
            target_label=f"série {series_id}",
            field_count=len(payload),
        ):
            return

        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("series", series_id)
            self.backup.save_json(
                "operation",
                "series",
                series_id,
                {"current": current, "mangabaka": MangaBakaClient.candidate_to_dict(self.mangabaka_candidate) if self.mangabaka_candidate else {}},
                "avant PATCH MangaBaka série",
            )
            return self._write_metadata_update(api, "series", series_id, payload, current, source="mangabaka_manual", note="Application MangaBaka série")

        self.run_worker("Application MangaBaka série", do_apply, lambda r: self.log(f"✅ Metadata MangaBaka appliquée sur série:{series_id}"))

    def send_mangabaka_cover_to_posters(self) -> None:
        if not self.mangabaka_candidate or not self.mangabaka_candidate.cover_url:
            QMessageBox.warning(self, "MangaBaka", "Aucune cover MangaBaka disponible pour le résultat chargé")
            return
        series_id = self._current_mangabaka_series_id()
        if not series_id:
            QMessageBox.warning(self, "MangaBaka", "Aucune série Komga sélectionnée")
            return
        self.poster_type.setCurrentText("series")
        self.poster_id.setText(series_id)
        self.poster_url.setText(self.mangabaka_candidate.cover_url)
        if hasattr(self, "poster_tab"):
            self._set_current_tab_for_widget(self.poster_tab)
        self.log("MangaBaka : cover copiée dans l'onglet Couvertures. Elle ne sera uploadée que si tu cliques le bouton URL, simulation désactivée.")

    # ------------------------------------------------------------------
    # Manga News
    # ------------------------------------------------------------------
    def load_manga_news_komga_series(self) -> None:
        lib_id = self._library_id("manga_news")
        search = self.mn_komga_search.text().strip()
        generation = self._next_series_load_generation("manga_news")

        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("manga_news", generation):
                return
            rows = self._filter_global_series_visibility(rows)
            self._source_series_unfiltered_rows["mn"] = rows
            self._refresh_source_link_filter_options("mn", rows)
            rows, active_filters = self._apply_source_series_filters("mn", rows)
            self.mn_komga_series_rows = rows
            self._set_table(
                self.mn_komga_series_table,
                self._series_table_headers(include_history=True),
                self._series_table_rows_for_source("mn", rows),
                stretch_from=1,
            )
            suffix = f" — {', '.join(active_filters)}" if active_filters else ""
            self.log(f"✅ Manga News : {len(rows)} séries Komga chargées{suffix}")

        self.run_worker("Chargement séries Komga pour Manga News", lambda: self.komga_api().series(lib_id, search=search, page_size=200), done)

    def _clear_manga_news_views(self, message: str = "") -> None:
        self.manga_news_results = []
        self.manga_news_candidate = None
        self.mn_volume_candidate = None
        self.mn_komga_book_rows = []
        for table in (getattr(self, "mn_results_table", None),):
            if table is not None:
                table.setRowCount(0)
        if hasattr(self, "mn_komga_books_table"):
            self.mn_komga_books_table.setRowCount(0)
        if hasattr(self, "mn_book_metadata_table"):
            self._fill_metadata_table(self.mn_book_metadata_table, {}, {}, BOOK_METADATA_FIELDS)
        if hasattr(self, "mn_book_preview"):
            self.mn_book_preview.setPlainText("Sélectionne un tome Komga puis charge une URL volume Manga News.")
        self._fill_manga_news_metadata_table({}, {})
        self.mn_series_preview.setPlainText(message or "Sélectionne une série Komga puis un résultat Manga News.")
        self.mn_raw.setPlainText(message or "")
        self._set_selection_detail("manga_news.result", "", {"info": message or "Aucun résultat Manga News sélectionné."}, "")

    def _select_manga_news_komga_series_row_by_id(self, series_id: str) -> bool:
        if not series_id:
            return False
        for row, series in enumerate(self.mn_komga_series_rows):
            if getattr(series, "id", "") == series_id:
                self.mn_komga_series_table.blockSignals(True)
                self.mn_komga_series_table.clearSelection()
                self.mn_komga_series_table.selectRow(row)
                item = self.mn_komga_series_table.item(row, 0)
                if item is not None:
                    self.mn_komga_series_table.scrollToItem(item)
                self.mn_komga_series_table.blockSignals(False)
                return True
        return False

    def on_manga_news_komga_series_selected(self) -> None:
        row = self._selected_row_index(self.mn_komga_series_table)
        if row < 0 or row >= len(self.mn_komga_series_rows):
            return
        series = self.mn_komga_series_rows[row]
        self._set_context_selection(series=series)
        self.mn_context_generation += 1
        self.mn_target_id.setText(series.id)
        self.mn_query.setText(clean_search_title(series.title))
        slug, url = self._manga_news_link_for_series(series)
        if slug or url:
            self._clear_manga_news_views(f"Série Komga sélectionnée : {series.title}\nLien Manga News existant détecté. Chargement direct…")
            self.load_manga_news_komga_books(series)
            self.fetch_manga_news_series_direct(slug=slug, url=url)
        else:
            self._clear_manga_news_views(f"Série Komga sélectionnée : {series.title}\nRecherche Manga News en cours…")
            self.load_manga_news_komga_books(series)
            self.search_manga_news()

    def use_selected_for_manga_news(self) -> None:
        row = self._selected_row_index(self.mn_komga_series_table)
        if row >= 0 and row < len(self.mn_komga_series_rows):
            self.mn_target_id.setText(self.mn_komga_series_rows[row].id)
            self.mn_query.setText(clean_search_title(self.mn_komga_series_rows[row].title))
            return
        sid = self._selected_id_from_table(self.series_table)
        if sid:
            row = self._selected_row_index(self.series_table)
            if row >= 0 and row < len(self.series_rows):
                series = self.series_rows[row]
                self.mn_target_id.setText(sid)
                self.mn_query.setText(clean_search_title(series.title))

    def search_manga_news(self) -> None:
        raw_query = self.mn_query.text().strip()
        query = clean_search_title(raw_query)
        if query != raw_query:
            self.mn_query.setText(query)
        if not query:
            QMessageBox.warning(self, "Manga News", "Recherche vide")
            return
        if not self.manga_news_enabled.isChecked():
            QMessageBox.warning(self, "Manga News", "Module Manga News désactivé dans l'onglet Connexion")
            return
        generation = self.mn_context_generation
        manga_only = self.mn_filter_manga_only.isChecked()
        self._record_enrichment_search("mn", self._current_manga_news_series_id())

        def done(search_result: Dict[str, Any]) -> None:
            if generation != self.mn_context_generation:
                return
            rows = search_result.get("rows") or []
            raw_rows = search_result.get("raw_rows") or []
            display_rows = rows or raw_rows
            ranked = ranked_title_results(query, display_rows)
            display_rows = [row for _, row in ranked]
            used_query = str(search_result.get("used_query") or query)
            attempts = search_result.get("attempts") or []
            self.manga_news_results = display_rows
            self._set_table(self.mn_results_table, ["Slug", "Titre", "Score match", "Score source", "Kind", "Media", "VF", "Volumes", "Titre VO", "URL"], [
                [r.slug, r.title, f"{score:.3f}", r.score, r.kind, r.media_kind, r.vf_status, r.vf_volumes, r.title_vo, r.url]
                for score, r in ranked
            ], stretch_from=1)
            if rows:
                self._reset_table_to_first_row(self.mn_results_table)
                suffix = "" if used_query == query else f"\nVariante utilisée : {used_query}"
                error_suffix = f"\nDernière erreur ignorée : {search_result.get('error')}" if search_result.get("error") else ""
                self.mn_raw.setPlainText(
                    f"{len(rows)} résultat(s), triés par score. La première ligne est sélectionnée et chargée automatiquement."
                    f"{suffix}\nEssais : {self._format_search_attempts(attempts)}{error_suffix}"
                )
                self.log(f"Manga News recherche : {query} → {self._format_search_attempts(attempts)}")
                self.fetch_selected_manga_news_series()
            elif raw_rows:
                self._reset_table_to_first_row(self.mn_results_table)
                self.mn_raw.setPlainText(
                    f"0 résultat après filtre manga, mais {len(raw_rows)} résultat(s) brut(s) trié(s) par score. "
                    "La première ligne est sélectionnée et chargée automatiquement.\n"
                    f"Essais : {self._format_search_attempts(attempts)}"
                )
                self.log(f"Manga News recherche : {query} → 0 filtré / {len(raw_rows)} brut(s) — {self._format_search_attempts(attempts)}")
                self.fetch_selected_manga_news_series()
            else:
                self._reset_table_to_first_row(self.mn_results_table)
                error_text = f"\nErreur : {search_result.get('error')}" if search_result.get("error") else ""
                self.mn_raw.setPlainText(f"0 résultat Manga News. Essais : {self._format_search_attempts(attempts)}{error_text}")
                self.log(f"Manga News recherche sans résultat : {query} → {self._format_search_attempts(attempts)}")

        self.mn_raw.setPlainText(f"Recherche Manga News en cours…\nRequête : {query}\nFiltre manga uniquement : {'oui' if manga_only else 'non'}\nLes essais et erreurs seront affichés ici.")
        self._set_table(self.mn_results_table, ["Slug", "Titre", "Score match", "Score source", "Kind", "Media", "VF", "Volumes", "Titre VO", "URL"], [], stretch_from=1)
        self._reset_table_to_first_row(self.mn_results_table)
        self.run_worker("Recherche Manga News", lambda: self._search_manga_news_with_fallback(query, manga_only), done)

    def on_manga_news_result_selected(self) -> None:
        row = self._selected_row_index(self.mn_results_table)
        if row < 0 or row >= len(self.manga_news_results):
            self._set_selection_detail("manga_news.result", "", {"info": "Aucun résultat Manga News sélectionné."}, "")
            return
        result = self.manga_news_results[row]
        data = {
            "source": "manga_news",
            "slug": result.slug,
            "title": result.title,
            "kind": result.kind,
            "score": result.score,
            "media_kind": result.media_kind,
            "vf_status": result.vf_status,
            "vf_volumes": result.vf_volumes,
            "title_vo": result.title_vo,
            "translated_title": result.translated_title,
            "source_url": result.url,
        }
        self._set_selection_detail("manga_news.result", result.title, data, result.url)

    def fetch_manga_news_series_direct(self, slug: str = "", url: str = "") -> None:
        slug = str(slug or "").strip()
        url = str(url or "").strip()
        if not slug and not url:
            QMessageBox.warning(self, "Manga News", "Slug/URL Manga News vide")
            return
        generation = self.mn_context_generation

        def done(candidate: MangaNewsCandidate) -> None:
            if generation != self.mn_context_generation:
                return
            self.manga_news_candidate = candidate
            self.manga_news_results = []
            self.mn_results_table.setRowCount(0)
            self._set_selection_detail(
                "manga_news.result",
                candidate.title,
                MangaNewsClient.candidate_to_dict(candidate),
                candidate.source_url,
            )
            self.mn_raw.setPlainText(json_text(MangaNewsClient.candidate_to_dict(candidate)))
            self.preview_manga_news_series()
            direct = slug or url
            self.log(f"✅ Série Manga News chargée directement : {candidate.title} — {direct}")

        self.mn_raw.setPlainText(f"Chargement fiche Manga News en cours…\nCible : {slug or url}")
        if slug:
            self.run_worker("Chargement série Manga News direct", lambda: self.manga_news_client().get_series(slug), done)
        else:
            self.run_worker("Chargement série Manga News direct", lambda: self.manga_news_client().get_series_by_url(url), done)

    def fetch_selected_manga_news_series(self) -> None:
        row = self._selected_row_index(self.mn_results_table)
        if row < 0 or row >= len(self.manga_news_results):
            QMessageBox.warning(self, "Manga News", "Aucun résultat Manga News sélectionné")
            return
        result = self.manga_news_results[row]
        generation = self.mn_context_generation

        def done(candidate: MangaNewsCandidate) -> None:
            if generation != self.mn_context_generation:
                return
            self.manga_news_candidate = candidate
            self.mn_raw.setPlainText(json_text(MangaNewsClient.candidate_to_dict(candidate)))
            self.preview_manga_news_series()
            self.log(f"✅ Série Manga News chargée : {candidate.title} — {candidate.slug}")

        self.mn_raw.setPlainText(f"Chargement fiche Manga News en cours…\nSlug : {result.slug}\nTitre : {result.title}")
        self.run_worker("Chargement série Manga News", lambda: self.manga_news_client().get_series(result.slug), done)

    def _current_manga_news_series_id(self) -> str:
        explicit_id = self.mn_target_id.text().strip()
        if explicit_id:
            return explicit_id
        row = self._selected_row_index(self.mn_komga_series_table)
        if row >= 0 and row < len(self.mn_komga_series_rows):
            return self.mn_komga_series_rows[row].id
        return ""

    def load_manga_news_komga_books(self, series: Any = None) -> None:
        if not hasattr(self, "mn_komga_books_table"):
            return
        if series is None:
            row = self._selected_row_index(self.mn_komga_series_table)
            series = self.mn_komga_series_rows[row] if 0 <= row < len(self.mn_komga_series_rows) else None
        if series is None:
            return
        generation = self.mn_context_generation
        lib_id = self._library_id("manga_news") or getattr(series, "library_id", "")

        def done(rows: List[Any]) -> None:
            if generation != self.mn_context_generation:
                return
            self.mn_komga_book_rows = rows
            self._set_table(
                self.mn_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(x) for x in rows],
                stretch_from=1,
            )
            self.log(f"✅ Manga News : {len(rows)} tomes Komga chargés pour {getattr(series, 'title', '')}")
            self._reset_table_to_first_row(self.mn_komga_books_table)

        self.mn_komga_book_rows = []
        self.mn_komga_books_table.setRowCount(0)
        self.run_worker(
            "Chargement tomes Komga pour Manga News",
            lambda: self.komga_api().books(
                lib_id,
                getattr(series, "id", "") or None,
                page_size=200,
                direct_series_only=True,
                timeout=min(int(self.timeout_seconds.value()), 12),
            ),
            done,
        )

    def _current_manga_news_book(self) -> Any:
        row = self._selected_row_index(self.mn_komga_books_table)
        if row < 0 or row >= len(self.mn_komga_book_rows):
            return None
        return self.mn_komga_book_rows[row]

    def on_manga_news_komga_book_selected(self) -> None:
        book = self._current_manga_news_book()
        if book is None:
            return
        self._set_context_selection(book=book)
        title = getattr(book, "title", "")
        number = getattr(book, "number", "")
        if self.mn_volume_candidate:
            self.preview_manga_news_book()
        else:
            self.mn_book_preview.setPlainText(
                f"Tome Komga sélectionné : {title} ({number})\n"
                "Colle une URL volume Manga News puis clique sur Charger volume."
            )

    def _current_manga_news_slug_url(self) -> tuple[str, str]:
        if self.manga_news_candidate:
            slug = str(self.manga_news_candidate.slug or "").strip()
            url = str(self.manga_news_candidate.source_url or "").strip()
            if slug or url:
                return slug, url
        row = self._selected_row_index(self.mn_results_table)
        if 0 <= row < len(self.manga_news_results):
            result = self.manga_news_results[row]
            return str(result.slug or "").strip(), str(result.url or "").strip()
        row = self._selected_row_index(self.mn_komga_series_table)
        if 0 <= row < len(self.mn_komga_series_rows):
            return self._manga_news_link_for_series(self.mn_komga_series_rows[row])
        return "", ""

    def _current_manga_news_book_number(self) -> str:
        book = self._current_manga_news_book()
        if book is None:
            return ""
        metadata = getattr(book, "metadata", {}) if isinstance(getattr(book, "metadata", {}), dict) else {}
        for value in (
            getattr(book, "number", ""),
            metadata.get("number"),
            metadata.get("numberSort"),
            getattr(book, "number_sort", ""),
        ):
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def load_manga_news_volume_by_number(self) -> None:
        book = self._current_manga_news_book()
        if book is None:
            QMessageBox.warning(self, "Manga News", "Sélectionne d'abord un tome Komga")
            return
        slug, url = self._current_manga_news_slug_url()
        if not slug and url:
            slug = extract_manga_news_series_slug_from_url(url)
        if not slug:
            QMessageBox.warning(self, "Manga News", "Slug Manga News introuvable. Charge/matche d'abord la série Manga News.")
            return
        number = self._current_manga_news_book_number()
        if not number:
            QMessageBox.warning(self, "Manga News", "Numéro du tome Komga introuvable")
            return
        generation = self.mn_context_generation

        def done(candidate: MangaNewsVolumeCandidate) -> None:
            if generation != self.mn_context_generation:
                return
            self.mn_volume_candidate = candidate
            self.mn_volume_url.setText(candidate.source_url or "")
            self.mn_raw.setPlainText(json_text(MangaNewsClient.volume_candidate_to_dict(candidate)))
            self.preview_manga_news_book()
            self.log(f"✅ Volume Manga News chargé par numéro : {candidate.title} — {candidate.number or number}")

        self.mn_book_preview.setPlainText(f"Chargement volume Manga News par numéro…\nSlug : {slug}\nNuméro : {number}")
        self.run_worker("Chargement volume Manga News par numéro", lambda: self.manga_news_client().get_volume_by_number(slug, number), done)

    def load_manga_news_volume_by_url(self) -> None:
        url = self.mn_volume_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Manga News", "URL volume Manga News vide")
            return
        if self._current_manga_news_book() is None:
            QMessageBox.warning(self, "Manga News", "Sélectionne d'abord un tome Komga")
            return
        generation = self.mn_context_generation

        def done(candidate: MangaNewsVolumeCandidate) -> None:
            if generation != self.mn_context_generation:
                return
            self.mn_volume_candidate = candidate
            self.mn_raw.setPlainText(json_text(MangaNewsClient.volume_candidate_to_dict(candidate)))
            self.preview_manga_news_book()
            self.log(f"✅ Volume Manga News chargé : {candidate.title} — {candidate.number or candidate.volume_slug}")

        self.mn_book_preview.setPlainText(f"Chargement volume Manga News en cours…\nURL : {url}")
        self.run_worker("Chargement volume Manga News", lambda: self.manga_news_client().get_volume_by_url(url), done)

    def preview_manga_news_book(self) -> None:
        book = self._current_manga_news_book()
        candidate = self.mn_volume_candidate
        if book is None:
            self.mn_book_preview.setPlainText("Aucun tome Komga sélectionné.")
            return
        if candidate is None:
            self.mn_book_preview.setPlainText("Aucun volume Manga News chargé.")
            return
        book_id = getattr(book, "id", "")
        proposed = candidate.book_metadata

        def done(current: Dict[str, Any]) -> None:
            self._fill_metadata_table(self.mn_book_metadata_table, current, proposed, BOOK_METADATA_FIELDS)
            payload = self._payload_from_metadata_table(self.mn_book_metadata_table)
            endpoint = f"PATCH /api/v1/books/{book_id}/metadata"
            self.mn_book_preview.setPlainText(self._format_diff(current, payload, endpoint))

        self.run_worker("Preview Manga News tome", lambda: self._fetch_current_metadata("book", book_id), done)

    def apply_manga_news_book(self) -> None:
        book = self._current_manga_news_book()
        candidate = self.mn_volume_candidate
        if book is None or candidate is None:
            QMessageBox.warning(self, "Manga News", "Tome Komga ou volume Manga News manquant")
            return
        payload = self._normalize_payload_for_target("book", self._payload_from_metadata_table(self.mn_book_metadata_table))
        if not payload:
            QMessageBox.warning(self, "Manga News", "Payload tome vide")
            return
        if self.simulation_enabled():
            self.preview_manga_news_book()
            self.log("Simulation active : aucune écriture tome Manga News")
            return
        if not self._confirm_source_write(
            source_name="Manga News",
            target_label=f"tome {book.id}",
            field_count=len(payload),
        ):
            return

        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("book", book.id)
            self.backup.save_json(
                "operation",
                "book",
                book.id,
                {"current": current, "manga_news_volume": MangaNewsClient.volume_candidate_to_dict(candidate), "payload": payload},
                "avant PATCH Manga News tome",
            )
            return self._write_metadata_update(api, "book", book.id, payload, current, source="manga_news_book_manual", note="Application Manga News tome")

        self.run_worker("Application Manga News tome", do_apply, lambda r: self.log(f"✅ Metadata Manga News appliquée sur book:{book.id}"))

    def _fill_manga_news_metadata_table(self, current: Dict[str, Any], candidate: Dict[str, Any]) -> None:
        """Fill Manga News diff table with conservative defaults.

        Manga News is primarily trusted for summaries, but series lifecycle
        refresh fields remain critical: status and totalBookCount must be
        included when the source proposes a real change, including
        ONGOING -> ENDED. Other advanced fields stay conservative when Komga
        already has a value.
        """
        self._fill_series_preview_metadata_table(self.mn_series_metadata_table, current, candidate)
        conservative_when_present = {"publisher", "ageRating", "alternateTitles", "authors"}
        for row in range(self.mn_series_metadata_table.rowCount()):
            field_item = self.mn_series_metadata_table.item(row, 0)
            include_item = self.mn_series_metadata_table.item(row, 3)
            if field_item is None or include_item is None:
                continue
            field = self._metadata_field_key(field_item)
            if field == "status":
                # Keep the generic critical-field rule from _fill_metadata_table:
                # changed status is checked even when Komga is non-empty.
                continue
            if field == "totalBookCount":
                # Manga-News usually exposes the VF count. It is useful, but it
                # must not silently lower an existing Komga count, because the
                # library may contain VO/specials or another source may be more
                # complete. Lower counts remain visible and can be checked manually.
                try:
                    current_count = int(str((current or {}).get("totalBookCount") or "0").strip() or "0")
                    proposed_count = int(str(candidate.get("totalBookCount") or "0").strip() or "0")
                except Exception:
                    current_count = 0
                    proposed_count = 0
                if current_count > 0 and proposed_count > 0 and proposed_count < current_count:
                    include_item.setCheckState(Qt.Unchecked)
                    tooltip = (
                        "Manga News propose un totalBookCount inférieur au total Komga actuel. "
                        "Non inclus par défaut pour éviter une régression ; coche manuellement si c'est voulu."
                    )
                    field_item.setToolTip(tooltip)
                    new_item = self.mn_series_metadata_table.item(row, 2)
                    if new_item is not None:
                        new_item.setToolTip(f"{tooltip}\n\n{new_item.text()}")
                continue
            if field not in conservative_when_present:
                continue
            if not is_blank_metadata_value((current or {}).get(field)):
                include_item.setCheckState(Qt.Unchecked)
                tooltip = (
                    "Manga News : champ utile mais non inclus par défaut quand Komga a déjà une valeur. "
                    "Coche manuellement si tu veux remplacer/fusionner."
                )
                field_item.setToolTip(tooltip)
                new_item = self.mn_series_metadata_table.item(row, 2)
                if new_item is not None:
                    new_item.setToolTip(f"{tooltip}\n\n{new_item.text()}")

    def _manga_news_payload_from_metadata_maps(self, current: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._payload_from_metadata_maps(
            current,
            candidate,
            SERIES_METADATA_FIELDS,
            target_type="series",
            critical_changes=True,
        )
        # Final source-specific guard: never auto-inject noisy Manga-News fields.
        for noisy in ("tags", "genres", "themes", "strengths"):
            payload.pop(noisy, None)
        # Manga News is allowed to show these advanced fields, but batch auto-apply
        # should not alter them when Komga already has a value. The manual table
        # keeps them visible and lets the user check them explicitly.
        for conservative in ("publisher", "ageRating", "alternateTitles", "authors"):
            if not is_blank_metadata_value((current or {}).get(conservative)):
                payload.pop(conservative, None)

        # Do not auto-apply a lower Manga-News VF volume count over a higher
        # existing Komga count. This avoids damaging metadata when the local
        # library has VO/special volumes or another provider already gave a
        # broader count. Manual application remains possible from the table.
        if "totalBookCount" in payload:
            try:
                current_count = int(str((current or {}).get("totalBookCount") or "0").strip() or "0")
                proposed_count = int(str(payload.get("totalBookCount") or "0").strip() or "0")
            except Exception:
                current_count = 0
                proposed_count = 0
            if current_count > 0 and proposed_count > 0 and proposed_count < current_count:
                payload.pop("totalBookCount", None)
        return payload

    def preview_manga_news_series(self) -> None:
        if not self.manga_news_candidate:
            self.mn_series_preview.setPlainText("Aucune série Manga News chargée.")
            return
        series_id = self._current_manga_news_series_id()
        if not series_id:
            self.mn_series_preview.setPlainText("Aucune série Komga sélectionnée.")
            return
        proposed = self.manga_news_candidate.series_metadata

        def done(current: Dict[str, Any]) -> None:
            self._fill_manga_news_metadata_table(current, proposed)
            payload = self._payload_from_metadata_table(self.mn_series_metadata_table)
            endpoint = f"PATCH /api/v1/series/{series_id}/metadata"
            self.mn_series_preview.setPlainText(self._format_diff(current, payload, endpoint))

        self.run_worker("Preview Manga News série", lambda: self._fetch_current_series_preview_metadata(series_id), done)

    def apply_manga_news_series(self) -> None:
        series_id = self._current_manga_news_series_id()
        payload = self._payload_from_metadata_table(self.mn_series_metadata_table)
        if not series_id or not payload:
            QMessageBox.warning(self, "Manga News", "Série Komga ou payload vide")
            return
        if self.simulation_enabled():
            self.preview_manga_news_series()
            self.log("Simulation active : aucune écriture série Manga News")
            return
        if not self._confirm_source_write(
            source_name="Manga News",
            target_label=f"série {series_id}",
            field_count=len(payload),
        ):
            return

        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("series", series_id)
            self.backup.save_json(
                "operation",
                "series",
                series_id,
                {"current": current, "manga_news": MangaNewsClient.candidate_to_dict(self.manga_news_candidate) if self.manga_news_candidate else {}, "payload": payload},
                "avant PATCH Manga News série",
            )
            return self._write_metadata_update(api, "series", series_id, payload, current, source="manga_news_manual", note="Application Manga News série")

        self.run_worker("Application Manga News série", do_apply, lambda r: self.log(f"✅ Metadata Manga News appliquée sur série:{series_id}"))

    # ------------------------------------------------------------------
    # Prochaines sorties Manga News
    # ------------------------------------------------------------------
    @staticmethod
    def _next_release_existing_tag(metadata: Dict[str, Any]) -> str:
        for tag in value_as_list((metadata or {}).get("tags")):
            text = str(tag or "").strip()
            if text.casefold().startswith(NEXT_RELEASE_TAG_PREFIX):
                return text
        return ""

    @staticmethod
    def _next_release_tag(candidate: Any) -> str:
        if not candidate.number or not candidate.release_date:
            return ""
        try:
            yyyy, mm, dd = candidate.release_date.split("-", 2)
        except ValueError:
            return ""
        number = re.sub(r"\s+", "", str(candidate.number).strip())
        return f"{NEXT_RELEASE_TAG_PREFIX}{number}-{dd}.{mm}.{yyyy}"

    @staticmethod
    def _next_release_empty_reason(raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        details: List[str] = []
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        status = str(data.get("status") or raw.get("status") or "").strip()
        confidence = str(data.get("confidence") or raw.get("confidence") or "").strip()
        if status:
            details.append(status)
        if confidence:
            details.append(f"confidence={confidence}")
        warnings: List[str] = []
        for source in (raw.get("warnings"), data.get("warnings")):
            if isinstance(source, list):
                warnings.extend(str(item) for item in source if str(item or "").strip())
            elif str(source or "").strip():
                warnings.append(str(source).strip())
        if warnings:
            details.append("; ".join(warnings))
        errors = raw.get("errors")
        if isinstance(errors, list) and errors:
            details.append(" | ".join(str(item) for item in errors[:3]))
        return " — ".join(details)

    def _next_release_source(self) -> str:
        combo = getattr(self, "nr_source_combo", None)
        return str(combo.currentData() or "manga_news") if combo is not None else "manga_news"

    def _next_release_source_label(self) -> str:
        return "MangaBaka" if self._next_release_source() == "mangabaka" else "Manga News"

    def _next_release_link_for_series(self, series: Any) -> tuple[str, str]:
        if self._next_release_source() == "mangabaka":
            return self._mangabaka_link_for_series(series)
        return self._manga_news_link_for_series(series)

    def _next_release_rows_from_series(self, rows: List[Any]) -> List[Any]:
        received = len(rows or [])
        visible = self._filter_global_series_visibility(list(rows or []))
        hidden_global = received - len(visible)
        before_status = len(visible)
        if self.nr_only_not_ended.isChecked():
            visible = [
                row for row in visible
                if str((getattr(row, "metadata", {}) or {}).get("status") or "").upper() != "ENDED"
            ]
        hidden_status = before_status - len(visible)
        before_link = len(visible)
        if self.nr_require_source_link.isChecked():
            visible = [row for row in visible if any(self._next_release_link_for_series(row))]
        hidden_link = before_link - len(visible)
        self.nr_filter_counts = {
            "received": received,
            "visible": len(visible),
            "hidden_global": hidden_global,
            "hidden_status": hidden_status,
            "hidden_link": hidden_link,
        }
        return visible

    def _apply_next_release_series_filters(self) -> None:
        if not hasattr(self, "nr_series_table"):
            return
        self.nr_series_rows = self._next_release_rows_from_series(self.nr_series_unfiltered_rows)
        self._set_table(
            self.nr_series_table,
            self._series_table_headers(include_library=True),
            [self._series_table_row(row, include_library=True) for row in self.nr_series_rows],
            stretch_from=1,
            selection_mode=QAbstractItemView.ExtendedSelection,
            row_data=self.nr_series_rows,
        )
        counts = self.nr_filter_counts
        source_label = self._next_release_source_label()
        reasons: List[str] = []
        if counts.get("hidden_global"):
            reasons.append(f"{counts['hidden_global']} masquée(s) par la visibilité globale")
        if counts.get("hidden_status"):
            reasons.append(f"{counts['hidden_status']} terminée(s)")
        if counts.get("hidden_link"):
            reasons.append(f"{counts['hidden_link']} sans lien {source_label}")
        reason_text = "; ".join(reasons) if reasons else "aucune série masquée par les filtres"
        message = (
            f"Komga : {counts.get('received', 0)} série(s) reçue(s) → "
            f"{counts.get('visible', 0)} candidate(s) affichée(s) pour {source_label}. {reason_text}."
        )
        self.nr_detail.setPlainText(message)
        self._update_next_release_scope_labels()

    def load_next_release_series(self) -> None:
        lib_id = self._library_id("next_releases")
        search = self.nr_search.text().strip()
        generation = self._next_series_load_generation("next_releases")
        self.nr_detail.setPlainText("Chargement des séries depuis Komga… Les filtres seront appliqués localement à la réception.")

        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("next_releases", generation):
                return
            self.nr_series_unfiltered_rows = list(rows or [])
            self._apply_next_release_series_filters()
            source_label = self._next_release_source_label()
            counts = self.nr_filter_counts
            self.log(
                f"✅ Prochaines sorties {source_label} : {counts.get('received', 0)} reçue(s), "
                f"{counts.get('visible', 0)} affichée(s) après filtres locaux"
            )

        self.run_worker("Chargement séries prochaines sorties", lambda: self.komga_api().series(lib_id, search=search, page_size=200), done)

    def _selected_next_release_series(self) -> List[Any]:
        rows: List[Any] = []
        for index in self._selected_row_indexes(self.nr_series_table):
            if 0 <= index < len(self.nr_series_rows):
                rows.append(self.nr_series_rows[index])
        return rows

    def _update_next_release_scope_labels(self) -> None:
        selected_series = len(self._selected_next_release_series()) if hasattr(self, "nr_series_table") else 0
        loaded_series = len(getattr(self, "nr_series_rows", []) or [])
        pending_rows = [
            row for row in (getattr(self, "nr_rows", []) or [])
            if self._next_release_row_has_pending_change(row)
        ]
        selected_result_rows = self._selected_next_release_result_rows() if hasattr(self, "nr_results_table") else []
        selected_results = len(selected_result_rows)
        source_label = self._next_release_source_label() if hasattr(self, "nr_source_combo") else "source"
        self.nr_scope_label.setText(
            f"Périmètre : {self.nr_filter_counts.get('received', loaded_series)} reçue(s) de Komga → "
            f"{loaded_series} affichée(s) pour {source_label} — sélection : {selected_series}."
        )
        self.nr_result_scope_label.setText(
            f"Changements trouvés : {len(pending_rows)} — résultats sélectionnés : {selected_results}."
        )
        self.nr_scan_selected_button.setEnabled(selected_series > 0)
        self.nr_apply_selected_button.setEnabled(
            any(self._next_release_row_has_pending_change(row) for row in selected_result_rows)
        )
        self.nr_apply_all_button.setEnabled(bool(pending_rows))

    def scan_next_releases_selected(self) -> None:
        rows = self._selected_next_release_series()
        if not rows:
            QMessageBox.warning(self, "Prochaines sorties", "Aucune série sélectionnée")
            return
        self.scan_next_releases(rows, label="sélection")

    def scan_next_releases_all(self) -> None:
        rows = [row for row in self.nr_series_rows if str((getattr(row, "metadata", {}) or {}).get("status") or "").upper() != "ENDED"]
        if not rows:
            QMessageBox.warning(self, "Prochaines sorties", "Aucune série non terminée à scanner")
            return
        self.scan_next_releases(rows, label="toutes non terminées")

    def scan_next_releases(self, series_rows: List[Any], label: str) -> None:
        source = self._next_release_source()
        source_label = self._next_release_source_label()

        def do_scan() -> List[Dict[str, Any]]:
            mn_client = self.manga_news_client() if source == "manga_news" else None
            mbk_client = self.mangabaka_client() if source == "mangabaka" else None
            rows: List[Dict[str, Any]] = []
            total = len(series_rows)
            for index, series in enumerate(series_rows, start=1):
                title = getattr(series, "title", "")
                self.auto_match_progress_signal.emit(f"Prochaines sorties {source_label} {index}/{total} — {title}", index, total)
                metadata = getattr(series, "metadata", {}) or {}
                source_id, url = self._next_release_link_for_series(series)
                row: Dict[str, Any] = {
                    "series_id": getattr(series, "id", ""),
                    "title": title,
                    "source": source,
                    "source_id": source_id,
                    "slug": source_id,
                    "status": metadata.get("status", ""),
                    "old_tag": self._next_release_existing_tag(metadata),
                    "new_tag": "",
                    "volume": "",
                    "date": "",
                    "source_url": url,
                    "request_path": "",
                    "action": "",
                    "error": "",
                    "raw": {},
                }
                if not source_id and not url:
                    row["action"] = f"Ignoré : aucun lien {source_label}"
                    rows.append(row)
                    continue
                try:
                    if source == "mangabaka":
                        if not source_id:
                            raise ValueError("ID série MangaBaka introuvable dans le lien")
                        candidate = mbk_client.get_next_release(source_id) if mbk_client is not None else MangaBakaNextReleaseCandidate()
                        row["raw"] = MangaBakaClient.next_release_candidate_to_dict(candidate)
                        raw_data = candidate.raw if isinstance(candidate.raw, dict) else {}
                        row["source_url"] = candidate.source_url or url
                        row["volume"] = candidate.number
                        row["date"] = candidate.release_date
                        row["request_path"] = f"/v1/series/{source_id}"
                    else:
                        candidate = mn_client.get_next_release(slug=source_id, url=url) if mn_client is not None else MangaNewsNextReleaseCandidate()
                        row["raw"] = MangaNewsClient.next_release_candidate_to_dict(candidate)
                        raw_data = candidate.raw if isinstance(candidate.raw, dict) else {}
                        row["slug"] = str(raw_data.get("request_slug") or row.get("slug") or "")
                        row["source_id"] = row["slug"]
                        row["request_path"] = str(raw_data.get("request_path") or "")
                        row["source_url"] = candidate.source_url or url
                        row["volume"] = candidate.number
                        row["date"] = candidate.release_date
                    tag = self._next_release_tag(candidate)
                    row["new_tag"] = tag
                    if not tag:
                        row["action"] = "Aucune prochaine sortie"
                        row["error"] = self._next_release_empty_reason(candidate.raw)
                        if source == "mangabaka" and not row["error"]:
                            row["error"] = "MangaBaka ne fournit pas de date/volume de prochaine sortie exploitable pour cette fiche."
                    elif tag == row["old_tag"]:
                        row["action"] = "Déjà à jour"
                    else:
                        fallback = raw_data.get("fallback") if isinstance(raw_data, dict) else None
                        row["action"] = "À appliquer (date série)" if fallback else "À appliquer"
                except Exception as exc:
                    row["action"] = "Erreur"
                    row["error"] = str(exc)
                rows.append(row)
            return rows

        def done(rows: List[Dict[str, Any]]) -> None:
            self.nr_rows = rows
            self._refresh_next_release_results_table()
            found = sum(1 for row in rows if row.get("new_tag"))
            self.nr_detail.setPlainText(json_text({"scope": label, "rows": len(rows), "tags_found": found}))
            self.log(f"✅ Prochaines sorties scannées ({label}) : {found}/{len(rows)} tag(s) trouvé(s)")

        self.run_worker(f"Scan prochaines sorties {source_label} ({label})", do_scan, done)

    @staticmethod
    def _next_release_row_has_pending_change(row: Dict[str, Any]) -> bool:
        new_tag = str((row or {}).get("new_tag") or "").strip()
        old_tag = str((row or {}).get("old_tag") or "").strip()
        return bool(new_tag) and new_tag != old_tag

    def _visible_next_release_rows(self) -> List[Dict[str, Any]]:
        rows = list(self.nr_rows or [])
        if getattr(self, "nr_show_only_changes", None) is not None and self.nr_show_only_changes.isChecked():
            rows = [row for row in rows if self._next_release_row_has_pending_change(row)]
        return rows

    def _refresh_next_release_results_table(self) -> None:
        self.nr_visible_rows = self._visible_next_release_rows()
        self._set_table(
            self.nr_results_table,
            ["Series ID", "Titre", "Source", "Source ID", "Status", "Ancien tag", "Nouveau tag", "Tome", "Date", "Action", "URL", "Erreur"],
            [
                [
                    row.get("series_id", ""),
                    row.get("title", ""),
                    row.get("source", ""),
                    row.get("source_id", "") or row.get("slug", ""),
                    row.get("status", ""),
                    row.get("old_tag", ""),
                    row.get("new_tag", ""),
                    row.get("volume", ""),
                    row.get("date", ""),
                    row.get("action", ""),
                    row.get("source_url", ""),
                    row.get("error", ""),
                ]
                for row in self.nr_visible_rows
            ],
            stretch_from=1,
            selection_mode=QAbstractItemView.ExtendedSelection,
            row_data=self.nr_visible_rows,
        )
        self._update_next_release_scope_labels()

    def on_next_release_result_selected(self) -> None:
        row = self._selected_row_index(self.nr_results_table)
        if row < 0 or row >= len(self.nr_visible_rows):
            return
        self.nr_detail.setPlainText(json_text(self.nr_visible_rows[row]))

    def _selected_next_release_result_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for index in self._selected_row_indexes(self.nr_results_table):
            if 0 <= index < len(self.nr_visible_rows):
                rows.append(self.nr_visible_rows[index])
        return rows

    def apply_next_release_tags_selected(self) -> None:
        rows = self._selected_next_release_result_rows()
        if not rows:
            QMessageBox.warning(self, "Prochaines sorties", "Aucun résultat sélectionné")
            return
        self.apply_next_release_tags(rows, label="sélection")

    def apply_next_release_tags_all(self) -> None:
        rows = [row for row in self.nr_rows if self._next_release_row_has_pending_change(row)]
        if not rows:
            QMessageBox.warning(self, "Prochaines sorties", "Aucun changement de tag trouvé à appliquer")
            return
        self.apply_next_release_tags(rows, label="tous tags trouvés")

    def _next_release_payload_for_series(self, current: Dict[str, Any], next_tag: str) -> Dict[str, Any]:
        tags = [
            str(tag).strip()
            for tag in value_as_list((current or {}).get("tags"))
            if str(tag).strip() and not str(tag).strip().casefold().startswith(NEXT_RELEASE_TAG_PREFIX)
        ]
        if next_tag:
            tags.append(next_tag)
        return {"tags": tags}

    def apply_next_release_tags(self, rows: List[Dict[str, Any]], label: str) -> None:
        candidates = [row for row in rows if row.get("series_id") and self._next_release_row_has_pending_change(row)]
        if not candidates:
            QMessageBox.warning(self, "Prochaines sorties", "Aucune ligne avec changement de tag valide")
            return
        simulation = self.simulation_enabled()
        if not simulation:
            confirmation = (
                "Appliquer les tags de prochaines sorties ?\n\n"
                f"Périmètre : {label}\n"
                f"Séries modifiées : {len(candidates)}\n\n"
                "Chaque série sera sauvegardée avant l'écriture."
            )
            if QMessageBox.question(self, "Confirmer l'écriture des tags", confirmation) != QMessageBox.Yes:
                return

        def do_apply() -> Dict[str, Any]:
            api = self.komga_api()
            report_rows: List[Dict[str, Any]] = []
            for index, row in enumerate(candidates, start=1):
                report = dict(row)
                report["index"] = index
                try:
                    series_id = str(row.get("series_id") or "")
                    current = self._fetch_current_metadata("series", series_id)
                    payload = self._next_release_payload_for_series(current, str(row.get("new_tag") or ""))
                    report["payload_fields"] = "; ".join(payload.keys())
                    if payload.get("tags") == value_as_list(current.get("tags")):
                        report["apply_status"] = "OK : déjà à jour"
                    elif simulation:
                        report["apply_status"] = "OK simulation"
                    else:
                        row_source = str(row.get("source") or self._next_release_source())
                        row_source_label = "MangaBaka" if row_source == "mangabaka" else "Manga News"
                        self.backup.save_json(
                            "operation",
                            "series",
                            series_id,
                            {"current": current, "next_release": row, "payload": payload},
                            "avant PATCH tag prochaine sortie",
                        )
                        self._write_metadata_update(api, "series", series_id, payload, current, source=f"{row_source}_next_release", note=f"Tag prochaine sortie {row_source_label}")
                        report["apply_status"] = "OK appliqué"
                except Exception as exc:
                    report["apply_status"] = "Erreur"
                    report["error"] = str(exc)
                report_rows.append(report)
            return {"rows": report_rows}

        def done(result: Dict[str, Any]) -> None:
            report_rows = result.get("rows") or []
            self._show_batch_report(
                f"Compte rendu tags prochaines sorties ({label})",
                report_rows,
                columns=[
                    ("index", "#"),
                    ("series_id", "Series ID"),
                    ("title", "Titre"),
                    ("old_tag", "Ancien tag"),
                    ("new_tag", "Nouveau tag"),
                    ("apply_status", "Statut"),
                    ("error", "Erreur"),
                ],
                status_filter_key="apply_status",
            )
            self.log(f"✅ Tags prochaines sorties appliqués ({label}) : {len(report_rows)} ligne(s)")

        self.run_worker(f"Application tags prochaines sorties ({label})", do_apply, done)

    # ------------------------------------------------------------------
    # ComicVine
    # ------------------------------------------------------------------
    def load_comicvine_komga_series(self) -> None:
        lib_id = self._library_id("comicvine")
        search = self.cv_komga_search.text().strip()
        generation = self._next_series_load_generation("comicvine")

        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("comicvine", generation):
                return
            rows = self._filter_global_series_visibility(rows)
            self._source_series_unfiltered_rows["cv"] = rows
            self._refresh_source_link_filter_options("cv", rows)
            rows, active_filters = self._apply_source_series_filters("cv", rows)
            self.cv_komga_series_rows = rows
            self._set_table(
                self.cv_komga_series_table,
                self._series_table_headers(include_history=True),
                self._series_table_rows_for_source("cv", rows),
                stretch_from=1,
            )
            suffix = f" — {', '.join(active_filters)}" if active_filters else ""
            self.log(f"✅ ComicVine : {len(rows)} séries Komga chargées{suffix}")

        self.run_worker("Chargement séries Komga pour ComicVine", lambda: self.komga_api().series(lib_id, search=search, page_size=200), done)

    def _clear_comicvine_views(self, message: str = "") -> None:
        self.comicvine_results = []
        self.comicvine_candidate = None
        self.cv_komga_book_rows = []
        self.cv_issue_rows = []
        self.cv_issue_candidates_by_id = {}
        self.cv_book_matches = []
        for table in (getattr(self, "cv_results_table", None),):
            if table is not None:
                table.setRowCount(0)
        for table in (
            getattr(self, "cv_komga_books_table", None),
            getattr(self, "cv_issues_table", None),
            getattr(self, "cv_book_match_table", None),
        ):
            if table is not None:
                table.setRowCount(0)
        self._fill_comicvine_metadata_table({}, {})
        if hasattr(self, "cv_book_metadata_table"):
            self._fill_metadata_table(self.cv_book_metadata_table, {}, {}, BOOK_METADATA_FIELDS)
        self.cv_series_preview.setPlainText(message or "Sélectionne une série Komga puis un résultat ComicVine.")
        if hasattr(self, "cv_book_preview"):
            self.cv_book_preview.setPlainText("Charge un volume ComicVine puis vérifie le matching des tomes.")
        self.cv_raw.setPlainText(message or "")
        self._set_selection_detail("comicvine.result", "", {"info": message or "Aucun résultat ComicVine sélectionné."}, "")
        self._set_selection_detail("comicvine.issue", "", {"info": "Aucune issue ComicVine sélectionnée."}, "")

    def _select_comicvine_komga_series_row_by_id(self, series_id: str) -> bool:
        if not series_id:
            return False
        for row, series in enumerate(self.cv_komga_series_rows):
            if getattr(series, "id", "") == series_id:
                self.cv_komga_series_table.blockSignals(True)
                self.cv_komga_series_table.clearSelection()
                self.cv_komga_series_table.selectRow(row)
                item = self.cv_komga_series_table.item(row, 0)
                if item is not None:
                    self.cv_komga_series_table.scrollToItem(item)
                self.cv_komga_series_table.blockSignals(False)
                return True
        return False

    def on_comicvine_komga_series_selected(self) -> None:
        row = self._selected_row_index(self.cv_komga_series_table)
        if row < 0 or row >= len(self.cv_komga_series_rows):
            return
        series = self.cv_komga_series_rows[row]
        self._set_context_selection(series=series)
        self.cv_context_generation += 1
        self.cv_target_id.setText(series.id)
        self.cv_query.setText(clean_search_title(series.title))
        volume_id, url = self._comicvine_link_for_series(series)
        if volume_id:
            self._clear_comicvine_views(f"Série Komga sélectionnée : {series.title}\nLien ComicVine existant détecté. Chargement direct…")
            self.load_comicvine_komga_books(series)
            self.fetch_comicvine_series_direct(volume_id=volume_id, url=url)
        else:
            self._clear_comicvine_views(f"Série Komga sélectionnée : {series.title}\nRecherche ComicVine en cours…")
            self.load_comicvine_komga_books(series)
            self.search_comicvine()

    def use_selected_for_comicvine(self) -> None:
        row = self._selected_row_index(self.cv_komga_series_table)
        if row >= 0 and row < len(self.cv_komga_series_rows):
            self.cv_target_id.setText(self.cv_komga_series_rows[row].id)
            self.cv_query.setText(clean_search_title(self.cv_komga_series_rows[row].title))
            return
        sid = self._selected_id_from_table(self.series_table)
        if sid:
            row = self._selected_row_index(self.series_table)
            if row >= 0 and row < len(self.series_rows):
                series = self.series_rows[row]
                self.cv_target_id.setText(sid)
                self.cv_query.setText(clean_search_title(series.title))

    def search_comicvine(self) -> None:
        raw_query = self.cv_query.text().strip()
        query = clean_search_title(raw_query)
        if query != raw_query:
            self.cv_query.setText(query)
        if not query:
            QMessageBox.warning(self, "ComicVine", "Recherche vide")
            return
        if not self.comicvine_enabled.isChecked():
            QMessageBox.warning(self, "ComicVine", "Module ComicVine désactivé dans l'onglet Connexion")
            return
        if not self.comicvine_api_key.text().strip():
            QMessageBox.warning(self, "ComicVine", "Clé API ComicVine absente")
            return
        generation = self.cv_context_generation
        self._record_enrichment_search("cv", self._current_comicvine_series_id())

        def done(search_result: Dict[str, Any]) -> None:
            if generation != self.cv_context_generation:
                return
            rows = search_result.get("rows") or []
            ranked = ranked_title_results(query, rows)
            rows = [row for _, row in ranked]
            used_query = str(search_result.get("used_query") or query)
            attempts = search_result.get("attempts") or []
            self.comicvine_results = rows
            self._set_table(self.cv_results_table, ["ID", "Titre", "Score match", "Année", "Publisher", "Issues", "Résumé", "URL"], [
                [r.id, r.title, f"{score:.3f}", r.start_year, r.publisher, "" if r.issue_count is None else r.issue_count, r.deck, r.source_url]
                for score, r in ranked
            ], stretch_from=1)
            if rows:
                self._reset_table_to_first_row(self.cv_results_table)
                suffix = "" if used_query == query else f"\nVariante utilisée : {used_query}"
                self.cv_raw.setPlainText(
                    f"{len(rows)} résultat(s), triés par score. La première ligne est sélectionnée et chargée automatiquement."
                    f"{suffix}\nEssais : {self._format_search_attempts(attempts)}"
                )
                self.log(f"ComicVine recherche : {query} → {self._format_search_attempts(attempts)}")
                self.fetch_selected_comicvine_series()
            else:
                self._reset_table_to_first_row(self.cv_results_table)
                error_text = f"\nErreur : {search_result.get('error')}" if search_result.get("error") else ""
                self.cv_raw.setPlainText(f"0 résultat ComicVine. Essais : {self._format_search_attempts(attempts)}{error_text}")
                self.log(f"ComicVine recherche sans résultat : {query} → {self._format_search_attempts(attempts)}")

        self.cv_raw.setPlainText(f"Recherche ComicVine en cours…\nRequête : {query}\nLes essais et erreurs seront affichés ici.")
        self._set_table(self.cv_results_table, ["ID", "Titre", "Score match", "Année", "Publisher", "Issues", "Résumé", "URL"], [], stretch_from=1)
        self._reset_table_to_first_row(self.cv_results_table)
        self.run_worker("Recherche ComicVine", lambda: self._search_comicvine_with_fallback(query), done)

    def on_comicvine_result_selected(self) -> None:
        row = self._selected_row_index(self.cv_results_table)
        if row < 0 or row >= len(self.comicvine_results):
            self._set_selection_detail("comicvine.result", "", {"info": "Aucun résultat ComicVine sélectionné."}, "")
            return
        result = self.comicvine_results[row]
        data = {
            "source": "comicvine",
            "id": result.id,
            "title": result.title,
            "start_year": result.start_year,
            "publisher": result.publisher,
            "issue_count": result.issue_count,
            "source_url": result.source_url,
            "image_url": result.image_url,
            "deck": result.deck,
        }
        self._set_selection_detail("comicvine.result", result.title, data, result.source_url)

    def fetch_comicvine_series_direct(self, volume_id: str = "", url: str = "") -> None:
        vid = str(volume_id or "").strip() or extract_comicvine_volume_id_from_url(url)
        if not vid:
            QMessageBox.warning(self, "ComicVine", "ID/URL ComicVine vide")
            return
        generation = self.cv_context_generation

        def done(candidate: ComicVineCandidate) -> None:
            if generation != self.cv_context_generation:
                return
            self.comicvine_candidate = candidate
            self.comicvine_results = []
            self.cv_results_table.setRowCount(0)
            self._set_selection_detail("comicvine.result", candidate.title, ComicVineClient.candidate_to_dict(candidate), candidate.source_url)
            self.cv_raw.setPlainText(json_text(ComicVineClient.candidate_to_dict(candidate)))
            self.preview_comicvine_series()
            self.load_comicvine_issues_for_current_volume()
            self.log(f"✅ Série ComicVine chargée directement : {candidate.title} — {vid}")

        self.cv_raw.setPlainText(f"Chargement fiche ComicVine en cours…\nVolume ID : {vid}")
        self.run_worker("Chargement série ComicVine direct", lambda: self.comicvine_client().get_volume(vid), done)

    def fetch_selected_comicvine_series(self) -> None:
        row = self._selected_row_index(self.cv_results_table)
        if row < 0 or row >= len(self.comicvine_results):
            QMessageBox.warning(self, "ComicVine", "Aucun résultat ComicVine sélectionné")
            return
        result = self.comicvine_results[row]
        generation = self.cv_context_generation

        def done(candidate: ComicVineCandidate) -> None:
            if generation != self.cv_context_generation:
                return
            self.comicvine_candidate = candidate
            self.cv_raw.setPlainText(json_text(ComicVineClient.candidate_to_dict(candidate)))
            self.preview_comicvine_series()
            self.load_comicvine_issues_for_current_volume()
            self.log(f"✅ Série ComicVine chargée : {candidate.title} — {candidate.volume_id}")

        self.cv_raw.setPlainText(f"Chargement fiche ComicVine en cours…\nVolume ID : {result.id}\nTitre : {result.title}")
        self.run_worker("Chargement série ComicVine", lambda: self.comicvine_client().get_volume(result.id), done)

    def _current_comicvine_series_id(self) -> str:
        explicit_id = self.cv_target_id.text().strip()
        if explicit_id:
            return explicit_id
        row = self._selected_row_index(self.cv_komga_series_table)
        if row >= 0 and row < len(self.cv_komga_series_rows):
            return self.cv_komga_series_rows[row].id
        return ""

    def _fill_comicvine_metadata_table(self, current: Dict[str, Any], candidate: Dict[str, Any]) -> None:
        self._fill_series_preview_metadata_table(self.cv_series_metadata_table, current, candidate)
        conservative_when_present = {"publisher", "authors"}
        for row in range(self.cv_series_metadata_table.rowCount()):
            field_item = self.cv_series_metadata_table.item(row, 0)
            include_item = self.cv_series_metadata_table.item(row, 3)
            if field_item is None or include_item is None:
                continue
            field = self._metadata_field_key(field_item)
            if field not in conservative_when_present:
                continue
            if not is_blank_metadata_value((current or {}).get(field)):
                include_item.setCheckState(Qt.Unchecked)
                tooltip = "ComicVine : champ visible mais non inclus par défaut quand Komga a déjà une valeur."
                field_item.setToolTip(tooltip)
                new_item = self.cv_series_metadata_table.item(row, 2)
                if new_item is not None:
                    new_item.setToolTip(f"{tooltip}\n\n{new_item.text()}")

    def load_comicvine_komga_books(self, series: Any = None) -> None:
        if series is None:
            row = self._selected_row_index(self.cv_komga_series_table)
            series = self.cv_komga_series_rows[row] if 0 <= row < len(self.cv_komga_series_rows) else None
        if series is None:
            return
        generation = self.cv_context_generation
        lib_id = self._library_id("comicvine") or getattr(series, "library_id", "")

        def done(rows: List[Any]) -> None:
            if generation != self.cv_context_generation:
                return
            self.cv_komga_book_rows = rows
            self._set_table(
                self.cv_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(x) for x in rows],
                stretch_from=1,
            )
            self.log(f"✅ ComicVine : {len(rows)} tomes Komga chargés pour {getattr(series, 'title', '')}")
            self.match_comicvine_tomes()

        self.cv_komga_book_rows = []
        if hasattr(self, "cv_komga_books_table"):
            self.cv_komga_books_table.setRowCount(0)
        self.run_worker(
            "Chargement tomes Komga pour ComicVine",
            lambda: self.komga_api().books(
                lib_id,
                getattr(series, "id", "") or None,
                page_size=200,
                direct_series_only=True,
                timeout=min(int(self.timeout_seconds.value()), 12),
            ),
            done,
        )

    def _comicvine_issue_source_rows(self) -> List[SourceBookRow]:
        return [
            SourceBookRow(
                id=issue.issue_id,
                number=issue.issue_number,
                title=issue.title,
                url=issue.source_url,
                metadata=issue.book_metadata,
                raw=issue,
            )
            for issue in self.cv_issue_rows
        ]

    def load_comicvine_issues_for_current_volume(self) -> None:
        if not self.comicvine_candidate or not self.comicvine_candidate.volume_id:
            QMessageBox.warning(self, "ComicVine", "Charge d'abord une série/volume ComicVine.")
            return
        generation = self.cv_context_generation
        volume_id = self.comicvine_candidate.volume_id

        def done(rows: List[ComicVineIssueCandidate]) -> None:
            if generation != self.cv_context_generation:
                return
            self.cv_issue_rows = rows
            self.cv_issue_candidates_by_id = {row.issue_id: row for row in rows if row.issue_id}
            self._set_table(
                self.cv_issues_table,
                ["ID", "N°", "Titre", "Date", "URL"],
                [
                    [
                        issue.issue_id,
                        issue.issue_number,
                        issue.title,
                        issue.book_metadata.get("releaseDate", ""),
                        issue.source_url,
                    ]
                    for issue in rows
                ],
                stretch_from=2,
            )
            self._reset_table_to_first_row(self.cv_issues_table, select=False)
            self.match_comicvine_tomes()
            self.log(f"✅ ComicVine : {len(rows)} issue(s) chargée(s) pour volume {volume_id}")

        self.cv_book_preview.setPlainText(f"Chargement des issues ComicVine du volume {volume_id}…")
        self.run_worker("Chargement issues ComicVine", lambda: self.comicvine_client().list_volume_issues(volume_id), done)

    def on_comicvine_issue_selected(self) -> None:
        row = self._selected_row_index(self.cv_issues_table)
        if row < 0 or row >= len(self.cv_issue_rows):
            self._set_selection_detail("comicvine.issue", "", {"info": "Aucune issue ComicVine sélectionnée."}, "")
            return
        issue = self.cv_issue_rows[row]
        self._set_selection_detail("comicvine.issue", issue.title, ComicVineClient.issue_candidate_to_dict(issue), issue.source_url)

    def fetch_selected_comicvine_issue(self) -> None:
        row = self._selected_row_index(self.cv_issues_table)
        if row < 0 or row >= len(self.cv_issue_rows):
            QMessageBox.warning(self, "ComicVine", "Aucune issue ComicVine sélectionnée")
            return
        issue = self.cv_issue_rows[row]
        generation = self.cv_context_generation

        def done(candidate: ComicVineIssueCandidate) -> None:
            if generation != self.cv_context_generation:
                return
            self.cv_issue_rows[row] = candidate
            self.cv_issue_candidates_by_id[candidate.issue_id] = candidate
            self.cv_raw.setPlainText(json_text(ComicVineClient.issue_candidate_to_dict(candidate)))
            self.on_comicvine_issue_selected()
            self.match_comicvine_tomes()
            self.on_comicvine_book_match_selected()
            self.log(f"✅ Issue ComicVine chargée : {candidate.title} — {candidate.issue_id}")

        self.run_worker("Chargement issue ComicVine", lambda: self.comicvine_client().get_issue(issue.issue_id), done)

    def match_comicvine_tomes(self) -> None:
        if not hasattr(self, "cv_book_match_table"):
            return
        source_rows = self._comicvine_issue_source_rows()
        self.cv_book_matches = match_source_books(self.cv_komga_book_rows, source_rows) if source_rows else []
        rows: List[List[Any]] = []
        for match in self.cv_book_matches:
            book = self.cv_komga_book_rows[match["book_index"]] if 0 <= match.get("book_index", -1) < len(self.cv_komga_book_rows) else None
            issue = self.cv_issue_rows[match["source_index"]] if 0 <= match.get("source_index", -1) < len(self.cv_issue_rows) else None
            rows.append([
                getattr(book, "id", "") if book else "",
                getattr(book, "number", "") if book else "",
                getattr(book, "title", "") if book else "",
                issue.issue_id if issue else "",
                issue.issue_number if issue else "",
                issue.title if issue else "",
                match.get("confidence", ""),
                match.get("score", ""),
                issue.source_url if issue else "",
            ])
        self._set_table(
            self.cv_book_match_table,
            ["Book ID", "N° Komga", "Titre Komga", "Issue ID", "N° CV", "Titre CV", "Confiance", "Score", "URL issue"],
            rows,
            stretch_from=2,
            selection_mode=QAbstractItemView.ExtendedSelection,
        )
        self._reset_table_to_first_row(self.cv_book_match_table, select=False)

    def _selected_comicvine_book_match_context(self) -> Dict[str, Any]:
        row = self._selected_row_index(self.cv_book_match_table)
        if row < 0 or row >= len(self.cv_book_matches):
            return {}
        match = self.cv_book_matches[row]
        book = self.cv_komga_book_rows[match["book_index"]] if 0 <= match.get("book_index", -1) < len(self.cv_komga_book_rows) else None
        issue = self.cv_issue_rows[match["source_index"]] if 0 <= match.get("source_index", -1) < len(self.cv_issue_rows) else None
        return {"match": match, "book": book, "issue": issue}

    def _selected_comicvine_book_match_contexts(self) -> List[Dict[str, Any]]:
        contexts: List[Dict[str, Any]] = []
        for row in self._selected_row_indexes(self.cv_book_match_table):
            if row < 0 or row >= len(self.cv_book_matches):
                continue
            match = self.cv_book_matches[row]
            book = self.cv_komga_book_rows[match["book_index"]] if 0 <= match.get("book_index", -1) < len(self.cv_komga_book_rows) else None
            issue = self.cv_issue_rows[match["source_index"]] if 0 <= match.get("source_index", -1) < len(self.cv_issue_rows) else None
            contexts.append({"match": match, "book": book, "issue": issue})
        return contexts

    def _all_matched_comicvine_book_contexts(self) -> List[Dict[str, Any]]:
        contexts: List[Dict[str, Any]] = []
        for match in self.cv_book_matches:
            book_index = match.get("book_index", -1)
            source_index = match.get("source_index", -1)
            if not (0 <= book_index < len(self.cv_komga_book_rows)):
                continue
            if not (0 <= source_index < len(self.cv_issue_rows)):
                continue
            book = self.cv_komga_book_rows[book_index]
            issue = self.cv_issue_rows[source_index]
            if book and issue:
                contexts.append({"match": match, "book": book, "issue": issue})
        return contexts

    def on_comicvine_book_match_selected(self) -> None:
        ctx = self._selected_comicvine_book_match_context()
        book = ctx.get("book") if ctx else None
        issue = ctx.get("issue") if ctx else None
        if not book or not issue:
            self._fill_metadata_table(self.cv_book_metadata_table, {}, {}, BOOK_METADATA_FIELDS)
            self.cv_book_preview.setPlainText("Sélectionne une ligne matchée avec un tome Komga et une issue ComicVine.")
            return
        current = book.metadata or {}
        proposed = issue.book_metadata
        self._fill_metadata_table(self.cv_book_metadata_table, current, proposed, BOOK_METADATA_FIELDS)
        self.preview_comicvine_book()

    def preview_comicvine_book(self) -> None:
        ctx = self._selected_comicvine_book_match_context()
        book = ctx.get("book") if ctx else None
        issue = ctx.get("issue") if ctx else None
        if not book or not issue:
            self.cv_book_preview.setPlainText("Aucun tome Komga / issue ComicVine matché sélectionné.")
            return
        current = book.metadata or {}
        proposed = issue.book_metadata
        self._fill_metadata_table(self.cv_book_metadata_table, current, proposed, BOOK_METADATA_FIELDS)
        payload = self._payload_from_metadata_table(self.cv_book_metadata_table)
        endpoint = f"PATCH /api/v1/books/{book.id}/metadata"
        self.cv_book_preview.setPlainText(self._format_diff(current, payload, endpoint))

    def apply_comicvine_book(self) -> None:
        contexts = [ctx for ctx in self._selected_comicvine_book_match_contexts() if ctx.get("book") and ctx.get("issue")]
        if not contexts:
            QMessageBox.warning(self, "ComicVine", "Aucun tome Komga matché sélectionné")
            return
        if len(contexts) > 1:
            self.apply_comicvine_books(contexts)
            return
        ctx = contexts[0]
        book = ctx["book"]
        issue = ctx["issue"]
        payload = self._normalize_payload_for_target("book", self._payload_from_metadata_table(self.cv_book_metadata_table))
        if not payload:
            QMessageBox.warning(self, "ComicVine", "Payload tome vide")
            return
        if self.simulation_enabled():
            self.preview_comicvine_book()
            self.log("Simulation active : aucune écriture tome ComicVine")
            return
        if not self._confirm_source_write(
            source_name="ComicVine",
            target_label=f"tome {book.id}",
            field_count=len(payload),
        ):
            return

        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("book", book.id)
            self.backup.save_json(
                "operation",
                "book",
                book.id,
                {"current": current, "comicvine_issue": ComicVineClient.issue_candidate_to_dict(issue), "payload": payload},
                "avant PATCH ComicVine tome",
            )
            return self._write_metadata_update(api, "book", book.id, payload, current, source="comicvine_book_manual", note="Application ComicVine tome")

        self.run_worker("Application ComicVine tome", do_apply, lambda r: self.log(f"✅ Metadata ComicVine appliquée sur book:{book.id}"))

    def apply_all_matched_comicvine_books(self) -> None:
        contexts = self._all_matched_comicvine_book_contexts()
        if not contexts:
            QMessageBox.warning(self, "ComicVine", "Aucun tome Komga avec issue ComicVine matchée")
            return
        self.apply_comicvine_books(contexts)

    def apply_comicvine_books(self, contexts: List[Dict[str, Any]]) -> None:
        total = len(contexts)
        simulation = self.simulation_enabled()
        if not simulation and not self._confirm_source_write(
            source_name="ComicVine",
            target_label=f"{total} tome(s) matché(s)",
        ):
            return

        def do_apply() -> Dict[str, Any]:
            api = self.komga_api()
            rows: List[Dict[str, Any]] = []
            for index, ctx in enumerate(contexts, start=1):
                book = ctx.get("book")
                issue = ctx.get("issue")
                report = {
                    "index": index,
                    "book_id": getattr(book, "id", "") if book else "",
                    "komga_title": getattr(book, "title", "") if book else "",
                    "issue_id": getattr(issue, "issue_id", "") if issue else "",
                    "comicvine_title": getattr(issue, "title", "") if issue else "",
                    "status": "",
                    "payload_fields": "",
                    "error": "",
                }
                try:
                    if not book or not issue:
                        report["status"] = "Ignoré : match incomplet"
                    else:
                        current = self._fetch_current_metadata("book", book.id)
                        payload = self._payload_from_metadata_maps(current, issue.book_metadata, BOOK_METADATA_FIELDS, target_type="book")
                        report["payload_fields"] = "; ".join(payload.keys())
                        if not payload:
                            report["status"] = "OK : aucun changement"
                        elif simulation:
                            report["status"] = "OK simulation"
                        else:
                            self.backup.save_json(
                                "operation",
                                "book",
                                book.id,
                                {"current": current, "comicvine_issue": ComicVineClient.issue_candidate_to_dict(issue), "payload": payload},
                                "avant PATCH ComicVine tomes",
                            )
                            self._write_metadata_update(api, "book", book.id, payload, current, source="comicvine_books_multi", note="Application ComicVine tomes")
                            report["status"] = "OK appliqué"
                except Exception as exc:
                    report["status"] = "Erreur"
                    report["error"] = str(exc)
                rows.append(report)
            return {"rows": rows}

        def done(result: Dict[str, Any]) -> None:
            rows = result.get("rows") or []
            self._show_batch_report(
                "Compte rendu application ComicVine tomes",
                rows,
                columns=[
                    ("index", "#"),
                    ("book_id", "Book ID"),
                    ("komga_title", "Tome Komga"),
                    ("issue_id", "Issue ID"),
                    ("comicvine_title", "Issue ComicVine"),
                    ("status", "Statut"),
                    ("payload_fields", "Champs"),
                    ("error", "Erreur"),
                ],
                status_filter_key="status",
            )
            self.log(f"✅ Application ComicVine tomes terminée : {total} ligne(s)")

        self.run_worker("Application ComicVine tomes", do_apply, done)

    def preview_comicvine_series(self) -> None:
        if not self.comicvine_candidate:
            self.cv_series_preview.setPlainText("Aucune série ComicVine chargée.")
            return
        series_id = self._current_comicvine_series_id()
        if not series_id:
            self.cv_series_preview.setPlainText("Aucune série Komga sélectionnée.")
            return
        proposed = self.comicvine_candidate.series_metadata

        def done(current: Dict[str, Any]) -> None:
            self._fill_comicvine_metadata_table(current, proposed)
            payload = self._payload_from_metadata_table(self.cv_series_metadata_table)
            endpoint = f"PATCH /api/v1/series/{series_id}/metadata"
            self.cv_series_preview.setPlainText(self._format_diff(current, payload, endpoint))

        self.run_worker("Preview ComicVine série", lambda: self._fetch_current_series_preview_metadata(series_id), done)

    def apply_comicvine_series(self) -> None:
        series_id = self._current_comicvine_series_id()
        payload = self._payload_from_metadata_table(self.cv_series_metadata_table)
        if not series_id or not payload:
            QMessageBox.warning(self, "ComicVine", "Série Komga ou payload vide")
            return
        if self.simulation_enabled():
            self.preview_comicvine_series()
            self.log("Simulation active : aucune écriture série ComicVine")
            return
        if not self._confirm_source_write(
            source_name="ComicVine",
            target_label=f"série {series_id}",
            field_count=len(payload),
        ):
            return

        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("series", series_id)
            self.backup.save_json(
                "operation",
                "series",
                series_id,
                {"current": current, "comicvine": ComicVineClient.candidate_to_dict(self.comicvine_candidate) if self.comicvine_candidate else {}, "payload": payload},
                "avant PATCH ComicVine série",
            )
            return self._write_metadata_update(api, "series", series_id, payload, current, source="comicvine_manual", note="Application ComicVine série")

        self.run_worker("Application ComicVine série", do_apply, lambda r: self.log(f"✅ Metadata ComicVine appliquée sur série:{series_id}"))

    def send_comicvine_cover_to_posters(self) -> None:
        if not self.comicvine_candidate or not self.comicvine_candidate.cover_url:
            QMessageBox.warning(self, "ComicVine", "Aucune cover ComicVine disponible pour le résultat chargé")
            return
        series_id = self._current_comicvine_series_id()
        if not series_id:
            QMessageBox.warning(self, "ComicVine", "Aucune série Komga sélectionnée")
            return
        self.poster_type.setCurrentText("series")
        self.poster_id.setText(series_id)
        self.poster_url.setText(self.comicvine_candidate.cover_url)
        if hasattr(self, "poster_tab"):
            self._set_current_tab_for_widget(self.poster_tab)
        self.log("ComicVine : cover copiée dans l'onglet Couvertures. Elle ne sera uploadée que si tu cliques le bouton URL, simulation désactivée.")


    # ------------------------------------------------------------------
    # Bedetheque
    # ------------------------------------------------------------------
    def load_bedetheque_komga_series(self) -> None:
        lib_id = self._library_id("bedetheque")
        search = self.bdt_komga_search.text().strip()
        generation = self._next_series_load_generation("bedetheque")
        def done(rows: List[Any]) -> None:
            if not self._is_current_series_load_generation("bedetheque", generation):
                return
            rows = self._filter_global_series_visibility(rows)
            self._source_series_unfiltered_rows["bdt"] = rows
            self._refresh_source_link_filter_options("bdt", rows)
            rows, active_filters = self._apply_source_series_filters("bdt", rows)
            self.bdt_komga_series_rows = rows
            self._set_table(
                self.bdt_komga_series_table,
                self._series_table_headers(include_history=True),
                self._series_table_rows_for_source("bdt", rows),
                stretch_from=1,
                selection_mode=QAbstractItemView.ExtendedSelection,
            )
            suffix = f" — {', '.join(active_filters)}" if active_filters else ""
            self.log(f"✅ Bedetheque : {len(rows)} séries Komga chargées{suffix}")
        self.run_worker("Chargement séries Komga pour Bedetheque", lambda: self.komga_api().series(lib_id, search=search, page_size=200), done)

    def add_selected_bedetheque_series_to_queue(self) -> None:
        rows = self._selected_row_indexes(self.bdt_komga_series_table)
        if not rows:
            QMessageBox.warning(self, "Bedetheque", "Sélectionne une ou plusieurs séries Komga à ajouter à la file.")
            return
        added = 0
        known = {getattr(x, "id", "") for x in self.bdt_queue}
        for row in rows:
            if 0 <= row < len(self.bdt_komga_series_rows):
                series = self.bdt_komga_series_rows[row]
                if series.id not in known:
                    self.bdt_queue.append(series)
                    known.add(series.id)
                    added += 1
        self.log(f"✅ Bedetheque : {added} série(s) ajoutée(s) à la file ({len(self.bdt_queue)} total)")

    def open_bedetheque_queue_dialog(self) -> None:
        if not self.bdt_queue:
            # Aide UX : si rien n'est encore dans la file, on tente d'y mettre la sélection courante.
            self.add_selected_bedetheque_series_to_queue()
            if not self.bdt_queue:
                return
        dialog = QDialog(self)
        dialog.setWindowTitle("File Bedetheque — validation série par série")
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        self.bdt_queue_status = QLabel("")
        self.bdt_queue_status.setWordWrap(True)
        layout.addWidget(self.bdt_queue_status)
        self.bdt_queue_list = QListWidget()
        layout.addWidget(self.bdt_queue_list, 1)
        row = QHBoxLayout()
        btn_start = QPushButton("Ouvrir / relancer série courante")
        btn_next = QPushButton("Passer à la suivante")
        btn_remove = QPushButton("Retirer série courante")
        btn_close = QPushButton("Fermer")
        row.addWidget(btn_start)
        row.addWidget(btn_next)
        row.addWidget(btn_remove)
        row.addStretch(1)
        row.addWidget(btn_close)
        layout.addLayout(row)
        btn_start.clicked.connect(self.start_current_bedetheque_queue_item)
        btn_next.clicked.connect(self.next_bedetheque_queue_item)
        btn_remove.clicked.connect(self.remove_current_bedetheque_queue_item)
        btn_close.clicked.connect(dialog.close)
        self.bdt_queue_list.currentRowChanged.connect(self.set_bedetheque_queue_index_from_dialog)
        self.bdt_queue_dialog = dialog
        if self.bdt_queue_index < 0:
            self.bdt_queue_index = 0
        self.refresh_bedetheque_queue_dialog()
        dialog.show()

    def refresh_bedetheque_queue_dialog(self) -> None:
        if not getattr(self, "bdt_queue_list", None):
            return
        self.bdt_queue_list.blockSignals(True)
        self.bdt_queue_list.clear()
        for i, series in enumerate(self.bdt_queue):
            prefix = "▶ " if i == self.bdt_queue_index else "   "
            self.bdt_queue_list.addItem(f"{prefix}{i + 1:02d}. {series.title} — {series.id}")
        total = len(self.bdt_queue)
        current = self.bdt_queue_index + 1 if total and self.bdt_queue_index >= 0 else 0
        self.bdt_queue_status.setText(
            f"File : {current}/{total}. Workflow : ouvre la série, recherche Bedetheque auto, "
            "contrôle le matching/toutes les métadonnées, applique ou ignore, puis passe à la suivante."
        )
        if total and 0 <= self.bdt_queue_index < total:
            self.bdt_queue_list.setCurrentRow(self.bdt_queue_index)
        self.bdt_queue_list.blockSignals(False)

    def set_bedetheque_queue_index_from_dialog(self, row: int) -> None:
        if 0 <= row < len(self.bdt_queue):
            self.bdt_queue_index = row
            self.refresh_bedetheque_queue_dialog()

    def _clear_bedetheque_comparison_views(self, message: str = "") -> None:
        self.bedetheque_results = []
        self.bedetheque_candidate = None
        self.bdt_series_candidate = None
        self.bdt_album_candidates_by_url = {}
        self.bdt_matches = []
        for table in (
            getattr(self, "bdt_results_table", None),
            getattr(self, "bdt_albums_table", None),
            getattr(self, "bdt_match_table", None),
        ):
            if table is not None:
                table.setRowCount(0)
        self._fill_series_preview_metadata_table(self.bdt_series_metadata_table, {}, {})
        self._fill_metadata_table(self.bdt_book_metadata_table, {}, {}, BOOK_METADATA_FIELDS)
        self.bdt_series_preview.setPlainText(message or "Nouvelle série cible : lance une recherche Bedetheque.")
        self.bdt_book_preview.setPlainText("Sélectionne un tome matché pour afficher son diff.")
        self.bdt_raw.setPlainText(message or "")
        if getattr(self, "bdt_albums_status", None) is not None:
            self.bdt_albums_status.setText("")
        self._set_selection_detail("bedetheque.result", "", {"info": message or "Aucun résultat Bedetheque sélectionné."}, "")
        self._set_selection_detail("bedetheque.album", "", {"info": "Aucun album Bedetheque sélectionné."}, "")

    def _select_bedetheque_komga_series_row_by_id(self, series_id: str) -> bool:
        if not series_id:
            return False
        for row, series in enumerate(self.bdt_komga_series_rows):
            if getattr(series, "id", "") == series_id:
                self.bdt_komga_series_table.blockSignals(True)
                self.bdt_komga_series_table.clearSelection()
                self.bdt_komga_series_table.selectRow(row)
                item = self.bdt_komga_series_table.item(row, 0)
                if item is not None:
                    self.bdt_komga_series_table.scrollToItem(item)
                self.bdt_komga_series_table.blockSignals(False)
                return True
        return False

    def start_current_bedetheque_queue_item(self) -> None:
        if not self.bdt_queue or self.bdt_queue_index < 0 or self.bdt_queue_index >= len(self.bdt_queue):
            return
        series = self.bdt_queue[self.bdt_queue_index]
        self.bdt_context_generation += 1
        generation = self.bdt_context_generation
        self.bdt_target_type.setCurrentText("series")
        self.bdt_target_id.setText(series.id)
        self.bdt_query.setText(clean_search_title(series.title))
        self._select_bedetheque_komga_series_row_by_id(series.id)
        self._clear_bedetheque_comparison_views(
            f"File Bedetheque : série {self.bdt_queue_index + 1}/{len(self.bdt_queue)} — {series.title}\n"
            "Recherche Bedetheque en cours…"
        )
        self.log(f"▶ File Bedetheque : ouverture {self.bdt_queue_index + 1}/{len(self.bdt_queue)} — {series.title}")
        lib_id = self._library_id("bedetheque") or series.library_id

        def done_books(rows: List[Any]) -> None:
            if generation != self.bdt_context_generation:
                return
            self.bdt_komga_book_rows = rows
            self._set_table(
                self.bdt_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(x) for x in rows],
                stretch_from=1,
            )
            self.search_bedetheque()

        self.run_worker("File Bedetheque : chargement tomes Komga", lambda: self.komga_api().books(lib_id, series.id), done_books)

    def next_bedetheque_queue_item(self) -> None:
        if not self.bdt_queue:
            return
        if self.bdt_queue_index >= len(self.bdt_queue) - 1:
            self.log("File Bedetheque : déjà sur la dernière série.")
            self.refresh_bedetheque_queue_dialog()
            return
        self.bdt_queue_index += 1
        self.refresh_bedetheque_queue_dialog()
        self.start_current_bedetheque_queue_item()

    def remove_current_bedetheque_queue_item(self) -> None:
        if not self.bdt_queue or self.bdt_queue_index < 0 or self.bdt_queue_index >= len(self.bdt_queue):
            return
        removed = self.bdt_queue.pop(self.bdt_queue_index)
        if self.bdt_queue_index >= len(self.bdt_queue):
            self.bdt_queue_index = len(self.bdt_queue) - 1
        self.log(f"🗑️ File Bedetheque : retiré {removed.title}")
        self.refresh_bedetheque_queue_dialog()

    def on_bedetheque_komga_series_selected(self) -> None:
        row = self._selected_row_index(self.bdt_komga_series_table)
        if row < 0 or row >= len(self.bdt_komga_series_rows):
            return
        series = self.bdt_komga_series_rows[row]
        self._set_context_selection(series=series)
        self.bdt_context_generation += 1
        generation = self.bdt_context_generation
        self.bdt_target_type.setCurrentText("series")
        self.bdt_target_id.setText(series.id)
        self.bdt_query.setText(clean_search_title(series.title))
        self.bdt_album_number.setText("")
        self._clear_bedetheque_comparison_views(f"Série Komga sélectionnée : {series.title}\nRecherche Bedetheque en cours…")
        lib_id = self._library_id("bedetheque") or series.library_id
        def done(rows: List[Any]) -> None:
            if generation != self.bdt_context_generation:
                return
            self.bdt_komga_book_rows = rows
            self._set_table(
                self.bdt_komga_books_table,
                self._book_table_headers(),
                [self._book_table_row(x) for x in rows],
                stretch_from=1,
            )
            self.log(f"✅ Bedetheque : {len(rows)} tomes Komga chargés pour {series.title}")
            self.search_bedetheque()
        self.run_worker("Chargement tomes Komga pour Bedetheque", lambda: self.komga_api().books(lib_id, series.id), done)

    def on_bedetheque_komga_book_selected(self) -> None:
        row = self._selected_row_index(self.bdt_komga_books_table)
        if row < 0 or row >= len(self.bdt_komga_book_rows):
            return
        book = self.bdt_komga_book_rows[row]
        self._set_context_selection(book=book)
        self.bdt_target_type.setCurrentText("book")
        self.bdt_target_id.setText(book.id)
        self.bdt_album_number.setText(str(book.number or ""))

    def use_selected_for_bedetheque(self) -> None:
        # Prefer the Bedetheque-specific selection, then fallback to Explorer.
        row = self._selected_row_index(self.bdt_komga_series_table)
        if row >= 0 and row < len(self.bdt_komga_series_rows):
            self.bdt_query.setText(clean_search_title(self.bdt_komga_series_rows[row].title))
            self.search_bedetheque()
            return
        bid = self._selected_id_from_table(self.books_table)
        sid = self._selected_id_from_table(self.series_table)
        if bid:
            selected_data = self._selected_row_data(self.books_table)
            book = selected_data if self._record_id(selected_data) == bid else None
            if book is None:
                book = next((item for item in self.book_rows if self._record_id(item) == bid), None)
            if book is not None:
                self.bdt_target_type.setCurrentText("book")
                self.bdt_target_id.setText(bid)
                self.bdt_query.setText(clean_search_title(book.title))
                self.bdt_album_number.setText(book.number)
                self.search_bedetheque()
        elif sid:
            selected_data = self._selected_row_data(self.series_table)
            series = selected_data if self._record_id(selected_data) == sid else None
            if series is None:
                series = next((item for item in self.series_rows if self._record_id(item) == sid), None)
            if series is not None:
                self.bdt_target_type.setCurrentText("series")
                self.bdt_target_id.setText(sid)
                self.bdt_query.setText(clean_search_title(series.title))
                self.search_bedetheque()

    def _format_search_attempts(self, attempts: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for item in attempts:
            query = item.get("query")
            count = item.get("count")
            raw_count = item.get("raw_count")
            duration = item.get("duration_ms")
            if raw_count is not None and raw_count != count:
                text = f"{query} ({count}/{raw_count})"
            else:
                text = f"{query} ({count})"
            if duration not in (None, ""):
                text += f" — {duration} ms"
            error = str(item.get("error") or "").strip()
            if error:
                text += f" — erreur: {error}"
            parts.append(text)
        return "; ".join(parts)

    def _build_manga_news_search_queries(self, query: str, *, max_queries: int = 2) -> List[str]:
        """Return conservative Manga News query variants.

        Manga News searches become very slow on broad queries. Unlike the generic
        fallback builder used for Bedetheque/MangaBaka, this intentionally avoids
        first-token and heavily stripped variants such as ``Fairy`` or ``Creature``.
        """
        variants: List[str] = []
        seen: set[str] = set()

        def add(candidate: Any) -> None:
            value = clean_search_title(candidate)
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                variants.append(value)

        primary = clean_search_title(query)
        add(primary)

        # Controlled fallback only: remove short parenthetical edition/article
        # fragments while preserving the full title. This handles titles such
        # as ``Attaque Des Titans (L')`` without generating broad queries.
        no_parens = re.sub(r"\s*[\(\[\{][^\)\]\}]{1,40}[\)\]\}]\s*", " ", str(query or ""))
        add(no_parens)

        # Controlled fallback for leading/trailing French article fragments.
        # Keep at least two meaningful words to avoid slow generic searches.
        article_spaced = re.sub(r"\b(?:l|le|la|les)\b", " ", primary, flags=re.IGNORECASE)
        if len([x for x in article_spaced.split() if len(x) >= 3]) >= 2:
            add(article_spaced)

        return variants[:max(1, int(max_queries or 1))]

    def _filter_manga_news_rows(self, rows: List[MangaNewsSearchResult], manga_only: bool) -> List[MangaNewsSearchResult]:
        if not manga_only:
            return list(rows)
        filtered: List[MangaNewsSearchResult] = []
        for row in rows:
            media = str(getattr(row, "media_kind", "") or "").strip().casefold()
            # Manga News does not always expose media_kind. Blank media_kind is
            # accepted to avoid false negatives; explicit non-manga kinds are rejected.
            if not media or media == "manga" or media.startswith("manga_") or media.startswith("manga-"):
                filtered.append(row)
        return filtered

    def _manga_news_titles_for_log(self, rows: List[MangaNewsSearchResult], limit: int = 8) -> List[str]:
        titles: List[str] = []
        for row in rows[:limit]:
            suffix_parts = []
            if getattr(row, "score", None) not in (None, ""):
                suffix_parts.append(f"score={row.score}")
            if getattr(row, "media_kind", None):
                suffix_parts.append(f"media={row.media_kind}")
            suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
            titles.append(f"{getattr(row, 'title', '')}{suffix}")
        return titles

    def _search_bedetheque_with_fallback(self, query: str, client: Optional[BedethequeClient] = None) -> Dict[str, Any]:
        client = client or self.bedetheque_client()
        attempts: List[Dict[str, Any]] = []
        queries = build_search_queries(query)
        last_rows: List[BedethequeSearchResult] = []
        for candidate_query in queries:
            started = time.monotonic()
            rows = client.search(candidate_query)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            attempts.append({"query": candidate_query, "count": len(rows), "duration_ms": duration_ms})
            last_rows = rows
            if rows:
                return {"rows": rows, "used_query": candidate_query, "attempts": attempts, "primary_query": queries[0] if queries else query}
        return {"rows": last_rows, "used_query": queries[-1] if queries else query, "attempts": attempts, "primary_query": queries[0] if queries else query}

    def _search_mangabaka_with_fallback(self, query: str, manga_only: bool, client: Optional[MangaBakaClient] = None) -> Dict[str, Any]:
        client = client or self.mangabaka_client()
        attempts: List[Dict[str, Any]] = []
        queries = build_search_queries(query)
        last_rows: List[MangaBakaSearchResult] = []
        for candidate_query in queries:
            started = time.monotonic()
            rows = client.search(candidate_query)
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            filtered_rows = [r for r in rows if str(r.type or "").casefold() == "manga"] if manga_only else rows
            attempts.append({"query": candidate_query, "count": len(filtered_rows), "raw_count": len(rows), "duration_ms": duration_ms})
            last_rows = filtered_rows
            if filtered_rows:
                return {"rows": filtered_rows, "used_query": candidate_query, "attempts": attempts, "primary_query": queries[0] if queries else query}
        return {"rows": last_rows, "used_query": queries[-1] if queries else query, "attempts": attempts, "primary_query": queries[0] if queries else query}

    def _search_manga_news_with_fallback(self, query: str, manga_only: bool, client: Optional[MangaNewsClient] = None) -> Dict[str, Any]:
        client = client or self.manga_news_client()
        attempts: List[Dict[str, Any]] = []
        queries = self._build_manga_news_search_queries(query, max_queries=2)
        last_raw_rows: List[MangaNewsSearchResult] = []
        last_filtered_rows: List[MangaNewsSearchResult] = []
        last_error = ""
        primary_query = queries[0] if queries else query

        for index, candidate_query in enumerate(queries):
            started = time.monotonic()
            try:
                # Use /search, not /search/resolve. The diagnostic pass showed
                # that the slow requests were caused by broad fallback queries,
                # not by /search itself. /search also gives visible raw candidates
                # instead of hiding them behind a confidence decision.
                raw_rows = client.search(candidate_query, limit=10, manga_only=False)
            except Exception as exc:
                duration_ms = round((time.monotonic() - started) * 1000, 2)
                last_error = str(exc)
                attempt = {
                    "query": candidate_query,
                    "count": 0,
                    "raw_count": 0,
                    "filtered_count": 0,
                    "duration_ms": duration_ms,
                    "error": last_error,
                    "variant_index": index,
                }
                attempts.append(attempt)
                self._write_diagnostic_event({
                    "event": "manga_news_search_attempt",
                    "trigger": "search_with_conservative_fallback",
                    "query_original": query,
                    "query_cleaned": primary_query,
                    "query_variant": candidate_query,
                    "query_variant_index": index,
                    "raw_count": 0,
                    "filtered_count": 0,
                    "duration_ms": duration_ms,
                    "error": last_error,
                })
                # Stop on timeout: a second variant usually makes the UI feel
                # worse and can queue another expensive upstream scrape.
                if "timeout" in last_error.casefold():
                    break
                continue

            duration_ms = round((time.monotonic() - started) * 1000, 2)
            filtered_rows = self._filter_manga_news_rows(raw_rows, manga_only)
            last_raw_rows = raw_rows
            last_filtered_rows = filtered_rows
            attempt = {
                "query": candidate_query,
                "count": len(filtered_rows),
                "raw_count": len(raw_rows),
                "filtered_count": len(filtered_rows),
                "duration_ms": duration_ms,
                "variant_index": index,
                "raw_titles": self._manga_news_titles_for_log(raw_rows),
                "filtered_titles": self._manga_news_titles_for_log(filtered_rows),
            }
            attempts.append(attempt)
            self._write_diagnostic_event({
                "event": "manga_news_search_attempt",
                "trigger": "search_with_conservative_fallback",
                "query_original": query,
                "query_cleaned": primary_query,
                "query_variant": candidate_query,
                "query_variant_index": index,
                "raw_count": len(raw_rows),
                "raw_titles": self._manga_news_titles_for_log(raw_rows),
                "filtered_count": len(filtered_rows),
                "filtered_titles": self._manga_news_titles_for_log(filtered_rows),
                "duration_ms": duration_ms,
                "reject_reason": "filtered_empty" if raw_rows and not filtered_rows else "",
            })
            if filtered_rows:
                return {
                    "rows": filtered_rows,
                    "raw_rows": raw_rows,
                    "used_query": candidate_query,
                    "attempts": attempts,
                    "primary_query": primary_query,
                    "error": "",
                }

        # For manual UI searches, expose raw rows if the local manga filter is the
        # only reason nothing was shown. Auto-match still uses rows=[], so it
        # remains prudent.
        return {
            "rows": last_filtered_rows,
            "raw_rows": last_raw_rows,
            "used_query": queries[-1] if queries else query,
            "attempts": attempts,
            "primary_query": primary_query,
            "error": last_error,
        }

    def _search_comicvine_with_fallback(self, query: str, client: Optional[ComicVineClient] = None) -> Dict[str, Any]:
        client = client or self.comicvine_client()
        attempts: List[Dict[str, Any]] = []
        queries = build_search_queries(query, max_queries=2)
        last_rows: List[ComicVineSearchResult] = []
        last_error = ""
        for candidate_query in queries:
            started = time.monotonic()
            try:
                rows = client.search(candidate_query, limit=20)
            except ExternalSourceBlocked:
                raise
            except Exception as exc:
                duration_ms = round((time.monotonic() - started) * 1000, 2)
                last_error = str(exc)
                attempts.append({"query": candidate_query, "count": 0, "duration_ms": duration_ms, "error": last_error})
                if "timeout" in last_error.casefold():
                    break
                continue
            duration_ms = round((time.monotonic() - started) * 1000, 2)
            attempts.append({"query": candidate_query, "count": len(rows), "duration_ms": duration_ms})
            last_rows = rows
            if rows:
                return {
                    "rows": rows,
                    "used_query": candidate_query,
                    "attempts": attempts,
                    "primary_query": queries[0] if queries else query,
                    "error": "",
                }
        return {
            "rows": last_rows,
            "used_query": queries[-1] if queries else query,
            "attempts": attempts,
            "primary_query": queries[0] if queries else query,
            "error": last_error,
        }

    def search_bedetheque(self) -> None:
        raw_query = self.bdt_query.text().strip()
        query = clean_search_title(raw_query)
        if query != raw_query:
            self.bdt_query.setText(query)
        if not query:
            QMessageBox.warning(self, "Bedetheque", "Recherche vide")
            return
        generation = self.bdt_context_generation
        self._record_enrichment_search("bdt", self._current_bedetheque_series_id())
        def done(search_result: Dict[str, Any]) -> None:
            if generation != self.bdt_context_generation:
                return
            rows = search_result.get("rows") or []
            ranked = ranked_title_results(query, rows)
            rows = [row for _, row in ranked]
            used_query = str(search_result.get("used_query") or query)
            attempts = search_result.get("attempts") or []
            self.bedetheque_results = rows
            self._set_table(
                self.bdt_results_table,
                ["Type", "Titre", "Score match", "URL"],
                [[r.kind, r.title, f"{score:.3f}", r.url] for score, r in ranked],
                stretch_from=1,
            )
            if rows:
                best_score = ranked[0][0]
                self._reset_table_to_first_row(self.bdt_results_table)
                suffix = "" if used_query == query else f"\nVariante utilisée : {used_query}"
                if used_query != query:
                    suffix += f"\nMeilleur score de titre : {best_score:.3f}"
                self.bdt_raw.setPlainText(
                    f"{len(rows)} résultat(s), triés par score. La première ligne est sélectionnée et chargée automatiquement."
                    f"{suffix}\nEssais : {self._format_search_attempts(attempts)}"
                )
                self.log(f"Bedetheque recherche : {query} → {self._format_search_attempts(attempts)}")
                self.scrape_selected_bedetheque_series()
            else:
                self._reset_table_to_first_row(self.bdt_results_table)
                self.bdt_raw.setPlainText(f"0 résultat Bedetheque. Essais : {self._format_search_attempts(attempts)}")
                self.log(f"Bedetheque recherche sans résultat : {query} → {self._format_search_attempts(attempts)}")
        self.bdt_raw.setPlainText(f"Recherche Bedetheque en cours…\nRequête : {query}\nSi aucun résultat ne revient, le détail des essais sera affiché ici.")
        self._set_table(self.bdt_results_table, ["Type", "Titre", "Score match", "URL"], [], stretch_from=1)
        self._reset_table_to_first_row(self.bdt_results_table)
        self.run_worker("Recherche Bedetheque", lambda: self._search_bedetheque_with_fallback(query), done)

    def on_bedetheque_result_selected(self) -> None:
        row = self._selected_row_index(self.bdt_results_table)
        if row < 0 or row >= len(self.bedetheque_results):
            self._set_selection_detail("bedetheque.result", "", {"info": "Aucun résultat Bedetheque sélectionné."}, "")
            return
        result = self.bedetheque_results[row]
        data = {
            "source": "bedetheque",
            "kind": result.kind,
            "title": result.title,
            "url": result.url,
        }
        self._set_selection_detail("bedetheque.result", result.title, data, result.url)

    def on_bedetheque_album_selected(self) -> None:
        album = self._selected_bedetheque_album()
        if not album:
            self._set_selection_detail("bedetheque.album", "", {"info": "Aucun album Bedetheque sélectionné."}, "")
            return
        data = dict(album)
        data.setdefault("source", "bedetheque")
        self._set_selection_detail("bedetheque.album", str(album.get("title") or ""), data, str(album.get("url") or ""))

    def scrape_selected_bedetheque(self) -> None:
        # Backward-compatible alias.
        self.scrape_selected_bedetheque_series()

    def scrape_selected_bedetheque_series(self) -> None:
        row = self._selected_row_index(self.bdt_results_table)
        if row < 0 or row >= len(self.bedetheque_results):
            QMessageBox.warning(self, "Bedetheque", "Aucune série Bedetheque sélectionnée")
            return
        result = self.bedetheque_results[row]
        generation = self.bdt_context_generation
        def done(candidate: BedethequeCandidate) -> None:
            if generation != self.bdt_context_generation:
                return
            self.bdt_series_candidate = candidate
            self.bedetheque_candidate = candidate
            self.bdt_album_candidates_by_url = {}
            albums = candidate.raw.get("albums", []) if isinstance(candidate.raw, dict) else []
            if getattr(self, "bdt_albums_status", None) is not None:
                if albums:
                    self.bdt_albums_status.setText(f"{len(albums)} album(s) Bedetheque trouves pour le matching des tomes.")
                elif isinstance(candidate.raw, dict) and candidate.raw.get("source") == "bedetheque_csv":
                    self.bdt_albums_status.setText("Le mode CSV Bedetheque ne contient pas les albums : l'auto-match des tomes n'est pas possible avec cette source.")
                else:
                    self.bdt_albums_status.setText("Aucun album Bedetheque extrait pour cette serie : la page peut etre bloquee, vide, ou dans un format non reconnu.")
            self._set_table(self.bdt_albums_table, ["#", "Titre", "URL"], [[a.get("number", ""), a.get("title", ""), a.get("url", "")] for a in albums], stretch_from=1)
            self._reset_table_to_first_row(self.bdt_albums_table, select=False)
            self.bdt_raw.setPlainText(json_text(BedethequeClient.candidate_to_dict(candidate)))
            self.preview_bedetheque_series()
            self.match_bedetheque_tomes()
            self.log(f"✅ Série Bedetheque scrapée : {candidate.series_title} ({len(albums)} albums)")
        self.run_worker("Scrape série Bedetheque", lambda: self.bedetheque_client().scrape_series(result.url), done)

    def _selected_bedetheque_album(self) -> Dict[str, str]:
        if not self.bdt_series_candidate:
            return {}
        row = self._selected_row_index(self.bdt_albums_table)
        albums = self.bdt_series_candidate.raw.get("albums", []) if isinstance(self.bdt_series_candidate.raw, dict) else []
        if row < 0 or row >= len(albums):
            return {}
        return albums[row]

    def scrape_selected_bedetheque_album(self) -> None:
        album = self._selected_bedetheque_album()
        if not album:
            QMessageBox.warning(self, "Bedetheque", "Aucun album Bedetheque sélectionné")
            return
        url = album.get("url", "")
        def done(candidate: BedethequeCandidate) -> None:
            self.bdt_album_candidates_by_url[url] = candidate
            self.bedetheque_candidate = candidate
            self.bdt_raw.setPlainText(json_text(BedethequeClient.candidate_to_dict(candidate)))
            self.match_bedetheque_tomes()
            self.on_bedetheque_match_selected()
            self.log(f"✅ Album Bedetheque scrapé : {candidate.album_title or url}")
        self.run_worker("Scrape album Bedetheque", lambda: self.bedetheque_client().scrape_album(url), done)

    def scrape_all_bedetheque_albums(self) -> None:
        if not self.bdt_series_candidate:
            QMessageBox.warning(self, "Bedetheque", "Scrape d'abord une série Bedetheque")
            return
        albums = self.bdt_series_candidate.raw.get("albums", []) if isinstance(self.bdt_series_candidate.raw, dict) else []
        def do_scrape() -> Dict[str, Any]:
            client = self.bedetheque_client()
            done: Dict[str, Any] = {"ok": 0, "errors": []}
            cache: Dict[str, BedethequeCandidate] = {}
            for album in albums:
                url = album.get("url", "")
                if not url:
                    continue
                try:
                    cache[url] = client.scrape_album(url)
                    done["ok"] += 1
                except ExternalSourceBlocked:
                    raise
                except Exception as exc:
                    done["errors"].append({"url": url, "error": str(exc)})
            return {"report": done, "cache": cache}
        def done(result: Dict[str, Any]) -> None:
            self.bdt_album_candidates_by_url.update(result["cache"])
            self.bdt_raw.setPlainText(json_text(result["report"]))
            self.match_bedetheque_tomes()
            self.log(f"✅ Scrape albums terminé : {result['report']['ok']} OK, {len(result['report']['errors'])} erreurs")
        self.run_worker("Scrape tous albums Bedetheque", do_scrape, done)

    def match_bedetheque_tomes(self) -> None:
        if not self.bdt_series_candidate:
            self.bdt_match_table.setRowCount(0)
            return
        from .bedetheque import match_album_rows
        albums = self.bdt_series_candidate.raw.get("albums", []) if isinstance(self.bdt_series_candidate.raw, dict) else []
        if not albums:
            reason = ""
            if getattr(self, "bdt_albums_status", None) is not None:
                reason = self.bdt_albums_status.text().strip()
            if not reason:
                reason = "Aucun album Bedetheque disponible pour cette serie."
            self.bdt_matches = [
                {
                    "book_index": index,
                    "album_index": -1,
                    "confidence": "Aucun album Bedetheque",
                    "score": 0.0,
                    "book_number_norm": normalize_volume_number(getattr(book, "number", "")),
                }
                for index, book in enumerate(self.bdt_komga_book_rows)
            ]
            rows = [
                [
                    getattr(book, "id", ""),
                    getattr(book, "number", ""),
                    getattr(book, "title", ""),
                    "",
                    "",
                    "Aucun album Bedetheque",
                    0.0,
                    "",
                ]
                for book in self.bdt_komga_book_rows
            ]
            self._set_table(
                self.bdt_match_table,
                ["Book ID", "N° Komga", "Titre Komga", "N° BDT", "Titre BDT", "Confiance", "Score", "URL album"],
                rows,
                stretch_from=2,
                selection_mode=QAbstractItemView.ExtendedSelection,
            )
            self.bdt_book_preview.setPlainText(reason)
            return
        self.bdt_matches = match_album_rows(self.bdt_komga_book_rows, albums)
        rows: List[List[Any]] = []
        for match in self.bdt_matches:
            book = self.bdt_komga_book_rows[match["book_index"]] if match.get("book_index", -1) >= 0 and match["book_index"] < len(self.bdt_komga_book_rows) else None
            album = albums[match["album_index"]] if match.get("album_index", -1) >= 0 and match["album_index"] < len(albums) else {}
            rows.append([
                getattr(book, "id", "") if book else "",
                getattr(book, "number", "") if book else "",
                getattr(book, "title", "") if book else "",
                album.get("number", ""),
                album.get("title", ""),
                match.get("confidence", ""),
                match.get("score", ""),
                album.get("url", ""),
            ])
        self._set_table(
            self.bdt_match_table,
            ["Book ID", "N° Komga", "Titre Komga", "N° BDT", "Titre BDT", "Confiance", "Score", "URL album"],
            rows,
            stretch_from=2,
            selection_mode=QAbstractItemView.ExtendedSelection,
        )

    def _current_bedetheque_series_id(self) -> str:
        # La cible explicite est prioritaire. En mode file, la sélection visuelle
        # du tableau peut rester sur une autre ligne ou être multiple ; le diff
        # doit suivre la série ouverte dans la file, pas le premier index sélectionné.
        if self.bdt_target_type.currentText() == "series":
            explicit_id = self.bdt_target_id.text().strip()
            if explicit_id:
                return explicit_id
        row = self._selected_row_index(self.bdt_komga_series_table)
        if row >= 0 and row < len(self.bdt_komga_series_rows):
            return self.bdt_komga_series_rows[row].id
        return ""

    def _selected_match_context(self) -> Dict[str, Any]:
        row = self._selected_row_index(self.bdt_match_table)
        if row < 0 or row >= len(self.bdt_matches) or not self.bdt_series_candidate:
            return {}
        match = self.bdt_matches[row]
        albums = self.bdt_series_candidate.raw.get("albums", []) if isinstance(self.bdt_series_candidate.raw, dict) else []
        book = self.bdt_komga_book_rows[match["book_index"]] if match.get("book_index", -1) >= 0 and match["book_index"] < len(self.bdt_komga_book_rows) else None
        album = albums[match["album_index"]] if match.get("album_index", -1) >= 0 and match["album_index"] < len(albums) else {}
        return {"match": match, "book": book, "album": album}

    def _selected_match_contexts(self) -> List[Dict[str, Any]]:
        if not self.bdt_series_candidate:
            return []
        contexts: List[Dict[str, Any]] = []
        rows = self._selected_row_indexes(self.bdt_match_table)
        albums = self.bdt_series_candidate.raw.get("albums", []) if isinstance(self.bdt_series_candidate.raw, dict) else []
        for row in rows:
            if row < 0 or row >= len(self.bdt_matches):
                continue
            match = self.bdt_matches[row]
            book_index = match.get("book_index", -1)
            album_index = match.get("album_index", -1)
            book = self.bdt_komga_book_rows[book_index] if book_index >= 0 and book_index < len(self.bdt_komga_book_rows) else None
            album = albums[album_index] if album_index >= 0 and album_index < len(albums) else {}
            contexts.append({"row": row, "match": match, "book": book, "album": album})
        return contexts

    def on_bedetheque_match_selected(self) -> None:
        ctx = self._selected_match_context()
        if not ctx or not ctx.get("book"):
            self._fill_metadata_table(self.bdt_book_metadata_table, {}, {}, BOOK_METADATA_FIELDS)
            self.bdt_book_preview.setPlainText("Sélectionne une ligne matchée avec un book Komga.")
            return
        book = ctx["book"]
        album = ctx.get("album") or {}
        candidate = self.bdt_album_candidates_by_url.get(album.get("url", ""))
        current = book.metadata or {}
        if not candidate and album.get("url"):
            # UX: remplir le diff du tome dès qu'une ligne matchée est sélectionnée.
            # L'utilisateur ne doit pas devoir deviner qu'il faut cliquer un autre bouton.
            self.bdt_book_preview.setPlainText("Scrape automatique de l'album Bedetheque sélectionné…")
            url = album.get("url", "")
            def done(scraped: BedethequeCandidate) -> None:
                self.bdt_album_candidates_by_url[url] = scraped
                self.on_bedetheque_match_selected()
            self.run_worker("Scrape auto album Bedetheque", lambda: self.bedetheque_client().scrape_album(url), done)
            return
        proposed = candidate.book_metadata if candidate else {}
        self._fill_metadata_table(self.bdt_book_metadata_table, current, proposed, BOOK_METADATA_FIELDS)
        if candidate:
            self.preview_bedetheque_book()
        else:
            self.bdt_book_preview.setPlainText("Album non scrapé ou non matché.")

    def preview_bedetheque_series(self) -> None:
        if not self.bdt_series_candidate:
            self.bdt_series_preview.setPlainText("Aucune série Bedetheque scrapée.")
            return
        series_id = self._current_bedetheque_series_id()
        if not series_id:
            self.bdt_series_preview.setPlainText("Aucune série Komga sélectionnée.")
            return
        proposed = self.bdt_series_candidate.series_metadata
        def done(current: Dict[str, Any]) -> None:
            self._fill_series_preview_metadata_table(self.bdt_series_metadata_table, current, proposed)
            payload = self._payload_from_metadata_table(self.bdt_series_metadata_table)
            endpoint = f"PATCH /api/v1/series/{series_id}/metadata"
            self.bdt_series_preview.setPlainText(self._format_diff(current, payload, endpoint))
        self.run_worker("Preview Bedetheque série", lambda: self._fetch_current_series_preview_metadata(series_id), done)

    def apply_bedetheque_series(self) -> None:
        series_id = self._current_bedetheque_series_id()
        payload = self._payload_from_metadata_table(self.bdt_series_metadata_table)
        if not series_id or not payload:
            QMessageBox.warning(self, "Bedetheque", "Série Komga ou payload vide")
            return
        if self.simulation_enabled():
            self.preview_bedetheque_series()
            self.log("Simulation active : aucune écriture série Bedetheque")
            return
        if not self._confirm_source_write(
            source_name="Bedetheque",
            target_label=f"série {series_id}",
            field_count=len(payload),
        ):
            return
        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("series", series_id)
            self.backup.save_json("operation", "series", series_id, {"current": current, "bedetheque": BedethequeClient.candidate_to_dict(self.bdt_series_candidate) if self.bdt_series_candidate else {}}, "avant PATCH Bedetheque série")
            return self._write_metadata_update(api, "series", series_id, payload, current, source="bedetheque_manual", note="Application Bedetheque série")
        self.run_worker("Application Bedetheque série", do_apply, lambda r: self.log(f"✅ Metadata Bedetheque appliquée sur série:{series_id}"))

    def open_bedetheque_book_preview_popup(self) -> None:
        text = self.bdt_book_preview.toPlainText().strip()
        if not text:
            text = "Aucun aperçu de tome Bedetheque. Sélectionne une ligne puis clique sur Prévisualiser tome sélectionné."
        self._show_text_popup("Aperçu diff Bedetheque tome", text, (1040, 680))

    def preview_bedetheque_book(self) -> None:
        ctx = self._selected_match_context()
        book = ctx.get("book") if ctx else None
        album = ctx.get("album") if ctx else {}
        if not book:
            self.bdt_book_preview.setPlainText("Aucun tome Komga matché sélectionné.")
            return
        candidate = self.bdt_album_candidates_by_url.get(album.get("url", ""))
        if not candidate:
            self.bdt_book_preview.setPlainText("Album Bedetheque non scrapé.")
            return
        current = book.metadata or {}
        proposed = candidate.book_metadata
        self._fill_metadata_table(self.bdt_book_metadata_table, current, proposed, BOOK_METADATA_FIELDS)
        payload = self._payload_from_metadata_table(self.bdt_book_metadata_table)
        endpoint = f"PATCH /api/v1/books/{book.id}/metadata"
        self.bdt_book_preview.setPlainText(self._format_diff(current, payload, endpoint))

    def apply_bedetheque_book(self) -> None:
        contexts = [ctx for ctx in self._selected_match_contexts() if ctx.get("book")]
        if not contexts:
            QMessageBox.warning(self, "Bedetheque", "Aucun tome Komga matché sélectionné")
            return
        if len(contexts) > 1:
            self.apply_bedetheque_books(contexts)
            return

        ctx = contexts[0]
        book = ctx.get("book")
        album = ctx.get("album") if ctx else {}
        if not book:
            QMessageBox.warning(self, "Bedetheque", "Aucun tome Komga matché sélectionné")
            return
        candidate = self.bdt_album_candidates_by_url.get(album.get("url", ""))
        if not candidate:
            QMessageBox.warning(self, "Bedetheque", "Album Bedetheque non scrapé")
            return
        payload = self._normalize_payload_for_target("book", self._payload_from_metadata_table(self.bdt_book_metadata_table))
        if not payload:
            QMessageBox.warning(self, "Bedetheque", "Payload vide")
            return
        if self.simulation_enabled():
            self.preview_bedetheque_book()
            self.log("Simulation active : aucune écriture tome Bedetheque")
            return
        if not self._confirm_source_write(
            source_name="Bedetheque",
            target_label=f"tome {book.id}",
            field_count=len(payload),
        ):
            return
        def do_apply() -> Any:
            api = self.komga_api()
            current = self._fetch_current_metadata("book", book.id)
            self.backup.save_json("operation", "book", book.id, {"current": current, "bedetheque": BedethequeClient.candidate_to_dict(candidate), "payload": payload}, "avant PATCH Bedetheque tome")
            return self._write_metadata_update(api, "book", book.id, payload, current, source="bedetheque_book_manual", note="Application Bedetheque tome")
        self.run_worker("Application Bedetheque tome", do_apply, lambda r: self.log(f"✅ Metadata Bedetheque appliquée sur book:{book.id}"))

    def apply_bedetheque_books(self, contexts: List[Dict[str, Any]]) -> None:
        total = len(contexts)
        if total <= 0:
            QMessageBox.warning(self, "Bedetheque", "Aucun tome Komga matché sélectionné")
            return
        simulation = self.simulation_enabled()
        if not simulation and not self._confirm_source_write(
            source_name="Bedetheque",
            target_label=f"{total} tome(s) matché(s)",
        ):
            return
        self._set_auto_match_progress("Application Bedetheque tomes — démarrage", 0, total)
        progress = self._auto_match_progress_callback()

        def done(result: Dict[str, Any]) -> None:
            self._set_auto_match_progress("Application Bedetheque tomes — terminé", total, total)
            self._show_bedetheque_books_apply_report(result)

        self.run_worker(
            "Application Bedetheque tomes",
            lambda: self._run_apply_bedetheque_books(contexts, simulation, progress),
            done,
        )

    def _run_apply_bedetheque_books(
        self,
        contexts: List[Dict[str, Any]],
        simulation: bool,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        api = self.komga_api()
        client = self.bedetheque_client()
        rows: List[Dict[str, Any]] = []
        total = len(contexts)
        for index, ctx in enumerate(contexts, start=1):
            book = ctx.get("book")
            album = ctx.get("album") or {}
            title = getattr(book, "title", "") if book else ""
            report: Dict[str, Any] = {
                "index": index,
                "book_id": getattr(book, "id", "") if book else "",
                "komga_title": title,
                "bedetheque_title": album.get("title", ""),
                "album_url": album.get("url", ""),
                "status": "",
                "payload_fields": "",
                "error": "",
            }
            self._emit_auto_match_progress(progress, "Application Bedetheque tomes", index - 1, total, f"{index}/{total} — {title}")
            try:
                if not book:
                    report["status"] = "Ignoré : aucun book Komga"
                elif not album.get("url"):
                    report["status"] = "Ignoré : album Bedetheque sans URL"
                else:
                    candidate = self.bdt_album_candidates_by_url.get(album.get("url", ""))
                    if not candidate:
                        candidate = client.scrape_album(album.get("url", ""))
                        self.bdt_album_candidates_by_url[album.get("url", "")] = candidate
                    current = self._fetch_current_metadata("book", book.id)
                    payload = self._payload_from_metadata_maps(current, candidate.book_metadata, BOOK_METADATA_FIELDS, target_type="book")
                    report["payload_fields"] = "; ".join(payload.keys())
                    if not payload:
                        report["status"] = "OK : aucun changement"
                    elif simulation:
                        report["status"] = "OK simulation"
                    else:
                        self.backup.save_json(
                            "operation",
                            "book",
                            book.id,
                            {"current": current, "bedetheque": BedethequeClient.candidate_to_dict(candidate), "payload": payload},
                            "avant PATCH Bedetheque tomes",
                        )
                        self._write_metadata_update(api, "book", book.id, payload, current, source="bedetheque_books_multi", note="Application Bedetheque tomes")
                        report["status"] = "OK appliqué"
            except Exception as exc:
                report["status"] = "Erreur"
                report["error"] = str(exc)
            rows.append(report)
            self._emit_auto_match_progress(
                progress,
                "Application Bedetheque tomes",
                index,
                total,
                f"{index}/{total} — {title} — {report.get('status') or 'traité'}",
            )

        csv_name = f"apply_bedetheque_books_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        csv_path = self.backup.export_csv(csv_name, rows)
        return {"rows": rows, "csv_path": csv_path, "simulation": simulation}

    def _show_bedetheque_books_apply_report(self, result: Dict[str, Any]) -> None:
        rows = result.get("rows") or []
        csv_path = result.get("csv_path", "")
        counts: Dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "Sans statut")
            counts[status] = counts.get(status, 0) + 1

        lines = [
            "Compte rendu application Bedetheque tomes",
            f"Mode : {'simulation' if result.get('simulation') else 'écriture réelle'}",
            f"CSV : {csv_path}",
            "",
            "Synthèse :",
        ]
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
        lines.append("Détail :")
        for row in rows:
            detail = f"{row.get('index')}. {row.get('komga_title')} → {row.get('status') or row.get('status')}"
            if row.get("bedetheque_title"):
                detail += f" | BDT: {row.get('bedetheque_title')}"
            if row.get("payload_fields"):
                detail += f" | champs: {row.get('payload_fields')}"
            if row.get("source_notes"):
                detail += f" | source: {row.get('source_notes')}"
            if row.get("error"):
                detail += f" | erreur: {row.get('error')}"
            lines.append(detail)

        text = "\n".join(lines)
        self._show_structured_report_dialog(
            "Compte rendu application Bedetheque tomes",
            text,
            rows,
            csv_path,
            columns=[
                ("index", "#"),
                ("komga_title", "Tome Komga"),
                ("status", "Statut"),
                ("bedetheque_title", "Tome Bedetheque"),
                ("payload_fields", "Champs"),
                ("error", "Erreur"),
            ],
            secondary_filter_keys=["status"],
        )

    def preview_bedetheque_to_target(self) -> None:
        if self.bdt_target_type.currentText() == "series":
            self.preview_bedetheque_series()
        else:
            self.preview_bedetheque_book()

    def apply_bedetheque_metadata(self) -> None:
        if self.bdt_target_type.currentText() == "series":
            self.apply_bedetheque_series()
        else:
            self.apply_bedetheque_book()

    def closeEvent(self, event: Any) -> None:
        """Persist connection/config fields automatically on exit.

        The explicit button still exists, but connection credentials should not
        vanish simply because the user forgot to click it.
        """
        try:
            self.config = self._ui_to_config()
            save_config(self.config, self.config_path)
        except Exception as exc:
            self.log(f"⚠️ Impossible de sauvegarder les réglages à la fermeture : {exc}")
        self._set_diagnostics_enabled_cached(False)
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
