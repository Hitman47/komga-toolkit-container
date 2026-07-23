from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

APP_USER_AGENT = "komga-db-tool/0.6.0 mangabaka-adapter"
DEFAULT_API_BASE_URL = "https://api.mangabaka.org"
MANGABAKA_SITE_URL = "https://mangabaka.org"
SEARCH_CACHE_TTL_SECONDS = 2 * 60 * 60
LOOKUP_CACHE_TTL_SECONDS = 12 * 60 * 60

STATUS_MAP = {
    "completed": "ENDED",
    "releasing": "ONGOING",
    "hiatus": "HIATUS",
    "cancelled": "ABANDONED",
    "canceled": "ABANDONED",
    "upcoming": "ONGOING",
    "unknown": "",
}

CONTENT_RATING_TO_AGE = {
    "safe": 0,
    "suggestive": 12,
    "erotica": 18,
    "pornographic": 18,
}

LANGUAGE_ALIASES = {
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
    "zh": "zh",
    "cn": "zh",
    "chi": "zh",
    "zho": "zh",
    "chinese": "zh",
    "en": "en",
    "eng": "en",
    "english": "en",
    "fr": "fr",
    "fre": "fr",
    "fra": "fr",
    "french": "fr",
}


@dataclass
class MangaBakaSearchResult:
    id: str
    title: str
    type: str = ""
    status: str = ""
    year: str = ""
    publisher: str = ""
    genres: List[str] = field(default_factory=list)
    cover_url: str = ""
    source_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MangaBakaCandidate:
    source_url: str
    series_id: str = ""
    title: str = ""
    cover_url: str = ""
    series_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MangaBakaNextReleaseCandidate:
    series_id: str = ""
    number: str = ""
    release_date: str = ""
    source_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def normalize_api_base_url(url: str) -> str:
    text = (url or DEFAULT_API_BASE_URL).strip()
    if not text:
        text = DEFAULT_API_BASE_URL
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    return text.rstrip("/")


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


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


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return None


def _date_value(value: Any) -> str:
    text = _safe_str(value)
    if not text:
        return ""
    match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        yyyy, mm, dd = match.groups()
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    match = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
    if match:
        dd, mm, yyyy = match.groups()
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    return ""


def _number_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("number", "volume", "volume_number", "volumeNumber", "num", "no"):
            found = _number_value(value.get(key))
            if found:
                return found
        return ""
    text = _safe_str(value)
    if not text:
        return ""
    match = re.search(r"\d+(?:[.,]\d+)?", text)
    return match.group(0).replace(",", ".") if match else text


def _looks_like_next_release_key(key: str) -> bool:
    lowered = _safe_str(key).lower()
    return any(part in lowered for part in ("next", "upcoming", "future", "prochain"))


def _find_next_release_payload(payload: Any) -> Dict[str, Any]:
    root = _series_data(payload)
    candidates: List[Dict[str, Any]] = []
    if isinstance(root, dict):
        for key, value in root.items():
            if _looks_like_next_release_key(str(key)):
                if isinstance(value, dict):
                    candidates.append(value)
                elif value not in (None, "", [], {}):
                    candidates.append({key: value})
        for key in ("releases", "volumes", "publication", "published"):
            value = root.get(key)
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    if _looks_like_next_release_key(str(nested_key)):
                        if isinstance(nested_value, dict):
                            candidates.append(nested_value)
                        elif nested_value not in (None, "", [], {}):
                            candidates.append({nested_key: nested_value})
    for candidate in candidates:
        if _date_from_next_release_data(candidate) and _number_from_next_release_data(candidate):
            return candidate
    return {}


def _date_from_next_release_data(data: Dict[str, Any]) -> str:
    for key in (
        "date", "release_date", "releaseDate", "publication_date", "publicationDate",
        "published_at", "publishedAt", "scheduled_at", "scheduledAt",
        "next_release_date", "nextReleaseDate", "next_volume_date", "nextVolumeDate",
    ):
        found = _date_value(data.get(key))
        if found:
            return found
    for value in data.values():
        if isinstance(value, dict):
            found = _date_from_next_release_data(value)
            if found:
                return found
    return ""


def _number_from_next_release_data(data: Dict[str, Any]) -> str:
    for key in (
        "number", "volume", "volume_number", "volumeNumber", "number_int", "numberInt",
        "next_volume", "nextVolume", "next_volume_number", "nextVolumeNumber",
        "next_release_volume", "nextReleaseVolume",
    ):
        found = _number_value(data.get(key))
        if found:
            return found
    for value in data.values():
        if isinstance(value, dict):
            found = _number_from_next_release_data(value)
            if found:
                return found
    return ""


