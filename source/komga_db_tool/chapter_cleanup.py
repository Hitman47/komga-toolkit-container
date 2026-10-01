from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Iterable
from urllib.parse import unquote


_LANGUAGE_MARKERS = {
    "EN": "en",
    "ENG": "en",
    "FR": "fr",
    "FRA": "fr",
    "VF": "fr",
    "JA": "ja",
    "JP": "ja",
    "JAP": "ja",
    "JPN": "ja",
}
_REMOVABLE_MARKERS = set(_LANGUAGE_MARKERS) | {"INT", "VO", "RAW"}
_PLAIN_MARKER_RE = re.compile(
    r"^\s*(?:\d+\s+)?(" + "|".join(sorted(_REMOVABLE_MARKERS, key=len, reverse=True)) + r")\s*$",
    re.IGNORECASE,
)
_SUFFIX_MARKER_RE = re.compile(
    r"\s*\((" + "|".join(sorted(_REMOVABLE_MARKERS, key=len, reverse=True)) + r")\)\s*$",
    re.IGNORECASE,
)
_UNIVERSE_RE = re.compile(r"\(\s*univers(?:e)?\s*\)\s*$", re.IGNORECASE)
_ORDER_PREFIX_RE = re.compile(r"^\s*\d+\s+(?=\D)")
_CHAPTER_TITLE_SUFFIX_RE = re.compile(r"\s*\(\s*chap\s*\)\s*$", re.IGNORECASE)


def has_chapter_title_suffix(value: Any) -> bool:
    """Return whether a metadata title is protected by the ``(Chap)`` marker."""
    return bool(_CHAPTER_TITLE_SUFFIX_RE.search(_text(value)))


def ensure_chapter_title_suffix(value: Any) -> Any:
    """Append one canonical ``(Chap)`` suffix to a non-empty title."""
    title = _text(value)
    if not title:
        return value
    base = _text(_CHAPTER_TITLE_SUFFIX_RE.sub("", title))
    return f"{base} (Chap)" if base else "(Chap)"


