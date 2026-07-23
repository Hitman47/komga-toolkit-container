from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable
from urllib.parse import urlparse

from ..manga_news import MangaNewsClient, series_slug_from_manga_news_url
from ..mangabaka import MangaBakaClient
from ..metadata_quality import (
    bedetheque_main_album_count,
    combine_release_tracking_risk,
    release_tracking_status_decision,
    release_tracking_total_decision,
)

NEXT_RELEASE_TAG_PREFIX = "nextrelease:"
GUIDED_RELEASE_TRACKING_SOURCES = {
    "bedetheque",
    "manga_news",
    "mangabaka",
    "comicvine",
}
MANGA_RELEASE_TRACKING_SOURCES = {"manga_news", "mangabaka"}

MANGA_NEWS_NON_MANGA_PATTERNS = (
    ("novel", re.compile(r"\b(roman|novel|light\s*novel)\b", re.IGNORECASE)),
    ("essay", re.compile(r"\b(essai|philosophie)\b", re.IGNORECASE)),
    ("cookbook", re.compile(r"\b(recette|recettes|cook\s*book|cookbook|cuisine)\b", re.IGNORECASE)),
    ("guide", re.compile(r"\b(guide\s*book|guidebook|guide|fan\s*book|fanbook|databook)\b", re.IGNORECASE)),
    ("artbook", re.compile(r"\b(art\s*book|artbook)\b", re.IGNORECASE)),
    ("anime_comics", re.compile(r"\b(anime\s*comics?|anime\s*comic)\b", re.IGNORECASE)),
)