def candidate_from_next_release(payload: Any, series_id: str = "") -> MangaBakaNextReleaseCandidate:
    root = _series_data(payload)
    data = _find_next_release_payload(root)
    sid = _safe_str(series_id or (root.get("id") if isinstance(root, dict) else ""))
    if not data:
        raw = root if isinstance(root, dict) else {"raw": payload}
        return MangaBakaNextReleaseCandidate(series_id=sid, source_url=_source_url(sid), raw=raw)
    return MangaBakaNextReleaseCandidate(
        series_id=sid,
        number=_number_from_next_release_data(data),
        release_date=_date_from_next_release_data(data),
        source_url=_source_url(sid),
        raw={"next_release": data, "series": root},
    )


def _series_data(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _result_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(payload.get("content"), list):
            return [x for x in payload["content"] if isinstance(x, dict)]
        if isinstance(payload.get("items"), list):
            return [x for x in payload["items"] if isinstance(x, dict)]
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _publisher_name(pub: Any) -> str:
    if isinstance(pub, str):
        return pub.strip()
    if isinstance(pub, dict):
        return _safe_str(pub.get("name") or pub.get("title") or pub.get("label"))
    return ""


def _publisher_type(pub: Any) -> str:
    return _safe_str(pub.get("type") if isinstance(pub, dict) else "").casefold()


def _pick_publishers(publishers: Any) -> str:
    if not isinstance(publishers, list):
        return ""
    names_by_type: Dict[str, List[str]] = {"english": [], "original": [], "other": []}
    for pub in publishers:
        name = _publisher_name(pub)
        if not name:
            continue
        ptype = _publisher_type(pub)
        if "english" in ptype:
            names_by_type["english"].append(name)
        elif "original" in ptype:
            names_by_type["original"].append(name)
        else:
            names_by_type["other"].append(name)
    for key in ("english", "original", "other"):
        if names_by_type[key]:
            return "; ".join(_dedupe_strings(names_by_type[key]))
    return ""


def _title_candidates_from_secondary(secondary_titles: Any) -> List[str]:
    titles: List[Any] = []
    if isinstance(secondary_titles, dict):
        for values in secondary_titles.values():
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        titles.append(item.get("title") or item.get("name") or item.get("value"))
                    else:
                        titles.append(item)
            elif isinstance(values, dict):
                titles.append(values.get("title") or values.get("name") or values.get("value"))
            else:
                titles.append(values)
    elif isinstance(secondary_titles, list):
        for item in secondary_titles:
            if isinstance(item, dict):
                titles.append(item.get("title") or item.get("name") or item.get("value"))
            else:
                titles.append(item)
    return _dedupe_strings(titles)



def _guess_alternate_title_label(value: str) -> str:
    """Return a Komga-compatible label for an alternate title."""
    text = value or ""
    if any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text):
        return "ja"
    if any("\uac00" <= ch <= "\ud7af" for ch in text):
        return "ko"
    if any("\u0400" <= ch <= "\u04ff" for ch in text):
        return "ru"
    return "alt"

def _secondary_language_candidates(secondary_titles: Any) -> List[str]:
    candidates: List[str] = []
    if isinstance(secondary_titles, dict):
        for key in secondary_titles.keys():
            text = _safe_str(key).lower().replace("_", "-")
            if text:
                candidates.append(text.split("-", 1)[0])
    return candidates


def _normalize_language(value: Any) -> str:
    text = _safe_str(value).lower().replace("_", "-").strip()
    if not text:
        return ""
    first = text.split("-", 1)[0]
    return LANGUAGE_ALIASES.get(text) or LANGUAGE_ALIASES.get(first) or (first if 2 <= len(first) <= 3 else "")


def _infer_language(series: Dict[str, Any]) -> str:
    for key in (
        "language",
        "original_language",
        "originalLanguage",
        "country_of_origin",
        "countryOfOrigin",
        "native_language",
        "nativeLanguage",
    ):
        lang = _normalize_language(series.get(key))
        if lang:
            return lang
    for candidate in _secondary_language_candidates(series.get("secondary_titles")):
        lang = _normalize_language(candidate)
        if lang:
            return lang
    return ""


