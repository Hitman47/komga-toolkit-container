from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .api import KomgaApi
from .backup import BackupManager
from .cache import CacheStore
from .models import PendingChange, SeriesItem
from .tag_logic import extract_kora_genres, merge_series_tags_for_genres


def metadata_from_series_payload(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("metadata")
    return meta if isinstance(meta, dict) else {}


def apply_pending_changes(
    api: KomgaApi,
    cache: CacheStore,
    backup: BackupManager,
    changes: list[PendingChange],
    dry_run: bool = True,
) -> dict[str, Any]:
    """Relire Komga, fusionner les tags actuels et appliquer les changements.

    Seuls les tags kora:genre:* sont remplacés. Les tags non-Kora et kora:tag:* sont conservés.
    tagsLock est lu et sauvegardé, mais jamais modifié.
    """
    backup_rows: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    updated_items: list[SeriesItem] = []

    for change in changes:
        current = api.get_series(change.series_id)
        meta_before = metadata_from_series_payload(current)
        current_tags = meta_before.get("tags") if isinstance(meta_before.get("tags"), list) else []
        merged_tags = merge_series_tags_for_genres(current_tags, change.new_kora_genres)
        before_genres = extract_kora_genres(current_tags)
        payload = {"tags": merged_tags}
        backup_rows.append({
            "series_id": change.series_id,
            "library_name": change.library_name,
            "title": change.title,
            "source": change.source,
            "note": change.note,
            "before_kora_genres": before_genres,
            "after_kora_genres": change.new_kora_genres,
            "metadata_before": {
                "tags": current_tags,
                "genres": meta_before.get("genres", []),
                "tagsLock": bool(meta_before.get("tagsLock")),
            },
            "planned_metadata_after": payload,
            "raw_before": current,
        })
        if not dry_run:
            api.update_series_metadata(change.series_id, payload)
            # Rebuild cached record from current raw with patched metadata; enough for immediate UI refresh.
            patched = dict(current)
            patched_meta = dict(meta_before)
            patched_meta["tags"] = merged_tags
            patched["metadata"] = patched_meta
            lib = patched.get("library") if isinstance(patched.get("library"), dict) else {}
            updated_items.append(SeriesItem(
                id=change.series_id,
                library_id=str(patched.get("libraryId") or lib.get("id") or ""),
                library_name=change.library_name or str(lib.get("name") or ""),
                title=change.title or str(patched_meta.get("title") or patched.get("name") or change.series_id),
                book_count=int(patched.get("bookCount") or patched.get("booksCount") or 0),
                metadata=patched_meta,
                raw=patched,
            ))
        results.append({"series_id": change.series_id, "title": change.title, "status": "DRY_RUN" if dry_run else "UPDATED"})

    json_path, csv_path = backup.save_operation_backup(backup_rows, prefix="komga_kora_backup_dry_run" if dry_run else "komga_kora_backup_apply")
    if updated_items:
        # Preserve library names already in the pending rows.
        cache.upsert_series(updated_items, {})
        cache.remove_pending([c.series_id for c in changes])
    return {
        "dry_run": dry_run,
        "count": len(changes),
        "backup_json": str(json_path),
        "backup_csv": str(csv_path),
        "results": results,
    }
