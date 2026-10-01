"""Inspect the three persistent file catalogs using their actual parsers."""
from datetime import datetime, timezone
from pathlib import Path

from .bedetheque_csv import BedethequeCsvClient
from .nautiljon import NautiljonCsvClient
from .mangacollec import parse_export


FILE_SOURCES = {"bedetheque": "Bedetheque", "nautiljon": "Nautiljon", "mangacollec": "MangaCollec"}


def test_file_source(kind: str, path: str, *, fallback=False, mangacollec_store=None) -> dict:
    if kind not in FILE_SOURCES:
        raise ValueError("Source fichier inconnue")
    if not str(path).strip():
        raise ValueError("Choisissez un fichier source")
    source = Path(path).expanduser()
    resolved = source
    mode = "Fichier externe validé"
    if kind == "mangacollec":
        try:
            _, summary = parse_export(source.read_bytes(), source.name)
        except (OSError, ValueError, UnicodeError):
            if not fallback or mangacollec_store is None:
                raise
            stored = mangacollec_store.status()
            if not stored["configured"]:
                raise
            summary = stored
            resolved = mangacollec_store.catalog_path
            mode = stored["source_status"]
        count = summary["release_count"]
        validation = f"{count} sortie(s), {summary['series_count']} série(s), {summary['invalid']} ligne(s) invalide(s) écartée(s)"
    else:
        cls = BedethequeCsvClient if kind == "bedetheque" else NautiljonCsvClient
        client = cls(str(source), mirror=fallback)
        validation = client.test()
        count = len(client._rows())
        if client.catalog_mirror:
            resolved = client.catalog_mirror.local
            mode = client.catalog_mirror.status
    stat = resolved.stat()
    return {"source": kind, "label": FILE_SOURCES[kind], "path": str(source),
            "resolved_path": str(resolved), "status": mode, "validation": validation,
            "entries": count, "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")}
