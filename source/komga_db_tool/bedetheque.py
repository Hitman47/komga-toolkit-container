from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class BedethequeSearchResult:
    kind: str
    title: str
    url: str
    source: str = "bedetheque_csv"


@dataclass
class BedethequeCandidate:
    source_url: str
    series_title: str = ""
    album_title: str = ""
    album_number: str = ""
    album_url: str = ""
    cover_url: str = ""
    series_metadata: Dict[str, Any] = field(default_factory=dict)
    book_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def normalize_volume_number(value: Any) -> str:
    """Normalize Komga/Bedetheque numbering for local matching."""
    text = _fold(str(value or "")).strip()
    text = re.sub(
        r"\b(?:tome|vol(?:ume)?|livre|book|album|n[°o]?|numero|num|#)\b",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not text:
        return ""
    parts = text.split()
    for part in parts:
        if part.isdigit():
            return str(int(part))
    if text.isdigit():
        return str(int(text))
    return text


def title_similarity(left: str, right: str) -> float:
    def variants(value: str) -> List[str]:
        raw = _fold(value).strip()
        rows = [raw]
        # Manga News can append bibliographic data such as
        # "(2006) DOUMAN Seiman / DOWMAN Sayman" to the actual title.
        without_bibliography = re.sub(
            r"\s*[\(\[\{]\s*(?:18|19|20)\d{2}(?:\s*[-/–—]\s*(?:18|19|20)?\d{2})?\s*[\)\]\}](?:\s+.*)?$",
            "",
            raw,
        ).strip()
        if without_bibliography and without_bibliography != raw:
            rows.append(without_bibliography)
        normalized: List[str] = []
        for row in rows:
            text = re.sub(r"[^a-z0-9]+", " ", row).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    left_variants = variants(left)
    right_variants = variants(right)
    if not left_variants or not right_variants:
        return 0.0
    if set(left_variants).intersection(right_variants):
        return 1.0
    return max(
        difflib.SequenceMatcher(None, left_value, right_value).ratio()
        for left_value in left_variants
        for right_value in right_variants
    )


def match_album_rows(
    komga_books: List[Any],
    bedetheque_albums: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Match supplied local album rows to Komga books without network access."""
    matches: List[Dict[str, Any]] = []
    used_album_indexes: set[int] = set()
    for book_index, book in enumerate(komga_books):
        book_number = normalize_volume_number(
            getattr(book, "number", "")
            or (getattr(book, "metadata", {}) or {}).get("number", "")
        )
        book_title = (
            getattr(book, "title", "")
            or (getattr(book, "metadata", {}) or {}).get("title", "")
        )

        best_album_index = -1
        best_score = 0.0
        best_reason = "Non matché"

        for album_index, album in enumerate(bedetheque_albums):
            album_number = normalize_volume_number(album.get("number", ""))
            album_title = album.get("title", "")
            score = 0.0
            reason = ""
            if book_number and album_number and book_number == album_number:
                score = 1.0
                reason = "Exact numéro"
            else:
                similarity = title_similarity(book_title, album_title)
                if similarity >= 0.88:
                    score = similarity
                    reason = "Titre proche"
                elif similarity >= 0.72:
                    score = similarity * 0.8
                    reason = "Ambigu"
            if score > best_score:
                best_score = score
                best_album_index = album_index
                best_reason = reason

        if best_album_index in used_album_indexes and best_reason == "Exact numéro":
            best_reason = "Ambigu"
        if best_album_index >= 0:
            used_album_indexes.add(best_album_index)
        matches.append(
            {
                "book_index": book_index,
                "album_index": best_album_index,
                "confidence": best_reason,
                "score": round(best_score, 3),
                "book_number_norm": book_number,
            }
        )

    for album_index, _album in enumerate(bedetheque_albums):
        if album_index not in used_album_indexes:
            matches.append(
                {
                    "book_index": -1,
                    "album_index": album_index,
                    "confidence": "Album Bedetheque non associé",
                    "score": 0.0,
                    "book_number_norm": "",
                }
            )
    return matches


def candidate_to_dict(candidate: BedethequeCandidate) -> Dict[str, Any]:
    return {
        "source_url": candidate.source_url,
        "series_title": candidate.series_title,
        "album_title": candidate.album_title,
        "album_number": candidate.album_number,
        "album_url": candidate.album_url,
        "cover_url": candidate.cover_url,
        "series_metadata": candidate.series_metadata,
        "book_metadata": candidate.book_metadata,
        "raw": candidate.raw,
    }
