from __future__ import annotations

import threading
import os
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..api import AuthConfig, KomgaApi
from ..bedetheque_csv import BedethequeCsvClient
from ..comicvine import ComicVineClient, DEFAULT_COMICVINE_API_BASE_URL
from ..external_rate_limit import (
    EXTERNAL_SOURCE_MIN_DELAY_SECONDS,
    RateLimitedSourceClient,
)
from ..manga_news import MangaNewsClient
from ..mangabaka import DEFAULT_API_BASE_URL as DEFAULT_MANGABAKA_API_BASE_URL, MangaBakaClient
from ..mangacollec import MangaCollecStore
from ..metron import DEFAULT_METRON_API_BASE_URL, MetronClient
from ..nautiljon import NautiljonCsvClient
from ..app_settings import MatchingConfig
from ..integrations import CachedKomgaService
from ..runtime import MemoryCache
from ..file_sources import FILE_SOURCES, test_file_source
from ..catalog_mirror import CatalogMirror


def _request_delay_from_env(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(EXTERNAL_SOURCE_MIN_DELAY_SECONDS, min(30.0, value))


class WebSessionStore:
    """Keep connection material in memory only for the current process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api: KomgaApi | None = None
        self._komga_cache = MemoryCache(max_entries=128)
        self._komga_cache_api: Any | None = None
        self._public: dict[str, Any] = {
            "connected": False,
            "base_url": "",
            "auth_mode": "none",
            "message": "Non connecté",
        }
        self._automatic_base_url = str(os.getenv("KOMGA_BASE_URL") or "").strip()
        self._automatic_api_key = str(os.getenv("KOMGA_API_KEY") or "").strip()
        self._automatic_api_key_file = str(os.getenv("KOMGA_API_KEY_FILE") or "").strip()
        self._automatic_comicvine_api_key = str(os.getenv("COMICVINE_API_KEY") or "").strip()
        self._automatic_comicvine_api_key_file = str(os.getenv("COMICVINE_API_KEY_FILE") or "").strip()
        self._automatic_metron_token = str(os.getenv("METRON_API_TOKEN") or "").strip()
        self._automatic_metron_token_file = str(os.getenv("METRON_API_TOKEN_FILE") or "").strip()
        try:
            configured_timeout = int(os.getenv("KOMGA_TIMEOUT") or 30)
        except (TypeError, ValueError):
            configured_timeout = 30
        self._automatic_timeout = max(3, min(300, configured_timeout))
        data_dir = Path(os.getenv("KOMGA_TOOLKIT_DATA_DIR") or ".komga_db_tool_cache/web")
        self._file_settings_path = data_dir / "file_sources.json"
        try:
            self._file_paths = json.loads(self._file_settings_path.read_text(encoding="utf-8"))
            if not isinstance(self._file_paths, dict):
                self._file_paths = {}
        except (OSError, ValueError):
            self._file_paths = {}
        self._mangacollec = MangaCollecStore(data_dir / "mangacollec")
        default_bedetheque_csv = data_dir / "uploads" / "bedetheque.csv"
        configured_bedetheque_csv = Path(
            os.getenv("BEDETHEQUE_CSV_PATH") or default_bedetheque_csv
        )
        self._bedetheque_csv_storage_path = default_bedetheque_csv
        active_bedetheque_csv = (
            default_bedetheque_csv if default_bedetheque_csv.is_file() else configured_bedetheque_csv
        )
        default_nautiljon_csv = data_dir / "uploads" / "nautiljon.csv"
        configured_nautiljon_csv = Path(
            os.getenv("NAUTILJON_CSV_PATH") or default_nautiljon_csv
        )
        self._nautiljon_csv_storage_path = default_nautiljon_csv
        active_nautiljon_csv = (
            default_nautiljon_csv if default_nautiljon_csv.is_file() else configured_nautiljon_csv
        )
        self._source_config: dict[str, Any] = {
            "manga_news_url": os.getenv("MANGA_NEWS_BASE_URL") or "http://host.docker.internal:8017",
            "manga_news_token": "",
            "mangabaka_url": DEFAULT_MANGABAKA_API_BASE_URL,
            "comicvine_url": os.getenv("COMICVINE_BASE_URL") or DEFAULT_COMICVINE_API_BASE_URL,
            "comicvine_api_key": "",
            "metron_url": os.getenv("METRON_BASE_URL") or DEFAULT_METRON_API_BASE_URL,
            "metron_token": "",
            "bedetheque_csv_path": str(self._file_paths.get("bedetheque") or active_bedetheque_csv),
            "nautiljon_csv_path": str(self._file_paths.get("nautiljon") or active_nautiljon_csv),
            "timeout": 30,
            "cache_dir": str(data_dir / "cache"),
        }
        self._external_rate_limit_state: dict[str, dict[str, Any]] = {
            "manga_news": {"next_allowed": 0.0, "lock": threading.Lock()},
            "mangabaka": {"next_allowed": 0.0, "lock": threading.Lock()},
            "comicvine": {"next_allowed": 0.0, "lock": threading.Lock()},
            "metron": {"next_allowed": 0.0, "lock": threading.Lock()},
        }
        self._external_request_delays = {
            "manga_news": _request_delay_from_env(
                "MANGA_NEWS_AUTOMATION_DELAY_SECONDS", 1.0
            ),
            "mangabaka": _request_delay_from_env(
                "MANGABAKA_AUTOMATION_DELAY_SECONDS", 1.0
            ),
            "comicvine": _request_delay_from_env(
                "COMICVINE_AUTOMATION_DELAY_SECONDS", 1.2
            ),
            "metron": _request_delay_from_env(
                "METRON_AUTOMATION_DELAY_SECONDS", 3.1
            ),
        }
        self._matching = MatchingConfig()

    def automation_request_delays(self) -> dict[str, float]:
        return {
            "bedetheque": 0.0,
            **{
                provider: self._external_request_delays[provider]
                for provider in ("manga_news", "mangabaka", "comicvine")
            },
        }

    def _rate_limited_source_client(self, provider: str, client: Any) -> RateLimitedSourceClient:
        return RateLimitedSourceClient(
            provider,
            client,
            self._external_rate_limit_state[provider],
            lambda *_args: None,
            delay_seconds=self._external_request_delays[provider],
        )

    def connect(
        self,
        *,
        base_url: str,
        auth_mode: str,
        api_key: str = "",
        username: str = "",
        password: str = "",
        timeout: int = 30,
    ) -> dict[str, Any]:
        auth = AuthConfig(
            mode=auth_mode,
            api_key=api_key,
            username=username,
            password=password,
        )
        api = KomgaApi(base_url, auth=auth, timeout=timeout)
        message = api.test()
        with self._lock:
            self._komga_cache.invalidate("komga:")
            self._api = api
            self._komga_cache_api = api
            self._public = {
                "connected": True,
                "base_url": api.client.base_url,
                "auth_mode": auth_mode,
                "message": message,
            }
            return dict(self._public)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._komga_cache.invalidate("komga:")
            self._api = None
            self._komga_cache_api = None
            self._public = {
                "connected": False,
                "base_url": "",
                "auth_mode": "none",
                "message": "Non connecté",
            }
            return dict(self._public)

    def public_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._public,
                "automatic_connection_configured": bool(self._automatic_base_url),
            }

    def _automatic_secret(self) -> str:
        if self._automatic_api_key_file:
            path = Path(self._automatic_api_key_file)
            if path.name.casefold() == "config.json":
                raise LookupError("Le fichier de connexion configuré est interdit")
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LookupError("Le secret API Komga configuré est indisponible") from exc
        return self._automatic_api_key

    def _automatic_comicvine_secret(self) -> str:
        if self._automatic_comicvine_api_key_file:
            path = Path(self._automatic_comicvine_api_key_file)
            if path.name.casefold() == "config.json":
                raise LookupError("Le fichier ComicVine configuré est interdit")
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LookupError("Le secret API ComicVine configuré est indisponible") from exc
        return self._automatic_comicvine_api_key

    def _automatic_metron_secret(self) -> str:
        if self._automatic_metron_token_file:
            path = Path(self._automatic_metron_token_file)
            if path.name.casefold() == "config.json":
                raise LookupError("Le fichier Metron configuré est interdit")
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LookupError("Le jeton API Metron configuré est indisponible") from exc
        return self._automatic_metron_token

    def _connect_automatically(self) -> KomgaApi:
        parsed = urlparse(self._automatic_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LookupError("KOMGA_BASE_URL est invalide")
        if parsed.username or parsed.password:
            raise LookupError("KOMGA_BASE_URL ne doit contenir aucun identifiant")
        api_key = self._automatic_secret()
        if not api_key:
            raise LookupError("La clé API Komga automatique n'est pas configurée")
        api = KomgaApi(
            self._automatic_base_url,
            auth=AuthConfig(mode="api_key", api_key=api_key),
            timeout=self._automatic_timeout,
        )
        message = api.test()
        with self._lock:
            self._komga_cache.invalidate("komga:")
            self._api = api
            self._komga_cache_api = api
            self._public = {
                "connected": True,
                "base_url": api.client.base_url,
                "auth_mode": "api_key",
                "connection_mode": "automatic",
                "message": message,
            }
        return api

    def require_api(self) -> CachedKomgaService:
        with self._lock:
            api = self._api
        if api is None:
            if not self._automatic_base_url:
                raise LookupError("Connexion Komga requise")
            api = self._connect_automatically()
        with self._lock:
            # Also protects development/test hot swaps and any future reconnect
            # path that replaces the underlying client directly.
            if self._komga_cache_api is not api:
                self._komga_cache.invalidate("komga:")
                self._komga_cache_api = api
        return CachedKomgaService(
            api,
            self._komga_cache,
            content_ttl_seconds=30,
            thumbnail_ttl_seconds=300,
        )

    def configure_sources(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "manga_news_url",
            "manga_news_token",
            "mangabaka_url",
            "comicvine_url",
            "comicvine_api_key",
            "metron_url",
            "metron_token",
            "timeout",
            "bedetheque_csv_path",
            "nautiljon_csv_path",
        }
        with self._lock:
            for key, value in values.items():
                if key in allowed and value is not None:
                    if key == "timeout":
                        self._source_config[key] = int(value)
                    else:
                        self._source_config[key] = str(value).strip()
            return self.public_sources()

    def public_sources(self) -> dict[str, Any]:
        with self._lock:
            cfg = dict(self._source_config)
        bedetheque_csv_path = Path(str(cfg["bedetheque_csv_path"] or "")).expanduser()
        nautiljon_csv_path = Path(str(cfg["nautiljon_csv_path"] or "")).expanduser()
        from ..catalog_mirror import CatalogMirror
        def available_stat(path, kind):
            try:
                if path.is_file():
                    return path.stat()
            except OSError:
                pass
            local = CatalogMirror(str(path), kind).local
            return local.stat() if local.is_file() else None
        bedetheque_csv_stat = available_stat(bedetheque_csv_path, "bedetheque")
        nautiljon_csv_stat = available_stat(nautiljon_csv_path, "nautiljon")
        return {
            "manga_news_url": cfg["manga_news_url"],
            "manga_news_token_configured": bool(cfg["manga_news_token"]),
            "mangabaka_url": cfg["mangabaka_url"],
            "comicvine_url": cfg["comicvine_url"],
            "comicvine_api_key_configured": bool(
                cfg["comicvine_api_key"]
                or self._automatic_comicvine_api_key
                or self._automatic_comicvine_api_key_file
            ),
            "metron_url": cfg["metron_url"],
            "metron_token_configured": bool(
                cfg["metron_token"]
                or self._automatic_metron_token
                or self._automatic_metron_token_file
            ),
            "bedetheque_csv_configured": bedetheque_csv_stat is not None,
            "bedetheque_csv_filename": (
                bedetheque_csv_path.name if bedetheque_csv_stat is not None else ""
            ),
            "bedetheque_csv_size_bytes": (
                bedetheque_csv_stat.st_size if bedetheque_csv_stat is not None else 0
            ),
            "bedetheque_csv_updated_at": (
                datetime.fromtimestamp(
                    bedetheque_csv_stat.st_mtime,
                    tz=timezone.utc,
                ).isoformat()
                if bedetheque_csv_stat is not None
                else ""
            ),
            "nautiljon_csv_configured": nautiljon_csv_stat is not None,
            "nautiljon_csv_filename": nautiljon_csv_path.name if nautiljon_csv_stat is not None else "",
            "nautiljon_csv_size_bytes": nautiljon_csv_stat.st_size if nautiljon_csv_stat is not None else 0,
            "nautiljon_csv_updated_at": (
                datetime.fromtimestamp(nautiljon_csv_stat.st_mtime, tz=timezone.utc).isoformat()
                if nautiljon_csv_stat is not None
                else ""
            ),
            "timeout": cfg["timeout"],
        }

    def public_matching(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._matching)

    def file_sources(self) -> list[dict]:
        with self._lock:
            cfg = dict(self._source_config)
        result = []
        for kind, label in FILE_SOURCES.items():
            if kind == "mangacollec":
                status = self._mangacollec.status()
                result.append({"source": kind, "label": label, "path": status["source_path"],
                    "filename": status["filename"], "configured": status["configured"],
                    "status": status["source_status"], "entries": status.get("release_count", 0),
                    "updated_at": datetime.fromtimestamp(status["updated_at"], timezone.utc).isoformat() if status["updated_at"] else ""})
                continue
            path = Path(cfg[f"{kind}_csv_path"]).expanduser()
            mirror = CatalogMirror(str(path), kind).local
            direct = path.is_file()
            available = path if direct else mirror
            stat = available.stat() if available.is_file() else None
            result.append({"source": kind, "label": label, "path": str(path),
                "filename": path.name, "configured": stat is not None,
                "status": "Fichier disponible — à tester" if direct else "Copie locale de secours" if stat else "Fichier indisponible",
                "size_bytes": stat.st_size if stat else 0,
                "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() if stat else ""})
        return result

    def test_file(self, kind: str, path="", *, activate=False) -> dict:
        if kind not in FILE_SOURCES:
            raise ValueError("Source fichier inconnue")
        with self._lock:
            proposed = bool(path)
            if not path:
                path = (self._mangacollec.status()["source_path"] if kind == "mangacollec"
                        else self._source_config[f"{kind}_csv_path"])
            if kind == "mangacollec" and not path and not activate:
                status = self._mangacollec.status()
                if not status["configured"]:
                    raise ValueError("Importez un catalogue MangaCollec")
                return {"source": kind, "status": status["source_status"],
                        "validation": f"{status.get('release_count', 0)} sortie(s) dans le catalogue importé"}
            result = test_file_source(kind, path, fallback=not activate and not proposed,
                                      mangacollec_store=self._mangacollec)
            if activate:
                if kind == "mangacollec":
                    self._mangacollec.import_file(path)
                else:
                    test_file_source(kind, path, fallback=True)
                    candidate = {**self._file_paths, kind: str(Path(path).expanduser().resolve())}
                    MangaCollecStore._atomic_json(self._file_settings_path, candidate)
                    self._file_paths = candidate
                    self._source_config[f"{kind}_csv_path"] = candidate[kind]
            return result

    def configure_matching(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = MatchingConfig.__dataclass_fields__
        with self._lock:
            current = asdict(self._matching)
            for key, value in values.items():
                if key not in allowed:
                    continue
                current[key] = int(value) if key in {"tome_match_min_books", "max_bedetheque_candidates"} else float(value)
            candidate = MatchingConfig(**current)
            scores = (
                candidate.title_score_min,
                candidate.loaded_title_score_min,
                candidate.exact_title_score_min,
                candidate.tome_pair_score_min,
                candidate.tome_match_min_ratio,
                candidate.tome_match_min_avg_score,
            )
            if any(value < 0 or value > 1 for value in scores):
                raise ValueError("Les seuils de score doivent être compris entre 0 et 1")
            if candidate.tome_match_min_books < 1 or candidate.max_bedetheque_candidates < 1:
                raise ValueError("Les limites de matching doivent être supérieures à zéro")
            self._matching = candidate
            return asdict(self._matching)

    def bedetheque_client(self) -> BedethequeCsvClient:
        """Return the sole supported Bedetheque source: the local CSV."""
        return self.bedetheque_csv_client()

    def bedetheque_csv_client(self) -> BedethequeCsvClient:
        with self._lock:
            csv_path = str(self._source_config["bedetheque_csv_path"] or "").strip()
        path = Path(csv_path).expanduser() if csv_path else None
        from ..catalog_mirror import CatalogMirror
        if path is None or not (CatalogMirror(str(path), "bedetheque").local.is_file() or path.is_file()):
            raise RuntimeError(
                "Automatisation Bedetheque indisponible : chargez d'abord "
                "un CSV Bedetheque dans les paramètres WebUI."
            )
        return BedethequeCsvClient(str(path))

    def persist_bedetheque_csv(self, data: bytes) -> dict[str, Any]:
        """Validate and atomically replace the persistent Bedetheque catalog."""
        with self._lock:
            path = self._bedetheque_csv_storage_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.uploading")
            try:
                with temporary.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                validation = BedethequeCsvClient(str(temporary), mirror=False).test()
                temporary.replace(path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            self._source_config["bedetheque_csv_path"] = str(path)
            self._file_paths["bedetheque"] = str(path)
            MangaCollecStore._atomic_json(self._file_settings_path, self._file_paths)
            result = self.public_sources()
            result["bedetheque_csv_validation"] = validation
            return result

    def nautiljon_client(self) -> NautiljonCsvClient:
        """Return the sole supported Nautiljon source: the local CSV."""
        with self._lock:
            csv_path = str(self._source_config["nautiljon_csv_path"] or "").strip()
        path = Path(csv_path).expanduser() if csv_path else None
        from ..catalog_mirror import CatalogMirror
        if path is None or not (CatalogMirror(str(path), "nautiljon").local.is_file() or path.is_file()):
            raise RuntimeError(
                "Enrichissement Nautiljon indisponible : chargez d'abord "
                "un CSV Nautiljon dans les paramètres WebUI."
            )
        return NautiljonCsvClient(str(path))

    def persist_nautiljon_csv(self, data: bytes) -> dict[str, Any]:
        """Validate and atomically replace the persistent Nautiljon catalog."""
        with self._lock:
            path = self._nautiljon_csv_storage_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.uploading")
            try:
                with temporary.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                validation = NautiljonCsvClient(str(temporary), mirror=False).test()
                temporary.replace(path)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            self._source_config["nautiljon_csv_path"] = str(path)
            self._file_paths["nautiljon"] = str(path)
            MangaCollecStore._atomic_json(self._file_settings_path, self._file_paths)
            result = self.public_sources()
            result["nautiljon_csv_validation"] = validation
            return result

    def mangacollec_store(self) -> MangaCollecStore:
        return self._mangacollec

    def manga_news_client(self) -> RateLimitedSourceClient:
        with self._lock:
            cfg = dict(self._source_config)
        client = MangaNewsClient(
            base_url=cfg["manga_news_url"],
            token=cfg["manga_news_token"],
            timeout=cfg["timeout"],
            cache_dir=str(Path(cfg["cache_dir"]) / "manga_news"),
        )
        return self._rate_limited_source_client("manga_news", client)

    def mangabaka_client(self) -> RateLimitedSourceClient:
        with self._lock:
            cfg = dict(self._source_config)
        client = MangaBakaClient(
            base_url=cfg["mangabaka_url"],
            timeout=cfg["timeout"],
            cache_dir=str(Path(cfg["cache_dir"]) / "mangabaka"),
        )
        return self._rate_limited_source_client("mangabaka", client)

    def comicvine_client(self) -> RateLimitedSourceClient:
        with self._lock:
            cfg = dict(self._source_config)
        client = ComicVineClient(
            base_url=cfg["comicvine_url"],
            api_key=cfg["comicvine_api_key"] or self._automatic_comicvine_secret(),
            timeout=cfg["timeout"],
            cache_dir=str(Path(cfg["cache_dir"]) / "comicvine"),
        )
        return self._rate_limited_source_client("comicvine", client)

    def metron_client(self) -> RateLimitedSourceClient:
        with self._lock:
            cfg = dict(self._source_config)
        client = MetronClient(
            base_url=cfg["metron_url"],
            token=cfg["metron_token"] or self._automatic_metron_secret(),
            timeout=cfg["timeout"],
            cache_dir=str(Path(cfg["cache_dir"]) / "metron"),
        )
        return self._rate_limited_source_client("metron", client)


def public_dataclass(value: Any) -> dict[str, Any]:
    data = asdict(value)
    data.pop("raw", None)
    return data
