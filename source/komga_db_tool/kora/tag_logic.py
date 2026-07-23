from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

from .constants import (
    KORA_GENRE_LABELS,
    KORA_GENRE_PREFIX,
    KORA_GENRES,
    KORA_TAG_PREFIX,
    KORA_TAXONOMY_PREFIX,
    MAX_KORA_GENRES,
)


def normalize_slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.strip().lower()
    text = text.replace("&", " et ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    aliases = {
        "comedie": "comedie",
        "comedy": "comedie",
        "mystere": "mystere",
        "mystery": "mystere",
        "scifi": "science-fiction",
        "sci-fi": "science-fiction",
        "science-fiction": "science-fiction",
        "sciencefiction": "science-fiction",
        "superheros": "super-heros",
        "super-heroes": "super-heros",
        "super-hero": "super-heros",
        "superhero": "super-heros",
        "sport-art-martiaux": "sport-arts-martiaux",
        "sports-arts-martiaux": "sport-arts-martiaux",
        "arts-martiaux": "sport-arts-martiaux",
        "martial-arts": "sport-arts-martiaux",
        "documentaire-biographie": "documentaire-biographie",
        "documentaire-bio": "documentaire-biographie",
        "espion": "espionnage",
        "espions": "espionnage",
        "spy": "espionnage",
        "spies": "espionnage",
        "fantastique-surnaturel": "fantastique-surnaturel",
        "policier-crime": "policier-crime",
        "guerre-militaire": "guerre-militaire",
        "tranche-de-vie": "tranche-de-vie",
    }
    return aliases.get(text, text)


def genre_label(slug: str) -> str:
    return KORA_GENRE_LABELS.get(slug, slug)


def is_allowed_genre(slug: str) -> bool:
    return normalize_slug(slug) in KORA_GENRES


def build_kora_genre_tag(slug: str) -> str:
    normalized = normalize_slug(slug)
    if normalized not in KORA_GENRES:
        raise ValueError(f"Genre Kora non autorisé: {slug!r}")
    return f"{KORA_GENRE_PREFIX}{normalized}"


def _coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple) or isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    # Les exports précédents utilisent surtout " | "; certains fichiers utilisent ";".
    if "|" in text:
        parts = text.split("|")
    elif ";" in text:
        parts = text.split(";")
    elif "," in text and text.count(",") > 0:
        parts = text.split(",")
    else:
        parts = [text]
    return [p.strip() for p in parts if p.strip()]


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def extract_kora_genres(tags: Iterable[str]) -> list[str]:
    genres: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        lower = text.lower()
        if lower.startswith(KORA_GENRE_PREFIX):
            slug = normalize_slug(text[len(KORA_GENRE_PREFIX):])
            if slug in KORA_GENRES:
                genres.append(slug)
    return unique_preserve_order(genres)


def extract_kora_tags(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        lower = text.lower()
        if lower.startswith(KORA_TAG_PREFIX):
            out.append(text[len(KORA_TAG_PREFIX):])
    return unique_preserve_order(out)


def split_kora_and_other_tags(tags: Iterable[str]) -> tuple[list[str], list[str], list[str]]:
    """Return (kora_genre_tags, kora_secondary_tags, non_kora_tags).

    Any kora:taxonomy tag is intentionally dropped.
    """
    kora_genre_tags: list[str] = []
    kora_secondary_tags: list[str] = []
    non_kora: list[str] = []
    for tag in unique_preserve_order(str(x).strip() for x in tags):
        lower = tag.lower()
        if lower.startswith(KORA_TAXONOMY_PREFIX):
            continue
        if lower.startswith(KORA_GENRE_PREFIX):
            # Normalize valid legacy casing to the canonical value.
            slug = normalize_slug(tag[len(KORA_GENRE_PREFIX):])
            if slug in KORA_GENRES:
                kora_genre_tags.append(build_kora_genre_tag(slug))
            continue
        if lower.startswith(KORA_TAG_PREFIX):
            kora_secondary_tags.append(tag)
            continue
        non_kora.append(tag)
    return unique_preserve_order(kora_genre_tags), unique_preserve_order(kora_secondary_tags), unique_preserve_order(non_kora)


def validate_genres(genres: Iterable[str], max_genres: int = MAX_KORA_GENRES) -> list[str]:
    normalized = unique_preserve_order(normalize_slug(g) for g in genres)
    invalid = [g for g in normalized if g not in KORA_GENRES]
    if invalid:
        raise ValueError("Genre(s) Kora non autorisé(s): " + ", ".join(invalid))
    if len(normalized) > max_genres:
        raise ValueError(f"Maximum {max_genres} genres Kora par série, reçu {len(normalized)}")
    return normalized


def merge_series_tags_for_genres(current_tags: Iterable[str], selected_genres: Iterable[str]) -> list[str]:
    selected = validate_genres(selected_genres)
    _old_kora_genres, kora_secondary, non_kora = split_kora_and_other_tags(current_tags)
    new_genre_tags = [build_kora_genre_tag(g) for g in selected]
    return unique_preserve_order([*non_kora, *kora_secondary, *new_genre_tags])


def parse_genres_from_csv_cell(value: Any) -> list[str]:
    tags_or_values = _coerce_list(value)
    genres: list[str] = []
    for item in tags_or_values:
        lower = item.lower().strip()
        if lower.startswith(KORA_GENRE_PREFIX):
            item = item[len(KORA_GENRE_PREFIX):]
        slug = normalize_slug(item)
        if slug in KORA_GENRES:
            genres.append(slug)
    return validate_genres(genres)


def readable_genres(genres: Iterable[str]) -> str:
    return " | ".join(genre_label(g) for g in genres)
