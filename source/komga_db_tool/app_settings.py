from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .comicvine import DEFAULT_COMICVINE_API_BASE_URL
from .manga_news import DEFAULT_MANGA_NEWS_API_BASE_URL
from .mangabaka import DEFAULT_API_BASE_URL
from .secure_config import SecureConfigStore


DEFAULT_CONFIG_FILE = Path.cwd() / "config.json"


@dataclass
class KomgaSettings:
    url: str = "http://192.168.1.30:25600"
    auth_mode: str = "api_key"
    api_key: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: int = 45


@dataclass
class ServiceSettings:
    url: str = "http://127.0.0.1:8085"
    enabled: bool = False
    timeout_seconds: int = 45


@dataclass
class BedethequeSettings:
    mode: str = "web"
    csv_path: str = "X:/AppData/MangaTracker/docs/bedetheque/bedetheque.csv"


@dataclass
class SourceSettings:
    url: str = DEFAULT_API_BASE_URL
    enabled: bool = True
    timeout_seconds: int = 45
    cache_enabled: bool = True
    cache_dir: str = ".komga_db_tool_cache/mangabaka"


@dataclass
class MangaNewsSettings:
    url: str = DEFAULT_MANGA_NEWS_API_BASE_URL
    enabled: bool = True
    timeout_seconds: int = 45
    token: str = ""
    cache_enabled: bool = True
    cache_dir: str = ".komga_db_tool_cache/manga_news"


@dataclass
class ComicVineSettings:
    url: str = DEFAULT_COMICVINE_API_BASE_URL
    enabled: bool = True
    timeout_seconds: int = 45
    api_key: str = ""
    cache_enabled: bool = True
    cache_dir: str = ".komga_db_tool_cache/comicvine"


@dataclass
class MatchingConfig:
    title_score_min: float = 0.90
    loaded_title_score_min: float = 0.90
    exact_title_score_min: float = 0.999
    tome_pair_score_min: float = 0.85
    tome_match_min_books: int = 2
    tome_match_min_ratio: float = 0.60
    tome_match_min_avg_score: float = 0.85
    max_bedetheque_candidates: int = 10


@dataclass
class AppConfig:
    komga: KomgaSettings = field(default_factory=KomgaSettings)
    komf: ServiceSettings = field(default_factory=ServiceSettings)
    bedetheque: BedethequeSettings = field(default_factory=BedethequeSettings)
    mangabaka: SourceSettings = field(default_factory=SourceSettings)
    manga_news: MangaNewsSettings = field(default_factory=MangaNewsSettings)
    comicvine: ComicVineSettings = field(default_factory=ComicVineSettings)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    simulation: bool = True
    backup_root: str = "_komga_db_tool_backups"
    ui: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "AppConfig":
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        data = data if isinstance(data, dict) else {}
        return cls(
            komga=_section(KomgaSettings, data.get("komga")),
            komf=_section(ServiceSettings, data.get("komf")),
            bedetheque=_section(BedethequeSettings, data.get("bedetheque")),
            mangabaka=_section(SourceSettings, data.get("mangabaka")),
            manga_news=_section(MangaNewsSettings, data.get("manga_news")),
            comicvine=_section(ComicVineSettings, data.get("comicvine")),
            matching=_section(MatchingConfig, data.get("matching")),
            simulation=bool(data.get("simulation", True)),
            backup_root=str(data.get("backup_root") or "_komga_db_tool_backups"),
            ui=dict(data.get("ui")) if isinstance(data.get("ui"), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _section(cls: type[Any], value: Any) -> Any:
    if not isinstance(value, dict):
        return cls()
    allowed = cls.__dataclass_fields__
    return cls(**{key: value[key] for key in allowed if key in value})


def load_config(path: str | Path = DEFAULT_CONFIG_FILE, include_secrets: bool = True) -> AppConfig:
    data = SecureConfigStore(Path(path)).load(include_secrets=include_secrets)
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: str | Path = DEFAULT_CONFIG_FILE) -> Path:
    return SecureConfigStore(Path(path)).save(config.to_dict())
