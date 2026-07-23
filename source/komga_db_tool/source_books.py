from __future__ import annotations

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


def match_source_books(komga_books: List[Any], source_books: List[SourceBookRow]) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    used_source_indexes: set[int] = set()
    for book_index, book in enumerate(komga_books):
        book_number = normalize_volume_number(
            getattr(book, "number", "") or (getattr(book, "metadata", {}) or {}).get("number", "")
        )
        book_title = getattr(book, "title", "") or (getattr(book, "metadata", {}) or {}).get("title", "")
        best_source_index = -1
        best_score = 0.0
        best_reason = "Non matché"
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
                if sim >= 0.88:
                    score = sim
                    reason = "Titre proche"
                elif sim >= 0.72:
                    score = sim * 0.8
                    reason = "Ambigu"
            if score > best_score:
                best_score = score
                best_source_index = source_index
                best_reason = reason
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
