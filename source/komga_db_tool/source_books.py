from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .bedetheque import normalize_volume_number, title_similarity


@dataclass
class SourceBookRow:
    id: str
    number: str = ""
    title: str = ""
    url: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Any = None


def _title_tokens(value: Any) -> List[str]:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    folded = "".join(char for char in normalized if not unicodedata.combining(char)).casefold()
    return re.findall(r"[a-z0-9]+", folded)


def volume_number_from_title(title: Any, series_title: Any = "") -> str:
    """Return a volume number only when the title expresses it unambiguously.

    A trailing number is trusted when the preceding tokens are exactly the series
    title (``Kakushigoto 12``), or when it follows an explicit volume marker. This
    deliberately ignores arbitrary numbers in subtitles.
    """
    title_tokens = _title_tokens(title)
    if not title_tokens:
        return ""

    series_tokens = _title_tokens(series_title)
    remaining = title_tokens
    if series_tokens and title_tokens[: len(series_tokens)] == series_tokens:
        remaining = title_tokens[len(series_tokens) :]

    markers = {"t", "tome", "vol", "volume", "livre", "book", "album", "n", "no", "numero", "num"}
    number = ""
    if len(remaining) == 1 and remaining[0].isdigit():
        number = remaining[0]
    elif len(remaining) == 2 and remaining[0] in markers and remaining[1].isdigit():
        number = remaining[1]
    elif len(title_tokens) == 2 and title_tokens[0] in markers and title_tokens[1].isdigit():
        number = title_tokens[1]
    return normalize_volume_number(number)


def resolved_book_volume_number(number: Any, title: Any = "", series_title: Any = "") -> str:
    """Prefer an explicit number embedded in the book title over Komga's sort number."""
    title_number = volume_number_from_title(title, series_title)
    if title_number:
        return title_number
    normalized = normalize_volume_number(number)
    if set(_title_tokens(normalized)) in ({"one", "shot"}, {"oneshot"}):
        return ""
    return normalized


def match_source_books(
    komga_books: List[Any],
    source_books: List[SourceBookRow],
    *,
    series_title: str = "",
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    used_source_indexes: set[int] = set()
    for book_index, book in enumerate(komga_books):
        book_number = resolved_book_volume_number(
            getattr(book, "number", "") or (getattr(book, "metadata", {}) or {}).get("number", ""),
            getattr(book, "title", "") or (getattr(book, "metadata", {}) or {}).get("title", ""),
            series_title,
        )
        book_title = getattr(book, "title", "") or (getattr(book, "metadata", {}) or {}).get("title", "")
        best_source_index = -1
        best_score = 0.0
        best_reason = "Non matché"
        exact_title_indexes: List[int] = []
        for source_index, source in enumerate(source_books):
            source_number = normalize_volume_number(source.number or source.metadata.get("number", ""))
            source_title = source.title or source.metadata.get("title", "")
            score = 0.0
            reason = ""
            if book_number and source_number and book_number == source_number:
                score = 1.0
                reason = "Exact numéro"
            else:
                sim = title_similarity(book_title, source_title)
                if not book_number and sim >= 0.999:
                    score = 0.99
                    reason = "Exact titre"
                    exact_title_indexes.append(source_index)
                elif not book_number and len(komga_books) == 1 and len(source_books) == 1:
                    score = 0.98
                    reason = "Exact one-shot unique"
                elif sim >= 0.88:
                    score = sim
                    reason = "Titre proche"
                elif sim >= 0.72:
                    score = sim * 0.8
                    reason = "Ambigu"
            if score > best_score:
                best_score = score
                best_source_index = source_index
                best_reason = reason
        if best_reason == "Exact titre" and len(exact_title_indexes) > 1:
            best_reason = "Ambigu"
        if best_source_index in used_source_indexes and best_reason == "Exact numéro":
            best_reason = "Ambigu"
        if best_source_index >= 0:
            used_source_indexes.add(best_source_index)
        matches.append(
            {
                "book_index": book_index,
                "source_index": best_source_index,
                "confidence": best_reason,
                "score": round(best_score, 3),
                "book_number_norm": book_number,
            }
        )
    for source_index, _source in enumerate(source_books):
        if source_index not in used_source_indexes:
            matches.append(
                {
                    "book_index": -1,
                    "source_index": source_index,
                    "confidence": "Source non associée",
                    "score": 0.0,
                    "book_number_norm": "",
                }
            )
    return matches
