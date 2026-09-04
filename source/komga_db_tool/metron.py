from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

import truststore


APP_USER_AGENT = "komga-db-tool/3.12 metron-adapter"
DEFAULT_METRON_API_BASE_URL = "https://metron.cloud/api"
SEARCH_CACHE_TTL_SECONDS = 2 * 60 * 60
LOOKUP_CACHE_TTL_SECONDS = 12 * 60 * 60
AUTH_FAILURE_GUARD_SECONDS = 5 * 60 + 5


@dataclass
class MetronSearchResult:
    id: str
    title: str
    year_began: str = ""
    year_end: str = ""
    volume: str = ""
    issue_count: Optional[int] = None
    source_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MetronCandidate:
    series_id: str
    title: str
    source_url: str = ""
    series_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def normalize_api_base_url(url: str) -> str:
    text = (url or DEFAULT_METRON_API_BASE_URL).strip()
    if not text:
        text = DEFAULT_METRON_API_BASE_URL
    if not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    return text.rstrip("/")


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _named_value(value: Any) -> str:
    if isinstance(value, dict):
        return _safe_str(value.get("name") or value.get("label") or value.get("display_name"))
    return _safe_str(value)


def _list_names(value: Any) -> List[str]:
    rows = value if isinstance(value, list) else []
    result: List[str] = []
    seen: set[str] = set()
    for item in rows:
        name = _named_value(item)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _detail_url(data: Dict[str, Any], series_id: str) -> str:
    direct = _safe_str(data.get("resource_url") or data.get("url"))
    if direct.startswith(("http://", "https://")):
        return direct
    return f"https://metron.cloud/series/{parse.quote(series_id, safe='')}/" if series_id else ""


def _display_title(data: Dict[str, Any]) -> str:
    title = _safe_str(data.get("name") or data.get("series") or data.get("title"))
    # La liste Metron affiche souvent « Nom (année) ». L'année reste une
    # colonne distincte et ne doit pas dégrader le score de correspondance.
    return re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip() or title


def _status(value: Any) -> str:
    raw = _named_value(value).casefold().replace("_", " ").replace("-", " ")
    if any(token in raw for token in ("ongoing", "continuing", "current", "in progress")):
        return "ONGOING"
    if any(token in raw for token in ("complete", "completed", "ended", "finished")):
        return "ENDED"
    if "hiatus" in raw:
        return "HIATUS"
    if any(token in raw for token in ("cancelled", "canceled", "abandoned")):
        return "ABANDONED"
    return ""


def _language(value: Any) -> str:
    raw = _named_value(value).casefold().replace("_", "-")
    aliases = {
        "english": "en",
        "eng": "en",
        "en-us": "en",
        "en-gb": "en",
        "french": "fr",
        "fra": "fr",
        "fre": "fr",
        "japanese": "ja",
        "jpn": "ja",
        "jp": "ja",
    }
    normalized = aliases.get(raw, raw)
    # Première version prudente : seules les langues explicitement validées
    # dans les parcours Komga sont proposées.
    return normalized if normalized in {"en", "fr", "ja"} else ""


def search_result_from_series(data: Dict[str, Any]) -> MetronSearchResult:
    series_id = _safe_str(data.get("id"))
    return MetronSearchResult(
        id=series_id,
        title=_display_title(data) or series_id,
        year_began=_safe_str(data.get("year_began")),
        year_end=_safe_str(data.get("year_end")),
        volume=_safe_str(data.get("volume")),
        issue_count=_int_or_none(data.get("issue_count")),
        source_url=_detail_url(data, series_id),
        raw=data,
    )