def preserve_chapter_series_title_suffix(
    current: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preserve ``(Chap)`` in title fields proposed by an external enrichment.

    The guard does not invent title changes for payloads which only enrich other
    fields. When a new title is proposed without a titleSort, both are aligned so
    a provider cannot silently remove the marker from either Komga field.
    """
    prepared = dict(candidate or {})
    current_metadata = current or {}
    protected = has_chapter_title_suffix(current_metadata.get("title")) or has_chapter_title_suffix(
        current_metadata.get("titleSort")
    )
    if not protected:
        return prepared

    title = prepared.get("title")
    if title is not None and _text(title):
        prepared["title"] = ensure_chapter_title_suffix(title)
        if not _text(prepared.get("titleSort")):
            prepared["titleSort"] = prepared["title"]

    title_sort = prepared.get("titleSort")
    if title_sort is not None and _text(title_sort):
        prepared["titleSort"] = ensure_chapter_title_suffix(title_sort)
    return prepared


def is_series_enrichment_source(source: Any) -> bool:
    """Identify metadata sources that represent external series enrichment."""
    value = _text(source).casefold().replace("-", "_")
    if not value:
        return False
    excluded = (
        "manual_metadata",
        "metadata_editor",
        "metadata_webui",
        "explorer_typed_editor",
        "csv",
        "rollback",
        "cleanup_",
        "series_fix",
        "release_tracking",
        "summary_from_first_book",
    )
    if value.startswith(excluded):
        return False
    return any(
        marker in value
        for marker in (
            "bedetheque",
            "mangabaka",
            "manga_news",
            "manganews",
            "comicvine",
            "metron",
            "nautiljon",
            "komf",
            "auto_match",
            "update_with_link",
            "enrichment",
        )
    )


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _raw(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    value = getattr(item, "raw", {})
    return value if isinstance(value, dict) else {}


def _metadata(item: Any) -> dict[str, Any]:
    raw = _raw(item)
    value = raw.get("metadata")
    if not isinstance(value, dict):
        value = getattr(item, "metadata", {})
    return value if isinstance(value, dict) else {}


def _item_id(item: Any) -> str:
    return _text(_raw(item).get("id") or getattr(item, "id", ""))


def _path_parts(raw: dict[str, Any]) -> list[str]:
    value = raw.get("url") or raw.get("path") or raw.get("folderName") or ""
    normalized = unquote(str(value)).replace("\\", "/").strip("/")
    return [part.strip() for part in normalized.split("/") if part.strip()]


def _marker(value: str) -> tuple[str, str] | None:
    match = _PLAIN_MARKER_RE.fullmatch(value or "")
    if not match:
        return None
    token = match.group(1).upper()
    return token, _LANGUAGE_MARKERS.get(token, "")


def _merge_language(current: str, detected: str) -> tuple[str, bool]:
    if not detected:
        return current, False
    if not current:
        return detected, False
    return current, current != detected


def normalize_language(value: Any) -> str:
    token = _text(value).upper()
    return _LANGUAGE_MARKERS.get(token, token.casefold())


def chapter_series_proposal(item: Any) -> dict[str, Any] | None:
    """Return the deterministic metadata proposal for a physical ``Chap`` series.

    Detection deliberately uses Komga's physical series name and URL, not its
    mutable metadata title. This keeps the operation idempotent after a rename.
    """
    raw = _raw(item)
    if _text(raw.get("name")).casefold() != "chap":
        return None
    parts = _path_parts(raw)
    if len(parts) < 2 or parts[-1].casefold() != "chap":
        return None

    parent_index = len(parts) - 2
    detected_language = ""
    direct_marker = _marker(parts[parent_index])
    if direct_marker:
        detected_language = direct_marker[1]
        parent_index -= 1
    if parent_index < 0:
        return None

    base = _text(parts[parent_index])
    while True:
        suffix = _SUFFIX_MARKER_RE.search(base)
        if not suffix:
            break
        token = suffix.group(1).upper()
        detected_language, _ = _merge_language(detected_language, _LANGUAGE_MARKERS.get(token, ""))
        base = _text(base[: suffix.start()])

    # ``1 EN/Series/Chap`` carries language in the organizational parent.
    if parent_index > 0:
        organizational_marker = _marker(parts[parent_index - 1])
        if organizational_marker:
            detected_language, _ = _merge_language(detected_language, organizational_marker[1])

    # Numbered entries under an explicit universe are ordering prefixes, not
    # part of the title. Decimal titles such as "2.5 Dimensional Seduction"
    # never match this rule.
    if parent_index > 0 and _UNIVERSE_RE.search(parts[parent_index - 1]):
        base = _text(_ORDER_PREFIX_RE.sub("", base))

    if not base or _marker(base):
        return None
    return {
        "base_title": base,
        "proposed_title": f"{base} (Chap)",
        "detected_language": detected_language,
        "path": "/" + "/".join(parts),
    }


def analyze_chapter_series_item(item: Any) -> dict[str, Any] | None:
    proposal = chapter_series_proposal(item)
    if proposal is None:
        return None
    raw = _raw(item)
    metadata = _metadata(item)
    current_title = _text(metadata.get("title") or raw.get("name") or getattr(item, "title", ""))
    current_sort = _text(metadata.get("titleSort"))
    current_language = normalize_language(metadata.get("language"))
    detected_language = proposal["detected_language"]
    payload: dict[str, Any] = {}
    if current_title != proposal["proposed_title"]:
        payload["title"] = proposal["proposed_title"]
    if current_sort != proposal["proposed_title"]:
        payload["titleSort"] = proposal["proposed_title"]
    language_conflict = bool(detected_language and current_language and current_language != detected_language)
    if detected_language and current_language != detected_language and not language_conflict:
        payload["language"] = detected_language

    locked_fields = []
    if "title" in payload and bool(metadata.get("titleLock")):
        locked_fields.append("title")
    if "titleSort" in payload and bool(metadata.get("titleSortLock")):
        locked_fields.append("titleSort")
    if "language" in payload and bool(metadata.get("languageLock")):
        locked_fields.append("language")

    status = "ready"
    reason = "Titre et titre de tri seront harmonisés"
    if locked_fields:
        status = "locked"
        reason = "Champ(s) verrouillé(s) : " + ", ".join(locked_fields)
    elif language_conflict:
        status = "review"
        reason = f"Conflit de langue : {current_language} actuellement, {detected_language} détecté"
    elif not payload:
        status = "unchanged"
        reason = "Déjà conforme"

    return {
        "series_id": _item_id(item),
        "library_id": _text(raw.get("libraryId") or getattr(item, "library_id", "")),
        "current_title": current_title,
        "current_title_sort": current_sort,
        "current_language": current_language,
        "proposed_title": proposal["proposed_title"],
        "proposed_language": detected_language or current_language,
        "detected_language": detected_language,
        "base_title": proposal["base_title"],
        "path": proposal["path"],
        "payload": payload,
        "changed": bool(payload),
        "status": status,
        "reason": reason,
        "locked_fields": locked_fields,
        "added_at": _text(metadata.get("created") or raw.get("createdDate")),
    }


def scan_chapter_series(items: Iterable[Any]) -> list[dict[str, Any]]:
    source = list(items)
    rows = [row for item in source if (row := analyze_chapter_series_item(item)) is not None]
    target_counts = Counter(row["proposed_title"].casefold() for row in rows)
    candidate_ids = {row["series_id"] for row in rows}
    title_owners: dict[str, set[str]] = defaultdict(set)
    for item in source:
        metadata = _metadata(item)
        title = _text(metadata.get("title") or _raw(item).get("name") or getattr(item, "title", ""))
        if title:
            title_owners[title.casefold()].add(_item_id(item))

    for row in rows:
        if row["status"] in {"locked", "unchanged"}:
            continue
        target = row["proposed_title"].casefold()
        external_owners = title_owners[target] - candidate_ids
        if target_counts[target] > 1:
            row["status"] = "duplicate"
            row["reason"] = "Plusieurs dossiers Chap produisent le même titre"
        elif external_owners:
            row["status"] = "collision"
            row["reason"] = "Une autre série Komga porte déjà ce titre"
    return sorted(rows, key=lambda row: (row["proposed_title"].casefold(), row["path"].casefold()))
