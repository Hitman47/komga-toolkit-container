from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib import error, parse, request


APP_USER_AGENT = "komga-db-tool/3.11 comicvine-adapter"
DEFAULT_COMICVINE_API_BASE_URL = "https://comicvine.gamespot.com/api"
SEARCH_CACHE_TTL_SECONDS = 2 * 60 * 60
LOOKUP_CACHE_TTL_SECONDS = 12 * 60 * 60


@dataclass
class ComicVineSearchResult:
    id: str
    title: str
    start_year: str = ""
    publisher: str = ""
    issue_count: Optional[int] = None
    source_url: str = ""
    image_url: str = ""
    deck: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComicVineCandidate:
    source_url: str
    volume_id: str = ""
    title: str = ""
    summary: str = ""
    cover_url: str = ""
    series_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComicVineIssueCandidate:
    source_url: str
    issue_id: str = ""
    volume_id: str = ""
    title: str = ""
    issue_number: str = ""
    cover_url: str = ""
    book_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def normalize_api_base_url(url: str) -> str:
    text = (url or DEFAULT_COMICVINE_API_BASE_URL).strip()
    if not text:
        text = DEFAULT_COMICVINE_API_BASE_URL
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    return text.rstrip("/")


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _int_or_none(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def _clean_html(value: Any) -> str:
    text = _safe_str(value)
    if not text:
        return ""
    text = re.sub(r"(?is)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?is)</\s*p\s*>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _publisher_name(value: Any) -> str:
    if isinstance(value, dict):
        return _safe_str(value.get("name"))
    return _safe_str(value)


def _image_url(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("original_url", "super_url", "screen_url", "medium_url", "small_url", "thumb_url"):
        url = _safe_str(value.get(key))
        if url.startswith(("http://", "https://")):
            return url
    return ""


def _person_name(value: Any) -> str:
    if isinstance(value, dict):
        return _safe_str(value.get("name"))
    return _safe_str(value)


def _author_entries(people: Any) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    if not isinstance(people, list):
        return entries
    for person in people:
        name = _person_name(person)
        if not name:
            continue
        role = _safe_str(person.get("role") if isinstance(person, dict) else "") or "writer"
        item = {"name": name, "role": role}
        key = (item["name"].casefold(), item["role"].casefold())
        if any((x.get("name", "").casefold(), x.get("role", "").casefold()) == key for x in entries):
            continue
        entries.append(item)
    return entries


def _source_url(data: Dict[str, Any], volume_id: str = "") -> str:
    url = _safe_str(data.get("site_detail_url"))
    if url:
        return url
    vid = _safe_str(data.get("id") or volume_id)
    return f"https://comicvine.gamespot.com/volume/4050-{parse.quote(vid, safe='')}/" if vid else ""


def _issue_source_url(data: Dict[str, Any], issue_id: str = "") -> str:
    url = _safe_str(data.get("site_detail_url"))
    if url:
        return url
    iid = _safe_str(data.get("id") or issue_id)
    return f"https://comicvine.gamespot.com/issue/4000-{parse.quote(iid, safe='')}/" if iid else ""


def _comicvine_date(value: Any) -> str:
    text = _safe_str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return text + "-01"
    if re.fullmatch(r"\d{4}", text):
        return text + "-01-01"
    return ""


def _results_list(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, dict)]
        if isinstance(results, dict):
            return [results]
    return []


def _map_series_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}

    title = _safe_str(data.get("name"))
    if title:
        metadata["title"] = title
        metadata["titleSort"] = title

    summary = _clean_html(data.get("deck") or data.get("description"))
    if summary:
        metadata["summary"] = summary

    publisher = _publisher_name(data.get("publisher"))
    if publisher:
        metadata["publisher"] = publisher

    total = _int_or_none(data.get("count_of_issues"))
    if total is not None:
        metadata["totalBookCount"] = total

    authors = _author_entries(data.get("people"))
    if authors:
        metadata["authors"] = authors

    url = _source_url(data)
    if url:
        metadata["links"] = [{"label": "ComicVine", "url": url}]

    return metadata


def _map_issue_metadata(data: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}

    title = _safe_str(data.get("name"))
    if title:
        metadata["title"] = title
        metadata["titleSort"] = title

    number = _safe_str(data.get("issue_number"))
    if number:
        metadata["number"] = number
        metadata["numberSort"] = number

    summary = _clean_html(data.get("deck") or data.get("description"))
    if summary:
        metadata["summary"] = summary

    release_date = _comicvine_date(data.get("cover_date") or data.get("store_date"))
    if release_date:
        metadata["releaseDate"] = release_date

    authors = _author_entries(data.get("person_credits") or data.get("people"))
    if authors:
        metadata["authors"] = authors

    url = _issue_source_url(data)
    if url:
        metadata["links"] = [{"label": "ComicVine", "url": url}]

    return metadata


def _search_result_from_volume(data: Dict[str, Any]) -> ComicVineSearchResult:
    volume_id = _safe_str(data.get("id"))
    return ComicVineSearchResult(
        id=volume_id,
        title=_safe_str(data.get("name") or volume_id),
        start_year=_safe_str(data.get("start_year")),
        publisher=_publisher_name(data.get("publisher")),
        issue_count=_int_or_none(data.get("count_of_issues")),
        source_url=_source_url(data, volume_id),
        image_url=_image_url(data.get("image")),
        deck=_clean_html(data.get("deck")),
        raw=data,
    )


def candidate_from_volume(data: Dict[str, Any]) -> ComicVineCandidate:
    volume = data if isinstance(data, dict) else {}
    if isinstance(volume.get("results"), dict):
        volume = volume["results"]
    volume_id = _safe_str(volume.get("id"))
    summary = _clean_html(volume.get("deck") or volume.get("description"))
    return ComicVineCandidate(
        source_url=_source_url(volume, volume_id),
        volume_id=volume_id,
        title=_safe_str(volume.get("name") or volume_id),
        summary=summary,
        cover_url=_image_url(volume.get("image")),
        series_metadata=_map_series_metadata(volume),
        raw=volume,
    )


def candidate_from_issue(data: Dict[str, Any]) -> ComicVineIssueCandidate:
    issue = data if isinstance(data, dict) else {}
    if isinstance(issue.get("results"), dict):
        issue = issue["results"]
    issue_id = _safe_str(issue.get("id"))
    volume = issue.get("volume") if isinstance(issue.get("volume"), dict) else {}
    volume_id = _safe_str(volume.get("id"))
    title = _safe_str(issue.get("name") or issue.get("title") or "")
    number = _safe_str(issue.get("issue_number"))
    if not title and number:
        title = f"Issue #{number}"
    return ComicVineIssueCandidate(
        source_url=_issue_source_url(issue, issue_id),
        issue_id=issue_id,
        volume_id=volume_id,
        title=title or issue_id,
        issue_number=number,
        cover_url=_image_url(issue.get("image")),
        book_metadata=_map_issue_metadata(issue),
        raw=issue,
    )


class ComicVineClient:
    def __init__(
        self,
        base_url: str = DEFAULT_COMICVINE_API_BASE_URL,
        api_key: str = "",
        timeout: int = 30,
        cache_enabled: bool = True,
        cache_dir: str = ".komga_db_tool_cache/comicvine",
    ):
        self.base_url = normalize_api_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout = int(timeout or 30)
        self.cache_enabled = bool(cache_enabled)
        self.cache_dir = cache_dir or ".komga_db_tool_cache/comicvine"
        self.last_url = ""

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None, *, include_secret: bool = True) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            if not path.startswith("/"):
                path = "/" + path
            url = self.base_url + path
        clean_query: Dict[str, Any] = {"format": "json"}
        for key, value in (query or {}).items():
            if value is None or value == "":
                continue
            clean_query[key] = value
        if include_secret:
            if not self.api_key:
                raise ValueError("Clé API ComicVine absente")
            clean_query["api_key"] = self.api_key
        if clean_query:
            url += ("&" if "?" in url else "?") + parse.urlencode(clean_query, doseq=True)
        return url

    def _cache_key_url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        return self._url(path, query, include_secret=False)

    def _cache_path(self, cache_key: str) -> str:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest + ".json")

    def _read_cache(self, cache_key: str, ttl_seconds: int) -> Optional[Any]:
        if not self.cache_enabled:
            return None
        path = self._cache_path(cache_key)
        try:
            if not os.path.isfile(path):
                return None
            if ttl_seconds > 0 and (time.time() - os.path.getmtime(path)) > ttl_seconds:
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_cache(self, cache_key: str, data: Any) -> None:
        if not self.cache_enabled:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cache_path(cache_key), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            return

    def _get_json(self, path: str, query: Optional[Dict[str, Any]] = None, ttl_seconds: int = LOOKUP_CACHE_TTL_SECONDS) -> Any:
        cache_key = self._cache_key_url(path, query)
        cached = self._read_cache(cache_key, ttl_seconds)
        if cached is not None:
            self.last_url = cache_key
            return cached
        url = self._url(path, query)
        self.last_url = cache_key
        req = request.Request(
            url,
            headers={"User-Agent": APP_USER_AGENT, "Accept": "application/json"},
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                data = json.loads(text)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ComicVine HTTP {exc.code}: {body[:500]}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"ComicVine connexion impossible: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"ComicVine timeout: {exc}") from exc
        if isinstance(data, dict):
            status = _safe_str(data.get("status_code"))
            if status and status not in {"1", "OK"}:
                message = _safe_str(data.get("error") or data.get("message") or "erreur API")
                raise RuntimeError(f"ComicVine API status {status}: {message}")
        self._write_cache(cache_key, data)
        return data

    def test(self) -> str:
        rows = self.search("Batman", limit=1)
        return f"ComicVine OK — {len(rows)} résultat test, URL {self.last_url}"

    def search(self, query: str, limit: int = 10) -> List[ComicVineSearchResult]:
        text = (query or "").strip()
        if not text:
            return []
        payload = self._get_json(
            "/search/",
            query={
                "query": text,
                "resources": "volume",
                "limit": max(1, min(int(limit or 10), 50)),
                "field_list": "id,name,start_year,publisher,count_of_issues,site_detail_url,image,deck",
            },
            ttl_seconds=SEARCH_CACHE_TTL_SECONDS,
        )
        return [_search_result_from_volume(item) for item in _results_list(payload)]

    def get_volume(self, volume_id: str) -> ComicVineCandidate:
        vid = _safe_str(volume_id)
        if not vid:
            raise ValueError("ID ComicVine vide")
        payload = self._get_json(
            f"/volume/4050-{parse.quote(vid, safe='')}/",
            query={
                "field_list": "id,name,start_year,publisher,count_of_issues,site_detail_url,image,deck,description,people",
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_volume(payload)

    def list_volume_issues(self, volume_id: str, limit: int = 200) -> List[ComicVineIssueCandidate]:
        vid = _safe_str(volume_id)
        if not vid:
            raise ValueError("ID volume ComicVine vide")
        payload = self._get_json(
            "/issues/",
            query={
                "filter": f"volume:{vid}",
                "sort": "issue_number:asc",
                "limit": max(1, min(int(limit or 200), 500)),
                "field_list": "id,name,issue_number,cover_date,store_date,site_detail_url,image,deck,description,volume,person_credits",
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        rows = [candidate_from_issue(item) for item in _results_list(payload)]
        # Defensive guard: if ComicVine ignores/loosens the filter, do not show
        # unrelated issues and let the UI produce false tome matches.
        return [row for row in rows if row.volume_id == vid]

    def get_issue(self, issue_id: str) -> ComicVineIssueCandidate:
        iid = _safe_str(issue_id)
        if not iid:
            raise ValueError("ID issue ComicVine vide")
        payload = self._get_json(
            f"/issue/4000-{parse.quote(iid, safe='')}/",
            query={
                "field_list": "id,name,issue_number,cover_date,store_date,site_detail_url,image,deck,description,volume,person_credits",
            },
            ttl_seconds=LOOKUP_CACHE_TTL_SECONDS,
        )
        return candidate_from_issue(payload)

    @staticmethod
    def candidate_to_dict(candidate: ComicVineCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "volume_id": candidate.volume_id,
            "title": candidate.title,
            "summary": candidate.summary,
            "cover_url": candidate.cover_url,
            "series_metadata": candidate.series_metadata,
            "raw": candidate.raw,
        }

    @staticmethod
    def issue_candidate_to_dict(candidate: ComicVineIssueCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "issue_id": candidate.issue_id,
            "volume_id": candidate.volume_id,
            "title": candidate.title,
            "issue_number": candidate.issue_number,
            "cover_url": candidate.cover_url,
            "book_metadata": candidate.book_metadata,
            "raw": candidate.raw,
        }
