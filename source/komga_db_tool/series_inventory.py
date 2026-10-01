"""Exact series paging. Residual filters always run before slicing a page."""
from copy import deepcopy
import re


def series_inventory_page(api, library_id=None, *, search="", page=0, size=100, row_filter=None):
    size = max(25, min(200, int(size)))
    page = max(0, int(page))
    native = row_filter is None and callable(getattr(getattr(api, "api", api), "series_page", None))
    if native:
        result = api.series_page(library_id=library_id or None, search=search, page=page, page_size=size)
        pages = max(1, int(result.get("total_pages") or 0))
        if page >= pages:
            result = api.series_page(library_id=library_id or None, search=search, page=pages-1, page_size=size)
            pages = max(1, int(result.get("total_pages") or 0))
        total = int(result.get("total") or 0)
        current = int(result.get("page") or 0)
        return {"items": deepcopy(result.get("items") or []), "total": total, "filtered_total": total,
                "hidden": 0, "page": current, "size": size, "total_pages": pages,
                "first": current == 0, "last": current >= pages-1, "load_mode": "komga_page"}
    rows = api.series(library_id=library_id or None, search=search, page_size=500)
    if library_id:
        rows = [row for row in rows if row.library_id == library_id]
    filtered = row_filter(rows) if row_filter else rows
    total, count = len(rows), len(filtered)
    pages = max(1, (count+size-1)//size)
    page = min(page, pages-1)
    return {"items": deepcopy(filtered[page*size:(page+1)*size]), "total": total, "filtered_total": count,
            "hidden": total-count, "page": page, "size": size, "total_pages": pages,
            "first": page == 0, "last": page >= pages-1, "load_mode": "filtered_inventory"}


def web_series_filter(*, search="", language="", status="ALL", link="ALL", empty_summary=False, hide_chapters=False):
    """Preserve the Web SeriesFilterBar rules when moving them before pagination."""
    if not any((search, language, status != "ALL", link != "ALL", empty_summary, hide_chapters)):
        return None
    def normalized(value):
        return re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())
    def keep(row):
        meta = row.metadata or {}
        if search and normalized(search) not in normalized(row.title):
            return False
        if empty_summary and str(meta.get("summary") or "").strip():
            return False
        if hide_chapters and re.search(r"\(\s*chap(?:itre)?s?\s*\)\s*$", row.title or "", re.I):
            return False
        if language and not normalized(meta.get("language")).startswith(normalized(language)):
            return False
        actual = str(meta.get("status") or "").strip().upper()
        if status == "VIDE" and actual:
            return False
        if status == "NOT_ENDED" and actual in {"ENDED", "ABANDONED"}:
            return False
        if status not in {"", "ALL", "VIDE", "NOT_ENDED"} and actual != status:
            return False
        links = meta.get("links") or []
        entries = [("", item) if isinstance(item, str) else (item.get("label", ""), item.get("url", ""))
                   for item in links if isinstance(item, (str, dict))]
        entries = [(label, url) for label, url in entries if label or url]
        if link == "__NO_LINK__":
            return not entries
        if link == "ALL":
            return True
        without = link.startswith("__WITHOUT__:")
        needle = normalized(link[12:] if without else link)
        matched = any(normalized(label) == needle or needle in normalized(f"{label} {url}") for label, url in entries)
        return not matched if without else matched
    return lambda rows: [row for row in rows if keep(row)]
