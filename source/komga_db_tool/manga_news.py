from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib import error, parse, request

APP_USER_AGENT = "komga-db-tool/3.9.11 manga-news-v2-adapter"
DEFAULT_MANGA_NEWS_API_BASE_URL = "http://192.168.1.30:8017"
SEARCH_CACHE_TTL_SECONDS = 2 * 60 * 60
LOOKUP_CACHE_TTL_SECONDS = 12 * 60 * 60
V2_REQUIRED_PATHS = {"/health", "/search", "/search/resolve", "/series/{slug}", "/series/by-url"}
V2_VOLUME_PATHS = {"/volume/{series_slug}/{volume_slug}", "/volume/{series_slug}/number/{number}", "/volume/by-url"}
V2_NEXT_RELEASE_PATHS = {"/series/{slug}/release-state"}
V2_SERIES_FIELDS = (
    "title,title_vo,translated_title,type,summary,authors_story,authors_art,"
    "translators,publisher_fr,publisher_vo,vf,vo,genres,advisory_age,"
    "cover_image,source_url"
)
V2_SEARCH_PARAMETERS = {
    "q",
    "kind",
    "mode",
    "limit",
    "enrich",
    "include_editions",
    "prefer_main_series",
    "include_related",
    "include_books",
    "media_kinds",
    "exclude_media_kinds",
}

STATUS_MAP = {
    "en cours": "ONGOING",
    "ongoing": "ONGOING",
    "en pause": "HIATUS",
    "pause": "HIATUS",
    "hiatus": "HIATUS",
    "termine": "ENDED",
    "terminé": "ENDED",
    "terminee": "ENDED",
    "terminée": "ENDED",
    "fini": "ENDED",
    "finie": "ENDED",
    "completed": "ENDED",
    "complete": "ENDED",
    "abandonné": "ABANDONED",
    "abandonne": "ABANDONED",
    "stoppé": "ABANDONED",
    "stoppe": "ABANDONED",
    "cancelled": "ABANDONED",
    "canceled": "ABANDONED",
}


@dataclass
class MangaNewsSearchResult:
    slug: str
    title: str
    kind: str = ""
    score: int = 0
    url: str = ""
    media_kind: str = ""
    title_vo: str = ""
    translated_title: str = ""
    vf_status: str = ""
    vf_volumes: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MangaNewsCandidate:
    source_url: str
    slug: str = ""
    title: str = ""
    summary: str = ""
    cover_url: str = ""
    series_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MangaNewsVolumeCandidate:
    source_url: str
    series_slug: str = ""
    volume_slug: str = ""
    title: str = ""
    number: str = ""
    cover_url: str = ""
    book_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MangaNewsNextReleaseCandidate:
    source_url: str = ""
    title: str = ""
    number: str = ""
    release_date: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def normalize_api_base_url(url: str) -> str:
    text = (url or DEFAULT_MANGA_NEWS_API_BASE_URL).strip()
    if not text:
        text = DEFAULT_MANGA_NEWS_API_BASE_URL
    if not text.lower().startswith(("http://", "https://")):
        text = "http://" + text
    return text.rstrip("/")