def candidate_from_series(data: Dict[str, Any]) -> MetronCandidate:
    series = data if isinstance(data, dict) else {}
    series_id = _safe_str(series.get("id"))
    title = _display_title(series) or series_id
    source_url = _detail_url(series, series_id)
    metadata: Dict[str, Any] = {}

    summary = _safe_str(series.get("desc") or series.get("description"))
    if summary:
        metadata["summary"] = summary

    status = _status(series.get("status"))
    if status:
        metadata["status"] = status

    # L'imprint est l'éditeur effectivement visible sur l'ouvrage ; le
    # publisher reste le repli quand l'imprint est absent.
    publisher = _named_value(series.get("imprint")) or _named_value(series.get("publisher"))
    if publisher:
        metadata["publisher"] = publisher

    language = _language(series.get("language"))
    if language:
        metadata["language"] = language

    issue_count = _int_or_none(series.get("issue_count"))
    if issue_count is not None and issue_count > 0:
        metadata["totalBookCount"] = issue_count

    genres = _list_names(series.get("genres"))
    if genres:
        metadata["genres"] = genres

    alt_names = _list_names(series.get("alt_names"))
    alt_names = [name for name in alt_names if name.casefold() != title.casefold()]
    # Les titres alternatifs suivent la règle globale du Toolkit : aucun ajout
    # provenant d'une série dont la langue n'est pas explicitement FR/EN/JA.
    if alt_names and language:
        metadata["alternateTitles"] = [{"label": "alt", "title": name} for name in alt_names]

    if source_url:
        metadata["links"] = [{"label": "Metron", "url": source_url}]

    return MetronCandidate(
        series_id=series_id,
        title=title,
        source_url=source_url,
        series_metadata=metadata,
        raw=series,
    )


