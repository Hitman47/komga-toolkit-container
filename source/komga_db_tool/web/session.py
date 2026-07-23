from __future__ import annotations

import threading
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..api import AuthConfig, KomgaApi
from ..bedetheque import BedethequeClient
from ..bedetheque_csv import BedethequeCsvClient
from ..comicvine import ComicVineClient, DEFAULT_COMICVINE_API_BASE_URL
from ..external_rate_limit import (
    EXTERNAL_SOURCE_MIN_DELAY_SECONDS,
    RateLimitedSourceClient,
)
from ..manga_news import MangaNewsClient
from ..mangabaka import DEFAULT_API_BASE_URL as DEFAULT_MANGABAKA_API_BASE_URL, MangaBakaClient
from ..app_settings import MatchingConfig


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
        try:
            configured_timeout = int(os.getenv("KOMGA_TIMEOUT") or 30)
        except (TypeError, ValueError):
            configured_timeout = 30
        self._automatic_timeout = max(3, min(300, configured_timeout))
        data_dir = Path(os.getenv("KOMGA_TOOLKIT_DATA_DIR") or ".komga_db_tool_cache/web")
        default_bedetheque_csv = data_dir / "uploads" / "bedetheque.csv"
        self._source_config: dict[str, Any] = {
            "manga_news_url": os.getenv("MANGA_NEWS_BASE_URL") or "http://host.docker.internal:8017",
            "manga_news_token": "",
            "mangabaka_url": DEFAULT_MANGABAKA_API_BASE_URL,
            "comicvine_url": os.getenv("COMICVINE_BASE_URL") or DEFAULT_COMICVINE_API_BASE_URL,
            "comicvine_api_key": "",
            "bedetheque_csv_path": str(
                Path(os.getenv("BEDETHEQUE_CSV_PATH") or default_bedetheque_csv)
            ),
            "bedetheque_csv_only": False,
            "timeout": 30,
            "cache_dir": str(data_dir / "cache"),
        }
        self._external_rate_limit_state: dict[str, dict[str, Any]] = {
            "bedetheque": {"next_allowed": 0.0, "lock": threading.Lock()},
            "manga_news": {"next_allowed": 0.0, "lock": threading.Lock()},
            "mangabaka": {"next_allowed": 0.0, "lock": threading.Lock()},
            "comicvine": {"next_allowed": 0.0, "lock": threading.Lock()},
        }
        self._external_request_delays = {
            # Interactive website scraping keeps its protection. Bedetheque
            # automations use BedethequeCsvClient directly and never use this
            # limiter.
            "bedetheque": 2.0,
            "manga_news": _request_delay_from_env(
                "MANGA_NEWS_AUTOMATION_DELAY_SECONDS", 1.0
            ),
            "mangabaka": _request_delay_from_env(
                "MANGABAKA_AUTOMATION_DELAY_SECONDS", 1.0
            ),
            "comicvine": _request_delay_from_env(
                "COMICVINE_AUTOMATION_DELAY_SECONDS", 1.2
            ),
        }
        self._matching = MatchingConfig()

    def automation_request_delays(self) -> dict[str, float]:
        delays = dict(self._external_request_delays)
        delays["bedetheque"] = 0.0
        return delays

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
            self._api = api
            self._public = {
                "connected": True,
                "base_url": api.client.base_url,
                "auth_mode": auth_mode,
                "message": message,
            }
            return dict(self._public)

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._api = None
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
            self._api = api
            self._public = {
                "connected": True,
                "base_url": api.client.base_url,
                "auth_mode": "api_key",
                "connection_mode": "automatic",
                "message": message,
            }
        return api

    def require_api(self) -> KomgaApi:
        with self._lock:
            api = self._api
        if api is not None:
            return api
        if self._automatic_base_url:
            return self._connect_automatically()
        raise LookupError("Connexion Komga requise")

    def configure_sources(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "manga_news_url",
            "manga_news_token",
            "mangabaka_url",
            "comicvine_url",
            "comicvine_api_key",
            "timeout",
            "bedetheque_csv_path",
            "bedetheque_csv_only",
        }
        with self._lock:
            for key, value in values.items():
                if key in allowed and value is not None:
                    if key == "timeout":
                        self._source_config[key] = int(value)
                    elif key == "bedetheque_csv_only":
                        self._source_config[key] = bool(value)
                    else:
                        self._source_config[key] = str(value).strip()
            return self.public_sources()

    def public_sources(self) -> dict[str, Any]:
        with self._lock:
            cfg = dict(self._source_config)
        bedetheque_csv_path = Path(str(cfg["bedetheque_csv_path"] or "")).expanduser()
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
            "bedetheque_csv_configured": bedetheque_csv_path.is_file(),
            "bedetheque_csv_only": bool(cfg["bedetheque_csv_only"]),
            "timeout": cfg["timeout"],
        }

    def public_matching(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._matching)

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

    def bedetheque_client(self) -> BedethequeClient | BedethequeCsvClient | RateLimitedSourceClient:
        with self._lock:
            cfg = dict(self._source_config)
        if cfg["bedetheque_csv_only"]:
            return self.bedetheque_csv_client()
        timeout = int(cfg["timeout"])
        return self._rate_limited_source_client(
            "bedetheque",
            BedethequeClient(timeout=timeout),
        )

    def bedetheque_csv_client(self) -> BedethequeCsvClient:
        with self._lock:
            csv_path = str(self._source_config["bedetheque_csv_path"] or "").strip()
        path = Path(csv_path).expanduser() if csv_path else None
        if path is None or not path.is_file():
            raise RuntimeError(
                "Automatisation Bedetheque indisponible : chargez d'abord "
                "un CSV Bedetheque dans les paramètres WebUI."
            )
        return BedethequeCsvClient(str(path))

    def bedetheque_automation_client(self) -> BedethequeCsvClient:
        """Return the mandatory CSV client used by every Bedetheque automation."""
        return self.bedetheque_csv_client()

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


def public_dataclass(value: Any) -> dict[str, Any]:
    data = asdict(value)
    data.pop("raw", None)
    return data
