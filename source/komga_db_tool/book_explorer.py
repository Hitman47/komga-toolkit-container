from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .bedetheque import title_similarity
from .metadata_quality import (
    is_low_value_summary,
    is_supported_write_language,
    normalize_write_language,
)

BOOK_SOURCE_PRIORITY = ("manga_news", "bedetheque", "comicvine")
BOOK_SOURCE_LABELS = {
    "manga_news": "Manga News",
    "bedetheque": "Bedetheque",
    "comicvine": "ComicVine",
    "mangabaka": "MangaBaka",
}
BOOK_SOURCE_CAPABILITIES = {
    "manga_news": {
        "title",
        "titleSort",
        "summary",
        "releaseDate",
        "publisher",
        "language",
        "isbn",
        "authors",
        "numberOfPages",
        "tags",
        "links",
    },
    "bedetheque": {
        "title",
        "titleSort",
        "summary",
        "releaseDate",
        "publisher",
        "language",
        "isbn",
        "authors",
        "numberOfPages",
        "tags",
        "links",
        "ageRating",
    },
    "comicvine": {
        "title",
        "titleSort",
        "summary",
        "releaseDate",
        "publisher",
        "authors",
        "tags",
        "links",
    },
    # MangaBaka is intentionally visible as a mapped series source, but its
    # current client does not expose volume-level metadata.
    "mangabaka": set(),
}
DEFAULT_BOOK_ENRICHMENT_FIELDS = (
    "title",
    "titleSort",
    "summary",
    "releaseDate",
    "publisher",
    "language",
    "isbn",
    "authors",
    "numberOfPages",
)
BOOK_ENRICHMENT_EXTRA_FIELDS = ("tags", "links", "ageRating")
BOOK_ENRICHMENT_PROTECTED_FIELDS = {"number", "numberSort", "isbn"}


@dataclass(frozen=True)
class SourceChoice:
    source: str = ""
    url: str = ""
    available: tuple[str, ...] = ()
    reason: str = ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().upper() == "<NULL>"
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def _same(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(
            right, ensure_ascii=False, sort_keys=True
        )
    except TypeError:
        return str(left) == str(right)


def _merge_list_values(current: Any, candidate: Any) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    current_values = list(current) if isinstance(current, (list, tuple, set)) else []
    candidate_values = list(candidate) if isinstance(candidate, (list, tuple, set)) else []
    for item in (*current_values, *candidate_values):
        try:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True).casefold()
        except TypeError:
            key = str(item).casefold()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


