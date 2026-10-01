"""Validated last-known-good copies of external catalogs (never writes the source)."""
from __future__ import annotations

import hashlib
import csv
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import shutil
import tempfile
from threading import RLock
import time


def catalog_storage_dir() -> Path:
    configured = os.environ.get("KOMGA_TOOLKIT_DATA_DIR")
    if configured:
        return Path(configured) / "catalogs"
    local = os.environ.get("LOCALAPPDATA")
    return (Path(local) / "KomgaToolkit" if local else Path.home() / ".local/share/komga-toolkit") / "catalogs"


class CatalogMirror:
    _lock = RLock()
    _recent: dict[tuple[str, str], tuple[float, Path, str]] = {}

    def __init__(self, source: str, kind: str, root: Path | None = None):
        self.source = Path(source).expanduser()
        key = hashlib.sha256(os.path.normcase(os.path.abspath(self.source)).encode()).hexdigest()[:20]
        self.local = (root or catalog_storage_dir()) / kind / f"{key}.csv"
        self.status = ""

    def resolve(self, validate, *, refresh: bool = True) -> Path:
        key = (str(self.source), str(self.local))
        with self._lock:
            recent = self._recent.get(key)
            if not refresh and recent and time.monotonic() - recent[0] < 30 and recent[1].is_file():
                self.status = recent[2]
                return recent[1]
            try:
                before = self.source.stat()
                if self.local.is_file() and self.local.stat().st_mtime_ns >= before.st_mtime_ns:
                    validate(self.local)
                    self.status = "Copie locale à jour"
                else:
                    self.local.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix=".catalog-", suffix=".csv", dir=self.local.parent)
                    temporary = Path(name)
                    try:
                        with os.fdopen(fd, "wb") as output, self.source.open("rb") as source:
                            shutil.copyfileobj(source, output)
                            output.flush()
                            os.fsync(output.fileno())
                        after = self.source.stat()
                        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                            raise ValueError("Catalogue en cours d’écriture : ancienne copie conservée")
                        validate(temporary)
                        os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
                        temporary.replace(self.local)
                    finally:
                        temporary.unlink(missing_ok=True)
                    self.status = "Source externe validée — copie locale actualisée"
            except (OSError, ValueError, UnicodeError, csv.Error) as exc:
                if not self.local.is_file():
                    raise
                validate(self.local)
                self.status = f"Copie locale de secours — source indisponible ou invalide : {exc}"
                logging.getLogger(__name__).warning(self.status)
            date = datetime.fromtimestamp(self.local.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            self.status += f" — date source : {date}"
            self._recent[key] = (time.monotonic(), self.local, self.status)
            return self.local
