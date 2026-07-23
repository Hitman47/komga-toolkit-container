from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .api import clean_dict, parse_cell_value

DIRECTOR_COLUMNS = [
    "type", "operation", "id", "library_id", "series_id", "book_id", "collection_id", "readlist_id",
    "name", "title", "number", "volume", "summary", "publisher", "language", "status", "tags", "genres",
    "links", "authors", "isbn", "release_date", "ordered", "series_ids", "book_ids", "poster_path", "thumbnail_id",
    "payload_json", "note",
]

SPECIALIZED_COLUMNS = {
    "series": ["id", "library_id", "title", "summary", "publisher", "language", "status", "tags", "genres", "links", "payload_json"],
    "books": ["id", "series_id", "title", "number", "volume", "summary", "language", "isbn", "release_date", "tags", "links", "authors", "payload_json"],
    "collections": ["id", "name", "ordered", "series_ids", "payload_json"],
    "readlists": ["id", "name", "ordered", "book_ids", "payload_json"],
    "posters": ["type", "id", "poster_path", "thumbnail_id"],
    "comicinfo_director": [
        "RelativePath", "FileName", "FileType", "WritableMetadata", "LanguageISO", "Continuity", "Era",
        "PrimaryTheme", "SecondaryThemes", "Collection", "Readlist", "ReadlistType", "ReadlistOrder",
        "OrderMode", "ReadlistWriteField", "CheckStatus", "Confidence", "Note",
    ],
}

COMICINFO_DIRECTOR_COLUMNS = SPECIALIZED_COLUMNS["comicinfo_director"]
COMICINFO_TECHNICAL_FILENAMES = {"folder.pdf", "folder.jpg", "cover.jpg", "cover.png", "listing.py", "arborescence.csv"}
COMICINFO_TECHNICAL_EXTENSIONS = {".txt", ".nfo", ".url", ".ini"}

BOOK_INVENTORY_COLUMNS = [
    "book_id",
    "series_id",
    "library_id",
    "library_name",
    "series_title",
    "book_title",
    "book_number",
    "book_url_or_path",
    "book_size_bytes",
    "book_file_hash",
    "book_file_last_modified",
    "book_metadata_tags",
]


@dataclass
class CsvAction:
    target_type: str
    operation: str
    target_id: str
    payload: Dict[str, Any]
    raw: Dict[str, str]


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        return [dict(row) for row in csv.DictReader(f, dialect=dialect)]


def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fieldnames})


def _csv_value(value: Any) -> str:
    if value is None:
        return "<NULL>"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def book_inventory_row(book: Any, library_name: str = "") -> Dict[str, Any]:
    raw = book.raw if isinstance(getattr(book, "raw", None), dict) else {}
    metadata = book.metadata if isinstance(getattr(book, "metadata", None), dict) else {}
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        tags = []
    return {
        "book_id": getattr(book, "id", ""),
        "series_id": getattr(book, "series_id", ""),
        "library_id": getattr(book, "library_id", ""),
        "library_name": library_name,
        "series_title": raw.get("seriesTitle", ""),
        "book_title": getattr(book, "title", "") or metadata.get("title", ""),
        "book_number": getattr(book, "number", "") or metadata.get("number", ""),
        "book_url_or_path": raw.get("url") or raw.get("path") or raw.get("filePath") or "",
        "book_size_bytes": raw.get("sizeBytes", ""),
        "book_file_hash": raw.get("fileHash", ""),
        "book_file_last_modified": raw.get("fileLastModified", ""),
        "book_metadata_tags": " | ".join(str(tag) for tag in tags if str(tag).strip()),
    }


def parse_director_actions(rows: Iterable[Dict[str, str]]) -> List[CsvAction]:
    actions: List[CsvAction] = []
    for row in rows:
        target_type = (row.get("type") or row.get("target_type") or "").strip().lower()
        operation = (row.get("operation") or "update").strip().lower()
        if (row.get("poster_path") or row.get("thumbnail_id")) and target_type in {"series", "book", "collection", "readlist"}:
            target_id = (row.get("id") or row.get(f"{target_type}_id") or "").strip()
            payload = payload_from_row(row)
            payload["target_type"] = target_type
            if not row.get("operation"):
                operation = "upload" if row.get("poster_path") else "select"
            actions.append(CsvAction(target_type="poster", operation=operation, target_id=target_id, payload=payload, raw=dict(row)))
            continue
        if not target_type:
            target_type = infer_specialized_target_type(row)
        if target_type == "poster":
            poster_target_type, target_id = poster_target_from_row(row)
            payload = payload_from_row(row)
            if poster_target_type:
                payload["target_type"] = poster_target_type
            if not row.get("operation"):
                operation = "upload" if row.get("poster_path") else "select"
            actions.append(CsvAction(target_type="poster", operation=operation, target_id=target_id, payload=payload, raw=dict(row)))
            continue
        target_id = (row.get("id") or row.get(f"{target_type}_id") or row.get("series_id") or row.get("book_id") or "").strip()
        payload = payload_from_row(row)
        actions.append(CsvAction(target_type=target_type, operation=operation, target_id=target_id, payload=payload, raw=dict(row)))
    return actions