def book_enrichment_payload(
    current: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
    *,
    include_titles: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Build the conservative tome payload shared by desktop and WebUI.

    Titles remain available in the candidate for an explicit user choice, but
    are deliberately excluded from the default payload.
    """
    current_map = dict(current or {})
    proposed = dict(candidate or {})
    payload: dict[str, Any] = {}
    for field in (*DEFAULT_BOOK_ENRICHMENT_FIELDS, *BOOK_ENRICHMENT_EXTRA_FIELDS):
        if field in BOOK_ENRICHMENT_PROTECTED_FIELDS or field not in proposed:
            continue
        if field in {"title", "titleSort"} and not include_titles:
            continue
        value = proposed.get(field)
        if _blank(value):
            continue
        if field == "summary" and is_low_value_summary(value):
            continue
        if field == "language":
            if not is_supported_write_language(value):
                continue
            value = normalize_write_language(value)
        current_value = current_map.get(field)
        if isinstance(value, (list, tuple, set)):
            value = _merge_list_values(current_value, value)
            if _same(current_value or [], value):
                continue
            payload[field] = value
            continue
        if _same(current_value, value):
            continue
        if field in {"title", "titleSort"} or _blank(current_value):
            payload[field] = value

    current_title = _text(current_map.get("title"))
    proposed_title = _text(proposed.get("title"))
    title_requires_confirmation = bool(
        "title" in payload
        and current_title
        and proposed_title
        and title_similarity(current_title, proposed_title) < 0.93
    )
    return payload, title_requires_confirmation


def _metadata(record: Any) -> dict[str, Any]:
    value = record.get("metadata") if isinstance(record, dict) else getattr(record, "metadata", {})
    return value if isinstance(value, dict) else {}


def _raw(record: Any) -> dict[str, Any]:
    value = record.get("raw") if isinstance(record, dict) else getattr(record, "raw", {})
    return value if isinstance(value, dict) else {}


def _record_value(record: Any, key: str, default: Any = "") -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def normalize_source(value: Any) -> str:
    text = _text(value).casefold()
    text = re.sub(r"[\s_.-]+", "", text)
    aliases = {
        "bedetheque": "bedetheque",
        "bdtheque": "bedetheque",
        "bédéthèque": "bedetheque",
        "manganews": "manga_news",
        "mangabaka": "mangabaka",
        "comicvine": "comicvine",
        "gamespot": "comicvine",
    }
    return aliases.get(text, "")


def source_from_url(url: Any) -> str:
    text = _text(url)
    if not text:
        return ""
    try:
        host = urlparse(text if "://" in text else "https://" + text).netloc.casefold()
    except ValueError:
        return ""
    if "bedetheque" in host:
        return "bedetheque"
    if "manga-news" in host or "manganews" in host:
        return "manga_news"
    if "mangabaka" in host:
        return "mangabaka"
    if "comicvine" in host or "gamespot" in host:
        return "comicvine"
    return ""


def _link_items(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith(("[", "{")):
            try:
                return _link_items(json.loads(text))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return [{"label": "", "url": part.strip()} for part in re.split(r"[;\n]", text) if part.strip()]
    if isinstance(value, Mapping):
        label = next((_text(value.get(key)) for key in ("label", "name", "provider", "source", "site", "type") if _text(value.get(key))), "")
        url = next((_text(value.get(key)) for key in ("url", "href", "link") if _text(value.get(key))), "")
        return [{"label": label, "url": url}] if url else []
    if isinstance(value, (list, tuple, set)):
        rows: list[dict[str, str]] = []
        for item in value:
            rows.extend(_link_items(item))
        return rows
    return []


def series_source_links(series: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in _link_items(_metadata(series).get("links")):
        source = normalize_source(entry.get("label")) or source_from_url(entry.get("url"))
        url = _text(entry.get("url"))
        if source and url and source not in result:
            result[source] = url
    return result


def choose_book_source(
    series: Any,
    requested_source: str = "auto",
    fields: Iterable[str] = DEFAULT_BOOK_ENRICHMENT_FIELDS,
) -> SourceChoice:
    links = series_source_links(series)
    available = tuple(source for source in (*BOOK_SOURCE_PRIORITY, "mangabaka") if source in links)
    requested = normalize_source(requested_source)
    if requested:
        if requested not in links:
            return SourceChoice(
                available=available,
                reason=f"La série ne possède aucun lien {BOOK_SOURCE_LABELS[requested]}.",
            )
        if not BOOK_SOURCE_CAPABILITIES.get(requested):
            return SourceChoice(
                available=available,
                reason=f"{BOOK_SOURCE_LABELS[requested]} ne fournit pas encore de métadonnées par tome.",
            )
        return SourceChoice(requested, links[requested], available, "Source imposée et liée à la série.")

    wanted = set(fields)
    compatible = [source for source in BOOK_SOURCE_PRIORITY if source in links]
    if not compatible:
        if "mangabaka" in links:
            return SourceChoice(
                available=available,
                reason="La série est liée uniquement à MangaBaka, qui ne fournit pas de métadonnées par tome.",
            )
        return SourceChoice(available=available, reason="La série ne possède aucune source compatible pour les tomes.")
    ranked = sorted(
        compatible,
        key=lambda source: (
            -len(BOOK_SOURCE_CAPABILITIES[source] & wanted),
            BOOK_SOURCE_PRIORITY.index(source),
        ),
    )
    source = ranked[0]
    return SourceChoice(source, links[source], available, "Meilleure source compatible déjà liée à la série.")


def parse_komga_added_at(book: Any) -> datetime | None:
    raw = _raw(book)
    value = raw.get("created") or raw.get("createdDate")
    if not value:
        return None
    text = _text(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def book_explorer_row(book: Any, series: Any | None) -> dict[str, Any]:
    metadata = _metadata(book)
    series_metadata = _metadata(series) if series is not None else {}
    raw = _raw(book)
    sources = series_source_links(series) if series is not None else {}
    series_title = (
        _record_value(series, "title")
        if series is not None
        else raw.get("seriesTitle")
        or (raw.get("series") or {}).get("name", "")
    )
    return {
        "book": book,
        "series": series,
        "book_id": _text(_record_value(book, "id")),
        "series_id": _text(_record_value(book, "series_id") or raw.get("seriesId")),
        "library_id": _text(_record_value(book, "library_id") or raw.get("libraryId")),
        "series_title": _text(series_title),
        "series_status": _text(series_metadata.get("status")),
        "title": _text(_record_value(book, "title") or metadata.get("title")),
        "title_sort": _text(metadata.get("titleSort")),
        "number": _text(_record_value(book, "number") or metadata.get("number")),
        "release_date": _text(metadata.get("releaseDate")),
        "language": _text(metadata.get("language")),
        "isbn": _text(metadata.get("isbn")),
        "summary": _text(metadata.get("summary")),
        "publisher": _text(metadata.get("publisher")),
        "number_of_pages": metadata.get("numberOfPages"),
        "authors": metadata.get("authors") or [],
        "added_at": parse_komga_added_at(book),
        "sources": sources,
        "source_names": tuple(sources),
    }


def _fold(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value).casefold()).strip()


def _natural_number(value: Any) -> tuple[Any, ...]:
    parts = re.split(r"(\d+(?:[.,]\d+)?)", _fold(value))
    key: list[Any] = []
    for part in parts:
        if not part:
            continue
        try:
            key.append((0, float(part.replace(",", "."))))
        except ValueError:
            key.append((1, part))
    return tuple(key)


def filter_book_rows(
    rows: Sequence[dict[str, Any]],
    *,
    query: str = "",
    added_since: datetime | None = None,
    language: str = "",
    series_status: str = "",
    source_filter: str = "all",
    missing_field: str = "",
    empty_summary: bool = False,
) -> list[dict[str, Any]]:
    needle = _fold(query)
    language_folded = _fold(language)
    status_folded = _fold(series_status)
    source_filter = _text(source_filter).casefold() or "all"
    missing_field = _text(missing_field)
    out: list[dict[str, Any]] = []
    for row in rows:
        if needle:
            authors = row.get("authors") or []
            if isinstance(authors, list):
                authors_text = " ".join(
                    _text(item.get("name")) if isinstance(item, dict) else _text(item)
                    for item in authors
                )
            else:
                authors_text = _text(authors)
            haystack = _fold(
                " ".join(
                    (
                        _text(row.get("series_title")),
                        _text(row.get("title")),
                        _text(row.get("title_sort")),
                        _text(row.get("number")),
                        _text(row.get("isbn")),
                        _text(row.get("publisher")),
                        authors_text,
                    )
                )
            )
            if not all(token in haystack for token in needle.split()):
                continue
        if added_since is not None:
            added_at = row.get("added_at")
            if not isinstance(added_at, datetime) or added_at < added_since:
                continue
        if language_folded and _fold(row.get("language")) != language_folded:
            continue
        if status_folded and status_folded != "all" and _fold(row.get("series_status")) != status_folded:
            continue
        sources = set(row.get("source_names") or ())
        if source_filter == "with_any" and not sources:
            continue
        if source_filter == "without_any" and sources:
            continue
        if source_filter.startswith("with:") and source_filter[5:] not in sources:
            continue
        if source_filter.startswith("without:") and source_filter[8:] in sources:
            continue
        if empty_summary and _text(row.get("summary")).upper() not in {"", "<NULL>"}:
            continue
        if missing_field:
            value = row.get(missing_field)
            if value not in (None, "", [], (), {}) and _text(value).upper() != "<NULL>":
                continue
        out.append(row)
    return out


def sort_book_rows(
    rows: Sequence[dict[str, Any]],
    field: str = "added_at",
    descending: bool = True,
) -> list[dict[str, Any]]:
    field = _text(field) or "added_at"

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        value = row.get(field)
        if field == "added_at":
            timestamp = value.timestamp() if isinstance(value, datetime) else float("-inf")
            return (timestamp, _fold(row.get("series_title")), _natural_number(row.get("number")))
        if field == "number":
            return (_natural_number(value), _fold(row.get("series_title")), _fold(row.get("title")))
        return (_fold(value), _fold(row.get("series_title")), _natural_number(row.get("number")))

    return sorted(rows, key=key, reverse=bool(descending))