def series_slug_from_manga_news_url(url: str) -> str:
    text = _safe_str(url)
    if not text:
        return ""
    parsed = parse.urlparse(text if "://" in text else "https://" + text)
    host = parsed.netloc.casefold().removeprefix("www.")
    if "manga-news.com" not in host:
        return ""
    parts = [parse.unquote(part) for part in parsed.path.split("/") if part]
    lowered = [part.casefold() for part in parts]
    for marker in ("serie", "manga"):
        if marker in lowered:
            index = lowered.index(marker)
            if index + 1 < len(parts):
                slug = parts[index + 1].strip()
                if slug and not slug.casefold().startswith("vol-"):
                    return slug
    return ""


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _series_data(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _result_items(payload: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            rows = [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            candidates = data.get("candidates")
            if isinstance(candidates, list):
                best = data.get("best")
                if isinstance(best, dict):
                    rows.append(best)
                rows.extend([x for x in candidates if isinstance(x, dict)])
    elif isinstance(payload, list):
        rows = [x for x in payload if isinstance(x, dict)]

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in rows:
        identity = _safe_str(
            item.get("url")
            or item.get("slug")
            or item.get("series_slug")
            or item.get("title")
        ).casefold()
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        deduped.append(item)
    return deduped


def _dedupe_strings(values: List[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _safe_str(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _metadata_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    values: List[Any] = []
    for item in value:
        if isinstance(item, dict):
            values.append(item.get("name") or item.get("title") or item.get("label"))
        else:
            values.append(item)
    return _dedupe_strings(values)


def _edition_status_text(value: Any) -> str:
    if isinstance(value, dict):
        return _safe_str(value.get("status"))
    return ""


def _edition_volumes_text(value: Any) -> str:
    if isinstance(value, dict):
        raw = value.get("volumes")
        if raw is None or raw == "":
            return ""
        return _safe_str(raw)
    return ""


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return None


def _normalize_status(value: Any) -> str:
    text = _safe_str(value).casefold().replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return STATUS_MAP.get(text, "")


def _age_rating(value: Any) -> Optional[int]:
    text = _safe_str(value)
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _make_link(label: str, url: str) -> Dict[str, str]:
    return {"label": label.strip() or "Manga-News", "url": url.strip()}


def _date_value(value: Any) -> str:
    text = _safe_str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ].*", text):
        return text[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text + "-01"
    if re.fullmatch(r"\d{4}", text):
        return text + "-01-01"
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
    if match:
        day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}"
    match = re.fullmatch(r"(\d{4})[./](\d{1,2})[./](\d{1,2})", text)
    if match:
        year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _walk_dicts(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(value, dict):
        rows.append(value)
        for child in value.values():
            rows.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_walk_dicts(child))
    return rows


def _looks_like_next_release_key(key: str) -> bool:
    lowered = key.casefold().replace("-", "_")
    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    return any(part in lowered for part in ("next_release", "next_volume", "upcoming", "prochain", "future_release")) or any(
        part in compact
        for part in (
            "nextrelease",
            "nextvolume",
            "upcomingrelease",
            "futurerelease",
            "prochainesortie",
            "prochaintome",
        )
    )


def _first_value(data: Dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = data.get(key)
        text = _safe_str(value)
        if text:
            return text
    return ""


def _date_from_next_release_data(data: Dict[str, Any]) -> str:
    return _date_value(
        data.get("release_date")
        or data.get("releaseDate")
        or data.get("date")
        or data.get("published_at")
        or data.get("publishedAt")
        or data.get("publication_date")
        or data.get("publicationDate")
        or data.get("planned_date")
        or data.get("plannedDate")
        or data.get("scheduled_date")
        or data.get("scheduledDate")
        or data.get("date_sortie")
        or data.get("dateSortie")
        or data.get("sortie")
    )


def _number_from_next_release_data(data: Dict[str, Any]) -> str:
    number = _first_value(
        data,
        [
            "number",
            "number_int",
            "numberInt",
            "volume_number",
            "volumeNumber",
            "volume_num",
            "numero",
            "num",
            "volume",
            "tome",
            "next_volume",
            "nextVolume",
            "next_volume_number",
            "nextVolumeNumber",
            "book_number",
            "bookNumber",
        ],
    )
    if number:
        return number
    for key in ("volume", "tome", "next_volume", "nextVolume"):
        nested = data.get(key)
        if isinstance(nested, dict):
            number = _first_value(nested, ["number", "number_int", "numberInt", "volume_number", "numero", "num"])
            if number:
                return number
    return ""


def _find_next_release_payload(payload: Any) -> Dict[str, Any]:
    root = _series_data(payload)
    candidates: List[Dict[str, Any]] = []
    if isinstance(root, dict):
        if "next_release" in root:
            next_release = root.get("next_release")
            if isinstance(next_release, dict):
                date = _date_from_next_release_data(next_release)
                number = _number_from_next_release_data(next_release)
                return next_release if date and number else {}
            return {}
        if isinstance(root.get("next_release"), dict):
            candidates.append(root["next_release"])
        for key, value in root.items():
            if _looks_like_next_release_key(str(key)):
                if isinstance(value, dict):
                    candidates.append(value)
                elif isinstance(value, list):
                    candidates.extend(item for item in value if isinstance(item, dict))
        candidates.extend(_walk_dicts(root))
    for candidate in candidates:
        date = _date_from_next_release_data(candidate)
        number = _number_from_next_release_data(candidate)
        if date and number:
            return candidate
    return {}


def candidate_from_next_release(payload: Any) -> MangaNewsNextReleaseCandidate:
    data = _find_next_release_payload(payload)
    if not data:
        return MangaNewsNextReleaseCandidate(raw=payload if isinstance(payload, dict) else {"raw": payload})
    release_date = _date_from_next_release_data(data)
    number = _number_from_next_release_data(data)
    return MangaNewsNextReleaseCandidate(
        source_url=_safe_str(data.get("source_url") or data.get("url")),
        title=_safe_str(data.get("title") or data.get("name")),
        number=number,
        release_date=release_date,
        raw=data,
    )


def candidate_from_series_next_release(payload: Any, slug: str = "") -> MangaNewsNextReleaseCandidate:
    data = _series_data(payload)
    release_date = _date_value(data.get("next_release_date") or data.get("nextReleaseDate"))
    vf = data.get("vf") if isinstance(data.get("vf"), dict) else {}
    volumes = _int_or_none(vf.get("volumes"))
    if not release_date or volumes is None:
        raw = payload if isinstance(payload, dict) else {"raw": payload}
        return MangaNewsNextReleaseCandidate(raw=raw)
    number = str(volumes + 1)
    source_url = _safe_str(data.get("source_url") or (payload.get("source_url") if isinstance(payload, dict) else ""))
    title = _safe_str(data.get("title") or slug)
    raw = dict(payload) if isinstance(payload, dict) else {"raw": payload}
    raw["fallback"] = {
        "source": "series.next_release_date",
        "reason": "release-state sans next_release exploitable",
        "request_slug": slug,
        "inferred_from": "vf.volumes + 1",
        "vf_volumes": volumes,
    }
    return MangaNewsNextReleaseCandidate(
        source_url=source_url,
        title=f"{title} - Tome {number}" if title else f"Tome {number}",
        number=number,
        release_date=release_date,
        raw=raw,
    )


def _not_found_error(exc: Exception) -> bool:
    text = str(exc)
    return "HTTP 404" in text or "HTTPError 404" in text or "absent" in text.casefold()


def _clean_summary(value: Any) -> str:
    """Return a Komga-safe Manga-News synopsis.

    Manga-News pages sometimes expose a broad page text block as `summary`:
    synopsis + editorial strengths + news + comments + volume counters + video
    player boilerplate. Komga summary must keep only the synopsis.
    """
    text = _safe_str(value)
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)

    # Cut at the first known Manga-News page section that follows the real synopsis.
    cut_patterns = [
        r"\bLes\s+points\s+forts\s+(?:de\s+la\s+série|du\s+manga)\s*:",
        r"\bDernières?\s+news\s+du\s+manga\b",
        r"\bDerniers?\s+commentaires?\b",
        r"\bVoir\s+les\s+actualités\s+du\s+manga\b",
        r"\bVotre\s+nom\s*:",
        r"\bEmail\s*\(facultatif\)\s*:",
        r"\bVotre\s+commentaire\s*:",
        r"\bIndiquez\s+la\s+bonne\s+réponse\s+au\s+calcul\s+suivant\b",
        r"\bVoir\s+tous\s+les\s+commentaires\b",
        r"\bLes\s+Volumes\s+VF\s*:",
        r"\bVideo\s+youtube\b",
        r"\bTrailer\s+[^.!?]{0,160}Activer\s+JavaScript\b",
        r"\bActiver\s+JavaScript\b",
        r"\bchoi(?:s|ss)iss?er\s+un\s+navigateur\s+moderne\b",
        r"\bchoisissez\s+un\s+navigateur\s+moderne\b",
    ]
    cut_at = len(text)
    for pattern in cut_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            cut_at = min(cut_at, match.start())

    text = text[:cut_at].strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _alternate_title_label(value: str) -> str:
    text = value or ""
    if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text):
        return "ja"
    if any("\uac00" <= ch <= "\ud7af" for ch in text):
        return "ko"
    if any("\u0400" <= ch <= "\u04ff" for ch in text):
        return "ru"
    return "alt"


def _author_entries(names: Any, role: str) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    for name in names if isinstance(names, list) else [names]:
        text = _safe_str(name)
        if not text:
            continue
        item = {"name": text, "role": role}
        key = (item["name"].casefold(), item["role"].casefold())
        if any((x.get("name", "").casefold(), x.get("role", "").casefold()) == key for x in entries):
            continue
        entries.append(item)
    return entries


def _alternate_title_entries(data: Dict[str, Any]) -> List[Dict[str, str]]:
    title = _safe_str(data.get("title"))
    candidates = [data.get("title_vo"), data.get("translated_title")]
    out: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in candidates:
        text = _safe_str(raw)
        if not text or (title and text.casefold() == title.casefold()):
            continue
        label = _alternate_title_label(text)
        key = (label.casefold(), text.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append({"label": label, "title": text})
    return out


def _map_series_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map Manga-News series data to Komga-compatible metadata.

    The source can expose many useful fields, but it must never inject noisy
    page sections such as themes, strengths, raw sections or news into Komga
    tags/genres. Those were observed to pollute curated Kora tags.
    """
    metadata: Dict[str, Any] = {}

    title = _safe_str(data.get("title"))
    if title:
        metadata["title"] = title
        metadata["titleSort"] = title

    summary = _clean_summary(data.get("summary") or data.get("synopsis") or data.get("description"))
    if summary:
        metadata["summary"] = summary

    publisher = _safe_str(data.get("publisher_fr") or data.get("publisher") or data.get("publisher_vo"))
    if publisher:
        metadata["publisher"] = publisher

    status = _normalize_status((data.get("vf") or {}).get("status") if isinstance(data.get("vf"), dict) else "")
    if not status:
        status = _normalize_status((data.get("vo") or {}).get("status") if isinstance(data.get("vo"), dict) else "")
    if status:
        metadata["status"] = status

    total = _int_or_none((data.get("vf") or {}).get("volumes") if isinstance(data.get("vf"), dict) else None)
    if total is None:
        total = _int_or_none((data.get("vo") or {}).get("volumes") if isinstance(data.get("vo"), dict) else None)
    if total is not None:
        metadata["totalBookCount"] = total

    age = _age_rating(data.get("advisory_age"))
    if age is not None:
        metadata["ageRating"] = age

    alternate_titles = _alternate_title_entries(data)
    if alternate_titles:
        metadata["alternateTitles"] = alternate_titles

    authors: List[Dict[str, str]] = []
    authors.extend(_author_entries(data.get("authors_story"), "writer"))
    authors.extend(_author_entries(data.get("authors_art"), "penciller"))
    authors.extend(_author_entries(data.get("translators"), "translator"))
    if authors:
        metadata["authors"] = authors

    genres = _metadata_string_list(data.get("genres"))
    if genres:
        metadata["genres"] = genres

    source_url = _safe_str(data.get("source_url"))
    if source_url:
        metadata["links"] = [_make_link("Manga-News", source_url)]

    return metadata


def _map_volume_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    title = _safe_str(data.get("title") or data.get("name"))
    if title:
        metadata["title"] = title
        metadata["titleSort"] = title
    number = _safe_str(data.get("number") or data.get("volume_number") or data.get("volume") or data.get("tome"))
    if number:
        metadata["number"] = number
        metadata["numberSort"] = number
    summary = _clean_summary(data.get("summary") or data.get("synopsis") or data.get("description"))
    if summary:
        metadata["summary"] = summary
    release_date = _date_value(data.get("release_date") or data.get("date") or data.get("published_at") or data.get("publication_date"))
    if release_date:
        metadata["releaseDate"] = release_date
    isbn = _safe_str(data.get("isbn") or data.get("ean"))
    if isbn:
        metadata["isbn"] = isbn
    publisher = _safe_str(data.get("publisher_fr") or data.get("publisher") or data.get("publisher_vo"))
    if publisher:
        metadata["publisher"] = publisher
    page_values = (
        data.get("numberOfPages"),
        data.get("number_of_pages"),
        data.get("page_count"),
        data.get("pages"),
        data.get("nb_pages"),
        data.get("illustration"),
        data.get("illustration_details"),
    )
    for page_value in page_values:
        if isinstance(page_value, bool) or page_value in (None, ""):
            continue
        if isinstance(page_value, (int, float)):
            page_count = int(page_value)
        else:
            page_match = re.search(r"\b(\d{1,5})\s*(?:pages?|p\.)\b", _safe_str(page_value), re.IGNORECASE)
            if not page_match:
                continue
            page_count = int(page_match.group(1))
        if page_count > 0:
            metadata["numberOfPages"] = page_count
            break
    source_url = _safe_str(data.get("source_url") or data.get("url"))
    if source_url:
        metadata["links"] = [_make_link("Manga-News", source_url)]
    authors: List[Dict[str, str]] = []
    authors.extend(_author_entries(data.get("authors_story"), "writer"))
    authors.extend(_author_entries(data.get("authors_art"), "penciller"))
    authors.extend(_author_entries(data.get("translators"), "translator"))
    if authors:
        metadata["authors"] = authors
    return metadata

def _search_result_from_item(item: Dict[str, Any]) -> MangaNewsSearchResult:
    vf = item.get("vf") if isinstance(item.get("vf"), dict) else {}
    slug = _safe_str(item.get("slug") or item.get("series_slug"))
    return MangaNewsSearchResult(
        slug=slug,
        title=_safe_str(item.get("title") or slug),
        kind=_safe_str(item.get("kind")),
        score=int(item.get("score") or 0),
        url=_safe_str(item.get("url")),
        media_kind=_safe_str(item.get("media_kind")),
        title_vo=_safe_str(item.get("title_vo")),
        translated_title=_safe_str(item.get("translated_title")),
        vf_status=_edition_status_text(vf),
        vf_volumes=_edition_volumes_text(vf),
        raw=item,
    )


def candidate_from_series(payload: Dict[str, Any], slug_hint: str = "") -> MangaNewsCandidate:
    if isinstance(payload, dict) and payload.get("found") is False:
        raise LookupError("Série absente de l'API Manga News v2")
    data = _series_data(payload)
    slug = _safe_str(data.get("slug") or data.get("series_slug") or slug_hint)
    source_url = _safe_str(data.get("source_url") or (payload.get("source_url") if isinstance(payload, dict) else ""))
    raw = dict(data)
    if isinstance(payload, dict):
        raw["_api"] = {
            key: payload.get(key)
            for key in ("schema_version", "cached", "partial", "warnings", "fingerprint", "fetched_at")
            if payload.get(key) not in (None, "", [])
        }
    return MangaNewsCandidate(
        source_url=source_url,
        slug=slug,
        title=_safe_str(data.get("title") or slug),
        summary=_clean_summary(data.get("summary")),
        cover_url=_safe_str(data.get("cover_image")),
        series_metadata=_map_series_metadata(data),
        raw=raw,
    )


def candidate_from_volume(payload: Dict[str, Any], series_slug_hint: str = "", volume_slug_hint: str = "") -> MangaNewsVolumeCandidate:
    if isinstance(payload, dict) and payload.get("found") is False:
        raise LookupError("Volume absent de l'API Manga News v2")
    data = _series_data(payload)
    series_slug = _safe_str(data.get("series_slug") or data.get("parent_slug") or series_slug_hint)
    volume_slug = _safe_str(data.get("slug") or data.get("volume_slug") or volume_slug_hint)
    source_url = _safe_str(data.get("source_url") or data.get("url") or (payload.get("source_url") if isinstance(payload, dict) else ""))
    raw = dict(data)
    if isinstance(payload, dict):
        raw["_api"] = {
            key: payload.get(key)
            for key in ("schema_version", "cached", "partial", "warnings", "fingerprint", "fetched_at")
            if payload.get(key) not in (None, "", [])
        }
    metadata = _map_volume_metadata({**data, "source_url": source_url})
    return MangaNewsVolumeCandidate(
        source_url=source_url,
        series_slug=series_slug,
        volume_slug=volume_slug,
        title=_safe_str(data.get("title") or data.get("name") or volume_slug),
        number=_safe_str(metadata.get("number") or ""),
        cover_url=_safe_str(data.get("cover_image") or data.get("image") or data.get("cover")),
        book_metadata=metadata,
        raw=raw,
    )


class MangaNewsClient:
    def __init__(
        self,
        base_url: str = DEFAULT_MANGA_NEWS_API_BASE_URL,
        timeout: int = 30,
        token: str = "",
        cache_enabled: bool = True,
        cache_dir: str = ".komga_db_tool_cache/manga_news",
        diagnostic_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.base_url = normalize_api_base_url(base_url)
        self.timeout = int(timeout or 30)
        self.token = (token or "").strip()
        self.cache_enabled = bool(cache_enabled)
        self.cache_dir = cache_dir or ".komga_db_tool_cache/manga_news"
        self.diagnostic_callback = diagnostic_callback
        self.last_url = ""

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            if not path.startswith("/"):
                path = "/" + path
            url = self.base_url + path
        clean_query: Dict[str, Any] = {}
        for key, value in (query or {}).items():
            if value is None or value == "":
                continue
            clean_query[key] = value
        if clean_query:
            url += ("&" if "?" in url else "?") + parse.urlencode(clean_query, doseq=True)
        return url

    def _cache_path(self, url: str) -> str:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest + ".json")

    def _read_cache(self, url: str, ttl_seconds: int) -> Optional[Any]:
        if not self.cache_enabled:
            return None
        path = self._cache_path(url)
        try:
            if not os.path.isfile(path):
                return None
            if ttl_seconds > 0 and (time.time() - os.path.getmtime(path)) > ttl_seconds:
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_cache(self, url: str, data: Any) -> None:
        if not self.cache_enabled:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cache_path(url), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            return

    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": APP_USER_AGENT, "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def _diagnostic_event(self, data: Dict[str, Any]) -> None:
        callback = self.diagnostic_callback
        if not callback:
            return
        try:
            event = dict(data or {})
            event.setdefault("event", "manga_news_http")
            event.setdefault("source", "manga_news")
            callback(event)
        except Exception:
            return

    def _get_json(
        self,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = LOOKUP_CACHE_TTL_SECONDS,
        cache: bool = True,
    ) -> Any:
        url = self._url(path, query)
        self.last_url = url
        started = time.monotonic()
        cached = self._read_cache(url, ttl_seconds) if cache else None
        if cached is not None:
            self._diagnostic_event({
                "event": "manga_news_http_cache_hit",
                "path": path,
                "url": url,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            })
            return cached
        req = request.Request(url, headers=self._headers(), method="GET")
        self._diagnostic_event({"event": "manga_news_http_start", "path": path, "url": url, "timeout": self.timeout})
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                data = json.loads(text)
                self._diagnostic_event({
                    "event": "manga_news_http_success",
                    "path": path,
                    "url": url,
                    "status": getattr(resp, "status", None),
                    "bytes": len(text),
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                })
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            self._diagnostic_event({
                "event": "manga_news_http_error",
                "path": path,
                "url": url,
                "status": exc.code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error": body[:500],
            })
            raise RuntimeError(f"Manga News HTTP {exc.code}: {body[:500]}") from exc
        except error.URLError as exc:
            self._diagnostic_event({
                "event": "manga_news_http_error",
                "path": path,
                "url": url,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error": f"connexion impossible: {exc}",
            })
            raise RuntimeError(f"Manga News connexion impossible: {exc}") from exc
        except TimeoutError as exc:
            self._diagnostic_event({
                "event": "manga_news_http_timeout",
                "path": path,
                "url": url,
                "timeout": self.timeout,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            })
            raise RuntimeError(f"Manga News timeout après {self.timeout}s: {url}") from exc
        if cache:
            self._write_cache(url, data)
        return data

    def test(self) -> str:
        data = self._get_json("/health", ttl_seconds=60)
        ok = data.get("ok") if isinstance(data, dict) else "?"
        if ok is not True:
            raise RuntimeError(f"Manga News health invalide : ok={ok}")
        contract = self._get_json("/openapi.json", ttl_seconds=300)
        version = self._validate_v2_contract(contract)
        if not self._validate_v2_volume_routes(contract):
            raise RuntimeError("API Manga News incompatible avec l'enrichissement tome : route volume absente")
        if not self._validate_v2_next_release_routes(contract):
            raise RuntimeError("API Manga News incompatible avec les prochaines sorties : route release-state absente")
        return f"Manga News v2 OK — contrat {version}, routes et paramètres compatibles"

    @staticmethod
    def _validate_v2_contract(contract: Any) -> str:
        if not isinstance(contract, dict):
            raise RuntimeError("OpenAPI Manga News v2 illisible")
        paths = contract.get("paths") if isinstance(contract.get("paths"), dict) else {}
        missing_paths = sorted(V2_REQUIRED_PATHS - set(paths))
        search = paths.get("/search") if isinstance(paths.get("/search"), dict) else {}
        get_search = search.get("get") if isinstance(search.get("get"), dict) else {}
        parameters = get_search.get("parameters") if isinstance(get_search.get("parameters"), list) else []
        parameter_names = {
            _safe_str(item.get("name"))
            for item in parameters
            if isinstance(item, dict)
        }
        missing_parameters = sorted(V2_SEARCH_PARAMETERS - parameter_names)
        if missing_paths or missing_parameters:
            details = []
            if missing_paths:
                details.append("routes manquantes: " + ", ".join(missing_paths))
            if missing_parameters:
                details.append("paramètres /search manquants: " + ", ".join(missing_parameters))
            raise RuntimeError("API Manga News incompatible avec la branche v2 (" + "; ".join(details) + ")")
        info = contract.get("info") if isinstance(contract.get("info"), dict) else {}
        return _safe_str(info.get("version")) or "inconnue"

    @staticmethod
    def _validate_v2_volume_routes(contract: Any) -> bool:
        if not isinstance(contract, dict):
            return False
        paths = contract.get("paths") if isinstance(contract.get("paths"), dict) else {}
        return bool(V2_VOLUME_PATHS & set(paths))

    @staticmethod
    def _validate_v2_next_release_routes(contract: Any) -> bool:
        if not isinstance(contract, dict):
            return False
        paths = contract.get("paths") if isinstance(contract.get("paths"), dict) else {}
        return bool(V2_NEXT_RELEASE_PATHS & set(paths))

    def resolve(self, query: str, limit: int = 5, manga_only: bool = True) -> List[MangaNewsSearchResult]:
        """Resolve a query with the lightest API route usable for prudent auto-match."""
        text = (query or "").strip()
        if not text:
            return []
        payload = self._get_json(
            "/search/resolve",
            query={
                "q": text,
                "kind": "series",
                "limit": max(1, min(int(limit or 5), 10)),
                "enrich": "false",
                "include_editions": "false",
                "prefer_main_series": "true",
                "include_related": "true",
                "include_books": "false" if manga_only else "true",
            },
            ttl_seconds=SEARCH_CACHE_TTL_SECONDS,
        )
        rows = [_search_result_from_item(item) for item in _result_items(payload)]
        return [row for row in rows if row.kind == "series" or not row.kind]

    def search(self, query: str, limit: int = 10, manga_only: bool = True) -> List[MangaNewsSearchResult]:
        text = (query or "").strip()
        if not text:
            return []
        payload = self._get_json(
            "/search",
            query={
                "q": text,
                "kind": "series",
                "mode": "all",
                "limit": max(1, min(int(limit or 10), 50)),
                "enrich": "false",
                "include_editions": "false",
                "prefer_main_series": "true",
                "include_related": "true",
                "include_books": "false" if manga_only else "true",
            },
            ttl_seconds=SEARCH_CACHE_TTL_SECONDS,
        )
        rows = [_search_result_from_item(item) for item in _result_items(payload)]
        return [row for row in rows if row.kind == "series" or not row.kind]

    def get_series(self, slug: str) -> MangaNewsCandidate:
        sid = _safe_str(slug)
        if not sid:
            raise ValueError("Slug Manga News vide")
        payload = self._get_json(
            f"/series/{parse.quote(sid, safe='')}",
            query={
                "include_raw_sections": "false",
                "fields": V2_SERIES_FIELDS,
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_series(payload, sid)

    def get_series_by_url(self, url: str) -> MangaNewsCandidate:
        target_url = _safe_str(url)
        if not target_url:
            raise ValueError("URL Manga News vide")
        payload = self._get_json(
            "/series/by-url",
            query={
                "url": target_url,
                "include_raw_sections": "false",
                "fields": V2_SERIES_FIELDS,
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_series(payload, "")

    def get_volume(self, series_slug: str, volume_slug: str) -> MangaNewsVolumeCandidate:
        series_id = _safe_str(series_slug)
        volume_id = _safe_str(volume_slug)
        if not series_id or not volume_id:
            raise ValueError("Slug série/volume Manga News vide")
        payload = self._get_json(
            f"/volume/{parse.quote(series_id, safe='')}/{parse.quote(volume_id, safe='')}",
            query={
                "include_raw_sections": "false",
                "fields": "title,number,volume_number,summary,synopsis,description,release_date,date,isbn,ean,publisher_fr,publisher_vo,illustration,illustration_details,cover_image,source_url,authors_story,authors_art,translators",
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_volume(payload, series_id, volume_id)

    def get_volume_by_url(self, url: str) -> MangaNewsVolumeCandidate:
        target_url = _safe_str(url)
        if not target_url:
            raise ValueError("URL volume Manga News vide")
        payload = self._get_json(
            "/volume/by-url",
            query={
                "url": target_url,
                "include_raw_sections": "false",
                "fields": "title,number,volume_number,summary,synopsis,description,release_date,date,isbn,ean,publisher_fr,publisher_vo,illustration,illustration_details,cover_image,source_url,authors_story,authors_art,translators",
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_volume(payload, "", "")

    def get_volume_by_number(self, series_slug: str, number: Any) -> MangaNewsVolumeCandidate:
        series_id = _safe_str(series_slug)
        volume_number = _safe_str(number)
        if not series_id or not volume_number:
            raise ValueError("Slug série ou numéro de tome Manga News vide")
        payload = self._get_json(
            f"/volume/{parse.quote(series_id, safe='')}/number/{parse.quote(volume_number, safe='')}",
            query={
                "include_raw_sections": "false",
                "include_special": "false",
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_volume(payload, series_id, volume_number)

    def get_next_release(self, slug: str = "", url: str = "") -> MangaNewsNextReleaseCandidate:
        series_slug = _safe_str(slug)
        series_url = _safe_str(url)
        errors: List[str] = []

        if not series_slug and series_url:
            series_slug = series_slug_from_manga_news_url(series_url)
        if not series_slug and series_url:
            try:
                resolved = self.get_series_by_url(series_url)
                series_slug = resolved.slug
                if not series_slug:
                    series_slug = series_slug_from_manga_news_url(resolved.source_url)
                if not series_slug:
                    errors.append("/series/by-url: réponse sans slug exploitable")
            except Exception as exc:
                errors.append(f"/series/by-url: {exc}")

        if not series_slug:
            if errors:
                return MangaNewsNextReleaseCandidate(raw={"errors": errors})
            raise ValueError("Slug/URL Manga News vide pour prochaine sortie")

        path = f"/series/{parse.quote(series_slug, safe='')}/release-state"
        query = {"include_isbn": "false", "include_special": "false"}
        try:
            payload = self._get_json(path, query=query, ttl_seconds=0, cache=False)
            candidate = candidate_from_next_release(payload)
            if candidate.number and candidate.release_date:
                return candidate
            errors.append(f"{path}: aucune prochaine sortie exploitable")
            fallback = self._get_series_next_release_fallback(series_slug)
            if fallback.number and fallback.release_date:
                raw = fallback.raw if isinstance(fallback.raw, dict) else {"raw": fallback.raw}
                raw = dict(raw)
                raw["errors"] = errors
                raw["request_slug"] = series_slug
                raw["request_path"] = path
                fallback.raw = raw
                return fallback
            raw = candidate.raw
            if isinstance(raw, dict):
                raw = dict(raw)
                raw["errors"] = errors
                raw["request_slug"] = series_slug
                raw["request_path"] = path
            else:
                raw = {"raw": raw, "errors": errors, "request_slug": series_slug, "request_path": path}
            return MangaNewsNextReleaseCandidate(raw=raw)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
        return MangaNewsNextReleaseCandidate(raw={"errors": errors, "request_slug": series_slug, "request_path": path})

    def _get_series_next_release_fallback(self, series_slug: str) -> MangaNewsNextReleaseCandidate:
        payload = self._get_json(
            f"/series/{parse.quote(series_slug, safe='')}",
            query={
                "include_raw_sections": "false",
                "fields": "title,vf,next_release_date,last_release_date,source_url",
            },
            ttl_seconds=0,
            cache=False,
        )
        return candidate_from_series_next_release(payload, series_slug)

    @staticmethod
    def candidate_to_dict(candidate: MangaNewsCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "slug": candidate.slug,
            "title": candidate.title,
            "summary": candidate.summary,
            "cover_url": candidate.cover_url,
            "series_metadata": candidate.series_metadata,
            "raw": candidate.raw,
        }

    @staticmethod
    def volume_candidate_to_dict(candidate: MangaNewsVolumeCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "series_slug": candidate.series_slug,
            "volume_slug": candidate.volume_slug,
            "title": candidate.title,
            "number": candidate.number,
            "cover_url": candidate.cover_url,
            "book_metadata": candidate.book_metadata,
            "raw": candidate.raw,
        }

    @staticmethod
    def next_release_candidate_to_dict(candidate: MangaNewsNextReleaseCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "title": candidate.title,
            "number": candidate.number,
            "release_date": candidate.release_date,
            "raw": candidate.raw,
        }
