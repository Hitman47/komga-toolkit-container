from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


ALLOWED_ALTERNATE_TITLE_LANGUAGES = ("fr", "en", "ja")

_LANGUAGE_ALIASES = {
    "fr": "fr", "fra": "fr", "fre": "fr", "french": "fr",
    "francais": "fr", "francaise": "fr", "vf": "fr",
    "en": "en", "eng": "en", "english": "en", "anglais": "en",
    "anglaise": "en",
    "ja": "ja", "jp": "ja", "jpn": "ja", "jap": "ja",
    "japanese": "ja", "japonais": "ja", "japonaise": "ja",
    "ko": "ko", "kor": "ko", "korean": "ko", "coreen": "ko",
    "coreenne": "ko",
    "ru": "ru", "rus": "ru", "russian": "ru", "russe": "ru",
    "it": "it", "ita": "it", "italian": "it", "italien": "it",
    "italienne": "it",
    "es": "es", "spa": "es", "spanish": "es", "espagnol": "es",
    "de": "de", "deu": "de", "ger": "de", "german": "de", "allemand": "de",
    "pt": "pt", "por": "pt", "portuguese": "pt", "portugais": "pt",
    "zh": "zh", "zho": "zh", "chi": "zh", "cn": "zh", "chinese": "zh",
    "chinois": "zh",
    "ar": "ar", "ara": "ar", "arabic": "ar", "arabe": "ar",
    "he": "he", "heb": "he", "hebrew": "he", "hebreu": "he",
    "th": "th", "tha": "th", "thai": "th",
}

_GENERIC_LABEL_TOKENS = {
    "", "alt", "alternate", "alternative", "alternatif", "alternatifs",
    "title", "titre", "original", "originale", "translated", "traduit",
    "traduite", "romanized", "romanise", "romanisee", "vo",
}

_JAPANESE_ROMANIZATION_LABELS = {
    "romaji", "romanji", "romanized", "romanised", "romanise", "romanisee",
}
_NATIVE_LABELS = {"native", "natif", "native title", "titre natif", "vo"}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _tokens(value: Any) -> List[str]:
    return re.findall(r"[a-z0-9]+", _fold(value))


def _title_key(value: Any) -> str:
    return re.sub(r"[\W_]+", " ", _fold(value), flags=re.UNICODE).strip()


def canonical_alternate_title_language(label: Any) -> str:
    """Return a canonical language when a label contains an explicit language."""
    tokens = _tokens(label)
    for token in tokens:
        if token in _LANGUAGE_ALIASES:
            return _LANGUAGE_ALIASES[token]
    compact = "".join(tokens)
    return _LANGUAGE_ALIASES.get(compact, "")


def title_script_language(title: Any) -> str:
    """Detect only scripts that are reliable enough for safe cleanup."""
    text = str(title or "")
    if any("\u3040" <= char <= "\u30ff" for char in text):
        return "ja"
    if any("\uac00" <= char <= "\ud7af" for char in text):
        return "ko"
    if any("\u0400" <= char <= "\u052f" for char in text):
        return "ru"
    if any("\u0600" <= char <= "\u06ff" for char in text):
        return "ar"
    if any("\u0590" <= char <= "\u05ff" for char in text):
        return "he"
    if any("\u0e00" <= char <= "\u0e7f" for char in text):
        return "th"
    # Han characters alone are intentionally ambiguous (Chinese or Japanese).
    return ""


def _contains_han(title: Any) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in str(title or ""))


def alternate_title_entries(value: Any) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []

    def add(label: Any, title: Any) -> None:
        text = str(title or "").strip()
        if not text:
            return
        entries.append({"label": str(label or "").strip() or "alt", "title": text})

    if isinstance(value, dict):
        add(value.get("label") or value.get("language") or value.get("lang"), value.get("title") or value.get("name") or value.get("value"))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            entries.extend(alternate_title_entries(item))
    elif isinstance(value, str):
        for item in re.split(r"[;\n]", value):
            add("alt", item)
    elif value not in (None, ""):
        add("alt", value)
    return entries


def classify_alternate_title(label: Any, title: Any, *, language_hint: str = "") -> Dict[str, str]:
    explicit = canonical_alternate_title_language(label)
    script = title_script_language(title)
    hint = canonical_alternate_title_language(language_hint)
    folded_label = _fold(label).strip()
    label_tokens = set(_tokens(label))
    semantic_language = ""
    semantic_reason = ""
    if label_tokens & _JAPANESE_ROMANIZATION_LABELS:
        semantic_language = "ja"
        semantic_reason = "Label Romaji : romanisation japonaise"
    elif folded_label in _NATIVE_LABELS and _contains_han(title):
        # In this manga-oriented workflow a Han-only Native title is treated as
        # Japanese; explicit language labels and non-Japanese scripts still win.
        semantic_language = "ja"
        semantic_reason = "Titre Native en idéogrammes dans le contexte manga"
    language = explicit or script or semantic_language or hint
    if language in ALLOWED_ALTERNATE_TITLE_LANGUAGES:
        return {
            "status": "keep",
            "language": language,
            "reason": semantic_reason or f"Langue autorisée : {language.upper()}",
        }
    if language:
        return {
            "status": "remove",
            "language": language,
            "reason": f"Langue non autorisée : {language.upper()}",
        }
    return {
        "status": "review",
        "language": "",
        "reason": "Langue indéterminée — validation manuelle requise",
    }


