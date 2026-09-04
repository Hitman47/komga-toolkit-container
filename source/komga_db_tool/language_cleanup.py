from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable, Mapping


CANONICAL_LANGUAGES = {"fr", "en", "ja", "de", "es", "it", "pt", "nl"}
CANONICAL_SOURCES = ("MangaBaka", "ComicVine", "Metron", "Bedetheque", "Nautiljon", "Manga News")
_CHAPTER_SUFFIX_RE = re.compile(r"\s*\(\s*chap(?:itre)?\s*\)\s*$", re.IGNORECASE)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode("ascii")
    return text.casefold().replace("_", "-")


def normalize_language(value: Any) -> str:
    text = _fold(value)
    if not text:
        return ""
    aliases = {
        "fra": "fr", "fre": "fr", "french": "fr", "francais": "fr",
        "eng": "en", "english": "en", "anglais": "en",
        "jp": "ja", "jpn": "ja", "japanese": "ja", "japonais": "ja",
        "deu": "de", "ger": "de", "german": "de", "allemand": "de",
        "spa": "es", "spanish": "es", "espagnol": "es",
        "ita": "it", "italian": "it", "italien": "it",
        "por": "pt", "portuguese": "pt", "portugais": "pt",
        "dut": "nl", "nld": "nl", "dutch": "nl", "neerlandais": "nl",
    }
    text = aliases.get(text, text)
    primary = text.split("-", 1)[0]
    return primary if primary in CANONICAL_LANGUAGES else ""