def value_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def link_entries(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for entry in value_list(value):
        if isinstance(entry, dict):
            url = str(entry.get("url") or "").strip()
            label = str(entry.get("label") or "").strip()
        else:
            url = str(entry or "").strip()
            label = ""
        if url:
            rows.append({"label": label, "url": url})
    return rows


def mangabaka_id_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if "mangabaka" not in parsed.netloc.casefold():
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    for marker in ("series", "manga"):
        if marker in parts and parts.index(marker) + 1 < len(parts):
            return parts[parts.index(marker) + 1]
    return parts[-1] if parts else ""


def source_link(metadata: dict[str, Any], source: str) -> tuple[str, str]:
    fallback = ""
    for entry in link_entries(metadata.get("links")):
        url = entry["url"]
        label = entry["label"].casefold().replace(" ", "_").replace("-", "_")
        if source == "manga_news":
            source_id = series_slug_from_manga_news_url(url)
            label_match = label == "manga_news" or "manga-news" in url.casefold()
        elif source == "mangabaka":
            source_id = mangabaka_id_from_url(url)
            label_match = label == "mangabaka" or "mangabaka" in url.casefold()
        else:
            return "", ""
        if source_id:
            return source_id, url
        if label_match and not fallback:
            fallback = url
    return "", fallback


def existing_next_release_tag(metadata: dict[str, Any]) -> str:
    for tag in value_list(metadata.get("tags")):
        text = str(tag or "").strip()
        if text.casefold().startswith(NEXT_RELEASE_TAG_PREFIX):
            return text
    return ""


def next_release_tag(number: Any, release_date: str) -> str:
    if not number or not release_date:
        return ""
    try:
        yyyy, mm, dd = release_date.split("-", 2)
    except ValueError:
        return ""
    clean_number = re.sub(r"\s+", "", str(number).strip())
    return f"{NEXT_RELEASE_TAG_PREFIX}{clean_number}-{dd}.{mm}.{yyyy}"


def is_current_or_future_release_date(value: Any) -> bool:
    try:
        return date.fromisoformat(str(value or "").strip()) >= date.today()
    except (TypeError, ValueError):
        return False


def next_release_payload(current: dict[str, Any], next_tag: str) -> dict[str, Any]:
    tags = [
        str(tag).strip()
        for tag in value_list(current.get("tags"))
        if str(tag).strip() and not str(tag).strip().casefold().startswith(NEXT_RELEASE_TAG_PREFIX)
    ]
    if next_tag:
        tags.append(next_tag)
    return {"tags": tags}


def scan_next_releases(
    api: Any,
    source: str,
    series_ids: list[str],
    manga_news: MangaNewsClient | None,
    mangabaka: MangaBakaClient | None,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(series_ids)
    for index, series_id in enumerate(series_ids, start=1):
        if cancelled():
            break
        entity = api.get_series(series_id)
        metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
        title = str(metadata.get("title") or entity.get("name") or series_id)
        progress(index, total, title)
        source_id, url = source_link(metadata, source)
        row: dict[str, Any] = {
            "series_id": series_id,
            "title": title,
            "source": source,
            "source_id": source_id,
            "source_url": url,
            "status": metadata.get("status", ""),
            "old_tag": existing_next_release_tag(metadata),
            "new_tag": "",
            "volume": "",
            "date": "",
            "action": "",
            "error": "",
        }
        if not source_id and not url:
            row["action"] = "Ignoré : aucun lien source"
            rows.append(row)
            continue
        try:
            if source == "manga_news":
                if manga_news is None:
                    raise RuntimeError("Client Manga News indisponible")
                candidate = manga_news.get_next_release(slug=source_id, url=url)
                row["raw"] = MangaNewsClient.next_release_candidate_to_dict(candidate)
            elif source == "mangabaka":
                if mangabaka is None or not source_id:
                    raise RuntimeError("ID MangaBaka introuvable")
                candidate = mangabaka.get_next_release(source_id)
                row["raw"] = MangaBakaClient.next_release_candidate_to_dict(candidate)
            else:
                raise ValueError("Source prochaine sortie invalide")
            row["source_url"] = candidate.source_url or url
            row["volume"] = candidate.number
            row["date"] = candidate.release_date
            row["new_tag"] = (
                next_release_tag(candidate.number, candidate.release_date)
                if is_current_or_future_release_date(candidate.release_date)
                else ""
            )
            if not row["new_tag"]:
                row["action"] = "Aucune prochaine sortie"
            elif row["new_tag"] == row["old_tag"]:
                row["action"] = "Déjà à jour"
            else:
                row["action"] = "À appliquer"
                row["payload"] = next_release_payload(metadata, row["new_tag"])
        except Exception as exc:
            row["action"] = "Erreur"
            row["error"] = str(exc)
        rows.append(row)
    return rows


def prepare_next_release_automation(
    api: Any,
    source: str,
    library_id: str,
    manga_news: MangaNewsClient | None,
    mangabaka: MangaBakaClient | None,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """Build a confirmation plan containing only valid dated tag changes."""
    if source not in {"manga_news", "mangabaka"}:
        raise ValueError("Source prochaine sortie invalide")
    loaded = list(api.series(library_id=library_id or None, page_size=200) or [])
    non_ended: list[Any] = []
    linked: list[Any] = []
    for series in loaded:
        metadata = getattr(series, "metadata", {}) or {}
        if str(metadata.get("status") or "").strip().upper() in {"ENDED", "ABANDONED"}:
            continue
        non_ended.append(series)
        source_id, url = source_link(metadata, source)
        if source_id or url:
            linked.append(series)
    ids = [
        str(getattr(series, "id", "") or "")
        for series in linked
        if str(getattr(series, "id", "") or "")
    ]
    rows = scan_next_releases(
        api,
        source,
        ids,
        manga_news,
        mangabaka,
        progress,
        cancelled,
    ) if ids else []
    changes = [
        row for row in rows
        if row.get("action") == "À appliquer"
        and isinstance(row.get("payload"), dict)
        and row.get("new_tag")
        and is_current_or_future_release_date(row.get("date"))
    ]
    return {
        "mode": "next_release_preview",
        "source": source,
        "library_id": library_id,
        "loaded": len(loaded),
        "non_ended": len(non_ended),
        "linked": len(ids),
        "changes": len(changes),
        "unchanged": sum(1 for row in rows if row.get("action") == "Déjà à jour"),
        "no_release": sum(1 for row in rows if row.get("action") == "Aucune prochaine sortie"),
        "errors": sum(1 for row in rows if row.get("action") == "Erreur"),
        "returned": len(changes),
        "rows": changes,
    }


def apply_next_release_automation(
    api: Any,
    source: str,
    preview_result: dict[str, Any],
    operations: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """Revalidate and apply only the dated tag changes from a preview."""
    if preview_result.get("mode") != "next_release_preview" or preview_result.get("source") != source:
        raise ValueError("Prévisualisation prochaines sorties invalide")
    candidates = [
        row for row in list(preview_result.get("rows") or [])
        if isinstance(row, dict)
        and row.get("action") == "À appliquer"
        and row.get("new_tag")
        and is_current_or_future_release_date(row.get("date"))
    ]
    applied = unchanged = failed = skipped_guardrail = 0
    results: list[dict[str, Any]] = []
    total = len(candidates)
    for index, row in enumerate(candidates, start=1):
        if cancelled():
            break
        result_row = dict(row)
        title = str(row.get("title") or row.get("series_id") or "")
        progress(index, total, f"Application prochaine sortie — {title}")
        try:
            entity = api.get_series(str(row.get("series_id") or ""))
            current = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
            status = str(current.get("status") or "").strip().upper()
            source_id, url = source_link(current, source)
            if status in {"ENDED", "ABANDONED"} or not (source_id or url):
                skipped_guardrail += 1
                result_row["operation_status"] = "skipped_guardrail"
                results.append(result_row)
                continue
            new_tag = str(row.get("new_tag") or "")
            if not new_tag or not is_current_or_future_release_date(row.get("date")):
                skipped_guardrail += 1
                result_row["operation_status"] = "skipped_guardrail"
                results.append(result_row)
                continue
            result_row["old_tag"] = existing_next_release_tag(current)
            if result_row["old_tag"] == new_tag:
                unchanged += 1
                result_row["operation_status"] = "unchanged"
                results.append(result_row)
                continue
            payload = next_release_payload(current, new_tag)
            preview = operations.preview_metadata(
                api,
                "series",
                str(row.get("series_id") or ""),
                payload,
                source=f"{source}_next_release_guided",
            )
            result = operations.apply_metadata(api, str(preview["token"]))
            result_row["operation_status"] = result.get("status", "")
            if result.get("status") == "applied":
                applied += 1
            else:
                unchanged += 1
        except Exception as exc:
            failed += 1
            result_row["operation_status"] = "error"
            result_row["error"] = str(exc)
        results.append(result_row)
    return {
        "mode": "next_release_apply",
        "source": source,
        "planned": total,
        "applied": applied,
        "unchanged": unchanged,
        "failed": failed,
        "skipped_guardrail": skipped_guardrail,
        "cancelled": bool(cancelled()),
        "rows": results,
    }


def run_next_release_automation(
    api: Any,
    source: str,
    library_id: str,
    manga_news: MangaNewsClient | None,
    mangabaka: MangaBakaClient | None,
    operations: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """Scan, revalidate and apply safe next-release changes in one job."""
    preview = prepare_next_release_automation(
        api,
        source,
        library_id,
        manga_news,
        mangabaka,
        progress,
        cancelled,
    )
    application = apply_next_release_automation(
        api,
        source,
        preview,
        operations,
        progress,
        cancelled,
    )
    applied_rows = [
        {
            "series_id": row.get("series_id", ""),
            "title": row.get("title", ""),
            "source": source,
            "old_tag": row.get("old_tag", ""),
            "new_tag": row.get("new_tag", ""),
            "volume": row.get("volume", ""),
            "date": row.get("date", ""),
        }
        for row in application.get("rows", [])
        if row.get("operation_status") == "applied"
    ]
    return {
        "mode": "next_release_auto",
        "source": source,
        "scanned": preview.get("linked", 0),
        "valid_changes": preview.get("changes", 0),
        "applied": application.get("applied", 0),
        "unchanged": application.get("unchanged", 0),
        "skipped_guardrail": application.get("skipped_guardrail", 0),
        "failed": application.get("failed", 0),
        "cancelled": application.get("cancelled", False),
        "rows": applied_rows,
    }


def automate_next_releases(
    api: Any,
    source: str,
    library_id: str,
    manga_news: MangaNewsClient | None,
    mangabaka: MangaBakaClient | None,
    operations: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """Scan eligible series and apply only changed next-release tags.

    Every write still goes through WebOperationService so the current metadata is
    checked again and a rollback snapshot is created before the Komga PATCH.
    """
    loaded = list(api.series(library_id=library_id or None, page_size=200) or [])
    eligible: list[Any] = []
    for row in loaded:
        metadata = getattr(row, "metadata", {}) or {}
        status = str(metadata.get("status") or "").strip().upper()
        if status in {"ENDED", "ABANDONED"}:
            continue
        source_id, url = source_link(metadata, source)
        if source_id or url:
            eligible.append(row)
    ids = [str(getattr(row, "id", "") or "") for row in eligible if str(getattr(row, "id", "") or "")]
    total = max(1, len(ids) * 2)

    def scan_progress(current: int, _total: int, message: str) -> None:
        progress(current, total, f"Scan — {message}")

    rows = scan_next_releases(api, source, ids, manga_news, mangabaka, scan_progress, cancelled) if ids else []
    applied = unchanged = failed = 0
    for index, row in enumerate(rows, start=1):
        if cancelled():
            break
        progress(len(ids) + index, total, f"Application — {row.get('title') or row.get('series_id')}")
        if row.get("action") != "À appliquer" or not isinstance(row.get("payload"), dict):
            continue
        try:
            preview = operations.preview_metadata(
                api,
                "series",
                str(row.get("series_id") or ""),
                dict(row["payload"]),
                source=f"{source}_next_release_auto",
            )
            result = operations.apply_metadata(api, str(preview["token"]))
            row["operation_status"] = result.get("status", "")
            if result.get("status") == "applied":
                applied += 1
            else:
                unchanged += 1
        except Exception as exc:
            failed += 1
            row["operation_status"] = "error"
            row["error"] = str(exc)
    return {
        "loaded": len(loaded),
        "eligible": len(ids),
        "changes": sum(1 for row in rows if row.get("action") == "À appliquer"),
        "applied": applied,
        "unchanged": unchanged,
        "failed": failed,
        "cancelled": bool(cancelled()),
        "rows": rows,
    }


def comicvine_id_from_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if "comicvine" not in parsed.netloc.casefold():
        return ""
    match = re.search(r"(?:^|/)(?:4050-)?(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else ""


def release_source_link(metadata: dict[str, Any], source: str) -> tuple[str, str]:
    if source in {"manga_news", "mangabaka"}:
        return source_link(metadata, source)
    for entry in link_entries(metadata.get("links")):
        url = entry["url"]
        label = entry["label"].casefold().replace(" ", "_").replace("-", "_")
        if source == "bedetheque":
            if label == "bedetheque" or "bedetheque.com" in url.casefold():
                return url, url
            continue
        source_id = comicvine_id_from_url(url)
        if source_id:
            return source_id, url
    return "", ""


def release_source_kind(source: str, raw: dict[str, Any], title: str = "") -> str:
    """Return a usable media kind even when Manga News omits media_kind.

    The Manga News series endpoint exposes a source ``type`` but not the
    enriched search-only ``media_kind``. Series pages are manga by default,
    except for explicit book-like signals that mirror the API classifier.
    """
    explicit = str(raw.get("media_kind") or "").strip()
    if explicit:
        return explicit
    if source != "manga_news":
        return str(raw.get("type") or raw.get("kind") or "").strip()
    blob = " ".join(
        value for value in (str(title or "").strip(), str(raw.get("type") or "").strip())
        if value
    )
    for kind, pattern in MANGA_NEWS_NON_MANGA_PATTERNS:
        if pattern.search(blob):
            return kind
    return "manga"


def _tracking_candidate(source: str, source_id: str, url: str, client: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if source == "manga_news":
        candidate = client.get_series(source_id) if source_id else client.get_series_by_url(url)
    elif source == "mangabaka":
        candidate = client.get_series(source_id)
    elif source == "comicvine":
        candidate = client.get_volume(source_id)
    elif source == "bedetheque":
        candidate = client.scrape_series(url or source_id)
    else:
        raise ValueError("Source de suivi invalide")
    proposed = dict(candidate.series_metadata or {})
    raw = getattr(candidate, "raw", {}) or {}
    source_kind = release_source_kind(
        source,
        raw,
        str(proposed.get("title") or getattr(candidate, "title", "") or ""),
    )
    details: dict[str, Any] = {
        "source": source,
        "source_url": getattr(candidate, "source_url", "") or url,
        "source_kind": source_kind,
    }
    if source == "bedetheque":
        albums = (getattr(candidate, "raw", {}) or {}).get("albums", [])
        filtered_count = bedetheque_main_album_count(albums)
        if filtered_count:
            proposed["totalBookCount"] = filtered_count
        details.update({"raw_count": len(albums) if isinstance(albums, list) else 0, "filtered_count": filtered_count})
    return proposed, details


def _auto_tracking_candidate(current: dict[str, Any], clients: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    bdt_id, bdt_url = release_source_link(current, "bedetheque")
    mbk_id, mbk_url = release_source_link(current, "mangabaka")
    language = str(current.get("language") or "").strip().casefold().split("-", 1)[0]
    bdt: tuple[dict[str, Any], dict[str, Any]] | None = None
    mbk: tuple[dict[str, Any], dict[str, Any]] | None = None
    if bdt_url and (language == "fr" or not mbk_id):
        bdt = _tracking_candidate("bedetheque", bdt_id, bdt_url, clients["bedetheque"])
    if mbk_id and (language != "fr" or bdt is None):
        mbk = _tracking_candidate("mangabaka", mbk_id, mbk_url, clients["mangabaka"])
    if bdt is not None and not bdt[0].get("status") and mbk_id:
        try:
            mbk = mbk or _tracking_candidate("mangabaka", mbk_id, mbk_url, clients["mangabaka"])
        except Exception:
            pass
    if bdt is None and mbk is None:
        raise ValueError("Aucun lien Bedetheque ou MangaBaka exploitable")
    count_data = bdt or mbk
    status_data = bdt if bdt and bdt[0].get("status") else (mbk or bdt)
    proposed: dict[str, Any] = {}
    if count_data and count_data[0].get("totalBookCount") not in (None, ""):
        proposed["totalBookCount"] = count_data[0]["totalBookCount"]
    if status_data and status_data[0].get("status"):
        proposed["status"] = status_data[0]["status"]
    details = {
        "source": "auto: " + ", ".join(filter(None, [
            f"count={count_data[1]['source']}" if count_data else "",
            f"status={status_data[1]['source']}" if status_data else "",
        ])),
        "source_url": (count_data or status_data or ({}, {}))[1].get("source_url", ""),
    }
    if count_data:
        details.update({key: count_data[1][key] for key in ("raw_count", "filtered_count") if key in count_data[1]})
    return proposed, details


def scan_release_tracking(
    api: Any,
    source: str,
    series_ids: list[str],
    client: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total = len(series_ids)
    for index, series_id in enumerate(series_ids, start=1):
        if cancelled():
            break
        entity = api.get_series(series_id)
        current = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
        title = str(current.get("title") or entity.get("name") or series_id)
        progress(index, total, title)
        source_id, url = release_source_link(current, source)
        if source == "auto":
            bdt_id, bdt_url = release_source_link(current, "bedetheque")
            mbk_id, mbk_url = release_source_link(current, "mangabaka")
            source_id, url = bdt_id or mbk_id, bdt_url or mbk_url
        row: dict[str, Any] = {"series_id": series_id, "title": title, "source": source, "source_id": source_id, "source_url": url}
        if not source_id and not url:
            row.update({"action": "Ignoré : aucun lien source", "payload": {}})
            rows.append(row)
            continue
        try:
            if source == "auto":
                proposed, details = _auto_tracking_candidate(current, client)
            else:
                selected_client = client[source] if isinstance(client, dict) else client
                proposed, details = _tracking_candidate(source, source_id, url, selected_client)
            total_decision = release_tracking_total_decision(current.get("totalBookCount"), proposed.get("totalBookCount"))
            status_decision = release_tracking_status_decision(current.get("status"), proposed.get("status"))
            payload: dict[str, Any] = {}
            if total_decision.get("proposed") is not None:
                payload["totalBookCount"] = total_decision.get("proposed")
            if status_decision.get("proposed"):
                payload["status"] = status_decision.get("proposed")
            risk = combine_release_tracking_risk(total_decision.get("risk"), status_decision.get("risk"))
            if details.get("raw_count") not in (None, "") and details.get("filtered_count") not in (None, "") and str(details["raw_count"]) != str(details["filtered_count"]) and risk == "Faible":
                risk = "Moyen"
            apply_status = bool(status_decision.get("selected")) and risk == "Faible"
            apply_total = bool(total_decision.get("selected")) and risk == "Faible"
            action = (
                "À appliquer"
                if apply_status or apply_total
                else ("À vérifier" if payload else "Aucun changement prudent")
            )
            row.update({
                "source": details.get("source", source),
                "source_url": details.get("source_url", url),
                "raw_count": details.get("raw_count", ""),
                "filtered_count": details.get("filtered_count", ""),
                "source_kind": details.get("source_kind", ""),
                "current_total": current.get("totalBookCount"),
                "source_total": proposed.get("totalBookCount"),
                "current_status": current.get("status"),
                "source_status": proposed.get("status"),
                "total_decision": total_decision,
                "status_decision": status_decision,
                "risk": risk,
                "apply_status": apply_status,
                "apply_totalBookCount": apply_total,
                "payload": payload,
                "action": action,
            })
        except Exception as exc:
            row.update({"action": "Erreur", "payload": {}, "error": str(exc)})
        rows.append(row)
    return rows


def guided_high_confidence_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Return only changes that pass the strict automatic guardrails."""
    if row.get("risk") != "Faible" or row.get("action") == "Erreur":
        return {}
    proposed = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    payload: dict[str, Any] = {}
    if row.get("apply_status") and proposed.get("status"):
        status_check = release_tracking_status_decision(row.get("current_status"), proposed.get("status"))
        if status_check.get("risk") == "Faible" and status_check.get("selected") and status_check.get("proposed") == proposed.get("status"):
            payload["status"] = proposed["status"]
    if row.get("apply_totalBookCount") and proposed.get("totalBookCount") not in (None, ""):
        total_check = release_tracking_total_decision(row.get("current_total"), proposed.get("totalBookCount"))
        try:
            current = int(row.get("current_total") or 0)
            candidate = int(proposed["totalBookCount"])
        except (TypeError, ValueError):
            candidate = 0
            current = 0
        # A known total can only increase by one to three. A decrease, equality,
        # or a jump of four or more is never eligible for guided application.
        if total_check.get("risk") == "Faible" and total_check.get("selected") and candidate > 0 and (current <= 0 or 1 <= candidate - current <= 3):
            payload["totalBookCount"] = candidate
    return payload


def prepare_guided_release_tracking(
    api: Any,
    source: str,
    library_id: str,
    client: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    if source not in GUIDED_RELEASE_TRACKING_SOURCES:
        raise ValueError("Source guidée invalide")
    loaded = list(api.series(library_id=library_id or None, page_size=200) or [])
    non_ended: list[Any] = []
    linked: list[Any] = []
    for series in loaded:
        metadata = getattr(series, "metadata", {}) or {}
        if str(metadata.get("status") or "").strip().upper() in {"ENDED", "ABANDONED"}:
            continue
        non_ended.append(series)
        source_id, url = release_source_link(metadata, source)
        if source_id or url:
            linked.append(series)
    ids = [str(getattr(series, "id", "") or "") for series in linked if str(getattr(series, "id", "") or "")]
    rows = scan_release_tracking(api, source, ids, client, progress, cancelled) if ids else []
    counts = {"high_confidence": 0, "review": 0, "ignored": 0, "errors": 0, "non_manga": 0}
    valid_changes: list[dict[str, Any]] = []
    for row in rows:
        kind = str(row.get("source_kind") or "").strip().casefold()
        if source in MANGA_RELEASE_TRACKING_SOURCES and (not kind or "manga" not in kind):
            row["confidence"] = "Exclu : type source non manga ou inconnu"
            row["guided_payload"] = {}
            counts["non_manga"] += 1
            continue
        guided_payload = guided_high_confidence_payload(row)
        row["guided_payload"] = guided_payload
        if guided_payload:
            row["confidence"] = "Élevée — prêt après confirmation"
            counts["high_confidence"] += 1
            valid_changes.append(row)
        elif row.get("action") == "Erreur":
            row["confidence"] = "Erreur"
            counts["errors"] += 1
        elif isinstance(row.get("payload"), dict) and row["payload"]:
            row["confidence"] = "À vérifier — jamais appliqué automatiquement"
            counts["review"] += 1
        else:
            row["confidence"] = "Ignoré — aucun changement sûr"
            counts["ignored"] += 1
    return {
        "mode": "guided_preview",
        "source": source,
        "library_id": library_id,
        "loaded": len(loaded),
        "non_ended": len(non_ended),
        "linked": len(ids),
        **counts,
        "returned": len(valid_changes),
        # External callers only receive actionable, high-confidence changes.
        # Unsafe/unchanged/error rows remain represented by aggregate counters
        # and can never enter the confirmation plan.
        "rows": valid_changes,
    }


def apply_guided_release_tracking(
    api: Any,
    source: str,
    preview_result: dict[str, Any],
    operations: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    if preview_result.get("mode") != "guided_preview" or preview_result.get("source") != source:
        raise ValueError("Préparation guidée invalide")
    candidates = [
        row for row in list(preview_result.get("rows") or [])
        if isinstance(row, dict) and guided_high_confidence_payload(row)
    ]
    applied = unchanged = failed = 0
    results: list[dict[str, Any]] = []
    total = len(candidates)
    for index, row in enumerate(candidates, start=1):
        if cancelled():
            break
        title = str(row.get("title") or row.get("series_id") or "")
        progress(index, total, f"Application haute confiance — {title}")
        result_row = dict(row)
        try:
            # A preview can become stale. Re-read Komga and recompute every
            # guardrail immediately before writing.
            live_entity = api.get_series(str(row.get("series_id") or ""))
            live = live_entity.get("metadata") if isinstance(live_entity.get("metadata"), dict) else {}
            live_status = str(live.get("status") or "").strip().upper()
            if live_status in {"ENDED", "ABANDONED"}:
                result_row["operation_status"] = "skipped_guardrail"
                result_row["error"] = "Série désormais terminée ou abandonnée"
                results.append(result_row)
                continue

            proposed = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            total_decision = release_tracking_total_decision(
                live.get("totalBookCount"), proposed.get("totalBookCount")
            )
            status_decision = release_tracking_status_decision(
                live.get("status"), proposed.get("status")
            )
            risk = combine_release_tracking_risk(
                total_decision.get("risk"), status_decision.get("risk")
            )
            revalidated = {
                **row,
                "current_total": live.get("totalBookCount"),
                "current_status": live.get("status"),
                "risk": risk,
                "apply_status": bool(status_decision.get("selected")) and risk == "Faible",
                "apply_totalBookCount": bool(total_decision.get("selected")) and risk == "Faible",
            }
            payload = guided_high_confidence_payload(revalidated)
            if not payload:
                result_row["operation_status"] = "skipped_guardrail"
                result_row["error"] = "Changement devenu non sûr depuis la prévisualisation"
                results.append(result_row)
                continue

            result_row.update({
                "current_total": live.get("totalBookCount"),
                "current_status": live.get("status"),
                "guided_payload": payload,
            })

            preview = operations.preview_metadata(
                api,
                "series",
                str(row.get("series_id") or ""),
                payload,
                source=f"{source}_release_tracking_guided",
            )
            result = operations.apply_metadata(api, str(preview["token"]))
            result_row["operation_status"] = result.get("status", "")
            if result.get("status") == "applied":
                applied += 1
            else:
                unchanged += 1
        except Exception as exc:
            failed += 1
            result_row["operation_status"] = "error"
            result_row["error"] = str(exc)
        results.append(result_row)
    return {
        "mode": "guided_apply",
        "source": source,
        "planned": total,
        "applied": applied,
        "unchanged": unchanged,
        "failed": failed,
        "skipped_guardrail": sum(
            1 for row in results if row.get("operation_status") == "skipped_guardrail"
        ),
        "cancelled": bool(cancelled()),
        "rows": results,
    }


def run_guided_release_tracking_automation(
    api: Any,
    source: str,
    library_id: str,
    client: Any,
    operations: Any,
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    """Apply only high-confidence tracking changes without a second call."""
    preview = prepare_guided_release_tracking(
        api,
        source,
        library_id,
        client,
        progress,
        cancelled,
    )
    application = apply_guided_release_tracking(
        api,
        source,
        preview,
        operations,
        progress,
        cancelled,
    )
    applied_rows: list[dict[str, Any]] = []
    for row in application.get("rows", []):
        if row.get("operation_status") != "applied":
            continue
        payload = guided_high_confidence_payload(row)
        applied_rows.append({
            "series_id": row.get("series_id", ""),
            "title": row.get("title", ""),
            "source": source,
            "current_status": row.get("current_status", ""),
            "new_status": payload.get("status", ""),
            "current_totalBookCount": row.get("current_total", ""),
            "new_totalBookCount": payload.get("totalBookCount", ""),
        })
    return {
        "mode": "release_tracking_auto",
        "source": source,
        "scanned": preview.get("linked", 0),
        "high_confidence": preview.get("high_confidence", 0),
        "applied": application.get("applied", 0),
        "unchanged": application.get("unchanged", 0),
        "skipped_guardrail": application.get("skipped_guardrail", 0),
        "failed": application.get("failed", 0),
        "cancelled": application.get("cancelled", False),
        "rows": applied_rows,
    }
