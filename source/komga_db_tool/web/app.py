from __future__ import annotations

import hmac
import os
import tempfile
import csv
import io
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..runtime import SecretRedactor
from ..csv_tools import DIRECTOR_COLUMNS, SPECIALIZED_COLUMNS, book_inventory_row, parse_director_actions, read_csv
from ..metadata_quality import is_blank_metadata_value, is_low_value_summary
from ..kora.constants import KORA_GENRE_LABELS, KORA_GENRES, MAX_KORA_GENRES
from ..kora.tag_logic import extract_kora_genres, merge_series_tags_for_genres, validate_genres
from ..kora.local_exclusions import LocalExclusionsStore
from ..kora.cache import CacheStore
from ..kora.models import PendingChange
from ..bedetheque import match_album_rows
from ..enrichment_history import EnrichmentHistoryStore, format_search_timestamp
from .jobs import jobs
from .analysis import collection_path_suggestions
from .book_explorer import analyze_book_rows, list_book_rows
from .release_tracking import (
    apply_next_release_automation,
    apply_guided_release_tracking,
    automate_next_releases,
    prepare_next_release_automation,
    prepare_guided_release_tracking,
    run_guided_release_tracking_automation,
    run_next_release_automation,
    scan_next_releases,
    scan_release_tracking,
)
from .series_fix import scan_series_fix
from .services import operations, public_candidate
from .session import WebSessionStore, public_dataclass
from .reference import REFERENCE_ID, WEB_API_VERSION, public_reference


class ConnectionRequest(BaseModel):
    base_url: HttpUrl
    auth_mode: Literal["api_key", "basic", "none"] = "none"
    api_key: str = Field(default="", max_length=4096)
    username: str = Field(default="", max_length=1024)
    password: str = Field(default="", max_length=4096)
    timeout: int = Field(default=30, ge=3, le=300)


class SourceSettingsRequest(BaseModel):
    manga_news_url: str | None = Field(default=None, max_length=2048)
    manga_news_token: str | None = Field(default=None, max_length=4096)
    mangabaka_url: str | None = Field(default=None, max_length=2048)
    comicvine_url: str | None = Field(default=None, max_length=2048)
    comicvine_api_key: str | None = Field(default=None, max_length=4096)
    timeout: int | None = Field(default=None, ge=3, le=300)
    bedetheque_csv_only: bool | None = None


class MetadataPreviewRequest(BaseModel):
    target_type: Literal["series", "book"]
    target_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any]
    source: str = Field(default="webui", max_length=100)


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class ResourcePreviewRequest(BaseModel):
    resource_type: Literal["collection", "readlist"]
    operation: Literal["create", "update"]
    target_id: str = Field(default="", max_length=256)
    payload: dict[str, Any]


class EnrichmentHistoryRequest(BaseModel):
    source: str = Field(default="", max_length=100)
    series_ids: list[str] = Field(default_factory=list, max_length=5000)


class NextReleaseScanRequest(BaseModel):
    source: Literal["manga_news", "mangabaka"]
    series_ids: list[str] = Field(min_length=1, max_length=5000)


class NextReleaseAutoRequest(BaseModel):
    source: Literal["manga_news", "mangabaka"]
    library_id: str = Field(default="", max_length=256)
    confirmed: bool = False


class ReleaseTrackingScanRequest(BaseModel):
    source: Literal["auto", "bedetheque", "manga_news", "mangabaka", "comicvine"]
    series_ids: list[str] = Field(min_length=1, max_length=5000)


class ReleaseTrackingGuidedPreviewRequest(BaseModel):
    source: Literal["bedetheque", "manga_news", "mangabaka", "comicvine"]
    library_id: str = Field(min_length=1, max_length=256)


class ReleaseTrackingGuidedApplyRequest(BaseModel):
    preview_job_id: str = Field(min_length=1, max_length=128)
    confirmed: bool = False


class AutomationReleaseTrackingRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=256)


class AutomationReleaseTrackingConfirmRequest(BaseModel):
    preview_job_id: str = Field(min_length=1, max_length=128)
    confirmed: bool = False


class KoraGenresRequest(BaseModel):
    genres: list[str] = Field(max_length=MAX_KORA_GENRES)


class PosterSelectRequest(BaseModel):
    target_type: Literal["series", "book", "collection", "readlist"]
    target_id: str = Field(min_length=1, max_length=256)
    thumbnail_id: str = Field(min_length=1, max_length=256)
    confirmed: bool = False


class PosterUrlRequest(BaseModel):
    url: HttpUrl
    confirmed: bool = False


class AuditRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=256)
    include_books: bool = True


class BulkMembershipRequest(BaseModel):
    resource_type: Literal["collection", "readlist"]
    resource_ids: list[str] = Field(min_length=1, max_length=1000)
    member_ids: list[str] = Field(min_length=1, max_length=10000)
    mode: Literal["add", "remove"]


class TokenListRequest(BaseModel):
    tokens: list[str] = Field(min_length=1, max_length=10000)


class CsvActionsRequest(BaseModel):
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=10000)


class KoraExclusionRequest(BaseModel):
    series_id: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=1000)
    library_name: str = Field(default="", max_length=1000)


class TomeMatchRequest(BaseModel):
    series_id: str = Field(min_length=1, max_length=256)
    library_id: str = Field(default="", max_length=256)
    albums: list[dict[str, str]] = Field(max_length=5000)


class BookExplorerAnalyzeRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=256)
    book_ids: list[str] = Field(min_length=1, max_length=5000)
    source: Literal["auto", "bedetheque", "manga_news", "mangabaka", "comicvine"] = "auto"


class MatchingSettingsRequest(BaseModel):
    title_score_min: float = Field(ge=0, le=1)
    loaded_title_score_min: float = Field(ge=0, le=1)
    exact_title_score_min: float = Field(ge=0, le=1)
    tome_pair_score_min: float = Field(ge=0, le=1)
    tome_match_min_books: int = Field(ge=1, le=10000)
    tome_match_min_ratio: float = Field(ge=0, le=1)
    tome_match_min_avg_score: float = Field(ge=0, le=1)
    max_bedetheque_candidates: int = Field(ge=1, le=1000)


class KoraSyncRequest(BaseModel):
    library_ids: list[str] = Field(default=[], max_length=1000)


class KoraPendingRequest(BaseModel):
    series_id: str = Field(min_length=1, max_length=256)
    library_name: str = Field(default="", max_length=1000)
    title: str = Field(default="", max_length=1000)
    genres: list[str] = Field(max_length=MAX_KORA_GENRES)
    note: str = Field(default="", max_length=2000)