def _raw(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    raw = getattr(item, "raw", {})
    return raw if isinstance(raw, dict) else {}


def _metadata(item: Any) -> dict[str, Any]:
    raw = _raw(item)
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        metadata = getattr(item, "metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _id(item: Any) -> str:
    return _text(_raw(item).get("id") or getattr(item, "id", ""))


def _title(item: Any) -> str:
    metadata = _metadata(item)
    return _text(metadata.get("title") or _raw(item).get("name") or getattr(item, "title", ""))


def _series_id(item: Any) -> str:
    raw = _raw(item)
    series = raw.get("series") if isinstance(raw.get("series"), dict) else {}
    return _text(raw.get("seriesId") or series.get("id") or getattr(item, "series_id", ""))


def _link_entries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            out.append({"label": "", "url": item})
        elif isinstance(item, dict):
            out.append({"label": _text(item.get("label")), "url": _text(item.get("url"))})
    return out


def is_chapter_series_title(value: Any) -> bool:
    return bool(_CHAPTER_SUFFIX_RE.search(_text(value)))


def _canonical_link_source(label: Any, url: Any) -> str:
    folded_label = _fold(label).replace("-", " ")
    folded_url = _text(url).casefold()
    compact_label = folded_label.replace(" ", "")
    if "mangabaka" in folded_url or "mangabaka" in compact_label:
        return "MangaBaka"
    if "comicvine" in folded_url or "gamespot.com" in folded_url or "comicvine" in compact_label:
        return "ComicVine"
    if "metron.cloud" in folded_url or compact_label == "metron":
        return "Metron"
    if "bedetheque" in folded_url or "bedetheque" in compact_label:
        return "Bedetheque"
    if "nautiljon" in folded_url or "nautiljon" in compact_label:
        return "Nautiljon"
    if "manga-news" in folded_url or "manga news" in folded_label or "manganews" in compact_label:
        return "Manga News"
    return ""


def _linked_sources(metadata: Mapping[str, Any]) -> list[str]:
    found = {
        source
        for link in _link_entries(metadata.get("links"))
        if (source := _canonical_link_source(link.get("label"), link.get("url")))
    }
    return [source for source in CANONICAL_SOURCES if source in found]


def _source_evidence(metadata: Mapping[str, Any], bedetheque_languages: Mapping[str, str]) -> list[tuple[str, str, str]]:
    evidence: list[tuple[str, str, str]] = []
    for link in _link_entries(metadata.get("links")):
        label = _fold(link.get("label"))
        url = _text(link.get("url"))
        folded_url = url.casefold()
        bdt_language = bedetheque_languages.get(url.rstrip("/").casefold(), "")
        if bdt_language:
            evidence.append((bdt_language, "Bedetheque CSV", "high"))
        elif "manga-news" in folded_url or "manga news" in label or "manga-news" in label:
            evidence.append(("fr", "Manga News", "medium"))
        elif "mangabaka" in folded_url or "mangabaka" in label.replace(" ", ""):
            evidence.append(("en", "MangaBaka", "medium"))
        elif "comicvine" in folded_url or "gamespot.com" in folded_url or "comicvine" in label.replace(" ", ""):
            evidence.append(("en", "ComicVine", "medium"))
    unique: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in evidence:
        key = (row[0], row[1])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def _proposal(
    current_raw: Any,
    evidence: list[tuple[str, str, str]],
    *,
    inherited: tuple[str, str] | None = None,
) -> tuple[str, str, str, str]:
    current_text = _text(current_raw)
    current = normalize_language(current_text)
    if current and current_text.casefold() != current:
        return current, "safe", "Normalisation", f"Code de langue normalisé : {current_text} → {current}"

    high = [row for row in evidence if row[2] == "high"]
    candidates = high or evidence
    languages = sorted({row[0] for row in candidates if row[0]})
    sources = ", ".join(row[1] for row in candidates)
    if not current and len(languages) == 1:
        confidence = "safe" if high else "likely"
        return languages[0], confidence, sources, f"Langue déterminée par {sources}"
    if not current and not languages and inherited and inherited[0]:
        return inherited[0], "likely", inherited[1], f"Langue héritée de {inherited[1]}"
    if not current and len(languages) > 1:
        return "", "review", sources, "Sources en conflit : " + ", ".join(languages)
    if current and languages and any(language != current for language in languages):
        return current, "review", sources, f"Langue actuelle {current} en conflit avec {', '.join(languages)}"
    return current, "unchanged", sources, "Langue déjà conforme" if current else "Aucune preuve de langue suffisante"


def analyze_language_item(
    item: Any,
    *,
    target_type: str,
    bedetheque_languages: Mapping[str, str] | None = None,
    inherited: tuple[str, str] | None = None,
    inherited_sources: Iterable[str] = (),
    series_title: str = "",
) -> dict[str, Any]:
    metadata = _metadata(item)
    current_raw = metadata.get("language")
    current = normalize_language(current_raw) or _text(current_raw).casefold()
    evidence = _source_evidence(metadata, bedetheque_languages or {})
    linked_source_set = set(_linked_sources(metadata)) | {str(value) for value in inherited_sources if value}
    linked_sources = [source for source in CANONICAL_SOURCES if source in linked_source_set]
    proposed, confidence, source, reason = _proposal(current_raw, evidence, inherited=inherited)
    locked = bool(metadata.get("languageLock"))
    changed = bool(proposed and proposed != current)
    if locked and changed:
        confidence = "locked"
        reason = "Champ language verrouillé"
    return {
        "target_type": target_type,
        "target_id": _id(item),
        "series_id": _id(item) if target_type == "series" else _series_id(item),
        "title": _title(item),
        "series_title": series_title or (_title(item) if target_type == "series" else ""),
        "current_language": current,
        "current_raw": _text(current_raw),
        "proposed_language": proposed,
        "source": source,
        "linked_sources": linked_sources,
        "confidence": confidence,
        "changed": changed,
        "locked": locked,
        "reason": reason,
    }


def scan_language_cleanup(
    series_items: Iterable[Any],
    book_items: Iterable[Any] = (),
    *,
    bedetheque_languages: Mapping[str, str] | None = None,
    include_series: bool = True,
    include_books: bool = True,
) -> list[dict[str, Any]]:
    series = list(series_items)
    books = list(book_items)
    series_rows: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for item in series:
        row = analyze_language_item(
            item,
            target_type="series",
            bedetheque_languages=bedetheque_languages,
        )
        series_rows[row["series_id"]] = row
        if include_series:
            rows.append(row)

    series_titles = {_id(item): _title(item) for item in series}
    if include_books:
        for item in books:
            parent = series_rows.get(_series_id(item), {})
            inherited_language = _text(parent.get("proposed_language") or parent.get("current_language"))
            inherited = (inherited_language, f"la série {parent.get('title')}") if inherited_language else None
            rows.append(analyze_language_item(
                item,
                target_type="book",
                bedetheque_languages=bedetheque_languages,
                inherited=inherited,
                inherited_sources=parent.get("linked_sources") or [],
                series_title=series_titles.get(_series_id(item), ""),
            ))
    order = {"safe": 0, "likely": 1, "review": 2, "locked": 3, "unchanged": 4}
    return sorted(rows, key=lambda row: (order.get(row["confidence"], 9), row["series_title"].casefold(), row["title"].casefold()))
