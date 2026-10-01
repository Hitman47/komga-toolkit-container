"""Filters on currently assigned genres, never on uncommitted suggestions."""
from ..chapter_cleanup import has_chapter_title_suffix


def matches_inventory_filters(record, *, hide_chapters=False, max_genres=None):
    if hide_chapters and has_chapter_title_suffix(record.title):
        return False
    return max_genres is None or len(set(record.kora_genres)) <= max_genres