def filter_incoming_alternate_titles(
    value: Any,
    *,
    primary_title: str = "",
    language_hint: str = "",
) -> List[Dict[str, str]]:
    """Keep only incoming FR/EN/JA entries; unknown entries are not imported."""
    output: List[Dict[str, str]] = []
    seen: set[str] = {_title_key(primary_title)} if primary_title else set()
    for entry in alternate_title_entries(value):
        title = entry["title"]
        key = _title_key(title)
        if not key or key in seen:
            continue
        classification = classify_alternate_title(
            entry.get("label"),
            title,
            language_hint=language_hint,
        )
        if classification["status"] != "keep":
            continue
        seen.add(key)
        output.append({"label": classification["language"], "title": title})
    return output


def sanitize_incoming_series_metadata(
    metadata: Dict[str, Any],
    *,
    primary_title: str = "",
    language_hint: str = "",
) -> Dict[str, Any]:
    sanitized = dict(metadata or {})
    if "alternateTitles" not in sanitized:
        return sanitized
    entries = filter_incoming_alternate_titles(
        sanitized.get("alternateTitles"),
        primary_title=primary_title or str(sanitized.get("title") or ""),
        language_hint=language_hint,
    )
    if entries:
        sanitized["alternateTitles"] = entries
    else:
        sanitized.pop("alternateTitles", None)
    return sanitized


def merge_incoming_alternate_titles(
    current: Any,
    incoming: Any,
    *,
    primary_title: str = "",
) -> List[Dict[str, str]]:
    """Preserve existing values and append only valid FR/EN/JA source values."""
    output = alternate_title_entries(current)
    seen = {_title_key(primary_title)} if primary_title else set()
    seen.update(_title_key(row.get("title")) for row in output)
    for row in filter_incoming_alternate_titles(incoming, primary_title=primary_title):
        key = _title_key(row.get("title"))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def analyze_alternate_titles(
    value: Any,
    *,
    primary_title: str = "",
    locked: bool = False,
    overrides: Dict[int, str] | None = None,
) -> Dict[str, Any]:
    current = alternate_title_entries(value)
    proposed: List[Dict[str, str]] = []
    findings: List[Dict[str, Any]] = []
    seen: set[str] = {_title_key(primary_title)} if primary_title else set()
    overrides = dict(overrides or {})

    for index, entry in enumerate(current):
        title = entry["title"]
        key = _title_key(title)
        classification = classify_alternate_title(entry.get("label"), title)
        action = classification["status"]
        reason = classification["reason"]
        normalized_label = entry.get("label") or "alt"

        if key and key in seen:
            action = "remove"
            reason = "Doublon ou titre identique au titre principal"
        elif key:
            seen.add(key)

        override = str(overrides.get(index) or "")
        if override == "remove":
            action = "remove"
            reason = "Décision manuelle : retirer"
        elif override.startswith("keep_") and override[5:] in ALLOWED_ALTERNATE_TITLE_LANGUAGES:
            action = "keep"
            normalized_label = override[5:]
            reason = f"Décision manuelle : conserver en {normalized_label.upper()}"
        elif override == "keep":
            action = "keep"
            reason = "Décision manuelle : conserver"

        if action == "keep":
            proposed.append({"label": normalized_label, "title": title})
        elif action == "review":
            # Ambiguous legacy entries remain untouched by default.
            proposed.append({"label": entry.get("label") or "alt", "title": title})

        findings.append({
            "index": index,
            "label": entry.get("label") or "alt",
            "title": title,
            "language": classification["language"],
            "action": action,
            "reason": reason,
            "override": override,
        })

    removed = [row for row in findings if row["action"] == "remove"]
    review = [row for row in findings if row["action"] == "review"]
    kept = [row for row in findings if row["action"] == "keep"]
    return {
        "current": current,
        "proposed": proposed,
        "findings": findings,
        "kept": kept,
        "removed": removed,
        "review": review,
        "current_count": len(current),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "review_count": len(review),
        "changed": current != proposed,
        "locked": bool(locked),
        "overrides": overrides,
    }


def analyze_series_entity_alternate_titles(
    entity: Dict[str, Any],
    *,
    fallback_title: str = "",
    overrides: Dict[int, str] | None = None,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Recompute cleanup from a live Komga series using its metadata title.

    Komga's top-level ``name`` can be a technical or filesystem-derived name.
    The cleanup scan uses the series metadata title, so the final safety check
    must use that same title or duplicate decisions can disappear before write.
    """
    metadata = (
        dict(entity.get("metadata") or {})
        if isinstance(entity.get("metadata"), dict)
        else dict(entity)
    )
    primary_title = str(
        metadata.get("title")
        or entity.get("title")
        or entity.get("name")
        or fallback_title
        or ""
    )
    analysis = analyze_alternate_titles(
        metadata.get("alternateTitles"),
        primary_title=primary_title,
        locked=bool(metadata.get("alternateTitlesLock")),
        overrides=overrides,
    )
    return metadata, primary_title, analysis


def _series_added_at(series: Any) -> str:
    raw = getattr(series, "raw", {}) or {}
    value = raw.get("created") or raw.get("createdDate") or raw.get("createdAt")
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return text


def scan_series_alternate_titles(series_rows: Iterable[Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for series in series_rows:
        metadata = dict(getattr(series, "metadata", {}) or {})
        analysis = analyze_alternate_titles(
            metadata.get("alternateTitles"),
            primary_title=str(getattr(series, "title", "") or metadata.get("title") or ""),
            locked=bool(metadata.get("alternateTitlesLock")),
        )
        if not analysis["current_count"]:
            continue
        rows.append({
            "series_id": str(getattr(series, "id", "") or ""),
            "library_id": str(getattr(series, "library_id", "") or ""),
            "title": str(getattr(series, "title", "") or metadata.get("title") or ""),
            "added_at": _series_added_at(series),
            **analysis,
        })
    return rows
