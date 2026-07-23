from __future__ import annotations

import base64
import json
import mimetypes
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request

APP_USER_AGENT = "komga-db-tool/0.6.0"
KOMGA_LIST_LOCK = threading.Lock()


class HttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: Optional[int], body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status or '?'} {method} {url}\n{body}")


def _is_timeout_error(exc: Exception) -> bool:
    if not isinstance(exc, HttpError):
        return False
    if exc.status is not None:
        return False
    return "timeout" in str(exc.body or "").casefold() or "timed out" in str(exc.body or "").casefold()


def _is_transient_komga_sqlite_temp_error(exc: Exception) -> bool:
    if not isinstance(exc, HttpError):
        return False
    if exc.status != 500:
        return False
    body = str(exc.body or "").casefold()
    return "sqlite_error" in body and "no such table: temp_" in body


def _is_retryable_list_error(exc: Exception) -> bool:
    return _is_timeout_error(exc) or _is_transient_komga_sqlite_temp_error(exc)


def normalize_base_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise ValueError("URL vide")
    if not text.lower().startswith(("http://", "https://")):
        text = "http://" + text
    return text.rstrip("/")


def _page_items(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        content = data.get("content") or data.get("data") or data.get("items") or []
        if isinstance(content, list):
            return [x for x in content if isinstance(x, dict)]
    return []


def _page_last(data: Any) -> bool:
    if isinstance(data, dict):
        if "last" in data:
            return bool(data.get("last"))
        if "totalPages" in data and "number" in data:
            try:
                return int(data.get("number", 0)) >= int(data.get("totalPages", 1)) - 1
            except Exception:
                return True
    return True


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def clean_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove blank keys and preserve explicit nulls.

    Komga metadata PATCH semantics are important: omitted field = not changed,
    null = unset. UI fields therefore pass explicit None when the user writes
    <NULL>, and empty cells are omitted.
    """
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if not key:
            continue
        if value == "":
            continue
        out[key] = value
    return out


def parse_cell_value(value: str) -> Any:
    text = (value or "").strip()
    if text == "":
        return ""
    if text.upper() == "<NULL>":
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.startswith("[") or text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if ";" in text:
        return [x.strip() for x in text.split(";") if x.strip()]
    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return text
    return text


def _op_is(value: str) -> Dict[str, Any]:
    return {"operator": "is", "value": value}


def _condition_for(field: str, value: str) -> Dict[str, Any]:
    return {field: _op_is(value)}


def _all_of(conditions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    conds = [x for x in conditions if x]
    if not conds:
        return {}
    if len(conds) == 1:
        return conds[0]
    return {"allOf": conds}


@dataclass
class AuthConfig:
    mode: str = "api_key"  # api_key | basic | none
    api_key: str = ""
    username: str = ""
    password: str = ""


@dataclass
class LibraryItem:
    id: str
    name: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SeriesItem:
    id: str
    library_id: str
    title: str
    book_count: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BookItem:
    id: str
    series_id: str
    library_id: str
    title: str
    number: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionItem:
    id: str
    name: str
    ordered: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadlistItem:
    id: str
    name: str
    ordered: Optional[bool] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class ApiClient:
    def __init__(self, base_url: str, auth: Optional[AuthConfig] = None, timeout: int = 30):
        self.base_url = normalize_base_url(base_url)
        self.auth = auth or AuthConfig(mode="none")
        self.timeout = timeout
        self.last_request: Dict[str, Any] = {}

    def _headers(self, accept: str = "application/json") -> Dict[str, str]:
        headers = {"Accept": accept, "User-Agent": APP_USER_AGENT}
        if self.auth.mode == "api_key" and self.auth.api_key:
            headers["X-API-Key"] = self.auth.api_key
        elif self.auth.mode == "basic" and self.auth.username:
            token = f"{self.auth.username}:{self.auth.password}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
        return headers

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean:
                url += "?" + parse.urlencode(clean, doseq=True)
        return url

    def request_json(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        url = self._url(path, query)
        headers = self._headers()
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        self.last_request = {"method": method, "url": url, "body": body}
        req = request.Request(url, data=data, headers=headers, method=method)
        return self._open(req, timeout)

    def request_bytes(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> bytes:
        url = self._url(path, query)
        headers = self._headers(accept="image/*,*/*")
        self.last_request = {"method": method, "url": url, "body": None}
        req = request.Request(url, headers=headers, method=method)
        return self._open_bytes(req, timeout)

    def request_multipart_file(
        self,
        method: str,
        path: str,
        file_path: str,
        field_name: str = "file",
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(file_path)
        boundary = "----komga-db-tool-" + uuid.uuid4().hex
        filename = os.path.basename(file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        body = b"\r\n".join(
            [
                f"--{boundary}".encode(),
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode(),
                f"Content-Type: {content_type}".encode(),
                b"",
                file_bytes,
                f"--{boundary}--".encode(),
                b"",
            ]
        )
        url = self._url(path, query)
        headers = self._headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        self.last_request = {"method": method, "url": url, "body": f"multipart file={filename}"}
        req = request.Request(url, data=body, headers=headers, method=method)
        return self._open(req, timeout)

    def _open(self, req: request.Request, timeout: Optional[int]) -> Any:
        try:
            with request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                text = raw.decode("utf-8", errors="replace")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise HttpError(req.get_method(), req.full_url, exc.code, raw) from exc
        except error.URLError as exc:
            raise HttpError(req.get_method(), req.full_url, None, str(exc)) from exc
        except TimeoutError as exc:
            raise HttpError(req.get_method(), req.full_url, None, f"Timeout: {exc}") from exc

    def _open_bytes(self, req: request.Request, timeout: Optional[int]) -> bytes:
        try:
            with request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.read()
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise HttpError(req.get_method(), req.full_url, exc.code, raw) from exc
        except error.URLError as exc:
            raise HttpError(req.get_method(), req.full_url, None, str(exc)) from exc


class KomgaApi:
    """Komga 1.24.x API wrapper.

    No deprecated endpoints are used for listing series/books. The important
    change from 0.1.0 is that /series/list and /books/list now use Komga's
    Search DSL (`condition` + `fullTextSearch`) instead of ignored legacy keys
    such as `libraryIds`. Results are still client-side checked by libraryId so
    a bad filter can never silently show another library as if it matched.
    """

    def __init__(self, url: str, auth: Optional[AuthConfig] = None, timeout: int = 30):
        self.client = ApiClient(url, auth=auth, timeout=timeout)

    @property
    def last_request(self) -> Dict[str, Any]:
        return self.client.last_request

    def test(self) -> str:
        libs = self.libraries()
        return f"Komga OK — {len(libs)} bibliothèque(s)"

    def libraries(self) -> List[LibraryItem]:
        data = self.client.request_json("GET", "/api/v1/libraries")
        return [LibraryItem(id=safe_str(x.get("id")), name=safe_str(x.get("name") or x.get("id")), raw=x) for x in _page_items(data)]

    def _list_paged(self, path: str, body: Dict[str, Any], sort: str, page_size: int = 500) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        page = 0
        while True:
            last_timeout: Optional[HttpError] = None
            for attempt in range(3):
                try:
                    data = self.client.request_json(
                        "POST",
                        path,
                        body=body,
                        query={"page": page, "size": page_size, "sort": sort},
                    )
                    break
                except HttpError as exc:
                    if not _is_retryable_list_error(exc) or attempt >= 2:
                        raise
                    last_timeout = exc
                    time.sleep(0.5 * (attempt + 1))
            else:
                raise last_timeout or RuntimeError(f"Timeout répété pour {path} page {page}")
            rows.extend(_page_items(data))
            if _page_last(data):
                break
            page += 1
            if page > 2000:
                raise RuntimeError(f"Pagination trop longue pour {path}")
        return rows

    def _get_paged(
        self,
        path: str,
        sort: str,
        page_size: int = 500,
        *,
        timeout: Optional[int] = None,
        max_pages: int = 2000,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        page = 0
        while True:
            data = self.client.request_json(
                "GET",
                path,
                query={"page": page, "size": page_size, "sort": sort},
                timeout=timeout,
            )
            rows.extend(_page_items(data))
            if _page_last(data):
                break
            page += 1
            if page > max_pages:
                raise RuntimeError(f"Pagination trop longue pour {path}")
        return rows

    def _list_with_fallback(self, path: str, bodies: List[Dict[str, Any]], sort: str, page_size: int) -> List[Dict[str, Any]]:
        errors: List[str] = []
        requested_size = max(1, int(page_size or 500))
        page_sizes: List[int] = []
        for candidate_size in (requested_size, 200, 100, 50, 25):
            size = min(requested_size, candidate_size)
            if size >= 1 and size not in page_sizes:
                page_sizes.append(size)
        for body in bodies:
            body_rejected = False
            for adaptive_page_size in page_sizes:
                with KOMGA_LIST_LOCK:
                    try:
                        return self._list_paged(path, body, sort=sort, page_size=adaptive_page_size)
                    except HttpError as exc:
                        if exc.status == 400:
                            errors.append(f"body={body!r}: {exc.body[:300]}")
                            body_rejected = True
                            break
                        if _is_retryable_list_error(exc) and adaptive_page_size > page_sizes[-1]:
                            errors.append(f"body={body!r}, page_size={adaptive_page_size}: {exc.body[:160]}")
                            continue
                        raise
            if body_rejected:
                continue
        raise RuntimeError(f"Aucun body de recherche compatible pour {path}. Erreurs: {' | '.join(errors)}")

    def series(self, library_id: Optional[str] = None, search: str = "", page_size: int = 500) -> List[SeriesItem]:
        conditions = []
        if library_id:
            conditions.append(_condition_for("libraryId", library_id))
        body: Dict[str, Any] = {}
        condition = _all_of(conditions)
        if condition:
            body["condition"] = condition
        if search:
            body["fullTextSearch"] = search

        fallback_bodies = [body]
        # Compatibility fallback only if the Search DSL changes: still post/list, never deprecated GET.
        legacy_body = {"libraryIds": [library_id] if library_id else [], "search": search}
        fallback_bodies.append({k: v for k, v in legacy_body.items() if v})
        fallback_bodies.append({"fullTextSearch": search} if search else {})

        data_rows = self._list_with_fallback("/api/v1/series/list", fallback_bodies, "metadata.titleSort,asc", page_size)
        out: List[SeriesItem] = []
        for item in data_rows:
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            lib = item.get("library") if isinstance(item.get("library"), dict) else {}
            item_lib = safe_str(item.get("libraryId") or lib.get("id") or "")
            if library_id and item_lib and item_lib != library_id:
                continue
            out.append(
                SeriesItem(
                    id=safe_str(item.get("id")),
                    library_id=item_lib or safe_str(library_id or ""),
                    title=safe_str(meta.get("title") or item.get("name") or item.get("title") or item.get("id")),
                    book_count=safe_str(item.get("bookCount") or item.get("booksCount") or ""),
                    metadata=meta,
                    raw=item,
                )
            )
        return out

    def books(
        self,
        library_id: Optional[str] = None,
        series_id: Optional[str] = None,
        search: str = "",
        page_size: int = 1000,
        *,
        direct_series_only: bool = False,
        timeout: Optional[int] = None,
    ) -> List[BookItem]:
        def to_items(data_rows: List[Dict[str, Any]]) -> List[BookItem]:
            out: List[BookItem] = []
            needle = search.strip().casefold()
            for item in data_rows:
                meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                series = item.get("series") if isinstance(item.get("series"), dict) else {}
                lib = item.get("library") if isinstance(item.get("library"), dict) else {}
                item_series = safe_str(item.get("seriesId") or series.get("id") or "")
                item_lib = safe_str(item.get("libraryId") or lib.get("id") or "")
                title = safe_str(meta.get("title") or item.get("name") or item.get("id"))
                number = safe_str(meta.get("number") or meta.get("numberSort") or "")
                if library_id and item_lib and item_lib != library_id:
                    continue
                if series_id and item_series and item_series != series_id:
                    continue
                if needle:
                    haystack = " ".join(
                        str(part or "")
                        for part in (
                            title,
                            number,
                            item.get("name"),
                            item.get("url"),
                        )
                    ).casefold()
                    if needle not in haystack:
                        continue
                out.append(
                    BookItem(
                        id=safe_str(item.get("id")),
                        series_id=item_series or safe_str(series_id or ""),
                        library_id=item_lib or safe_str(library_id or ""),
                        title=title,
                        number=number,
                        metadata=meta,
                        raw=item,
                    )
                )
            return out

        if series_id:
            try:
                data_rows = self._get_paged(
                    f"/api/v1/series/{parse.quote(series_id, safe='')}/books",
                    "metadata.numberSort,asc",
                    min(max(1, int(page_size or 500)), 500),
                    timeout=timeout,
                    max_pages=20,
                )
                return to_items(data_rows)
            except HttpError as exc:
                if direct_series_only or exc.status not in {400, 404, 405}:
                    raise
            if direct_series_only:
                raise RuntimeError(f"Endpoint livres de série indisponible pour {series_id}")

        conditions = []
        if library_id:
            conditions.append(_condition_for("libraryId", library_id))
        if series_id:
            conditions.append(_condition_for("seriesId", series_id))
        body: Dict[str, Any] = {}
        condition = _all_of(conditions)
        if condition:
            body["condition"] = condition
        if search:
            body["fullTextSearch"] = search

        fallback_bodies = [body]
        legacy_body = {"libraryIds": [library_id] if library_id else [], "seriesIds": [series_id] if series_id else [], "search": search}
        fallback_bodies.append({k: v for k, v in legacy_body.items() if v})
        fallback_bodies.append({"fullTextSearch": search} if search else {})

        data_rows = self._list_with_fallback("/api/v1/books/list", fallback_bodies, "metadata.numberSort,asc", page_size)
        return to_items(data_rows)

    def get_series(self, series_id: str) -> Dict[str, Any]:
        return self.client.request_json("GET", f"/api/v1/series/{parse.quote(series_id, safe='')}")

    def get_book(self, book_id: str) -> Dict[str, Any]:
        return self.client.request_json("GET", f"/api/v1/books/{parse.quote(book_id, safe='')}")

    def update_series_metadata(self, series_id: str, metadata: Dict[str, Any]) -> Any:
        return self.client.request_json("PATCH", f"/api/v1/series/{parse.quote(series_id, safe='')}/metadata", body=metadata)

    def update_book_metadata(self, book_id: str, metadata: Dict[str, Any]) -> Any:
        return self.client.request_json("PATCH", f"/api/v1/books/{parse.quote(book_id, safe='')}/metadata", body=metadata)

    def update_books_metadata_batch(self, updates_by_book_id: Dict[str, Dict[str, Any]]) -> Any:
        return self.client.request_json("PATCH", "/api/v1/books/metadata", body=updates_by_book_id)

    def collections(self) -> List[CollectionItem]:
        data = self.client.request_json("GET", "/api/v1/collections", query={"unpaged": "true"})
        return [CollectionItem(id=safe_str(x.get("id")), name=safe_str(x.get("name") or x.get("title") or x.get("id")), ordered=x.get("ordered"), raw=x) for x in _page_items(data)]

    def get_collection(self, collection_id: str) -> Dict[str, Any]:
        return self.client.request_json("GET", f"/api/v1/collections/{parse.quote(collection_id, safe='')}")

    def create_collection(self, payload: Dict[str, Any]) -> Any:
        return self.client.request_json("POST", "/api/v1/collections", body=payload)

    def update_collection(self, collection_id: str, payload: Dict[str, Any]) -> Any:
        return self.client.request_json("PATCH", f"/api/v1/collections/{parse.quote(collection_id, safe='')}", body=payload)

    def collection_series(self, collection_id: str) -> List[Dict[str, Any]]:
        data = self.client.request_json("GET", f"/api/v1/collections/{parse.quote(collection_id, safe='')}/series", query={"unpaged": "true"})
        return _page_items(data)


    def series_collections(self, series_id: str) -> List[Dict[str, Any]]:
        data = self.client.request_json("GET", f"/api/v1/series/{parse.quote(series_id, safe='')}/collections", query={"unpaged": "true"})
        return _page_items(data)

    def book_readlists(self, book_id: str) -> List[Dict[str, Any]]:
        data = self.client.request_json("GET", f"/api/v1/books/{parse.quote(book_id, safe='')}/readlists", query={"unpaged": "true"})
        return _page_items(data)

    def readlists(self) -> List[ReadlistItem]:
        data = self.client.request_json("GET", "/api/v1/readlists", query={"unpaged": "true"})
        return [ReadlistItem(id=safe_str(x.get("id")), name=safe_str(x.get("name") or x.get("title") or x.get("id")), ordered=x.get("ordered"), raw=x) for x in _page_items(data)]

    def get_readlist(self, readlist_id: str) -> Dict[str, Any]:
        return self.client.request_json("GET", f"/api/v1/readlists/{parse.quote(readlist_id, safe='')}")

    def create_readlist(self, payload: Dict[str, Any]) -> Any:
        return self.client.request_json("POST", "/api/v1/readlists", body=payload)

    def update_readlist(self, readlist_id: str, payload: Dict[str, Any]) -> Any:
        return self.client.request_json("PATCH", f"/api/v1/readlists/{parse.quote(readlist_id, safe='')}", body=payload)

    def readlist_books(self, readlist_id: str) -> List[Dict[str, Any]]:
        data = self.client.request_json("GET", f"/api/v1/readlists/{parse.quote(readlist_id, safe='')}/books", query={"unpaged": "true"})
        return _page_items(data)

    def thumbnail_bytes(self, target_type: str, target_id: str) -> bytes:
        target_id_q = parse.quote(target_id, safe="")
        if target_type == "series":
            path = f"/api/v1/series/{target_id_q}/thumbnail"
        elif target_type == "book":
            path = f"/api/v1/books/{target_id_q}/thumbnail"
        elif target_type == "collection":
            path = f"/api/v1/collections/{target_id_q}/thumbnail"
        elif target_type == "readlist":
            path = f"/api/v1/readlists/{target_id_q}/thumbnail"
        else:
            raise ValueError(f"Type poster inconnu: {target_type}")
        return self.client.request_bytes("GET", path)

    def list_thumbnails(self, target_type: str, target_id: str) -> List[Dict[str, Any]]:
        base = self._thumbnail_base_path(target_type, target_id)
        data = self.client.request_json("GET", base)
        return _page_items(data)

    def add_thumbnail(self, target_type: str, target_id: str, file_path: str) -> Any:
        base = self._thumbnail_base_path(target_type, target_id)
        return self.client.request_multipart_file("POST", base, file_path=file_path)

    def select_thumbnail(self, target_type: str, target_id: str, thumbnail_id: str) -> Any:
        base = self._thumbnail_base_path(target_type, target_id)
        thumb = parse.quote(thumbnail_id, safe="")
        path = f"{base}/{thumb}/selected"
        try:
            return self.client.request_json("PUT", path)
        except HttpError as exc:
            if exc.status not in {404, 405}:
                raise
            return self.client.request_json("POST", path)

    def _thumbnail_base_path(self, target_type: str, target_id: str) -> str:
        q = parse.quote(target_id, safe="")
        if target_type == "series":
            return f"/api/v1/series/{q}/thumbnails"
        if target_type == "book":
            return f"/api/v1/books/{q}/thumbnails"
        if target_type == "collection":
            return f"/api/v1/collections/{q}/thumbnails"
        if target_type == "readlist":
            return f"/api/v1/readlists/{q}/thumbnails"
        raise ValueError(f"Type poster inconnu: {target_type}")


class KomfApi:
    def __init__(self, url: str, timeout: int = 30):
        self.client = ApiClient(url, auth=AuthConfig(mode="none"), timeout=timeout)

    def test(self) -> str:
        for path in ("/komga/providers", "/metadata/providers", "/api/komga/metadata/providers", "/api/metadata/providers"):
            try:
                data = self.client.request_json("GET", path)
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    count = len(data)
                else:
                    count = 1
                return f"Komf OK — {count} provider(s) via {path}"
            except HttpError as exc:
                if exc.status in (404, 405) or exc.status is None:
                    continue
                raise
        raise RuntimeError("Aucun endpoint provider Komf compatible n'a répondu")

    def search(self, name: str, library_id: str = "", series_id: str = "") -> Tuple[Any, str]:
        query = {"name": name}
        if library_id:
            query["libraryId"] = library_id
        if series_id:
            query["seriesId"] = series_id
        for path in ("/komga/search", "/metadata/search", "/api/komga/metadata/search", "/api/metadata/search"):
            try:
                return self.client.request_json("GET", path, query=query), path
            except HttpError as exc:
                if exc.status in (404, 405) or exc.status is None:
                    continue
                raise
        raise RuntimeError("Aucun endpoint recherche Komf compatible n'a répondu")

    def identify(self, library_id: str, series_id: str, provider: str, provider_series_id: str) -> Any:
        payload = {
            "libraryId": library_id,
            "seriesId": series_id,
            "provider": provider,
            "providerSeriesId": provider_series_id,
        }
        for path in ("/komga/identify", "/metadata/identify", "/api/komga/metadata/identify", "/api/metadata/identify"):
            try:
                return self.client.request_json("POST", path, body=payload, timeout=180)
            except HttpError as exc:
                if exc.status in (404, 405) or exc.status is None:
                    continue
                raise
        raise RuntimeError("Aucun endpoint identify Komf compatible n'a répondu")