def _find_url_in_value(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text.startswith(("http://", "https://")) else ""
    if isinstance(value, dict):
        preferred_keys = ("x1", "default", "raw", "large", "medium", "small", "url", "image", "src")
        for key in preferred_keys:
            found = _find_url_in_value(value.get(key))
            if found:
                return found
        for nested in value.values():
            found = _find_url_in_value(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_url_in_value(item)
            if found:
                return found
    return ""


def _source_url(series_id: Any) -> str:
    sid = _safe_str(series_id)
    return f"{MANGABAKA_SITE_URL}/{parse.quote(sid, safe='')}" if sid else MANGABAKA_SITE_URL


def _make_link(label: str, url: str) -> Dict[str, str]:
    return {"label": label.strip() or "MangaBaka", "url": url.strip()}


def _links_from_series(series: Dict[str, Any]) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    sid = series.get("id")
    if sid not in (None, ""):
        links.append(_make_link("MangaBaka", _source_url(sid)))

    raw_links = series.get("links")
    if isinstance(raw_links, list):
        for item in raw_links:
            if isinstance(item, str):
                url = item.strip()
                if url.startswith(("http://", "https://")):
                    host = parse.urlsplit(url).netloc.replace("www.", "") or "Lien"
                    links.append(_make_link(host, url))
            elif isinstance(item, dict):
                url = _safe_str(item.get("url") or item.get("href") or item.get("link"))
                label = _safe_str(item.get("label") or item.get("name") or item.get("site") or item.get("source"))
                if url.startswith(("http://", "https://")):
                    links.append(_make_link(label or parse.urlsplit(url).netloc.replace("www.", ""), url))

    source = series.get("source")
    if isinstance(source, dict):
        for provider, data in source.items():
            if not isinstance(data, dict):
                continue
            url = _safe_str(data.get("url") or data.get("link"))
            if url.startswith(("http://", "https://")):
                links.append(_make_link(str(provider), url))

    deduped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for link in links:
        url = link.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(link)
    return deduped


def _map_status(value: Any) -> str:
    text = _safe_str(value).lower().replace("-", "_")
    return STATUS_MAP.get(text, "")


def _map_age_rating(value: Any) -> Optional[int]:
    text = _safe_str(value).lower()
    return CONTENT_RATING_TO_AGE.get(text)


def _map_series_metadata(series: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}

    title = _safe_str(series.get("title") or series.get("name"))
    if title:
        metadata["title"] = title
        metadata["titleSort"] = title

    summary = _safe_str(series.get("description") or series.get("summary") or series.get("synopsis"))
    if summary:
        metadata["summary"] = summary

    status = _map_status(series.get("status"))
    if status:
        metadata["status"] = status

    publisher = _pick_publishers(series.get("publishers"))
    if publisher:
        metadata["publisher"] = publisher

    language = _infer_language(series)
    if language:
        metadata["language"] = language

    age_rating = _map_age_rating(series.get("content_rating") or series.get("contentRating"))
    if age_rating is not None:
        metadata["ageRating"] = age_rating

    total_book_count = _int_or_none(series.get("final_volume") or series.get("finalVolume") or series.get("total_volumes") or series.get("totalVolumes"))
    if total_book_count is not None:
        metadata["totalBookCount"] = total_book_count

    genres = series.get("genres")
    if isinstance(genres, list):
        cleaned = _dedupe_strings(genres)
        if cleaned:
            metadata["genres"] = cleaned

    tags = series.get("tags")
    if isinstance(tags, list):
        cleaned = _dedupe_strings(tags)
        if cleaned:
            metadata["tags"] = cleaned

    alternate_titles = _dedupe_strings([
        series.get("native_title"),
        series.get("nativeTitle"),
        series.get("romanized_title"),
        series.get("romanizedTitle"),
        *_title_candidates_from_secondary(series.get("secondary_titles") or series.get("secondaryTitles")),
    ])
    if title:
        alternate_titles = [x for x in alternate_titles if x.casefold() != title.casefold()]
    if alternate_titles:
        metadata["alternateTitles"] = [
            {"label": _guess_alternate_title_label(value), "title": value}
            for value in alternate_titles
            if isinstance(value, str) and value.strip()
        ]

    links = _links_from_series(series)
    if links:
        metadata["links"] = links

    return metadata


def _search_result_from_series(series: Dict[str, Any]) -> MangaBakaSearchResult:
    publisher = _pick_publishers(series.get("publishers"))
    sid = _safe_str(series.get("id"))
    return MangaBakaSearchResult(
        id=sid,
        title=_safe_str(series.get("title") or series.get("name") or sid),
        type=_safe_str(series.get("type")),
        status=_safe_str(series.get("status")),
        year=_safe_str(series.get("year")),
        publisher=publisher,
        genres=_dedupe_strings(series.get("genres") if isinstance(series.get("genres"), list) else []),
        cover_url=_find_url_in_value(series.get("cover")),
        source_url=_source_url(sid),
        raw=series,
    )


def candidate_from_series(series: Dict[str, Any]) -> MangaBakaCandidate:
    data = _series_data(series)
    sid = _safe_str(data.get("id"))
    return MangaBakaCandidate(
        source_url=_source_url(sid),
        series_id=sid,
        title=_safe_str(data.get("title") or data.get("name") or sid),
        cover_url=_find_url_in_value(data.get("cover")),
        series_metadata=_map_series_metadata(data),
        raw=data,
    )


class MangaBakaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_API_BASE_URL,
        timeout: int = 30,
        cache_enabled: bool = True,
        cache_dir: str = ".komga_db_tool_cache/mangabaka",
    ):
        self.base_url = normalize_api_base_url(base_url)
        self.timeout = int(timeout or 30)
        self.cache_enabled = bool(cache_enabled)
        self.cache_dir = cache_dir or ".komga_db_tool_cache/mangabaka"
        self.last_url = ""

    def _path(self, path: str) -> str:
        path = path if path.startswith("/") else "/" + path
        if self.base_url.endswith("/v1"):
            return path.replace("/v1/", "/", 1) if path.startswith("/v1/") else path
        return path if path.startswith("/v1/") else "/v1" + path

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = self.base_url + self._path(path)
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

    def _get_json(self, path: str, query: Optional[Dict[str, Any]] = None, ttl_seconds: int = LOOKUP_CACHE_TTL_SECONDS) -> Any:
        url = self._url(path, query)
        self.last_url = url
        cached = self._read_cache(url, ttl_seconds)
        if cached is not None:
            return cached
        req = request.Request(
            url,
            headers={
                "User-Agent": APP_USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                data = json.loads(text)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MangaBaka HTTP {exc.code}: {body[:500]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"MangaBaka connexion impossible: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"MangaBaka timeout: {exc}") from exc
        self._write_cache(url, data)
        return data

    def test(self) -> str:
        data = self._get_json("/series/1", ttl_seconds=LOOKUP_CACHE_TTL_SECONDS)
        status = data.get("status") if isinstance(data, dict) else "?"
        return f"MangaBaka OK — status {status}, URL {self.last_url}"

    def search(self, query: str, limit: int = 30) -> List[MangaBakaSearchResult]:
        text = (query or "").strip()
        if not text:
            return []
        payload = self._get_json(
            "/series/search",
            query={
                "q": text,
                "content_rating": ["safe", "suggestive", "erotica", "pornographic"],
                "page": 1,
                "limit": max(1, min(int(limit or 30), 50)),
            },
            ttl_seconds=SEARCH_CACHE_TTL_SECONDS,
        )
        return [_search_result_from_series(item) for item in _result_items(payload)]

    def get_series(self, series_id: str) -> MangaBakaCandidate:
        sid = _safe_str(series_id)
        if not sid:
            raise ValueError("ID MangaBaka vide")
        payload = self._get_json(f"/series/{parse.quote(sid, safe='')}", ttl_seconds=LOOKUP_CACHE_TTL_SECONDS)
        return candidate_from_series(payload)

    def get_next_release(self, series_id: str) -> MangaBakaNextReleaseCandidate:
        sid = _safe_str(series_id)
        if not sid:
            raise ValueError("ID MangaBaka vide")
        payload = self._get_json(f"/series/{parse.quote(sid, safe='')}", ttl_seconds=LOOKUP_CACHE_TTL_SECONDS)
        return candidate_from_next_release(payload, sid)

    @staticmethod
    def candidate_to_dict(candidate: MangaBakaCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "series_id": candidate.series_id,
            "title": candidate.title,
            "cover_url": candidate.cover_url,
            "series_metadata": candidate.series_metadata,
            "raw": candidate.raw,
        }

    @staticmethod
    def next_release_candidate_to_dict(candidate: MangaBakaNextReleaseCandidate) -> Dict[str, Any]:
        return {
            "series_id": candidate.series_id,
            "number": candidate.number,
            "release_date": candidate.release_date,
            "source_url": candidate.source_url,
            "raw": candidate.raw,
        }