class SeriesFixRequest(BaseModel):
    library_id: str = Field(min_length=1, max_length=256)
    mode: Literal["auto", "folder", "files", "sort"] = "auto"


KORA_EXCLUSIONS = LocalExclusionsStore(
    Path(os.getenv("KOMGA_TOOLKIT_DATA_DIR") or ".komga_db_tool_cache/web") / "kora_exclusions.json"
)
WEB_DATA_DIR = Path(os.getenv("KOMGA_TOOLKIT_DATA_DIR") or ".komga_db_tool_cache/web")
ENRICHMENT_HISTORY = EnrichmentHistoryStore(WEB_DATA_DIR / "enrichment_history.sqlite")
KORA_CACHE = CacheStore(WEB_DATA_DIR / "kora.sqlite")


session_store = WebSessionStore()
automation_bearer = HTTPBearer(auto_error=False)
app = FastAPI(
    title="Komga Toolkit Web API",
    version=WEB_API_VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        host.strip()
        for host in os.getenv(
            "WEB_ALLOWED_HOSTS",
            "localhost,127.0.0.1,host.docker.internal,testserver",
        ).split(",")
        if host.strip()
    ],
)


def api_or_401():
    try:
        return session_store.require_api()
    except LookupError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _automation_token() -> str:
    token_file = str(os.getenv("KOMGA_TOOLKIT_AUTOMATION_TOKEN_FILE") or "").strip()
    if token_file:
        if Path(token_file).name.casefold() == "config.json":
            raise HTTPException(status_code=503, detail="Fichier de jeton interdit")
        try:
            return Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail="Jeton d'automatisation indisponible",
            ) from exc
    return str(os.getenv("KOMGA_TOOLKIT_AUTOMATION_TOKEN") or "").strip()


def require_automation_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(automation_bearer),
) -> None:
    expected = _automation_token()
    if len(expected) < 24:
        raise HTTPException(status_code=503, detail="API d'automatisation non configurée")
    supplied = (
        credentials.credentials
        if credentials and credentials.scheme.casefold() == "bearer"
        else ""
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Jeton d'automatisation invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )


def safe_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=SecretRedactor.redact(exc))


def domain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=SecretRedactor.redact(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=SecretRedactor.redact(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=SecretRedactor.redact(exc))
    return safe_error(exc)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "komga-toolkit-web",
        "version": WEB_API_VERSION,
        "reference": REFERENCE_ID,
    }


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


@app.get("/api/reference")
def reference(response: Response) -> dict[str, object]:
    response.headers.update(NO_CACHE_HEADERS)
    return public_reference()


@app.get("/api/settings/public")
def public_settings() -> dict[str, str]:
    return {
        "manga_news_base_url": os.getenv("MANGA_NEWS_BASE_URL", ""),
        "data_dir": os.getenv("KOMGA_TOOLKIT_DATA_DIR", "/data"),
    }


@app.get("/api/sources/settings")
def source_settings() -> dict:
    return session_store.public_sources()


@app.put("/api/sources/settings")
def update_source_settings(payload: SourceSettingsRequest) -> dict:
    try:
        values = payload.model_dump(exclude_none=True)
        for key in ("manga_news_url", "mangabaka_url", "comicvine_url"):
            value = values.get(key)
            if value and ("@" in value.split("//", 1)[-1].split("/", 1)[0]):
                raise ValueError(f"{key} ne doit contenir aucun identifiant")
        return session_store.configure_sources(values)
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/matching/settings")
def matching_settings() -> dict:
    return session_store.public_matching()


@app.put("/api/matching/settings")
def update_matching_settings(payload: MatchingSettingsRequest) -> dict:
    try:
        return session_store.configure_matching(payload.model_dump())
    except Exception as exc:
        raise domain_error(exc) from exc


def _enrichment_history(source: str, series_ids: list[str]) -> list[dict[str, str]]:
    ids = [value for value in series_ids if value][:5000]
    values = (
        ENRICHMENT_HISTORY.last_searches(source, ids)
        if source
        else ENRICHMENT_HISTORY.last_searches_any_source(ids)
    )
    return [
        {"series_id": series_id, "searched_at": timestamp.isoformat(), "label": format_search_timestamp(timestamp)}
        for series_id, timestamp in sorted(values.items())
    ]


@app.get("/api/enrichment/history")
def enrichment_history(
    source: str = Query(default="", max_length=100),
    series_ids: list[str] = Query(default=[]),
) -> list[dict[str, str]]:
    return _enrichment_history(source, series_ids)


@app.post("/api/enrichment/history")
def enrichment_history_batch(payload: EnrichmentHistoryRequest) -> list[dict[str, str]]:
    return _enrichment_history(payload.source, payload.series_ids)


@app.post("/api/sources/bedetheque/csv")
async def upload_bedetheque_csv(request: Request) -> dict:
    data = await request.body()
    if not data or len(data) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV Bedetheque vide ou supérieur à 100 Mio")
    try:
        data_dir = Path(os.getenv("KOMGA_TOOLKIT_DATA_DIR") or ".komga_db_tool_cache/web")
        upload_dir = data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / "bedetheque.csv"
        temporary = upload_dir / "bedetheque.csv.tmp"
        temporary.write_bytes(data)
        temporary.replace(path)
        return session_store.configure_sources({"bedetheque_csv_path": str(path), "bedetheque_csv_only": True})
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/session")
def session_state() -> dict:
    return session_store.public_state()


@app.post("/api/session/connect")
def connect(payload: ConnectionRequest) -> dict:
    if payload.base_url.username or payload.base_url.password:
        raise HTTPException(
            status_code=422,
            detail="L'URL Komga ne doit contenir aucun identifiant.",
        )
    try:
        return session_store.connect(
            base_url=str(payload.base_url),
            auth_mode=payload.auth_mode,
            api_key=payload.api_key,
            username=payload.username,
            password=payload.password,
            timeout=payload.timeout,
        )
    except Exception as exc:
        raise safe_error(exc) from exc


@app.delete("/api/session")
def disconnect() -> dict:
    return session_store.disconnect()


