from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .constants import EXCLUDED_LIBRARY_NAMES_DEFAULT
from .models import AuthConfig


def default_app_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "KoraGenreManager"
    return Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "kora-genre-manager"


@dataclass
class KomgaConfig:
    url: str = "http://localhost:25600"
    auth_mode: str = "api_key"
    api_key: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: int = 30

    def auth(self) -> AuthConfig:
        return AuthConfig(mode=self.auth_mode, api_key=self.api_key, username=self.username, password=self.password)


@dataclass
class AppConfig:
    komga: KomgaConfig = field(default_factory=KomgaConfig)
    data_dir: str = ""
    cache_path: str = ""
    backup_dir: str = ""
    log_path: str = ""
    excluded_library_names: list[str] = field(default_factory=lambda: list(EXCLUDED_LIBRARY_NAMES_DEFAULT))
    page_size: int = 500

    @staticmethod
    def default() -> "AppConfig":
        data_dir = default_app_dir()
        return AppConfig(
            data_dir=str(data_dir),
            cache_path=str(data_dir / "kora_genre_manager.sqlite"),
            backup_dir=str(data_dir / "backups"),
            log_path=str(data_dir / "logs" / "app.log"),
        )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AppConfig":
        default = AppConfig.default()
        komga_data = data.get("komga") if isinstance(data.get("komga"), dict) else {}
        excluded = data.get("excluded_library_names", default.excluded_library_names)
        return AppConfig(
            komga=KomgaConfig(**{**asdict(default.komga), **komga_data}),
            data_dir=str(data.get("data_dir") or default.data_dir),
            cache_path=str(data.get("cache_path") or default.cache_path),
            backup_dir=str(data.get("backup_dir") or default.backup_dir),
            log_path=str(data.get("log_path") or default.log_path),
            excluded_library_names=[str(x) for x in excluded],
            page_size=int(data.get("page_size") or default.page_size),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_config_path() -> Path:
    return default_app_dir() / "config.json"


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    config_path = Path(path) if path else default_config_path()
    if not config_path.exists():
        config = AppConfig.default()
        save_config(config, config_path)
        return config
    with config_path.open("r", encoding="utf-8") as f:
        return AppConfig.from_dict(json.load(f))


def save_config(config: AppConfig, path: str | os.PathLike[str] | None = None) -> None:
    config_path = Path(path) if path else default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure derived directories exist even when user changed paths.
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)
    Path(config.backup_dir).mkdir(parents=True, exist_ok=True)
    Path(config.log_path).parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
