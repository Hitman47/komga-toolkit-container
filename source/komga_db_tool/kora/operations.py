from __future__ import annotations

from typing import Any

from .api import HttpError, KomgaApi
from .backup import BackupManager
from .cache import CacheStore
from .models import PendingChange, SeriesItem
from .tag_logic import extract_kora_genres, merge_series_tags_for_genres


def metadata_from_series_payload(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _cached_series_after_tags(
    change: PendingChange,
    current: dict[str, Any],
    meta_before: dict[str, Any],
    tags: list[str],
) -> SeriesItem:
    patched = dict(current)
    patched_meta = dict(meta_before)
    patched_meta["tags"] = tags
    patched["metadata"] = patched_meta
    library = patched.get("library") if isinstance(patched.get("library"), dict) else {}
    return SeriesItem(
        id=change.series_id,
        library_id=str(patched.get("libraryId") or library.get("id") or ""),
        library_name=change.library_name or str(library.get("name") or ""),
        title=change.title or str(patched_meta.get("title") or patched.get("name") or change.series_id),
        book_count=int(patched.get("bookCount") or patched.get("booksCount") or 0),
        metadata=patched_meta,
        raw=patched,
    )


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
    results: list[dict[str, Any] | None] = [None] * len(changes)
    prepared: list[tuple[int, PendingChange, dict[str, Any], dict[str, Any], list[str]]] = []
    cached_items: list[SeriesItem] = []
    satisfied_ids: list[str] = []

    # Read every target before writing anything. A stale Komga ID must not
    # interrupt a batch after earlier series have already been patched.
    for index, change in enumerate(changes):
        try:
            current = api.get_series(change.series_id)
        except HttpError as exc:
            if exc.status != 404:
                raise
            results[index] = {"series_id": change.series_id, "title": change.title, "status": "NOT_FOUND"}
            continue
        meta_before = metadata_from_series_payload(current)
        current_tags = meta_before.get("tags") if isinstance(meta_before.get("tags"), list) else []
        merged_tags = merge_series_tags_for_genres(current_tags, change.new_kora_genres)
        if merged_tags == current_tags:
            results[index] = {"series_id": change.series_id, "title": change.title, "status": "UNCHANGED"}
            if not dry_run:
                cached_items.append(_cached_series_after_tags(change, current, meta_before, current_tags))
                satisfied_ids.append(change.series_id)
            continue
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
        prepared.append((index, change, current, meta_before, merged_tags))

    # A failed backup aborts the run before its first Komga PATCH.
    json_path = csv_path = ""
    if backup_rows:
        saved_json, saved_csv = backup.save_operation_backup(
            backup_rows,
            prefix="komga_kora_backup_dry_run" if dry_run else "komga_kora_backup_apply",
        )
        json_path, csv_path = str(saved_json), str(saved_csv)

    if dry_run:
        for index, change, *_ in prepared:
            results[index] = {"series_id": change.series_id, "title": change.title, "status": "DRY_RUN"}
    else:
        for position, (index, change, current, meta_before, merged_tags) in enumerate(prepared):
            try:
                api.update_series_metadata(change.series_id, {"tags": merged_tags})
            except HttpError as exc:
                if exc.status == 404:
                    results[index] = {"series_id": change.series_id, "title": change.title, "status": "NOT_FOUND"}
                    continue
                results[index] = {"series_id": change.series_id, "title": change.title, "status": "FAILED", "error": str(exc)}
                for remaining_index, remaining_change, *_ in prepared[position + 1:]:
                    results[remaining_index] = {"series_id": remaining_change.series_id, "title": remaining_change.title, "status": "NOT_APPLIED"}
                break
            except Exception as exc:
                results[index] = {"series_id": change.series_id, "title": change.title, "status": "FAILED", "error": str(exc)}
                for remaining_index, remaining_change, *_ in prepared[position + 1:]:
                    results[remaining_index] = {"series_id": remaining_change.series_id, "title": remaining_change.title, "status": "NOT_APPLIED"}
                break
            cached_items.append(_cached_series_after_tags(change, current, meta_before, merged_tags))
            satisfied_ids.append(change.series_id)
            results[index] = {"series_id": change.series_id, "title": change.title, "status": "UPDATED"}

    cache_error = ""
    if not dry_run and satisfied_ids:
        try:
            cache.upsert_series(cached_items, {})
            cache.remove_pending(satisfied_ids)
        except Exception as exc:
            cache_error = str(exc)
    resolved_results = [row for row in results if row is not None]
    report_json = report_error = ""
    try:
        report_json = str(backup.save_results_report(resolved_results))
    except Exception as exc:
        report_error = str(exc)
    return {
        "dry_run": dry_run,
        "count": len(changes),
        "backup_json": json_path,
        "backup_csv": csv_path,
        "updated_count": sum(row["status"] == "UPDATED" for row in resolved_results),
        "unchanged_count": sum(row["status"] == "UNCHANGED" for row in resolved_results),
        "missing_count": sum(row["status"] == "NOT_FOUND" for row in resolved_results),
        "failed_count": sum(row["status"] == "FAILED" for row in resolved_results),
        "not_applied_count": sum(row["status"] == "NOT_APPLIED" for row in resolved_results),
        "cache_error": cache_error,
        "report_json": report_json,
        "report_error": report_error,
        "results": resolved_results,
    }
