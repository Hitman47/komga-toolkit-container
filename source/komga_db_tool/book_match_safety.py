"""Evidence required before a Manga News volume can be selected automatically."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .bedetheque import normalize_volume_number
from .manga_news import series_slug_from_manga_news_url


def _words(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _source_series_title(value: Any) -> str:
    text = re.sub(r"^\s*(?:vol(?:ume)?|tome|t)\s*[.°º#:-]?\s*\d+(?:[.,]\d+)?\s*[-:–—.]?\s*", "", str(value or ""), flags=re.I)
    return _words(text)


def _base_slug(value: Any) -> str:
    return re.sub(r"(?: edition)? (?:perfect|deluxe|collector|integrale|originale)$", "", _words(value))


def _alternate_titles(series: Any) -> list[str]:
    metadata = series.get("metadata", {}) if isinstance(series, dict) else getattr(series, "metadata", {})
    rows = metadata.get("alternateTitles", []) if isinstance(metadata, dict) else []
    return [str(row.get("title") or "") if isinstance(row, dict) else str(row) for row in rows if row]


def assess_manga_news_book_match(row: dict[str, Any], candidate: Any, requested_number: str, requested_slug: str) -> tuple[str, float, str]:
    """Return (confidence, score, explanation); empty confidence blocks a wrong source."""
    actual_number = normalize_volume_number(getattr(candidate, "number", ""))
    if requested_number and actual_number != requested_number:
        return "", 0.0, f"Numéro source différent : {actual_number or 'absent'} au lieu de {requested_number}."

    source_title = _source_series_title(getattr(candidate, "title", ""))
    titles = [_words(row.get("series_title"))]
    titles.extend(_words(title) for title in _alternate_titles(row.get("series")))
    titles = [title for title in titles if title]
    scores = []
    for title in titles:
        if not source_title:
            break
        if title == source_title:
            scores.append(1.0)
        elif len(title) >= 6 and (source_title.startswith(title + " ") or title.startswith(source_title + " ")):
            scores.append(0.95)
        else:
            scores.append(SequenceMatcher(None, title, source_title).ratio())
    score = max(scores, default=0.0)

    source_slug = series_slug_from_manga_news_url(getattr(candidate, "source_url", "")) or str(getattr(candidate, "series_slug", "") or "")
    slug_conflict = bool(requested_slug and source_slug and _base_slug(requested_slug) != _base_slug(source_slug))
    if slug_conflict:
        return "", score, f"Le tome source appartient à « {source_slug} », lien de série « {requested_slug} »."
    if score >= 0.85:
        return "high", score, ""
    if score < 0.45 and source_title and titles:
        return "", score, f"Titre de série incompatible : tome source « {getattr(candidate, 'title', '')} »."
    return "ambiguous", score, "Titre de série insuffisamment confirmé : vérifier le tome source."
