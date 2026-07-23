from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict

from .api import AuthConfig

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_FILE = os.path.join(PROJECT_ROOT, "config.json")


@dataclass
class KomgaConfig:
    url: str = "http://192.168.1.30:25600"
    auth_mode: str = "api_key"
    api_key: str = ""
    username: str = ""
    password: str = ""
    timeout_seconds: int = 30

    def auth(self) -> AuthConfig:
        return AuthConfig(mode=self.auth_mode, api_key=self.api_key, username=self.username, password=self.password)


@dataclass
class KomfConfig:
    url: str = "http://192.168.1.30:8085"
    enabled: bool = True
    timeout_seconds: int = 30


@dataclass
class AppConfig:
    komga: KomgaConfig
    komf: KomfConfig
    simulation: bool = True
    backup_root: str = "_komga_db_tool_backups"

    @staticmethod
    def default() -> "AppConfig":
        return AppConfig(komga=KomgaConfig(), komf=KomfConfig())

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AppConfig":
        default = AppConfig.default()
        komga_data = data.get("komga") if isinstance(data.get("komga"), dict) else {}
        komf_data = data.get("komf") if isinstance(data.get("komf"), dict) else {}
        return AppConfig(
            komga=KomgaConfig(**{**asdict(default.komga), **komga_data}),
            komf=KomfConfig(**{**asdict(default.komf), **komf_data}),
            simulation=bool(data.get("simulation", True)),
            backup_root=str(data.get("backup_root") or default.backup_root),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_config(path: str = DEFAULT_CONFIG_FILE) -> AppConfig:
    if not os.path.exists(path):
        return AppConfig.default()
    with open(path, "r", encoding="utf-8") as f:
        return AppConfig.from_dict(json.load(f))


def save_config(config: AppConfig, path: str = DEFAULT_CONFIG_FILE) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)