@app.get("/api/libraries")
def libraries() -> list[dict]:
    try:
        return [public_dataclass(row) for row in api_or_401().libraries()]
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/series")
def series(
    library_id: str = Query(default="", max_length=256),
    search: str = Query(default="", max_length=500),
) -> list[dict]:
    try:
        return [
            public_dataclass(row)
            for row in api_or_401().series(library_id=library_id or None, search=search)
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/series/{series_id}")
def series_detail(series_id: str) -> dict:
    try:
        return api_or_401().get_series(series_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/series/{series_id}/books")
def books(series_id: str, library_id: str = Query(default="", max_length=256)) -> list[dict]:
    try:
        return [
            public_dataclass(row)
            for row in api_or_401().books(
                library_id=library_id or None,
                series_id=series_id,
                direct_series_only=True,
            )
        ]
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/books/{book_id}")
def book_detail(book_id: str) -> dict:
    try:
        return api_or_401().get_book(book_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/book-explorer")
def book_explorer(
    library_id: str = Query(min_length=1, max_length=256),
    q: str = Query(default="", max_length=500),
    added_days: int = Query(default=0, ge=0, le=36500),
    language: str = Query(default="", max_length=32),
    series_status: str = Query(default="ALL", max_length=64),
    source_filter: str = Query(default="all", max_length=100),
    missing_field: str = Query(default="", max_length=100),
    empty_summary: bool = Query(default=False),
    sort_field: Literal["added_at", "series_title", "title", "number", "release_date"] = "added_at",
    descending: bool = Query(default=True),
) -> dict[str, Any]:
    try:
        added_since = (
            datetime.now(timezone.utc) - timedelta(days=added_days)
            if added_days
            else None
        )
        return list_book_rows(
            api_or_401(),
            library_id,
            query=q,
            added_since=added_since,
            language=language,
            series_status=series_status,
            source_filter=source_filter,
            missing_field=missing_field,
            empty_summary=empty_summary,
            sort_field=sort_field,
            descending=descending,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/book-explorer/analyze")
def start_book_explorer_analysis(payload: BookExplorerAnalyzeRequest) -> dict:
    api = api_or_401()

    def action(progress: Any, cancelled: Any) -> list[dict[str, Any]]:
        clients = {
            "bedetheque": session_store.bedetheque_client(),
            "manga_news": session_store.manga_news_client(),
            "comicvine": session_store.comicvine_client(),
        }
        return analyze_book_rows(
            api,
            payload.library_id,
            payload.book_ids,
            payload.source,
            clients,
            progress,
            cancelled,
            ENRICHMENT_HISTORY.record_search,
        )

    return jobs.submit("Analyse enrichissement des tomes", action).public()


@app.post("/api/metadata/preview")
def preview_metadata(payload: MetadataPreviewRequest) -> dict:
    try:
        return operations.preview_metadata(
            api_or_401(),
            payload.target_type,
            payload.target_id,
            payload.payload,
            payload.source,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/metadata/apply")
def apply_metadata(payload: TokenRequest) -> dict:
    try:
        return operations.apply_metadata(api_or_401(), payload.token)
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/collections")
def collections() -> list[dict]:
    try:
        return [public_dataclass(row) for row in api_or_401().collections()]
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/collections/{collection_id}")
def collection_detail(collection_id: str) -> dict:
    try:
        api = api_or_401()
        return {"collection": api.get_collection(collection_id), "series": api.collection_series(collection_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/collections/{collection_id}/suggestions")
def collection_suggestions(
    collection_id: str,
    library_id: str = Query(default="", max_length=256),
    query: str = Query(default="", max_length=1000),
) -> dict:
    try:
        return collection_path_suggestions(api_or_401(), collection_id, library_id, query)
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/series/{series_id}/collections")
def collections_for_series(series_id: str) -> list[dict]:
    try:
        return api_or_401().series_collections(series_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/readlists")
def readlists() -> list[dict]:
    try:
        return [public_dataclass(row) for row in api_or_401().readlists()]
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/readlists/{readlist_id}")
def readlist_detail(readlist_id: str) -> dict:
    try:
        api = api_or_401()
        return {"readlist": api.get_readlist(readlist_id), "books": api.readlist_books(readlist_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/books/{book_id}/readlists")
def readlists_for_book(book_id: str) -> list[dict]:
    try:
        return api_or_401().book_readlists(book_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/resources/preview")
def preview_resource(payload: ResourcePreviewRequest) -> dict:
    try:
        return operations.preview_resource(
            api_or_401(),
            payload.resource_type,
            payload.operation,
            payload.target_id,
            payload.payload,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/resources/apply")
def apply_resource(payload: TokenRequest) -> dict:
    try:
        return operations.apply_resource(api_or_401(), payload.token)
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/resources/bulk/preview")
def preview_bulk_membership(payload: BulkMembershipRequest) -> dict:
    try:
        api = api_or_401()
        key = "seriesIds" if payload.resource_type == "collection" else "bookIds"
        previews: list[dict[str, Any]] = []
        for resource_id in payload.resource_ids:
            current = api.get_collection(resource_id) if payload.resource_type == "collection" else api.get_readlist(resource_id)
            existing = [str(value) for value in current.get(key, []) if str(value)]
            if payload.mode == "add":
                merged = existing + [value for value in payload.member_ids if value not in existing]
            else:
                removed = set(payload.member_ids)
                merged = [value for value in existing if value not in removed]
            update = {"name": current.get("name") or current.get("title") or resource_id, key: merged}
            if "ordered" in current:
                update["ordered"] = current.get("ordered")
            previews.append(operations.preview_resource(api, payload.resource_type, "update", resource_id, update))
        return {"previews": previews, "tokens": [row["token"] for row in previews]}
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/resources/bulk/apply")
def apply_bulk_resources(payload: TokenListRequest) -> dict:
    api = api_or_401()
    rows: list[dict[str, Any]] = []
    for token in payload.tokens:
        try:
            rows.append(operations.apply_resource(api, token))
        except Exception as exc:
            rows.append({"status": "error", "error": SecretRedactor.redact(exc)})
    return {"rows": rows, "applied": sum(1 for row in rows if row.get("status") == "applied")}


@app.get("/api/resource-memberships/{resource_type}")
def resource_memberships(resource_type: Literal["collection", "readlist"]) -> dict:
    """Return member IDs once so local 'Sans collection/readlist' filters stay fast."""
    try:
        api = api_or_401()
        member_ids: set[str] = set()
        resources = api.collections() if resource_type == "collection" else api.readlists()
        for resource in resources:
            members = api.collection_series(resource.id) if resource_type == "collection" else api.readlist_books(resource.id)
            member_ids.update(str(row.get("id") or "") for row in members if str(row.get("id") or ""))
        return {"resource_type": resource_type, "resource_count": len(resources), "member_ids": sorted(member_ids)}
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/readlists/completeness/{library_id}")
def readlist_completeness(
    library_id: str,
    search: str = Query(default="", max_length=500),
    ignore_single: bool = Query(default=True),
    show_complete: bool = Query(default=False),
) -> dict:
    try:
        api = api_or_401()
        library_books = api.books(library_id=library_id, page_size=1000)
        books_by_series: dict[str, list[Any]] = {}
        for book in library_books:
            books_by_series.setdefault(book.series_id, []).append(book)
        issues: list[dict[str, Any]] = []
        readlists = api.readlists()
        needle = search.strip().casefold()
        for readlist in readlists:
            if needle and needle not in str(readlist.name or "").casefold():
                continue
            members = api.readlist_books(readlist.id)
            member_ids = {str(row.get("id") or "") for row in members}
            counts: dict[str, int] = {}
            for row in members:
                series = row.get("series") if isinstance(row.get("series"), dict) else {}
                series_id = str(row.get("seriesId") or series.get("id") or "")
                if series_id:
                    counts[series_id] = counts.get(series_id, 0) + 1
            for series_id, count in counts.items():
                if ignore_single and count < 2:
                    continue
                missing = [book.id for book in books_by_series.get(series_id, []) if book.id not in member_ids]
                if missing or show_complete:
                    issues.append({"readlist_id": readlist.id, "readlist_name": readlist.name, "series_id": series_id, "present": count, "missing_book_ids": missing, "complete": not missing})
        return {"issues": issues, "analyzed_readlists": len(readlists)}
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/sources/{source}/test")
def test_source(source: Literal["manga_news", "mangabaka", "comicvine"]) -> dict:
    try:
        client = {
            "manga_news": session_store.manga_news_client,
            "mangabaka": session_store.mangabaka_client,
            "comicvine": session_store.comicvine_client,
        }[source]()
        return {"source": source, "message": client.test()}
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/sources/{source}/search")
def search_source(
    source: Literal["bedetheque", "manga_news", "mangabaka", "comicvine"],
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
    series_id: str = Query(default="", max_length=256),
    series_title: str = Query(default="", max_length=1000),
) -> list[dict]:
    try:
        if source == "bedetheque":
            rows = session_store.bedetheque_client().search(q)
        elif source == "manga_news":
            rows = session_store.manga_news_client().search(q, limit=limit)
        elif source == "mangabaka":
            rows = session_store.mangabaka_client().search(q, limit=limit)
        else:
            rows = session_store.comicvine_client().search(q, limit=limit)
        if series_id:
            ENRICHMENT_HISTORY.record_search(source, series_id, series_title or q)
        return [public_candidate(row) for row in rows]
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/sources/{source}/candidate")
def source_candidate(
    source: Literal["bedetheque", "manga_news", "mangabaka", "comicvine"],
    source_id: str = Query(default="", max_length=1000),
    url: str = Query(default="", max_length=3000),
) -> dict:
    try:
        if source == "bedetheque":
            candidate = session_store.bedetheque_client().scrape_series(url or source_id)
        elif source == "manga_news":
            client = session_store.manga_news_client()
            candidate = client.get_series_by_url(url) if url else client.get_series(source_id)
        elif source == "mangabaka":
            candidate = session_store.mangabaka_client().get_series(source_id)
        else:
            candidate = session_store.comicvine_client().get_volume(source_id)
        return public_candidate(candidate)
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/sources/bedetheque/album")
def bedetheque_album(url: str = Query(min_length=1, max_length=3000)) -> dict:
    try:
        return public_candidate(session_store.bedetheque_client().scrape_album(url))
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/sources/tomes/match")
def match_source_tomes(payload: TomeMatchRequest) -> list[dict]:
    try:
        books = api_or_401().books(
            library_id=payload.library_id or None,
            series_id=payload.series_id,
            direct_series_only=True,
        )
        rows = match_album_rows(books, payload.albums)
        settings = session_store.public_matching()
        minimum = float(settings["tome_pair_score_min"])
        for row in rows:
            if int(row.get("book_index", -1)) >= 0 and float(row.get("score", 0)) < minimum:
                row["confidence"] = "Sous le seuil WebUI"
                row["album_index"] = -1
        return rows
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/sources/manga_news/volume")
def manga_news_volume(
    series_slug: str = Query(default="", max_length=500),
    number: str = Query(default="", max_length=100),
    url: str = Query(default="", max_length=3000),
) -> dict:
    try:
        client = session_store.manga_news_client()
        candidate = client.get_volume_by_url(url) if url else client.get_volume_by_number(series_slug, number)
        return public_candidate(candidate)
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/sources/comicvine/issues")
def comicvine_issues(volume_id: str = Query(min_length=1, max_length=256)) -> list[dict]:
    try:
        return [public_candidate(row) for row in session_store.comicvine_client().list_volume_issues(volume_id)]
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/tools/series-fix/scan")
def start_series_fix(payload: SeriesFixRequest) -> dict:
    api = api_or_401()
    return jobs.submit(
        f"Series Fix {payload.mode}",
        lambda progress, cancelled: scan_series_fix(api, payload.library_id, payload.mode, progress, cancelled),
    ).public()


@app.post("/api/next-releases/scan")
def start_next_release_scan(payload: NextReleaseScanRequest) -> dict:
    api = api_or_401()
    manga_news = session_store.manga_news_client() if payload.source == "manga_news" else None
    mangabaka = session_store.mangabaka_client() if payload.source == "mangabaka" else None

    def action(progress, cancelled):
        return scan_next_releases(
            api,
            payload.source,
            payload.series_ids,
            manga_news,
            mangabaka,
            progress,
            cancelled,
        )

    return jobs.submit(f"Prochaines sorties {payload.source}", action).public()


@app.post("/api/next-releases/auto")
def start_next_release_automation(payload: NextReleaseAutoRequest) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Confirmation explicite requise pour l'automatisation")
    api = api_or_401()
    manga_news = session_store.manga_news_client() if payload.source == "manga_news" else None
    mangabaka = session_store.mangabaka_client() if payload.source == "mangabaka" else None

    def action(progress, cancelled):
        return automate_next_releases(
            api,
            payload.source,
            payload.library_id,
            manga_news,
            mangabaka,
            operations,
            progress,
            cancelled,
        )

    return jobs.submit(f"Automatisation prochaines sorties {payload.source}", action).public()


@app.post("/api/release-tracking/scan")
def start_release_tracking_scan(payload: ReleaseTrackingScanRequest) -> dict:
    api = api_or_401()
    clients = {
        "bedetheque": session_store.bedetheque_client(),
        "manga_news": session_store.manga_news_client(),
        "mangabaka": session_store.mangabaka_client(),
        "comicvine": session_store.comicvine_client(),
    }

    def action(progress, cancelled):
        return scan_release_tracking(api, payload.source, payload.series_ids, clients, progress, cancelled)

    return jobs.submit(f"Suivi sorties {payload.source}", action).public()


@app.post("/api/release-tracking/guided/preview")
def start_guided_release_tracking_preview(payload: ReleaseTrackingGuidedPreviewRequest) -> dict:
    api = api_or_401()
    client = _release_tracking_client(payload.source)

    def action(progress, cancelled):
        return prepare_guided_release_tracking(
            api,
            payload.source,
            payload.library_id,
            client,
            progress,
            cancelled,
        )

    return jobs.submit(f"Préparation guidée {payload.source}", action).public()


@app.post("/api/release-tracking/guided/apply")
def start_guided_release_tracking_apply(payload: ReleaseTrackingGuidedApplyRequest) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation utilisateur requise")
    api = api_or_401()
    try:
        plan = jobs.consume_result(payload.preview_job_id, "guided_preview")
    except Exception as exc:
        raise domain_error(exc) from exc
    source = str(plan.get("source") or "")
    if source not in {"bedetheque", "manga_news", "mangabaka", "comicvine"}:
        raise HTTPException(status_code=400, detail="Source guidée invalide")

    def action(progress, cancelled):
        return apply_guided_release_tracking(
            api,
            source,
            plan,
            operations,
            progress,
            cancelled,
        )

    return jobs.submit(f"Application guidée {source}", action).public()


def _release_tracking_client(source: str, *, automation: bool = False) -> Any:
    factories = {
        "bedetheque": (
            session_store.bedetheque_automation_client
            if automation
            else session_store.bedetheque_client
        ),
        "manga_news": session_store.manga_news_client,
        "mangabaka": session_store.mangabaka_client,
        "comicvine": session_store.comicvine_client,
    }
    try:
        return factories[source]()
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="Source de suivi invalide") from exc
    except Exception as exc:
        raise domain_error(exc) from exc


def _start_automation_release_tracking_preview(source: str, library_id: str) -> dict:
    api = api_or_401()
    client = _release_tracking_client(source, automation=True)

    def action(progress, cancelled):
        return prepare_guided_release_tracking(
            api,
            source,
            library_id,
            client,
            progress,
            cancelled,
        )

    job = jobs.submit(
        f"Automatisation suivi sorties {source}",
        action,
        channel="automation",
    )
    return {
        "contract_version": "1.0",
        "kind": "preview",
        "source": source,
        "job": job.public(),
    }


def _start_automation_next_release_preview(source: str, library_id: str) -> dict:
    api = api_or_401()
    manga_news = session_store.manga_news_client() if source == "manga_news" else None
    mangabaka = session_store.mangabaka_client() if source == "mangabaka" else None

    def action(progress, cancelled):
        return prepare_next_release_automation(
            api,
            source,
            library_id,
            manga_news,
            mangabaka,
            progress,
            cancelled,
        )

    job = jobs.submit(
        f"Automatisation prochaines sorties {source}",
        action,
        channel="automation",
    )
    return {
        "contract_version": "1.0",
        "kind": "next_release_preview",
        "source": source,
        "job": job.public(),
    }


def _start_automatic_release_tracking(source: str, library_id: str) -> dict:
    api = api_or_401()
    client = _release_tracking_client(source, automation=True)

    def action(progress, cancelled):
        return run_guided_release_tracking_automation(
            api,
            source,
            library_id,
            client,
            operations,
            progress,
            cancelled,
        )

    job = jobs.submit(
        f"Automatisation complète suivi sorties {source}",
        action,
        channel="automation",
    )
    return {
        "contract_version": "1.0",
        "kind": "release_tracking_auto",
        "source": source,
        "job": job.public(),
    }


def _start_automatic_next_releases(source: str, library_id: str) -> dict:
    api = api_or_401()
    manga_news = session_store.manga_news_client() if source == "manga_news" else None
    mangabaka = session_store.mangabaka_client() if source == "mangabaka" else None

    def action(progress, cancelled):
        return run_next_release_automation(
            api,
            source,
            library_id,
            manga_news,
            mangabaka,
            operations,
            progress,
            cancelled,
        )

    job = jobs.submit(
        f"Automatisation complète prochaines sorties {source}",
        action,
        channel="automation",
    )
    return {
        "contract_version": "1.0",
        "kind": "next_release_auto",
        "source": source,
        "job": job.public(),
    }


def _confirm_automation_release_tracking(
    source: str,
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation utilisateur requise")
    api = api_or_401()
    try:
        preview_job = jobs.get(payload.preview_job_id, channel="automation")
        preview_result = preview_job.result if isinstance(preview_job.result, dict) else {}
        if preview_result.get("source") != source:
            raise RuntimeError("La source ne correspond pas à la prévisualisation")
        plan = jobs.consume_result(
            payload.preview_job_id,
            "guided_preview",
            channel="automation",
            max_age_seconds=30 * 60,
        )
    except Exception as exc:
        raise domain_error(exc) from exc
    def action(progress, cancelled):
        return apply_guided_release_tracking(
            api,
            source,
            plan,
            operations,
            progress,
            cancelled,
        )

    job = jobs.submit(
        f"Confirmation suivi sorties {source}",
        action,
        channel="automation",
    )
    return {
        "contract_version": "1.0",
        "kind": "apply",
        "source": source,
        "job": job.public(),
    }


def _confirm_automation_next_release(
    source: str,
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation utilisateur requise")
    api = api_or_401()
    try:
        preview_job = jobs.get(payload.preview_job_id, channel="automation")
        preview_result = preview_job.result if isinstance(preview_job.result, dict) else {}
        if preview_result.get("source") != source:
            raise RuntimeError("La source ne correspond pas à la prévisualisation")
        plan = jobs.consume_result(
            payload.preview_job_id,
            "next_release_preview",
            channel="automation",
            max_age_seconds=30 * 60,
        )
    except Exception as exc:
        raise domain_error(exc) from exc

    def action(progress, cancelled):
        return apply_next_release_automation(
            api,
            source,
            plan,
            operations,
            progress,
            cancelled,
        )

    job = jobs.submit(
        f"Confirmation prochaines sorties {source}",
        action,
        channel="automation",
    )
    return {
        "contract_version": "1.0",
        "kind": "next_release_apply",
        "source": source,
        "job": job.public(),
    }


@app.get(
    "/api/automation/status",
    dependencies=[Depends(require_automation_token)],
)
def automation_status() -> dict:
    connection_error = ""
    try:
        session_store.require_api()
    except Exception as exc:
        connection_error = SecretRedactor.redact(exc)
    state = session_store.public_state()
    source_settings = session_store.public_sources()
    return {
        "contract_version": "1.0",
        "ready": bool(state.get("connected")),
        "komga_connected": bool(state.get("connected")),
        "automatic_connection_configured": bool(state.get("automatic_connection_configured")),
        "connection_error": connection_error,
        "preview_expires_in_seconds": 30 * 60,
        "request_delays_seconds": session_store.automation_request_delays(),
        "sources": ["bedetheque", "manga_news", "mangabaka", "comicvine"],
        "source_ready": {
            "bedetheque": bool(source_settings.get("bedetheque_csv_configured")),
            "manga_news": True,
            "mangabaka": True,
            "comicvine": bool(source_settings.get("comicvine_api_key_configured")),
        },
    }


@app.post(
    "/api/automation/release-tracking/manga-news/preview",
    dependencies=[Depends(require_automation_token)],
)
def automation_manga_news_preview(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automation_release_tracking_preview("manga_news", payload.library_id)


@app.post(
    "/api/automation/release-tracking/mangabaka/preview",
    dependencies=[Depends(require_automation_token)],
)
def automation_mangabaka_preview(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automation_release_tracking_preview("mangabaka", payload.library_id)


@app.post(
    "/api/automation/release-tracking/bedetheque/preview",
    dependencies=[Depends(require_automation_token)],
)
def automation_bedetheque_preview(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automation_release_tracking_preview("bedetheque", payload.library_id)


@app.post(
    "/api/automation/release-tracking/comicvine/preview",
    dependencies=[Depends(require_automation_token)],
)
def automation_comicvine_preview(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automation_release_tracking_preview("comicvine", payload.library_id)


@app.post(
    "/api/automation/release-tracking/manga-news/run",
    dependencies=[Depends(require_automation_token)],
)
def automation_manga_news_run(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automatic_release_tracking("manga_news", payload.library_id)


@app.post(
    "/api/automation/release-tracking/mangabaka/run",
    dependencies=[Depends(require_automation_token)],
)
def automation_mangabaka_run(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automatic_release_tracking("mangabaka", payload.library_id)


@app.post(
    "/api/automation/release-tracking/bedetheque/run",
    dependencies=[Depends(require_automation_token)],
)
def automation_bedetheque_run(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automatic_release_tracking("bedetheque", payload.library_id)


@app.post(
    "/api/automation/release-tracking/comicvine/run",
    dependencies=[Depends(require_automation_token)],
)
def automation_comicvine_run(payload: AutomationReleaseTrackingRequest) -> dict:
    return _start_automatic_release_tracking("comicvine", payload.library_id)


@app.post(
    "/api/automation/next-releases/manga-news/preview",
    dependencies=[Depends(require_automation_token)],
)
def automation_manga_news_next_release_preview(
    payload: AutomationReleaseTrackingRequest,
) -> dict:
    return _start_automation_next_release_preview("manga_news", payload.library_id)


@app.post(
    "/api/automation/next-releases/mangabaka/preview",
    dependencies=[Depends(require_automation_token)],
)
def automation_mangabaka_next_release_preview(
    payload: AutomationReleaseTrackingRequest,
) -> dict:
    return _start_automation_next_release_preview("mangabaka", payload.library_id)


@app.post(
    "/api/automation/next-releases/manga-news/run",
    dependencies=[Depends(require_automation_token)],
)
def automation_manga_news_next_release_run(
    payload: AutomationReleaseTrackingRequest,
) -> dict:
    return _start_automatic_next_releases("manga_news", payload.library_id)


@app.post(
    "/api/automation/next-releases/mangabaka/run",
    dependencies=[Depends(require_automation_token)],
)
def automation_mangabaka_next_release_run(
    payload: AutomationReleaseTrackingRequest,
) -> dict:
    return _start_automatic_next_releases("mangabaka", payload.library_id)


@app.get(
    "/api/automation/jobs/{job_id}",
    dependencies=[Depends(require_automation_token)],
)
def automation_job(job_id: str) -> dict:
    try:
        return {
            "contract_version": "1.0",
            "job": jobs.get(job_id, channel="automation").public(),
        }
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post(
    "/api/automation/jobs/{job_id}/cancel",
    dependencies=[Depends(require_automation_token)],
)
def cancel_automation_job(job_id: str) -> dict:
    try:
        return {
            "contract_version": "1.0",
            "job": jobs.cancel(job_id, channel="automation").public(),
        }
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post(
    "/api/automation/release-tracking/manga-news/confirm",
    dependencies=[Depends(require_automation_token)],
)
def automation_manga_news_confirm(
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    return _confirm_automation_release_tracking("manga_news", payload)


@app.post(
    "/api/automation/release-tracking/mangabaka/confirm",
    dependencies=[Depends(require_automation_token)],
)
def automation_mangabaka_confirm(
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    return _confirm_automation_release_tracking("mangabaka", payload)


@app.post(
    "/api/automation/release-tracking/bedetheque/confirm",
    dependencies=[Depends(require_automation_token)],
)
def automation_bedetheque_confirm(
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    return _confirm_automation_release_tracking("bedetheque", payload)


@app.post(
    "/api/automation/release-tracking/comicvine/confirm",
    dependencies=[Depends(require_automation_token)],
)
def automation_comicvine_confirm(
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    return _confirm_automation_release_tracking("comicvine", payload)


@app.post(
    "/api/automation/next-releases/manga-news/confirm",
    dependencies=[Depends(require_automation_token)],
)
def automation_manga_news_next_release_confirm(
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    return _confirm_automation_next_release("manga_news", payload)


@app.post(
    "/api/automation/next-releases/mangabaka/confirm",
    dependencies=[Depends(require_automation_token)],
)
def automation_mangabaka_next_release_confirm(
    payload: AutomationReleaseTrackingConfirmRequest,
) -> dict:
    return _confirm_automation_next_release("mangabaka", payload)


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return jobs.list()


@app.get("/api/activity")
def activity() -> dict:
    return {
        "jobs": [
            {
                "id": row.get("id", ""), "label": row.get("label", ""),
                "status": row.get("status", ""), "current": row.get("current", 0),
                "total": row.get("total", 0),
                "message": SecretRedactor.redact(row.get("message", "")),
                "error": SecretRedactor.redact(row.get("error", "")),
            }
            for row in jobs.list()
        ],
        "backups": operations.rollback_records(),
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        return jobs.get(job_id, channel="web").public()
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    try:
        return jobs.cancel(job_id, channel="web").public()
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/kora/taxonomy")
def kora_taxonomy() -> dict:
    return {
        "max_genres": MAX_KORA_GENRES,
        "genres": [{"slug": slug, "label": KORA_GENRE_LABELS.get(slug, slug)} for slug in KORA_GENRES],
    }


@app.post("/api/kora/sync")
def start_kora_sync(payload: KoraSyncRequest) -> dict:
    api = api_or_401()

    def action(progress, cancelled):
        libraries = api.libraries()
        selected = set(payload.library_ids)
        libraries = [row for row in libraries if not selected or row.id in selected]
        KORA_CACHE.upsert_libraries(libraries, [])
        total = len(libraries)
        count = 0
        for index, library in enumerate(libraries, 1):
            if cancelled():
                break
            rows = api.series(library_id=library.id)
            KORA_CACHE.upsert_series(rows, {library.id: library.name})
            count += len(rows)
            progress(index, total, f"{library.name}: {len(rows)} séries")
        return {"libraries": len(libraries), "series": count}

    return jobs.submit("Synchronisation Kora", action).public()


@app.get("/api/kora/inventory")
def kora_inventory(
    library_id: str = Query(default="", max_length=256),
    search: str = Query(default="", max_length=500),
    genre: str = Query(default="", max_length=100),
    no_genre: bool = False,
    multiple_genres: bool = False,
) -> list[dict]:
    rows = KORA_CACHE.query_series(
        library_id=library_id,
        search=search,
        genre=genre,
        no_genre=no_genre,
        multiple_genres=multiple_genres,
    )
    pending = KORA_CACHE.pending_genres_by_series_id()
    return [
        {
            "id": row.id,
            "library_id": row.library_id,
            "library_name": row.library_name,
            "title": row.title,
            "book_count": row.book_count,
            "genres": row.kora_genres,
            "pending_genres": pending.get(row.id),
        }
        for row in rows
    ]


@app.get("/api/kora/pending")
def kora_pending() -> list[dict]:
    return [
        {
            "series_id": row.series_id,
            "library_name": row.library_name,
            "title": row.title,
            "genres": row.new_kora_genres,
            "source": row.source,
            "note": row.note,
        }
        for row in KORA_CACHE.pending()
    ]


@app.post("/api/kora/pending")
def add_kora_pending(payload: KoraPendingRequest) -> dict:
    try:
        genres = validate_genres(payload.genres)
        KORA_CACHE.add_pending(PendingChange(
            payload.series_id,
            payload.library_name,
            payload.title,
            genres,
            source="webui:inventory",
            note=payload.note,
        ))
        return {"status": "queued", "series_id": payload.series_id, "genres": genres}
    except Exception as exc:
        raise domain_error(exc) from exc


@app.delete("/api/kora/pending/{series_id}")
def delete_kora_pending(series_id: str) -> dict:
    KORA_CACHE.remove_pending([series_id])
    return {"status": "deleted", "series_id": series_id}


@app.post("/api/kora/pending/preview")
def preview_kora_pending() -> dict:
    try:
        api = api_or_401()
        tokens: list[str] = []
        for change in KORA_CACHE.pending():
            entity = api.get_series(change.series_id)
            metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
            current_tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
            preview = operations.preview_metadata(
                api,
                "series",
                change.series_id,
                {"tags": merge_series_tags_for_genres(current_tags, change.new_kora_genres)},
                "kora_webui_queue",
            )
            tokens.append(preview["token"])
        return {"tokens": tokens, "count": len(tokens)}
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/kora/pending/apply")
def apply_kora_pending(payload: TokenListRequest) -> dict:
    try:
        api = api_or_401()
        applied: list[str] = []
        for token in payload.tokens:
            result = operations.apply_any(api, token)
            target_id = str(result.get("target_id") or "")
            if target_id:
                applied.append(target_id)
        KORA_CACHE.remove_pending(applied)
        return {"applied": len(applied), "series_ids": applied}
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/kora/series/{series_id}")
def kora_series(series_id: str) -> dict:
    try:
        entity = api_or_401().get_series(series_id)
        metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
        tags = metadata.get("tags") if isinstance(metadata.get("tags"), list) else []
        return {"series_id": series_id, "genres": extract_kora_genres(tags), "tags": tags}
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/kora/series/{series_id}/preview")
def preview_kora_series(series_id: str, payload: KoraGenresRequest) -> dict:
    try:
        api = api_or_401()
        entity = api.get_series(series_id)
        metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
        genres = validate_genres(payload.genres)
        tags = merge_series_tags_for_genres(metadata.get("tags") or [], genres)
        return operations.preview_metadata(api, "series", series_id, {"tags": tags}, "kora_webui")
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/kora/exclusions")
def kora_exclusions() -> dict:
    data = KORA_EXCLUSIONS.load()
    return {
        "series": data.get("excluded_series_ids", {}),
        "title_rules": data.get("title_rules", []),
        "excluded_library_names": data.get("excluded_library_names", []),
    }


@app.post("/api/kora/exclusions")
def add_kora_exclusion(payload: KoraExclusionRequest) -> dict:
    KORA_EXCLUSIONS.add(payload.series_id, payload.title, payload.library_name, "webui")
    return kora_exclusions()


@app.delete("/api/kora/exclusions/{series_id}")
def remove_kora_exclusion(series_id: str) -> dict:
    KORA_EXCLUSIONS.remove(series_id)
    return kora_exclusions()


@app.get("/api/rollback")
def rollback_records() -> list[dict]:
    return operations.rollback_records()


@app.post("/api/rollback/{record_id}/preview")
def preview_rollback(record_id: str) -> dict:
    try:
        row, snapshot = operations.rollback_snapshot(record_id)
        return operations.preview_metadata(
            api_or_401(),
            row["target_type"],
            row["target_id"],
            snapshot,
            "rollback_webui",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.get("/api/thumbnails/{target_type}/{target_id}")
def thumbnail(target_type: Literal["series", "book"], target_id: str) -> Response:
    try:
        content = api_or_401().thumbnail_bytes(target_type, target_id)
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.get("/api/posters/{target_type}/{target_id}")
def posters(target_type: Literal["series", "book", "collection", "readlist"], target_id: str) -> list[dict]:
    try:
        return api_or_401().list_thumbnails(target_type, target_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/posters/select")
def select_poster(payload: PosterSelectRequest) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation explicite requise")
    try:
        api = api_or_401()
        current = api.list_thumbnails(payload.target_type, payload.target_id)
        operations.backup.save_json(
            "operation",
            payload.target_type,
            payload.target_id,
            {"thumbnails": current, "selected_thumbnail_id": payload.thumbnail_id},
            "WebUI avant sélection poster",
        )
        return {
            "status": "applied",
            "response": api.select_thumbnail(payload.target_type, payload.target_id, payload.thumbnail_id),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/posters/{target_type}/{target_id}/upload")
async def upload_poster(
    request: Request,
    target_type: Literal["series", "book", "collection", "readlist"],
    target_id: str,
    filename: str = Query(default="cover.jpg", max_length=255),
) -> dict:
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Un fichier image est requis")
    data = await request.body()
    if not data or len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image vide ou supérieure à 25 Mio")
    suffix = Path(filename).suffix.lower() if Path(filename).suffix else ".jpg"
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(data)
            temp_path = handle.name
        response = api_or_401().add_thumbnail(target_type, target_id, temp_path)
        return {"status": "uploaded", "response": response}
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _require_public_http_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL HTTP(S) publique requise")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = {
        item[4][0].split("%", 1)[0]
        for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    }
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("Les adresses locales, privées ou réservées sont refusées")


@app.post("/api/posters/{target_type}/{target_id}/upload-url")
def upload_poster_url(
    payload: PosterUrlRequest,
    target_type: Literal["series", "book", "collection", "readlist"],
    target_id: str,
) -> dict:
    if not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation explicite requise")
    url = str(payload.url)
    temp_path = ""
    try:
        _require_public_http_url(url)
        req = urlrequest.Request(url, headers={"User-Agent": "komga-toolkit-web/2.1"})
        with urlrequest.urlopen(req, timeout=60) as response:
            final_url = str(response.geturl() or url)
            _require_public_http_url(final_url)
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if not content_type.startswith("image/"):
                raise ValueError("L'URL ne renvoie pas une image")
            data = response.read(25 * 1024 * 1024 + 1)
        if not data or len(data) > 25 * 1024 * 1024:
            raise ValueError("Image vide ou supérieure à 25 Mio")
        suffix = Path(urlparse(final_url).path).suffix.lower() or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(data)
            temp_path = handle.name
        operations.backup.save_json(
            "operation", target_type, target_id,
            {"poster_upload_url": final_url},
            "WebUI avant upload poster URL",
        )
        return {
            "status": "uploaded",
            "response": api_or_401().add_thumbnail(target_type, target_id, temp_path),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@app.post("/api/csv/preview")
async def preview_csv(request: Request) -> dict:
    data = await request.body()
    if not data or len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV vide ou supérieur à 20 Mio")
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as handle:
            handle.write(data)
            temp_path = handle.name
        rows = read_csv(temp_path)
        actions = parse_director_actions(rows)
        return {
            "rows": len(rows),
            "actions": [
                {
                    "index": index,
                    "target_type": action.target_type,
                    "operation": action.operation,
                    "target_id": action.target_id,
                    "payload": action.payload,
                }
                for index, action in enumerate(actions, start=1)
            ],
        }
    except Exception as exc:
        raise domain_error(exc) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@app.get("/api/csv/templates/{template_name}")
def csv_template(template_name: str) -> Response:
    if template_name == "director":
        columns = DIRECTOR_COLUMNS
    elif template_name in SPECIALIZED_COLUMNS:
        columns = SPECIALIZED_COLUMNS[template_name]
    else:
        raise HTTPException(status_code=404, detail="Modèle CSV inconnu")
    stream = io.StringIO()
    csv.DictWriter(stream, fieldnames=columns).writeheader()
    return Response(
        content="\ufeff" + stream.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="komga_{template_name}_template.csv"'},
    )


@app.get("/api/csv/books/export")
def export_books_csv(library_id: str = Query(default="", max_length=256)) -> Response:
    try:
        rows = [book_inventory_row(book) for book in api_or_401().books(library_id=library_id or None, page_size=1000)]
        fieldnames = list(rows[0]) if rows else ["book_id"]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content="\ufeff" + stream.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="komga_books_inventory.csv"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_error(exc) from exc


@app.post("/api/csv/actions/preview")
def preview_csv_actions(payload: CsvActionsRequest) -> dict:
    try:
        api = api_or_401()
        previews: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for action in payload.actions:
            target_type = str(action.get("target_type") or "")
            operation = str(action.get("operation") or "update")
            target_id = str(action.get("target_id") or "")
            action_payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
            if target_type in {"series", "book"} and operation == "update":
                previews.append(operations.preview_metadata(api, target_type, target_id, action_payload, "csv_webui"))
            elif target_type in {"collection", "readlist"} and operation in {"create", "update"}:
                previews.append(operations.preview_resource(api, target_type, operation, target_id, action_payload))
            else:
                unsupported.append(action)
        return {"previews": previews, "tokens": [row["token"] for row in previews], "unsupported": unsupported}
    except HTTPException:
        raise
    except Exception as exc:
        raise domain_error(exc) from exc


@app.post("/api/csv/actions/apply")
def apply_csv_actions(payload: TokenListRequest) -> dict:
    api = api_or_401()
    rows: list[dict[str, Any]] = []
    for token in payload.tokens:
        try:
            rows.append(operations.apply_any(api, token))
        except Exception as exc:
            rows.append({"status": "error", "error": SecretRedactor.redact(exc)})
    return {"rows": rows, "applied": sum(1 for row in rows if row.get("status") in {"applied", "unchanged"})}


@app.post("/api/audit/run")
def start_audit(payload: AuditRequest) -> dict:
    api = api_or_401()

    def action(progress, cancelled):
        series_rows = api.series(payload.library_id, page_size=200)
        report: list[dict[str, Any]] = []
        total = len(series_rows)
        for index, series_item in enumerate(series_rows, start=1):
            if cancelled():
                break
            progress(index, total, series_item.title)
            metadata = series_item.metadata or {}
            missing = [field for field in ("title", "language", "status") if is_blank_metadata_value(metadata.get(field))]
            if is_low_value_summary(metadata.get("summary"), title=series_item.title):
                missing.append("summary")
            if not metadata.get("links"):
                missing.append("links")
            if missing:
                report.append({"target_type": "series", "target_id": series_item.id, "title": series_item.title, "issues": missing})
            if payload.include_books:
                for book in api.books(series_id=series_item.id, library_id=payload.library_id, direct_series_only=True):
                    book_missing = [field for field in ("title", "number") if is_blank_metadata_value((book.metadata or {}).get(field))]
                    if is_low_value_summary((book.metadata or {}).get("summary"), title=book.title):
                        book_missing.append("summary")
                    if book_missing:
                        report.append({"target_type": "book", "target_id": book.id, "title": book.title, "series_id": series_item.id, "issues": book_missing})
        return report

    return jobs.submit("Audit bibliothèque", action).public()


STATIC_DIR = Path(__file__).with_name("static")
if STATIC_DIR.is_dir():
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        candidate = (STATIC_DIR / path).resolve()
        if path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE_HEADERS)


def main() -> None:
    import uvicorn

    uvicorn.run("komga_db_tool.web.app:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()
