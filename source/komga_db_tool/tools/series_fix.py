#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Komga Series Fix GUI

Outil manuel pour corriger en masse les titres de séries Komga à partir du nom
réel de la série/dossier Komga et, en fallback, du consensus des noms de livres.

- Modifie les métadonnées de série Komga: title + titleSort, avec locks optionnels.
- Traite une seule bibliothèque à la fois.
- Simulation activée par défaut.
- Application uniquement sur les lignes sélectionnées.
- Backup JSON créé avant chaque application réelle.

Dépendance externe unique: PySide6.
Installation: python -m pip install PySide6
"""

from __future__ import annotations

import csv
import difflib
import faulthandler
import json
import os
import re
import sys
import threading
import traceback
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request

try:
    from PySide6.QtCore import Qt, QThreadPool, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QFileDialog,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except Exception as exc:  # pragma: no cover
    print("PySide6 est requis: python -m pip install PySide6", file=sys.stderr)
    raise

from ..runtime import SecretRedactor
from ..qt_tasks import Worker


APP_TITLE = "Komga Series Fix"
APP_VERSION = "0.1.2"
DEFAULT_KOMGA_URL = "http://192.168.1.30:25600"
BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "komga_series_fix_backups")
DEBUG_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "komga_series_fix_debug.log")
_CRASH_LOG_HANDLE = None

SOURCE_FOLDER_THEN_FILES = "Dossier puis fichiers"
SOURCE_FILES_THEN_FOLDER = "Fichiers puis dossier"
SOURCE_FOLDER_ONLY = "Dossier uniquement"
SOURCE_FILES_ONLY = "Fichiers uniquement"
SOURCE_MODES = (
    SOURCE_FOLDER_THEN_FILES,
    SOURCE_FILES_THEN_FOLDER,
    SOURCE_FOLDER_ONLY,
    SOURCE_FILES_ONLY,
)

CONFIDENCE_HIGH = "haute"
CONFIDENCE_MEDIUM = "moyenne"
CONFIDENCE_LOW = "basse"

SKIP_FOLDER_NAMES = {
    "a classer", "a trier", "mangas", "comics", "bd", "lectures", "books", "library",
    "fr", "en", "vf", "vo", "scan", "scans", "one shots", "one-shots", "albums",
}
CONTAINER_FOLDER_PATTERNS = [
    re.compile(r"\s*\((?:INT|OS|Univers)\)\s*$", re.IGNORECASE),
    re.compile(r"\s+1\s+(?:FR|EN)\s*$", re.IGNORECASE),
]
TRAILING_NOISE_PATTERNS = [
    re.compile(r"\s*\[(?:digital|zone[- ]empire|empire|dcp|phd|c2c|repack|scan [^\]]+|[^\]]*?\d{4}[^\]]*)\]\s*$", re.IGNORECASE),
    re.compile(r"\s+(?:PRINTER|HD|VF|VO|SCANTRAD|SCANLATION|WEBRIP)\s*$", re.IGNORECASE),
]
LANGUAGE_TOKENS = {
    "fr", "en", "us", "uk", "vf", "vo", "jp", "ja", "it", "es", "de", "nl", "pt", "br",
    "ru", "ko", "zh", "cn", "francais", "français", "english", "anglais", "japonais", "japanese",
}
FORMAT_TOKENS = {
    "digital", "numerique", "numérique", "scan", "scans", "scanlation", "scantrad", "papier",
    "print", "hd", "webrip", "c2c", "phd", "dcp", "repack", "redux", "couleur", "color",
    "n&b", "noir et blanc", "nb", "ebook", "cbz", "cbr", "pdf",
}
PUBLISHER_TOKENS = {
    "delcourt", "dargaud", "glenat", "glénat", "casterman", "dupuis", "soleil", "lombard",
    "le lombard", "fluide glacial", "humano", "humanoides associes", "humanoïdes associés",
    "ankama", "bamboo", "drakoo", "rue de sevres", "rue de sèvres", "futuropolis",
    "albin michel", "robinson", "vents d'ouest", "drugstore", "12 bis", "ksteer",
    "urban comics", "urban", "panini", "panini comics", "marvel", "dc", "dc comics", "image",
    "image comics", "vertigo", "dark horse", "idw", "boom", "valiant", "kazé", "kaze",
    "ki-oon", "kana", "pika", "kurokawa", "tonkam", "doki-doki", "doki doki", "akata",
    "asuka", "taifu", "taifu comics", "ototo", "michel lafon", "shogakukan", "kodansha", "shueisha",
}
META_PAREN_FIXED_TOKENS = LANGUAGE_TOKENS | FORMAT_TOKENS | PUBLISHER_TOKENS

TOME_MARKER_NUMBER = (
    r"(?:"
    r"V\d+\s*#\d+(?:\.\d+)?"
    r"|V\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Issues?\s*#?\d+(?:\.\d+)?[A-Z]?"
    r"|#\d+(?:\.\d+)?"
    r"|#One\s+Shot"
    r"|HS\s*\d+[A-Z]?"
    r"|HC\s*\d+[A-Z]?"
    r"|Tome\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Volume\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Vol\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Chapter\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Chap\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Ch\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Episode\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Ep\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Part\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Pt\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|Op\.?\s*\d+(?:\.\d+)?[A-Z]?"
    r"|T\s*\d+(?:\.\d+)?[A-Z]?"
    r")"
)
BARE_NUMBER = r"\d{1,4}\.\d{1,3}[A-Z]?|\d{1,4}[A-Z]?"
ANY_NUMBER = f"(?:{TOME_MARKER_NUMBER}|{BARE_NUMBER})"
TRAILING_TOME_WORDS_RE = re.compile(
    r"(?:[\s,;:_\-]+(?:Issues?|Chapter|Chap|Ch|Part|Pt|Episode|Ep|Op|Volume|Vol|Tome|T))+$",
    re.IGNORECASE,
)
DIRECT_NUMBER_PATTERNS = [
    re.compile(r"^(?P<series>.+?)(?P<number>#\d+(?:\.\d+)?[A-Z]?)(?:\s*(?:-|–|—|:)\s*(?P<title>.+)|\s+(?P<title2>.+))?$", re.IGNORECASE),
    re.compile(rf"^(?P<series>.+?)\s+(?P<number>{ANY_NUMBER})\s*(?:-|–|—|:)\s*(?P<title>.+)$", re.IGNORECASE),
    re.compile(rf"^(?P<series>.+?)\s+(?P<number>{ANY_NUMBER})\.\s+(?P<title>.+)$", re.IGNORECASE),
    re.compile(r"^(?P<series>.+?)\s+(?P<number>(?:V\d+\s*#\d+|#\d+|#One\s+Shot))(?:\s*(?:-|–|—|:)\s*(?P<title>.+)|\s+(?P<title2>.+))?$", re.IGNORECASE),
    re.compile(rf"^(?P<series>.+?)\s+(?P<number>{ANY_NUMBER})$", re.IGNORECASE),
    re.compile(rf"^(?P<series>.+?)\s+(?P<number>{TOME_MARKER_NUMBER})\s+(?P<title>.+)$", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Crash / debug logging
# ---------------------------------------------------------------------------


def append_debug_log(message: str) -> None:
    """Write a durable debug line even if the GUI log cannot be updated."""
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {SecretRedactor.redact(message)}\n")
    except Exception:
        # Never let logging create a secondary failure.
        pass


def setup_crash_logging() -> None:
    """Install last-resort logging for silent crashes and uncaught exceptions."""
    global _CRASH_LOG_HANDLE
    try:
        _CRASH_LOG_HANDLE = open(DEBUG_LOG_FILE, "a", encoding="utf-8")
        _CRASH_LOG_HANDLE.write("\n" + "=" * 80 + "\n")
        _CRASH_LOG_HANDLE.write(f"{APP_TITLE} {APP_VERSION} started at {datetime.now().isoformat(timespec='seconds')}\n")
        _CRASH_LOG_HANDLE.flush()
        faulthandler.enable(_CRASH_LOG_HANDLE, all_threads=True)
    except Exception:
        _CRASH_LOG_HANDLE = None

    def gui_excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        append_debug_log("UNCAUGHT EXCEPTION\n" + text)
        try:
            sys.__excepthook__(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = gui_excepthook

    if hasattr(threading, "excepthook"):
        def thread_excepthook(args):
            text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            append_debug_log(f"UNCAUGHT THREAD EXCEPTION in {getattr(args.thread, 'name', '?')}\n" + text)

        threading.excepthook = thread_excepthook


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class LibraryItem:
    id: str
    name: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BookItem:
    id: str
    name: str
    series_id: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SeriesItem:
    id: str
    current_title: str
    title_sort: str
    folder_name: str
    library_id: str
    book_count: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    books: List[BookItem] = field(default_factory=list)


@dataclass
class SeriesChange:
    library_id: str
    library_name: str
    series_id: str
    old_title: str
    new_title: str
    old_title_sort: str
    new_title_sort: str
    folder_name: str
    source: str
    confidence: str
    score: int
    book_count: int
    samples: List[str]
    notes: str
    options: List[str] = field(default_factory=list)
    raw_series: Dict[str, Any] = field(default_factory=dict)

    def to_backup_dict(self) -> Dict[str, Any]:
        metadata = self.raw_series.get("metadata") if isinstance(self.raw_series.get("metadata"), dict) else {}
        return {
            "library_id": self.library_id,
            "library_name": self.library_name,
            "series_id": self.series_id,
            "old_title": self.old_title,
            "new_title": self.new_title,
            "old_title_sort": self.old_title_sort,
            "new_title_sort": self.new_title_sort,
            "folder_name": self.folder_name,
            "source": self.source,
            "confidence": self.confidence,
            "score": self.score,
            "book_count": self.book_count,
            "samples": self.samples,
            "notes": self.notes,
            "old_metadata": metadata,
            "raw_series": self.raw_series,
        }


@dataclass
class SortTitleMismatch:
    library_id: str
    library_name: str
    series_id: str
    title: str
    title_sort: str
    proposed_title_sort: str
    folder_name: str
    notes: str
    raw_series: Dict[str, Any] = field(default_factory=dict)

    def to_backup_dict(self) -> Dict[str, Any]:
        metadata = self.raw_series.get("metadata") if isinstance(self.raw_series.get("metadata"), dict) else {}
        return {
            "library_id": self.library_id,
            "library_name": self.library_name,
            "series_id": self.series_id,
            "title": self.title,
            "old_title_sort": self.title_sort,
            "new_title_sort": self.proposed_title_sort,
            "folder_name": self.folder_name,
            "notes": self.notes,
            "old_metadata": metadata,
            "raw_series": self.raw_series,
        }


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(safe_str(x) for x in value if safe_str(x))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def truthy_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def clean_spaces(value: Optional[str]) -> str:
    value = (value or "").replace("\xa0", " ").replace("\u202f", " ").replace("\u2009", " ")
    value = re.sub(r"\s+", " ", value, flags=re.UNICODE)
    return value.strip()


def strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def normalize_for_compare(value: str) -> str:
    value = clean_spaces(value).lower().replace("’", "'")
    value = strip_accents(value)
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def fuzzy_ratio(a: str, b: str) -> float:
    na = normalize_for_compare(a)
    nb = normalize_for_compare(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def same_title(a: str, b: str, threshold: float = 0.94) -> bool:
    if not a or not b:
        return False
    if normalize_for_compare(a) == normalize_for_compare(b):
        return True
    return fuzzy_ratio(a, b) >= threshold


def same_sort_title(title: str, title_sort: str) -> bool:
    """Strict match for Komga title/titleSort consistency.

    This deliberately does not use fuzzy matching: the maintenance tab is meant
    to find every series where the displayed title and the sort title are not
    exactly aligned after harmless whitespace normalization.
    """
    return clean_spaces(title) == clean_spaces(title_sort)


def normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("URL vide")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    return url.rstrip("/")


def unique_nonempty(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    output: List[str] = []
    for value in values:
        text = clean_spaces(value).strip(" -_.,;:")
        if not text:
            continue
        key = normalize_for_compare(text)
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def extract_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("content", "data", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


# ---------------------------------------------------------------------------
# Title cleanup and filename parsing
# ---------------------------------------------------------------------------


LEADING_EVENT_TAG_RE = re.compile(r"^\s*\((?:C\d{2,4}|SC\d{2,4}|COMITIA\d+|COMIC\s*MARKET\s*\d+)\)\s*", re.IGNORECASE)
PAREN_RE = re.compile(r"\s*\(([^()]+)\)")


def strip_leading_event_tags(stem: str) -> str:
    out = clean_spaces(stem).replace("–", "-").replace("—", "-")
    previous = None
    while previous != out:
        previous = out
        out = LEADING_EVENT_TAG_RE.sub("", out)
    return clean_spaces(out)


def split_leading_square_bracket_tags(stem: str) -> Tuple[str, List[str]]:
    out = strip_leading_event_tags(stem)
    tags: List[str] = []
    while True:
        match = re.match(r"^\s*\[([^\]]+)\]\s*", out)
        if not match:
            break
        tag = clean_spaces(match.group(1))
        if tag:
            tags.append(tag)
        out = out[match.end():]
    return clean_spaces(out), tags


def is_metadata_paren(token: str) -> bool:
    raw = clean_spaces(token)
    low = raw.lower()
    norm = normalize_for_compare(raw)
    if not raw:
        return False
    if re.fullmatch(r"(19|20)\d{2}", raw):
        return True
    if re.fullmatch(r"of\s+\d+", low) or re.fullmatch(r"\d+\s*of\s*\d+", low) or norm == "complet":
        return True
    if low in META_PAREN_FIXED_TOKENS or norm in META_PAREN_FIXED_TOKENS:
        return True
    has_digit = any(ch.isdigit() for ch in raw)
    has_alpha = any(ch.isalpha() for ch in raw)
    if has_digit:
        return True
    return False


def strip_metadata_parens(value: str) -> str:
    out = clean_spaces(value)
    previous = None
    while previous != out:
        previous = out
        def repl(match: re.Match[str]) -> str:
            token = match.group(1)
            return "" if is_metadata_paren(token) else match.group(0)
        out = PAREN_RE.sub(repl, out)
        out = clean_spaces(out)
    return out


def strip_trailing_square_bracket_metadata(value: str) -> str:
    out = clean_spaces(value)
    previous = None
    while previous != out:
        previous = out
        match = re.search(r"\s*\[([^\[\]]+)\]\s*$", out)
        if not match:
            break
        token = clean_spaces(match.group(1))
        norm = normalize_for_compare(token)
        is_likely_metadata = (
            len(token) <= 16
            or any(ch.isdigit() for ch in token)
            or norm in META_PAREN_FIXED_TOKENS
            or token.upper() == token
            or " " not in token
        )
        if not is_likely_metadata:
            break
        out = out[: match.start()].strip(" -_.")
    return clean_spaces(out)


def strip_trailing_noise(value: str) -> str:
    out = clean_spaces(value)
    previous = None
    while previous != out:
        previous = out
        for pattern in TRAILING_NOISE_PATTERNS:
            out = pattern.sub("", out)
        out = clean_spaces(out).strip(" -_")
    return out


def clean_parent_series(name: str) -> str:
    name = clean_spaces(name)
    previous = None
    while previous != name:
        previous = name
        for pattern in CONTAINER_FOLDER_PATTERNS:
            name = pattern.sub("", name)
        name = clean_spaces(name).strip(" -_")
    return name


def is_generic_folder(name: str) -> bool:
    normalized = normalize_for_compare(name)
    return normalized in SKIP_FOLDER_NAMES or bool(re.fullmatch(r"1\s+(?:fr|en)", normalized))


def clean_series_candidate(value: str) -> str:
    out = clean_spaces(value)
    out, _tags = split_leading_square_bracket_tags(out)
    out = strip_trailing_square_bracket_metadata(out)
    out = clean_parent_series(out)
    out = strip_trailing_noise(out)
    out = strip_metadata_parens(out)
    out = TRAILING_TOME_WORDS_RE.sub("", out).strip(" -_.")
    out = re.sub(r"\s+(?:VF|VO|FR|EN|VOSTFR|RAW)\s*$", "", out, flags=re.IGNORECASE).strip(" -_.")
    out = clean_spaces(out)
    if is_generic_folder(out) or re.fullmatch(r"\d+", out):
        return ""
    return out


def normalize_filename_stem(name_or_path: str) -> str:
    text = safe_str(name_or_path)
    if not text:
        return ""
    # If Komga returns an URL/path, keep the last path segment.
    try:
        parsed = parse.urlsplit(text)
        if parsed.path and ("/" in parsed.path or "\\" in parsed.path):
            text = os.path.basename(parse.unquote(parsed.path))
    except Exception:
        pass
    text = PureWindowsPath(text).name
    stem = PureWindowsPath(text).stem
    stem, _tags = split_leading_square_bracket_tags(stem)
    stem = re.sub(r"^\d{3,5}\s*-\s*(?=[A-Za-zÀ-ÖØ-öø-ÿ])", "", stem)
    stem = re.sub(r"\.(?:cbz|cbr|cb7|cbt|pdf|epub)$", "", stem, flags=re.IGNORECASE)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s+", " ", stem)
    return stem.strip(" _")


def parse_filename_series(name_or_path: str) -> Tuple[str, str]:
    """Return (series, rule) extracted from a book filename/name."""
    stem = normalize_filename_stem(name_or_path)
    if not stem:
        return "", ""
    stem = strip_metadata_parens(stem)
    stem = strip_trailing_square_bracket_metadata(stem)
    stem = re.sub(r"\s+(?:VF|VO|FR|EN|VOSTFR|RAW)\s*$", "", stem, flags=re.IGNORECASE).strip(" -_.")

    for pattern in DIRECT_NUMBER_PATTERNS:
        match = pattern.match(stem)
        if not match:
            continue
        series = clean_series_candidate(match.groupdict().get("series", ""))
        if series:
            return series, "nom de fichier"

    # One-shot / book without explicit issue number.
    if re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", stem):
        series = clean_series_candidate(stem)
        if series:
            return series, "fichier one-shot"
    return "", ""


def title_confidence(score: int) -> str:
    if score >= 82:
        return CONFIDENCE_HIGH
    if score >= 58:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


# ---------------------------------------------------------------------------
# Komga API
# ---------------------------------------------------------------------------


class HttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: Optional[int], body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status or '?'} {method} {url}\n{body}")


class ApiClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 45, logger: Optional[Callable[[str], None]] = None):
        self.base_url = normalize_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.logger = logger

    def get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("GET", path, query=query)

    def post(self, path: str, body: Optional[Any] = None, query: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("POST", path, body=body, query=query)

    def patch(self, path: str, body: Optional[Any] = None, query: Optional[Dict[str, Any]] = None) -> Any:
        return self.request("PATCH", path, body=body, query=query)

    def request(self, method: str, path: str, body: Optional[Any] = None, query: Optional[Dict[str, Any]] = None) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            clean_query = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean_query:
                url += "?" + parse.urlencode(clean_query, doseq=True)

        headers = {
            "Accept": "application/json",
            "User-Agent": f"komga-series-fix/{APP_VERSION}",
        }
        data = None
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        suffix = "" if body is None else f" body={json.dumps(body, ensure_ascii=False)[:500]}"
        http_line = f"HTTP {method} {url}{suffix}"
        append_debug_log(http_line)
        # Avoid flooding QTextEdit during large scans. Full HTTP traces are always
        # available in komga_series_fix_debug.log; set this env var only when you
        # explicitly want every request in the GUI log too.
        if self.logger and os.getenv("KOMGA_SERIES_FIX_HTTP_UI_LOG") == "1":
            self.logger(http_line)

        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return None
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise HttpError(method, url, exc.code, raw) from exc
        except error.URLError as exc:
            raise HttpError(method, url, None, str(exc)) from exc
        except TimeoutError as exc:
            raise HttpError(method, url, None, f"Timeout après {self.timeout}s: {exc}") from exc


def extract_libraries(data: Any) -> List[LibraryItem]:
    libraries: List[LibraryItem] = []
    for item in extract_items(data):
        lib_id = safe_str(item.get("id"))
        name = safe_str(item.get("name") or item.get("title") or lib_id)
        if lib_id:
            libraries.append(LibraryItem(id=lib_id, name=name, raw=item))
    libraries.sort(key=lambda x: x.name.lower())
    return libraries


def extract_series(data: Any, library_filter: Optional[str] = None, strict_library: bool = False) -> List[SeriesItem]:
    series: List[SeriesItem] = []
    for item in extract_items(data):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        sid = safe_str(item.get("id"))
        lib_obj = item.get("library") if isinstance(item.get("library"), dict) else {}
        lib_id = safe_str(item.get("libraryId") or item.get("library_id") or lib_obj.get("id") or "")
        if library_filter:
            if lib_id:
                if lib_id != library_filter:
                    continue
            elif strict_library:
                continue
        folder_name = safe_str(item.get("name") or item.get("title") or metadata.get("title") or metadata.get("titleSort") or sid)
        current_title = safe_str(metadata.get("title") or item.get("title") or item.get("name") or metadata.get("titleSort") or sid)
        title_sort = safe_str(metadata.get("titleSort") or item.get("titleSort") or item.get("sortTitle") or metadata.get("sortTitle") or "")
        book_count = safe_str(item.get("bookCount") or item.get("booksCount") or item.get("numberOfBooks") or "")
        if sid:
            series.append(SeriesItem(
                id=sid,
                current_title=current_title,
                title_sort=title_sort,
                folder_name=folder_name,
                library_id=lib_id or (library_filter or ""),
                book_count=book_count,
                raw=item,
            ))
    series.sort(key=lambda s: s.current_title.lower())
    return series


def extract_books(data: Any, series_id: Optional[str] = None, strict_series: bool = False) -> List[BookItem]:
    books: List[BookItem] = []
    for item in extract_items(data):
        bid = safe_str(item.get("id"))
        sid = safe_str(item.get("seriesId") or item.get("series_id") or "")
        if series_id:
            if sid:
                if sid != series_id:
                    continue
            elif strict_series:
                continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = safe_str(item.get("name") or item.get("fileName") or item.get("filename") or metadata.get("title") or bid)
        if bid:
            books.append(BookItem(id=bid, name=name, series_id=sid or (series_id or ""), raw=item))
    books.sort(key=lambda b: b.name.lower())
    return books


class KomgaApi:
    def __init__(self, url: str, api_key: str, logger: Optional[Callable[[str], None]] = None):
        self.client = ApiClient(url, api_key=api_key, logger=logger)

    def test(self) -> str:
        data = self.client.get("/api/v1/libraries")
        libraries = extract_libraries(data)
        return f"Komga OK — {len(libraries)} bibliothèque(s) accessible(s)"

    def libraries(self) -> List[LibraryItem]:
        return extract_libraries(self.client.get("/api/v1/libraries"))

    def series(self, library_id: str) -> List[SeriesItem]:
        if not library_id:
            return []

        last_error: Optional[Exception] = None
        attempts: List[str] = []

        def keep_only_selected(data: Any, source: str, strict: bool = True) -> Optional[List[SeriesItem]]:
            items = extract_series(data, library_filter=library_id, strict_library=strict)
            attempts.append(f"{source}: {len(items)} série(s) gardée(s)")
            if items:
                for item in items:
                    item.library_id = library_id
                return items
            return None

        scoped_paths = (
            f"/api/v1/libraries/{parse.quote(library_id)}/series",
            f"/api/v1/library/{parse.quote(library_id)}/series",
        )
        for path in scoped_paths:
            try:
                data = self.client.get(path, query={"unpaged": "true", "sort": "metadata.titleSort,asc"})
                selected = keep_only_selected(data, f"GET {path}", strict=False)
                if selected is not None:
                    return selected
            except Exception as exc:
                last_error = exc
                attempts.append(f"GET {path}: échec")

        post_bodies = [
            {"libraryIds": [library_id]},
            {"libraryId": library_id},
            {"library_ids": [library_id]},
            {"library_id": library_id},
            {"library_id": [library_id]},
            {"libraries": [library_id]},
            {"condition": {"libraryIds": [library_id]}},
            {"filters": {"libraryIds": [library_id]}},
        ]
        for body in post_bodies:
            try:
                data = self.client.post(
                    "/api/v1/series/list",
                    body=body,
                    query={"unpaged": "true", "sort": "metadata.titleSort,asc"},
                )
                selected = keep_only_selected(data, f"POST /api/v1/series/list body={body!r}", strict=True)
                if selected is not None:
                    return selected
            except Exception as exc:
                last_error = exc
                attempts.append(f"POST /api/v1/series/list body={body!r}: échec")

        get_queries = (
            {"library_id": library_id, "unpaged": "true", "sort": "metadata.titleSort,asc"},
            {"libraryId": library_id, "unpaged": "true", "sort": "metadata.titleSort,asc"},
        )
        for query in get_queries:
            try:
                data = self.client.get("/api/v1/series", query=query)
                selected = keep_only_selected(data, f"GET /api/v1/series query={query!r}", strict=True)
                if selected is not None:
                    return selected
            except Exception as exc:
                last_error = exc
                attempts.append(f"GET /api/v1/series query={query!r}: échec")

        details = "\n".join(attempts[-20:]) or "aucune tentative exploitable"
        if last_error:
            raise RuntimeError(
                "Impossible de charger uniquement la bibliothèque sélectionnée. "
                "J'ai refusé de retourner la liste complète de Komga.\n\n"
                f"libraryId demandé : {library_id}\nTentatives :\n{details}\n\nDernière erreur : {last_error}"
            )
        raise RuntimeError(
            "Impossible de charger uniquement la bibliothèque sélectionnée. "
            f"libraryId demandé : {library_id}\nTentatives :\n{details}"
        )

    def books_for_series(self, series_id: str) -> List[BookItem]:
        if not series_id:
            return []
        last_error: Optional[Exception] = None
        attempts: List[str] = []

        # Endpoint déprécié mais fiable et simple; les docs Komga indiquent le remplacement
        # par POST /api/v1/books/list, utilisé en fallback plus bas.
        try:
            path = f"/api/v1/series/{parse.quote(series_id)}/books"
            data = self.client.get(path, query={"unpaged": "true", "sort": "metadata.numberSort,asc"})
            books = extract_books(data, series_id=series_id, strict_series=False)
            attempts.append(f"GET {path}: {len(books)} livre(s)")
            if books:
                return books
        except Exception as exc:
            last_error = exc
            attempts.append("GET /api/v1/series/{id}/books: échec")

        bodies = [
            {"seriesId": series_id},
            {"seriesIds": [series_id]},
            {"series_id": series_id},
            {"series_id": [series_id]},
            {"condition": {"seriesId": series_id}},
            {"condition": {"seriesIds": [series_id]}},
            {"filters": {"seriesId": series_id}},
            {"filters": {"seriesIds": [series_id]}},
        ]
        for body in bodies:
            try:
                data = self.client.post(
                    "/api/v1/books/list",
                    body=body,
                    query={"unpaged": "true", "sort": "metadata.numberSort,asc"},
                )
                books = extract_books(data, series_id=series_id, strict_series=True)
                attempts.append(f"POST /api/v1/books/list body={body!r}: {len(books)} livre(s)")
                if books:
                    return books
            except Exception as exc:
                last_error = exc
                attempts.append(f"POST /api/v1/books/list body={body!r}: échec")

        # Dernier fallback déprécié.
        try:
            data = self.client.get("/api/v1/books", query={"series_id": series_id, "unpaged": "true", "sort": "metadata.numberSort,asc"})
            books = extract_books(data, series_id=series_id, strict_series=True)
            attempts.append(f"GET /api/v1/books series_id={series_id}: {len(books)} livre(s)")
            if books:
                return books
        except Exception as exc:
            last_error = exc
            attempts.append("GET /api/v1/books: échec")

        if last_error:
            raise RuntimeError(f"Impossible de charger les livres de la série {series_id}.\n" + "\n".join(attempts[-20:]) + f"\nDernière erreur: {last_error}")
        return []

    def update_series_title(self, series_id: str, title: str, title_sort: Optional[str] = None, lock_title: bool = True) -> None:
        path = f"/api/v1/series/{parse.quote(series_id)}/metadata"
        title = clean_spaces(title)
        title_sort = clean_spaces(title_sort if title_sort is not None else title)
        if not title:
            raise ValueError("Titre vide")
        if not title_sort:
            raise ValueError("Titre de tri vide")
        body: Dict[str, Any] = {"title": title, "titleSort": title_sort}
        if lock_title:
            body["titleLock"] = True
            body["titleSortLock"] = True
        try:
            self.client.patch(path, body=body)
        except HttpError as exc:
            # Certaines versions peuvent refuser les champs de lock selon leur DTO.
            # On réessaie sans locks, mais on garde title + titleSort: les deux doivent matcher.
            if lock_title and exc.status == 400:
                self.client.patch(path, body={"title": title, "titleSort": title_sort})
                return
            raise

    def update_series_sort_title(self, series_id: str, title_sort: str, lock_sort: bool = True) -> None:
        path = f"/api/v1/series/{parse.quote(series_id)}/metadata"
        title_sort = clean_spaces(title_sort)
        if not title_sort:
            raise ValueError("Titre de tri vide")
        body: Dict[str, Any] = {"titleSort": title_sort}
        if lock_sort:
            body["titleSortLock"] = True
        try:
            self.client.patch(path, body=body)
        except HttpError as exc:
            if lock_sort and exc.status == 400:
                self.client.patch(path, body={"titleSort": title_sort})
                return
            raise


# ---------------------------------------------------------------------------
# Scan logic
# ---------------------------------------------------------------------------


def book_candidate_values(book: BookItem) -> List[str]:
    raw = book.raw
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    values = [
        book.name,
        safe_str(raw.get("fileName")),
        safe_str(raw.get("filename")),
        safe_str(raw.get("path")),
        safe_str(raw.get("filePath")),
        safe_str(raw.get("url")),
        safe_str(media.get("fileName")),
        safe_str(media.get("filePath")),
    ]
    # On évite metadata.title comme source forte: c'est souvent déjà la métadonnée Komga.
    return unique_nonempty(values)


def build_file_consensus(books: List[BookItem]) -> Tuple[str, int, int, float, List[str]]:
    counter: Counter[str] = Counter()
    display_by_key: Dict[str, str] = {}
    sample_by_key: Dict[str, List[str]] = {}

    for book in books:
        candidates: List[str] = []
        for value in book_candidate_values(book):
            series, _rule = parse_filename_series(value)
            if series:
                candidates.append(series)
        candidates = unique_nonempty(candidates)
        if not candidates:
            continue
        # Une voix par livre pour éviter qu'un même livre pèse plusieurs fois.
        best = candidates[0]
        key = normalize_for_compare(best)
        if not key:
            continue
        counter[key] += 1
        display_by_key.setdefault(key, best)
        sample_by_key.setdefault(key, [])
        if len(sample_by_key[key]) < 5:
            sample_by_key[key].append(book.name)

    if not counter:
        return "", 0, 0, 0.0, []

    key, count = counter.most_common(1)[0]
    total = sum(counter.values())
    ratio = count / total if total else 0.0
    return display_by_key.get(key, ""), count, total, ratio, sample_by_key.get(key, [])


def choose_proposal(series: SeriesItem, books: List[BookItem], library: LibraryItem, source_mode: str) -> Optional[SeriesChange]:
    old_title = clean_spaces(series.current_title)
    folder_candidate = clean_series_candidate(series.folder_name)
    file_candidate, file_count, file_total, file_ratio, samples = build_file_consensus(books)

    folder_valid = bool(folder_candidate)
    file_valid = bool(file_candidate)

    options = unique_nonempty([folder_candidate, file_candidate, old_title])
    notes: List[str] = []
    if folder_valid:
        notes.append(f"dossier='{folder_candidate}'")
    if file_valid:
        notes.append(f"fichiers='{file_candidate}' consensus {file_count}/{file_total} ({file_ratio:.0%})")
    if folder_valid and file_valid and not same_title(folder_candidate, file_candidate):
        notes.append("conflit dossier/fichiers: vérification manuelle conseillée")

    choices: List[Tuple[str, str, int, str]] = []  # candidate, source, base score, note
    if folder_valid:
        score = 88
        if file_valid and same_title(folder_candidate, file_candidate):
            score = 96
        elif file_valid and not same_title(folder_candidate, file_candidate):
            score = 68
        choices.append((folder_candidate, "dossier Komga", score, "source dossier"))
    if file_valid and (file_count >= 2 or file_ratio >= 0.80):
        score = min(90, 55 + int(file_ratio * 30) + min(file_count, 5))
        if folder_valid and same_title(folder_candidate, file_candidate):
            score = 94
        elif folder_valid and not same_title(folder_candidate, file_candidate):
            score = max(58, score - 15)
        choices.append((file_candidate, "consensus fichiers", score, "source fichiers"))
    elif file_valid:
        choices.append((file_candidate, "fichiers faible consensus", 45, "source fichiers faible"))

    if not choices:
        return None

    if source_mode == SOURCE_FOLDER_ONLY:
        choices = [c for c in choices if c[1].startswith("dossier")]
    elif source_mode == SOURCE_FILES_ONLY:
        choices = [c for c in choices if "fichier" in c[1]]
    elif source_mode == SOURCE_FILES_THEN_FOLDER:
        choices.sort(key=lambda c: (0 if "fichier" in c[1] else 1, -c[2]))
    else:
        choices.sort(key=lambda c: (0 if c[1].startswith("dossier") else 1, -c[2]))

    if not choices:
        return None

    candidate, source, score, source_note = choices[0]
    if not candidate or same_title(old_title, candidate):
        return None

    notes.append(source_note)
    if old_title:
        if fuzzy_ratio(old_title, candidate) >= 0.80:
            notes.append("ancien titre proche")
            score = min(100, score + 3)
        else:
            notes.append("ancien titre différent")
            score = max(0, score - 4)

    return SeriesChange(
        library_id=library.id,
        library_name=library.name,
        series_id=series.id,
        old_title=old_title,
        new_title=candidate,
        old_title_sort=clean_spaces(series.title_sort),
        new_title_sort=candidate,
        folder_name=series.folder_name,
        source=source,
        confidence=title_confidence(score),
        score=score,
        book_count=len(books),
        samples=samples[:5],
        notes="; ".join(notes),
        options=options,
        raw_series=series.raw,
    )


def scan_library(api: KomgaApi, library: LibraryItem, source_mode: str, progress: Optional[Callable[[str], None]] = None) -> List[SeriesChange]:
    if progress:
        progress(f"Chargement des séries de la bibliothèque: {library.name}")
    series_list = api.series(library.id)
    changes: List[SeriesChange] = []

    for idx, series in enumerate(series_list, start=1):
        if progress and (idx == 1 or idx % 25 == 0 or idx == len(series_list)):
            progress(f"Scan série {idx}/{len(series_list)}: {series.current_title}")
        try:
            books = api.books_for_series(series.id)
        except Exception as exc:
            if progress:
                progress(f"⚠️ Livres non chargés pour {series.current_title}: {exc}")
            books = []
        series.books = books
        change = choose_proposal(series, books, library, source_mode)
        if change is not None:
            changes.append(change)

    changes.sort(key=lambda c: (-c.score, c.old_title.lower()))
    if progress:
        progress(f"Scan terminé: {len(changes)} changement(s) proposé(s) sur {len(series_list)} série(s)")
    return changes


def scan_sort_mismatches(api: KomgaApi, library: LibraryItem, progress: Optional[Callable[[str], None]] = None) -> List[SortTitleMismatch]:
    if progress:
        progress(f"Chargement des séries pour contrôle titre/tri: {library.name}")
    series_list = api.series(library.id)
    mismatches: List[SortTitleMismatch] = []
    for idx, series in enumerate(series_list, start=1):
        if progress and (idx == 1 or idx % 100 == 0 or idx == len(series_list)):
            progress(f"Contrôle titre/tri {idx}/{len(series_list)}: {series.current_title}")
        title = clean_spaces(series.current_title)
        title_sort = clean_spaces(series.title_sort)
        if not title:
            continue
        if same_sort_title(title, title_sort):
            continue
        note = "titre de tri vide" if not title_sort else "titre et titre de tri différents"
        mismatches.append(SortTitleMismatch(
            library_id=library.id,
            library_name=library.name,
            series_id=series.id,
            title=title,
            title_sort=title_sort,
            proposed_title_sort=title,
            folder_name=series.folder_name,
            notes=note,
            raw_series=series.raw,
        ))
    mismatches.sort(key=lambda c: c.title.lower())
    if progress:
        progress(f"Contrôle titre/tri terminé: {len(mismatches)} mismatch(s) sur {len(series_list)} série(s)")
    return mismatches


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


COLUMNS = [
    ("score", "Score", 60),
    ("confidence", "Confiance", 85),
    ("source", "Source", 135),
    ("old_title", "Titre actuel", 220),
    ("new_title", "Titre proposé", 220),
    ("old_title_sort", "Tri actuel", 220),
    ("new_title_sort", "Tri proposé", 220),
    ("folder_name", "Nom dossier/série Komga", 230),
    ("book_count", "Livres", 65),
    ("samples", "Exemples fichiers", 280),
    ("notes", "Notes", 360),
    ("series_id", "ID série", 150),
]


SORT_COLUMNS = [
    ("title", "Titre", 280),
    ("title_sort", "Tri actuel", 280),
    ("proposed_title_sort", "Tri proposé", 280),
    ("folder_name", "Nom dossier/série Komga", 260),
    ("notes", "Notes", 260),
    ("series_id", "ID série", 150),
]


class MainWindow(QMainWindow):
    log_signal = Signal(str)

    def __init__(
        self,
        api_provider: Optional[Callable[[], Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION}")
        self.resize(1500, 900)
        self.api_provider = api_provider
        self.thread_pool = QThreadPool.globalInstance()
        self.active_workers: set[Worker] = set()
        self.libraries: List[LibraryItem] = []
        self.changes: List[SeriesChange] = []
        self.visible_changes: List[SeriesChange] = []
        self.sort_mismatches: List[SortTitleMismatch] = []
        self.visible_sort_mismatches: List[SortTitleMismatch] = []

        self._build_ui()
        self.log_signal.connect(self._append_log)
        self.log_info(f"Journal debug: {DEBUG_LOG_FILE}")
        if self.api_provider:
            self._configure_shared_connection_ui()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        grid = QGridLayout()
        main.addLayout(grid)

        self.komga_url = QLineEdit(DEFAULT_KOMGA_URL)
        self.komga_api_key = QLineEdit()
        self.komga_api_key.setEchoMode(QLineEdit.Password)
        self.save_api_key = QCheckBox("Mémoriser la clé API")
        self.save_api_key.setChecked(True)
        self.simulation = QCheckBox("Simulation")
        self.simulation.setChecked(True)
        self.lock_title = QCheckBox("Verrouiller titre + tri dans Komga")
        self.lock_title.setChecked(True)
        self.source_mode = QComboBox()
        self.source_mode.addItems(SOURCE_MODES)
        self.source_mode.setCurrentText(SOURCE_FOLDER_THEN_FILES)
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Filtrer les propositions…")
        self.filter_text.textChanged.connect(self.refresh_all_tables)

        grid.addWidget(QLabel("Komga URL"), 0, 0)
        grid.addWidget(self.komga_url, 0, 1)
        grid.addWidget(QLabel("Clé API"), 0, 2)
        grid.addWidget(self.komga_api_key, 0, 3)
        grid.addWidget(self.save_api_key, 0, 4)
        grid.addWidget(self.simulation, 0, 5)
        grid.addWidget(self.lock_title, 0, 6)

        self.library_combo = QComboBox()
        self.library_combo.setMinimumWidth(320)
        grid.addWidget(QLabel("Bibliothèque"), 1, 0)
        grid.addWidget(self.library_combo, 1, 1)
        grid.addWidget(QLabel("Source"), 1, 2)
        grid.addWidget(self.source_mode, 1, 3)
        grid.addWidget(QLabel("Filtre"), 1, 4)
        grid.addWidget(self.filter_text, 1, 5, 1, 2)

        buttons = QHBoxLayout()
        main.addLayout(buttons)
        self.test_btn = QPushButton("Tester Komga")
        self.load_libraries_btn = QPushButton("Charger bibliothèques")
        self.load_series_btn = QPushButton("Analyser la bibliothèque")
        self.scan_sort_btn = QPushButton("Analyser les titres de tri")
        self.apply_btn = QPushButton("Appliquer les changements")
        self.apply_sort_btn = QPushButton("Appliquer les changements de tri")
        self.export_btn = QPushButton("Exporter CSV")
        self.clear_btn = QPushButton("Vider")
        for btn in (self.test_btn, self.load_libraries_btn, self.load_series_btn, self.scan_sort_btn, self.apply_btn, self.apply_sort_btn, self.export_btn, self.clear_btn):
            buttons.addWidget(btn)
        buttons.addStretch(1)

        self.test_btn.clicked.connect(self.on_test)
        self.load_libraries_btn.clicked.connect(self.on_load_libraries)
        self.load_series_btn.clicked.connect(self.on_scan_library)
        self.scan_sort_btn.clicked.connect(self.on_scan_sort_titles)
        self.apply_btn.clicked.connect(self.on_apply_selected)
        self.apply_sort_btn.clicked.connect(self.on_apply_sort_selected)
        self.export_btn.clicked.connect(self.on_export_csv)
        self.clear_btn.clicked.connect(self.on_clear)

        splitter = QSplitter(Qt.Vertical)
        main.addWidget(splitter, stretch=1)

        tabs = QTabWidget()
        splitter.addWidget(tabs)

        changes_tab = QWidget()
        changes_layout = QVBoxLayout(changes_tab)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([h for _key, h, _width in COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for i, (_key, _h, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, width)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed | QTableWidget.SelectedClicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.on_table_context_menu)
        self.table.itemChanged.connect(self.on_item_changed)
        changes_layout.addWidget(self.table)
        tabs.addTab(changes_tab, "Changements")

        sort_tab = QWidget()
        sort_layout = QVBoxLayout(sort_tab)
        self.sort_table = QTableWidget(0, len(SORT_COLUMNS))
        self.sort_table.setHorizontalHeaderLabels([h for _key, h, _width in SORT_COLUMNS])
        self.sort_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for i, (_key, _h, width) in enumerate(SORT_COLUMNS):
            self.sort_table.setColumnWidth(i, width)
        self.sort_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.sort_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.sort_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed | QTableWidget.SelectedClicked)
        self.sort_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sort_table.customContextMenuRequested.connect(self.on_sort_table_context_menu)
        self.sort_table.itemChanged.connect(self.on_sort_item_changed)
        sort_layout.addWidget(self.sort_table)
        tabs.addTab(sort_tab, "Titres de tri")

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        tabs.addTab(log_tab, "Journal")

        self.summary = QLabel("Prêt.")
        main.addWidget(self.summary)
        splitter.setSizes([650, 220])

    # ---------- Session / logging ----------

    def _load_legacy_settings(self) -> None:
        return

    def _configure_shared_connection_ui(self) -> None:
        self.komga_url.setText("Connexion fournie par Komga Toolkit")
        self.komga_api_key.clear()
        self.save_api_key.setChecked(False)
        self.komga_url.setEnabled(False)
        self.komga_api_key.setEnabled(False)
        self.save_api_key.setEnabled(False)

    def _keep_session_settings(self) -> None:
        return

    def log_info(self, message: str) -> None:
        # This method may be called from worker threads. Never touch Qt widgets
        # directly here: emitting a signal avoids silent Qt crashes when QTextEdit
        # is updated outside the GUI thread.
        known = [self.komga_api_key.text()] if hasattr(self, "komga_api_key") else []
        safe = SecretRedactor.redact(message, known)
        append_debug_log(safe)
        try:
            self.log_signal.emit(safe)
        except Exception:
            pass

    def _append_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{stamp}] {message}")

    def api(self) -> KomgaApi:
        if self.api_provider:
            return self.api_provider()
        return KomgaApi(self.komga_url.text(), self.komga_api_key.text(), logger=self.log_info)

    def current_library(self) -> Optional[LibraryItem]:
        idx = self.library_combo.currentIndex()
        if idx < 0:
            return None
        lib_id = safe_str(self.library_combo.itemData(idx))
        for lib in self.libraries:
            if lib.id == lib_id:
                return lib
        return None

    def run_worker(self, label: str, fn: Callable[[], Any], on_result: Callable[[Any], None]) -> None:
        self.log_info(f"⏳ {label}...")
        worker = Worker(fn)
        try:
            worker.setAutoDelete(False)
        except Exception:
            pass
        self.active_workers.add(worker)

        def safe_result(result: Any) -> None:
            try:
                on_result(result)
            except Exception:
                self.on_worker_error(label, traceback.format_exc())

        def cleanup() -> None:
            self.active_workers.discard(worker)
            self.log_info(f"ℹ️ {label} terminé")

        worker.signals.result.connect(safe_result)
        worker.signals.error.connect(lambda e: self.on_worker_error(label, e))
        worker.signals.finished.connect(cleanup)
        self.thread_pool.start(worker)

    def on_worker_error(self, label: str, trace: str) -> None:
        self.log_info(f"❌ {label} a échoué:\n{trace}")
        QMessageBox.critical(self, "Erreur", f"{label} a échoué. Détail dans le journal.")

    # ---------- Actions ----------

    def on_test(self) -> None:
        self._keep_session_settings()
        self.run_worker("Test Komga", lambda: self.api().test(), lambda result: QMessageBox.information(self, APP_TITLE, safe_str(result)))

    def on_load_libraries(self) -> None:
        self._keep_session_settings()
        self.run_worker("Chargement bibliothèques", lambda: self.api().libraries(), self._on_libraries_loaded)

    def _on_libraries_loaded(self, libraries: List[LibraryItem]) -> None:
        self.libraries = libraries
        self.library_combo.clear()
        for lib in libraries:
            self.library_combo.addItem(f"{lib.name} ({lib.id})", lib.id)
        self.summary.setText(f"{len(libraries)} bibliothèque(s) chargée(s). Sélectionne une bibliothèque puis lance le scan.")
        if not libraries:
            QMessageBox.warning(self, APP_TITLE, "Aucune bibliothèque trouvée.")

    def on_scan_library(self) -> None:
        self._keep_session_settings()
        library = self.current_library()
        if library is None:
            QMessageBox.warning(self, APP_TITLE, "Charge puis sélectionne une bibliothèque.")
            return
        source_mode = self.source_mode.currentText()
        api = self.api()
        self.run_worker(
            f"Scan bibliothèque {library.name}",
            lambda: scan_library(api, library, source_mode, progress=self.log_info),
            self._on_scan_done,
        )

    def _on_scan_done(self, changes: List[SeriesChange]) -> None:
        self.changes = changes
        self.refresh_table()
        self.summary.setText(f"{len(changes)} changement(s) proposé(s). Rien n'est appliqué tant que tu ne sélectionnes pas des lignes.")
        if not changes:
            QMessageBox.information(self, APP_TITLE, "Aucun changement proposé pour cette bibliothèque.")

    def on_scan_sort_titles(self) -> None:
        self._keep_session_settings()
        library = self.current_library()
        if library is None:
            QMessageBox.warning(self, APP_TITLE, "Charge puis sélectionne une bibliothèque.")
            return
        api = self.api()
        self.run_worker(
            f"Scan titres de tri {library.name}",
            lambda: scan_sort_mismatches(api, library, progress=self.log_info),
            self._on_sort_scan_done,
        )

    def _on_sort_scan_done(self, mismatches: List[SortTitleMismatch]) -> None:
        self.sort_mismatches = mismatches
        self.refresh_sort_table()
        self.summary.setText(f"{len(mismatches)} titre(s) de tri à corriger. Les titres déjà alignés ne sont pas affichés.")
        if not mismatches:
            QMessageBox.information(self, APP_TITLE, "Aucun mismatch titre / titre de tri pour cette bibliothèque.")

    def on_clear(self) -> None:
        self.changes = []
        self.visible_changes = []
        self.sort_mismatches = []
        self.visible_sort_mismatches = []
        self.table.setRowCount(0)
        self.sort_table.setRowCount(0)
        self.summary.setText("Tableaux vidés.")

    def on_export_csv(self) -> None:
        if not self.changes:
            QMessageBox.information(self, APP_TITLE, "Aucun changement à exporter.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter CSV", "komga_series_fix_preview.csv", "CSV (*.csv);;Tous les fichiers (*.*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow([h for _k, h, _w in COLUMNS])
                for c in self.changes:
                    writer.writerow(self.row_values(c))
            self.log_info(f"CSV exporté: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", f"Export impossible:\n{exc}")

    def on_apply_selected(self) -> None:
        selected = self.selected_changes()
        if not selected:
            QMessageBox.information(self, APP_TITLE, "Sélectionne une ou plusieurs lignes.")
            return
        self.sync_edited_titles_from_table()

        if self.simulation.isChecked():
            self.log_info(f"SIMULATION: {len(selected)} modification(s) seraient appliquées.")
            for c in selected:
                self.log_info(f"SIMULATION {c.series_id}: '{c.old_title}' -> '{c.new_title}'")
            self.summary.setText(f"Simulation: {len(selected)} modification(s) listée(s) dans le journal. Rien n'a été écrit.")
            return

        msg = (
            f"Appliquer réellement {len(selected)} changement(s) dans Komga ?\n\n"
            "Un backup JSON sera créé avant l'écriture. Les lignes réussies disparaîtront du tableau."
        )
        if QMessageBox.question(self, APP_TITLE, msg) != QMessageBox.Yes:
            return

        self._keep_session_settings()
        api = self.api()
        lock_title = self.lock_title.isChecked()
        backup_path = self.write_backup(selected)

        def do_apply() -> Dict[str, Any]:
            applied: List[str] = []
            errors: List[Tuple[str, str]] = []
            for c in selected:
                try:
                    api.update_series_title(c.series_id, c.new_title, title_sort=c.new_title_sort or c.new_title, lock_title=lock_title)
                    applied.append(c.series_id)
                except Exception:
                    errors.append((c.series_id, traceback.format_exc()))
            return {"applied": applied, "errors": errors, "backup": backup_path}

        self.run_worker("Application Komga", do_apply, self._on_apply_done)

    def _on_apply_done(self, result: Dict[str, Any]) -> None:
        applied_ids = set(result.get("applied", []))
        errors = result.get("errors", [])
        backup = result.get("backup", "")
        if applied_ids:
            self.changes = [c for c in self.changes if c.series_id not in applied_ids]
            self.refresh_table()
        self.log_info(f"Backup JSON: {backup}")
        self.log_info(f"Application réussie: {len(applied_ids)} série(s). Erreurs: {len(errors)}.")
        for series_id, trace in errors:
            self.log_info(f"❌ Erreur série {series_id}:\n{trace}")
        self.summary.setText(f"{len(applied_ids)} série(s) appliquée(s), {len(errors)} erreur(s).")
        if errors:
            QMessageBox.warning(self, APP_TITLE, f"{len(errors)} erreur(s). Détail dans le journal.")

    def on_apply_sort_selected(self) -> None:
        selected = self.selected_sort_mismatches()
        if not selected:
            QMessageBox.information(self, APP_TITLE, "Sélectionne une ou plusieurs lignes dans l'onglet Titres de tri.")
            return
        self.sync_edited_sort_titles_from_table()

        if self.simulation.isChecked():
            self.log_info(f"SIMULATION TRI: {len(selected)} modification(s) seraient appliquées.")
            for c in selected:
                self.log_info(f"SIMULATION TRI {c.series_id}: titleSort '{c.title_sort}' -> '{c.proposed_title_sort}'")
            self.summary.setText(f"Simulation tri: {len(selected)} modification(s) listée(s) dans le journal. Rien n'a été écrit.")
            return

        msg = (
            f"Appliquer réellement {len(selected)} correction(s) de titre de tri dans Komga ?\n\n"
            "Un backup JSON sera créé avant l'écriture. Les lignes réussies disparaîtront du tableau."
        )
        if QMessageBox.question(self, APP_TITLE, msg) != QMessageBox.Yes:
            return

        self._keep_session_settings()
        api = self.api()
        lock_sort = self.lock_title.isChecked()
        backup_path = self.write_sort_backup(selected)

        def do_apply() -> Dict[str, Any]:
            applied: List[str] = []
            errors: List[Tuple[str, str]] = []
            for c in selected:
                try:
                    api.update_series_sort_title(c.series_id, c.proposed_title_sort, lock_sort=lock_sort)
                    applied.append(c.series_id)
                except Exception:
                    errors.append((c.series_id, traceback.format_exc()))
            return {"applied": applied, "errors": errors, "backup": backup_path}

        self.run_worker("Application titres de tri Komga", do_apply, self._on_apply_sort_done)

    def _on_apply_sort_done(self, result: Dict[str, Any]) -> None:
        applied_ids = set(result.get("applied", []))
        errors = result.get("errors", [])
        backup = result.get("backup", "")
        if applied_ids:
            self.sort_mismatches = [c for c in self.sort_mismatches if c.series_id not in applied_ids]
            self.refresh_sort_table()
        self.log_info(f"Backup JSON tri: {backup}")
        self.log_info(f"Application tri réussie: {len(applied_ids)} série(s). Erreurs: {len(errors)}.")
        for series_id, trace in errors:
            self.log_info(f"❌ Erreur tri série {series_id}:\n{trace}")
        self.summary.setText(f"{len(applied_ids)} titre(s) de tri appliqué(s), {len(errors)} erreur(s).")
        if errors:
            QMessageBox.warning(self, APP_TITLE, f"{len(errors)} erreur(s). Détail dans le journal.")

    # ---------- Backup ----------

    def write_backup(self, changes: List[SeriesChange]) -> str:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        library = self.current_library()
        lib_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", library.name if library else "library").strip("_") or "library"
        path = os.path.join(BACKUP_DIR, f"komga_series_fix_{lib_name}_{timestamp}.json")
        payload = {
            "app": APP_TITLE,
            "version": APP_VERSION,
            "type": "series_title_and_sort_title",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "komga_url": self.komga_url.text().strip(),
            "library": library.raw if library else None,
            "changes": [c.to_backup_dict() for c in changes],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    def write_sort_backup(self, changes: List[SortTitleMismatch]) -> str:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        library = self.current_library()
        lib_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", library.name if library else "library").strip("_") or "library"
        path = os.path.join(BACKUP_DIR, f"komga_series_sort_fix_{lib_name}_{timestamp}.json")
        payload = {
            "app": APP_TITLE,
            "version": APP_VERSION,
            "type": "sort_title_only",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "komga_url": self.komga_url.text().strip(),
            "library": library.raw if library else None,
            "changes": [c.to_backup_dict() for c in changes],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    # ---------- Table: title fixes ----------

    def row_values(self, c: SeriesChange) -> List[str]:
        return [
            str(c.score),
            c.confidence,
            c.source,
            c.old_title,
            c.new_title,
            c.old_title_sort,
            c.new_title_sort,
            c.folder_name,
            str(c.book_count),
            " | ".join(c.samples),
            c.notes,
            c.series_id,
        ]

    def refresh_all_tables(self) -> None:
        self.refresh_table()
        self.refresh_sort_table()

    def refresh_table(self) -> None:
        text = normalize_for_compare(self.filter_text.text())
        self.visible_changes = []
        for c in self.changes:
            haystack = normalize_for_compare(" ".join([
                c.old_title, c.new_title, c.old_title_sort, c.new_title_sort,
                c.folder_name, c.source, c.confidence, c.notes, " ".join(c.samples), c.series_id,
            ]))
            if text and text not in haystack:
                continue
            self.visible_changes.append(c)

        self.table.blockSignals(True)
        self.table.setRowCount(len(self.visible_changes))
        for row, c in enumerate(self.visible_changes):
            values = self.row_values(c)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, c.series_id)
                if COLUMNS[col][0] != "new_title":
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.summary.setText(f"{len(self.visible_changes)} proposition(s) affichée(s) / {len(self.changes)} totale(s).")

    def selected_rows(self) -> List[int]:
        return sorted({idx.row() for idx in self.table.selectedIndexes()})

    def selected_changes(self) -> List[SeriesChange]:
        rows = self.selected_rows()
        return [self.visible_changes[r] for r in rows if 0 <= r < len(self.visible_changes)]

    def sync_edited_titles_from_table(self) -> None:
        new_title_col = self.column_index("new_title")
        for row, c in enumerate(self.visible_changes):
            item = self.table.item(row, new_title_col)
            if item is not None:
                c.new_title = clean_spaces(item.text())
                c.new_title_sort = c.new_title

    def column_index(self, key: str) -> int:
        for idx, (col_key, _h, _w) in enumerate(COLUMNS):
            if col_key == key:
                return idx
        raise KeyError(key)

    def on_item_changed(self, item: QTableWidgetItem) -> None:
        if COLUMNS[item.column()][0] != "new_title":
            return
        row = item.row()
        if 0 <= row < len(self.visible_changes):
            change = self.visible_changes[row]
            change.new_title = clean_spaces(item.text())
            change.new_title_sort = change.new_title
            sort_col = self.column_index("new_title_sort")
            sort_item = self.table.item(row, sort_col)
            if sort_item is not None and sort_item.text() != change.new_title_sort:
                self.table.blockSignals(True)
                sort_item.setText(change.new_title_sort)
                self.table.blockSignals(False)

    def on_table_context_menu(self, pos) -> None:
        selected = self.selected_changes()
        menu = QMenu(self)
        apply_action = menu.addAction("Appliquer la sélection")
        edit_action = menu.addAction("Modifier le titre proposé…")
        choose_action = menu.addAction("Choisir une proposition disponible…")
        replace_action = menu.addAction("Remplacer le titre proposé pour la sélection…")
        ignore_action = menu.addAction("Retirer/ignorer la sélection")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == apply_action:
            self.on_apply_selected()
        elif action == edit_action:
            self.edit_first_selected_title()
        elif action == choose_action:
            self.choose_proposal_for_first_selected()
        elif action == replace_action:
            self.bulk_replace_selected_title()
        elif action == ignore_action:
            self.ignore_selected()

    def edit_first_selected_title(self) -> None:
        selected = self.selected_changes()
        if not selected:
            return
        c = selected[0]
        value, ok = QInputDialog.getText(self, APP_TITLE, "Nouveau titre proposé:", text=c.new_title)
        if ok and clean_spaces(value):
            c.new_title = clean_spaces(value)
            c.new_title_sort = c.new_title
            self.refresh_table()

    def choose_proposal_for_first_selected(self) -> None:
        selected = self.selected_changes()
        if not selected:
            return
        c = selected[0]
        options = unique_nonempty(c.options + [c.new_title, c.old_title])
        if not options:
            return
        value, ok = QInputDialog.getItem(self, APP_TITLE, "Choisir le titre proposé:", options, 0, False)
        if ok and clean_spaces(value):
            c.new_title = clean_spaces(value)
            c.new_title_sort = c.new_title
            self.refresh_table()

    def bulk_replace_selected_title(self) -> None:
        selected = self.selected_changes()
        if not selected:
            return
        default = selected[0].new_title
        value, ok = QInputDialog.getText(self, APP_TITLE, f"Nouveau titre pour {len(selected)} ligne(s):", text=default)
        if ok and clean_spaces(value):
            new_title = clean_spaces(value)
            for c in selected:
                c.new_title = new_title
                c.new_title_sort = new_title
            self.refresh_table()

    def ignore_selected(self) -> None:
        selected = self.selected_changes()
        if not selected:
            return
        ids = {c.series_id for c in selected}
        self.changes = [c for c in self.changes if c.series_id not in ids]
        self.refresh_table()
        self.summary.setText(f"{len(ids)} ligne(s) retirée(s).")

    # ---------- Table: sort title maintenance ----------

    def sort_row_values(self, c: SortTitleMismatch) -> List[str]:
        return [
            c.title,
            c.title_sort,
            c.proposed_title_sort,
            c.folder_name,
            c.notes,
            c.series_id,
        ]

    def refresh_sort_table(self) -> None:
        text = normalize_for_compare(self.filter_text.text())
        self.visible_sort_mismatches = []
        for c in self.sort_mismatches:
            # Une ligne déjà strictement alignée n'est pas affichée.
            if same_sort_title(c.title, c.title_sort):
                continue
            haystack = normalize_for_compare(" ".join([
                c.title, c.title_sort, c.proposed_title_sort, c.folder_name, c.notes, c.series_id,
            ]))
            if text and text not in haystack:
                continue
            self.visible_sort_mismatches.append(c)

        self.sort_table.blockSignals(True)
        self.sort_table.setRowCount(len(self.visible_sort_mismatches))
        for row, c in enumerate(self.visible_sort_mismatches):
            values = self.sort_row_values(c)
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, c.series_id)
                if SORT_COLUMNS[col][0] != "proposed_title_sort":
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.sort_table.setItem(row, col, item)
        self.sort_table.blockSignals(False)

    def selected_sort_rows(self) -> List[int]:
        return sorted({idx.row() for idx in self.sort_table.selectedIndexes()})

    def selected_sort_mismatches(self) -> List[SortTitleMismatch]:
        rows = self.selected_sort_rows()
        return [self.visible_sort_mismatches[r] for r in rows if 0 <= r < len(self.visible_sort_mismatches)]

    def sync_edited_sort_titles_from_table(self) -> None:
        sort_col = self.sort_column_index("proposed_title_sort")
        for row, c in enumerate(self.visible_sort_mismatches):
            item = self.sort_table.item(row, sort_col)
            if item is not None:
                c.proposed_title_sort = clean_spaces(item.text())

    def sort_column_index(self, key: str) -> int:
        for idx, (col_key, _h, _w) in enumerate(SORT_COLUMNS):
            if col_key == key:
                return idx
        raise KeyError(key)

    def on_sort_item_changed(self, item: QTableWidgetItem) -> None:
        if SORT_COLUMNS[item.column()][0] != "proposed_title_sort":
            return
        row = item.row()
        if 0 <= row < len(self.visible_sort_mismatches):
            self.visible_sort_mismatches[row].proposed_title_sort = clean_spaces(item.text())

    def on_sort_table_context_menu(self, pos) -> None:
        selected = self.selected_sort_mismatches()
        menu = QMenu(self)
        apply_action = menu.addAction("Appliquer le tri sélectionné")
        edit_action = menu.addAction("Modifier le tri proposé…")
        use_title_action = menu.addAction("Remettre le tri = titre")
        ignore_action = menu.addAction("Retirer/ignorer la sélection")
        action = menu.exec(self.sort_table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == apply_action:
            self.on_apply_sort_selected()
        elif action == edit_action:
            self.edit_first_selected_sort_title()
        elif action == use_title_action:
            for c in selected:
                c.proposed_title_sort = c.title
            self.refresh_sort_table()
        elif action == ignore_action:
            self.ignore_selected_sort_mismatches()

    def edit_first_selected_sort_title(self) -> None:
        selected = self.selected_sort_mismatches()
        if not selected:
            return
        c = selected[0]
        value, ok = QInputDialog.getText(self, APP_TITLE, "Nouveau titre de tri proposé:", text=c.proposed_title_sort)
        if ok and clean_spaces(value):
            c.proposed_title_sort = clean_spaces(value)
            self.refresh_sort_table()

    def ignore_selected_sort_mismatches(self) -> None:
        selected = self.selected_sort_mismatches()
        if not selected:
            return
        ids = {c.series_id for c in selected}
        self.sort_mismatches = [c for c in self.sort_mismatches if c.series_id not in ids]
        self.refresh_sort_table()
        self.summary.setText(f"{len(ids)} ligne(s) de tri retirée(s).")

def main() -> int:
    setup_crash_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