def infer_specialized_target_type(row: Dict[str, str]) -> str:
    keys = {_norm_header(key) for key in row.keys()}
    if {"poster_path", "thumbnail_id"} & keys:
        return "poster"
    if "book_ids" in keys:
        return "readlist"
    if "series_ids" in keys:
        return "collection"
    if "series_id" in keys and ("number" in keys or "isbn" in keys or "authors" in keys):
        return "book"
    if "library_id" in keys and "title" in keys:
        return "series"
    return ""


def poster_target_from_row(row: Dict[str, str]) -> tuple[str, str]:
    explicit = (row.get("type") or row.get("target_type") or "").strip().lower()
    if explicit in {"series", "book", "collection", "readlist"}:
        return explicit, (row.get("id") or row.get(f"{explicit}_id") or "").strip()
    for target_type, key in (("series", "series_id"), ("book", "book_id"), ("collection", "collection_id"), ("readlist", "readlist_id")):
        value = (row.get(key) or "").strip()
        if value:
            return target_type, value
    return "", (row.get("id") or "").strip()


def payload_from_row(row: Dict[str, str]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    payload_json = (row.get("payload_json") or "").strip()
    if payload_json:
        try:
            parsed = json.loads(payload_json)
            if isinstance(parsed, dict):
                payload.update(parsed)
        except json.JSONDecodeError:
            payload["_payload_json_error"] = payload_json
    for key, value in row.items():
        if key in {"type", "target_type", "operation", "id", "library_id", "series_id", "book_id", "collection_id", "readlist_id", "payload_json", "note"}:
            continue
        parsed_value = parse_cell_value(value)
        if parsed_value != "":
            payload[key] = parsed_value
    return clean_dict(payload)


def comicinfo_director_summary(rows: Iterable[Dict[str, str]]) -> Dict[str, Any]:
    reports = [validate_comicinfo_director_row(row, index) for index, row in enumerate(rows, start=2)]
    counts: Dict[str, int] = {"rows": 0, "writable": 0, "api_only": 0, "skipped": 0, "blocked": 0, "errors": 0}
    for report in reports:
        counts["rows"] += 1
        status = report["status"]
        if status in counts:
            counts[status] += 1
        if status == "error":
            counts["errors"] += 1
    return {"kind": "comicinfo_director", "counts": counts, "reports": reports}


def looks_like_comicinfo_director(rows: Sequence[Dict[str, str]]) -> bool:
    if not rows:
        return False
    headers = set(rows[0].keys())
    normalized = {_norm_header(header) for header in headers}
    return bool({"relativepath", "collection", "readlist"} & normalized) and bool(
        {"writablemetadata", "checkstatus", "readlistorder", "readlistwritefield"} & normalized
    )


def validate_comicinfo_director_row(row: Dict[str, str], row_index: int = 0) -> Dict[str, Any]:
    relative_path = _row_value(row, "RelativePath", "relative_path", "path", "file", "filename", "fichier", "chemin")
    file_type = _status_value(_row_value(row, "FileType", "file_type"))
    writable = _status_value(_row_value(row, "WritableMetadata", "writable_metadata"))
    check_status = _status_value(_row_value(row, "CheckStatus", "check_status"))
    confidence = _status_value(_row_value(row, "Confidence"))
    collection = _row_value(row, "Collection", "Collections", "SeriesGroup")
    readlist = _row_value(row, "Readlist", "StoryArc", "AlternateSeries")
    order = _row_value(row, "ReadlistOrder", "StoryArcNumber", "AlternateNumber")
    write_field = _row_value(row, "ReadlistWriteField", "readlist_write_field") or "StoryArc"
    messages: List[str] = []

    file_name = Path(relative_path.replace("\\", "/")).name.casefold() if relative_path else ""
    suffix = Path(file_name).suffix.casefold()
    if not relative_path:
        messages.append("RelativePath vide.")
    if file_name in COMICINFO_TECHNICAL_FILENAMES or suffix in COMICINFO_TECHNICAL_EXTENSIONS:
        return _comicinfo_report(row_index, relative_path, "skipped", "Fichier technique ignoré.", row, write_field)
    if writable == "apionly":
        return _comicinfo_report(row_index, relative_path, "api_only", "WritableMetadata=api_only : traitement API Komga, pas ComicInfo.xml.", row, write_field)
    if file_type and file_type != "cbz":
        return _comicinfo_report(row_index, relative_path, "skipped", f"FileType={file_type} : écriture ComicInfo réservée aux CBZ.", row, write_field)
    if writable in {"skip", "no", "non", "false", "0"}:
        return _comicinfo_report(row_index, relative_path, "skipped", f"WritableMetadata={writable} : écriture ignorée.", row, write_field)
    if writable and writable != "yes":
        messages.append(f"WritableMetadata={writable} invalide.")
    if check_status and check_status != "ok":
        messages.append(f"CheckStatus={check_status} : écriture bloquée.")
    if confidence in {"low", "faible", "basse"}:
        messages.append("Confidence=low : écriture bloquée.")
    if _header_exists(row, "Collection", "Collections", "SeriesGroup") and not collection:
        messages.append("Collection vide pour un CBZ modifiable.")
    if readlist and not order:
        messages.append("Readlist remplie mais ReadlistOrder vide.")
    if order and not readlist:
        messages.append("ReadlistOrder rempli mais Readlist vide.")
    if readlist and order:
        readlists = split_multi_value(readlist)
        orders = split_multi_value(order)
        if len(readlists) != len(orders):
            messages.append(f"Nombre de Readlist ({len(readlists)}) différent du nombre de ReadlistOrder ({len(orders)}).")
    if _status_value(write_field) not in {"storyarc", "alternateseries", "none", ""}:
        messages.append(f"ReadlistWriteField invalide : {write_field}.")
    if messages:
        return _comicinfo_report(row_index, relative_path, "blocked" if relative_path else "error", " ".join(messages), row, write_field)
    return _comicinfo_report(row_index, relative_path, "writable", "OK : ligne compatible avec le tuto ComicInfo.", row, write_field)


def comicinfo_payload_preview(row: Dict[str, str]) -> Dict[str, str]:
    payload: Dict[str, str] = {}
    collection = _row_value(row, "Collection", "Collections", "SeriesGroup")
    language = _row_value(row, "LanguageISO", "Language", "Langue")
    readlist = _row_value(row, "Readlist", "StoryArc", "AlternateSeries")
    order = _row_value(row, "ReadlistOrder", "StoryArcNumber", "AlternateNumber")
    write_field = _status_value(_row_value(row, "ReadlistWriteField") or "StoryArc")
    if collection:
        payload["SeriesGroup"] = join_multi_value(collection)
    if language and _status_value(language) not in {"unknown", "notapplicable", "none"}:
        payload["LanguageISO"] = language.upper() if language.lower() in {"fr", "en"} else language
    if readlist:
        if write_field == "alternateseries":
            payload["AlternateSeries"] = join_multi_value(readlist)
            if order:
                payload["AlternateNumber"] = join_multi_value(order)
        elif write_field not in {"none", ""}:
            payload["StoryArc"] = join_multi_value(readlist)
            if order:
                payload["StoryArcNumber"] = join_multi_value(order)
    return payload


def split_multi_value(value: str) -> List[str]:
    raw_parts = str(value or "").split(";") if ";" in str(value or "") else str(value or "").split(",")
    seen: set[str] = set()
    out: List[str] = []
    for part in raw_parts:
        clean = re.sub(r"\s+", " ", str(part or "").strip())
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def join_multi_value(value: str) -> str:
    return "; ".join(split_multi_value(value))


def _comicinfo_report(row_index: int, relative_path: str, status: str, message: str, row: Dict[str, str], write_field: str) -> Dict[str, Any]:
    return {
        "row": row_index,
        "status": status,
        "relative_path": relative_path,
        "file_type": _row_value(row, "FileType", "file_type"),
        "writable": _row_value(row, "WritableMetadata", "writable_metadata"),
        "check_status": _row_value(row, "CheckStatus", "check_status"),
        "confidence": _row_value(row, "Confidence"),
        "readlist_write_field": write_field,
        "payload_preview": comicinfo_payload_preview(row),
        "message": message,
    }


def _row_value(row: Dict[str, str], *aliases: str) -> str:
    lookup = {_norm_header(key): key for key in row.keys()}
    for alias in aliases:
        key = lookup.get(_norm_header(alias))
        if key is not None:
            return str(row.get(key) or "").strip()
    return ""


def _header_exists(row: Dict[str, str], *aliases: str) -> bool:
    lookup = {_norm_header(key) for key in row.keys()}
    return any(_norm_header(alias) in lookup for alias in aliases)


def _status_value(value: str) -> str:
    return _norm_header(value).replace("_", "")


def _norm_header(text: Any) -> str:
    text = str(text or "").strip().lstrip("\ufeff")
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")