class MetronClient:
    def __init__(
        self,
        base_url: str = DEFAULT_METRON_API_BASE_URL,
        token: str = "",
        timeout: int = 30,
        cache_enabled: bool = True,
        cache_dir: str = ".komga_db_tool_cache/metron",
    ):
        self.base_url = normalize_api_base_url(base_url)
        self.token = (token or "").strip()
        self.timeout = int(timeout or 30)
        self.cache_enabled = bool(cache_enabled)
        self.cache_dir = cache_dir or ".komga_db_tool_cache/metron"
        self.last_url = ""
        self.last_rate_limit: Dict[str, str] = {}
        # Desktop security tools may inspect HTTPS and issue a local
        # certificate trusted by Windows. Use the native OS trust store so
        # validation matches the browser without disabling TLS verification.
        self.ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            url = self.base_url + (path if path.startswith("/") else "/" + path)
        clean_query = {key: value for key, value in (query or {}).items() if value not in (None, "")}
        if clean_query:
            url += ("&" if "?" in url else "?") + parse.urlencode(clean_query, doseq=True)
        return url

    def _cache_path(self, cache_key: str) -> str:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest + ".json")

    def _auth_guard_path(self) -> str:
        # The token itself is never written. A random 64-character token cannot
        # be recovered from this truncated SHA-256 identifier.
        token_id = hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:16]
        return os.path.join(self.cache_dir, f"auth_guard_{token_id}.json")

    def _auth_guard_remaining(self) -> int:
        try:
            with open(self._auth_guard_path(), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            blocked_until = float(data.get("blocked_until") or 0)
        except Exception:
            return 0
        return max(0, int(blocked_until - time.time() + 0.999))

    def _check_auth_guard(self) -> None:
        remaining = self._auth_guard_remaining()
        if remaining <= 0:
            return
        minutes, seconds = divmod(remaining, 60)
        raise RuntimeError(
            "Nouvelle authentification Metron bloquée localement après un HTTP 401. "
            f"Attendre encore {minutes} min {seconds:02d} s avant de réessayer. "
            "Metron bannit une adresse IP pendant 24 h après 3 erreurs 401 en 5 minutes."
        )

    def _record_auth_failure(self) -> None:
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._auth_guard_path(), "w", encoding="utf-8") as handle:
                json.dump(
                    {"blocked_until": time.time() + AUTH_FAILURE_GUARD_SECONDS, "status": 401},
                    handle,
                    sort_keys=True,
                )
        except Exception:
            return

    def _clear_auth_guard(self) -> None:
        try:
            os.remove(self._auth_guard_path())
        except FileNotFoundError:
            return
        except OSError:
            return

    def _read_cache(self, cache_key: str, ttl_seconds: int) -> Optional[Any]:
        if not self.cache_enabled:
            return None
        path = self._cache_path(cache_key)
        try:
            if not os.path.isfile(path):
                return None
            if ttl_seconds > 0 and time.time() - os.path.getmtime(path) > ttl_seconds:
                return None
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def _write_cache(self, cache_key: str, data: Any) -> None:
        if not self.cache_enabled:
            return
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cache_path(cache_key), "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception:
            return

    def _get_json(self, path: str, query: Optional[Dict[str, Any]] = None, ttl_seconds: int = LOOKUP_CACHE_TTL_SECONDS) -> Any:
        if not self.token:
            raise ValueError("Jeton API Metron absent")
        url = self._url(path, query)
        cache_key = url
        cached = self._read_cache(cache_key, ttl_seconds)
        self.last_url = url
        if cached is not None:
            return cached
        self._check_auth_guard()
        req = request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": APP_USER_AGENT,
            },
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as response:
                self.last_rate_limit = {
                    key: value
                    for key in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")
                    if (value := response.headers.get(key)) is not None
                }
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            self._clear_auth_guard()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                self._record_auth_failure()
                message = (
                    "Jeton Metron refusé. Aucun nouvel essai ne sera autorisé pendant 5 minutes "
                    "car Metron bannit l’adresse IP 24 h après 3 erreurs 401 en 5 minutes."
                )
            elif exc.code == 403:
                message = "Accès Metron refusé pour ce jeton"
            elif exc.code == 429:
                message = "Limite de requêtes Metron atteinte"
            else:
                message = body[:500] or str(exc)
            wrapped = RuntimeError(f"Metron HTTP {exc.code}: {message}")
            retry_after = exc.headers.get("Retry-After") if exc.headers is not None else None
            try:
                wrapped.retry_after_seconds = float(retry_after) if retry_after not in (None, "") else None
            except (TypeError, ValueError):
                wrapped.retry_after_seconds = None
            raise wrapped from exc
        except error.URLError as exc:
            raise RuntimeError(f"Connexion Metron impossible: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"Timeout Metron: {exc}") from exc
        self._write_cache(cache_key, payload)
        return payload

    @staticmethod
    def _results(payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            rows = payload.get("results")
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
        return []

    def test(self) -> str:
        rows = self.search("Batman", limit=1)
        remaining = self.last_rate_limit.get("X-RateLimit-Remaining")
        suffix = f" — quota restant {remaining}" if remaining else ""
        return f"Metron OK — {len(rows)} résultat test{suffix}"

    def search(self, query: str, limit: int = 25) -> List[MetronSearchResult]:
        text = (query or "").strip()
        if not text:
            return []
        payload = self._get_json("/series/", {"q": text}, ttl_seconds=SEARCH_CACHE_TTL_SECONDS)
        return [search_result_from_series(item) for item in self._results(payload)[: max(1, int(limit or 25))]]

    def get_series(self, series_id: str) -> MetronCandidate:
        value = _safe_str(series_id)
        if not value:
            raise ValueError("ID série Metron vide")
        payload = self._get_json(f"/series/{parse.quote(value, safe='')}/", ttl_seconds=LOOKUP_CACHE_TTL_SECONDS)
        if not isinstance(payload, dict):
            raise RuntimeError("Réponse détail Metron invalide")
        return candidate_from_series(payload)

    @staticmethod
    def candidate_to_dict(candidate: MetronCandidate) -> Dict[str, Any]:
        return {
            "series_id": candidate.series_id,
            "title": candidate.title,
            "source_url": candidate.source_url,
            "series_metadata": candidate.series_metadata,
            "raw": candidate.raw,
        }
