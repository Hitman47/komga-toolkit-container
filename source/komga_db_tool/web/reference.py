from __future__ import annotations


REFERENCE_ID = "desktop-v3.12.0rc3-20260723"
REFERENCE_NAME = "Komga Toolkit Desktop 3.12.0rc3"
WEB_API_VERSION = "2.7.0"

# Public, non-sensitive contract used by the WebUI and deployment checks.
CAPABILITIES = (
    "explorer",
    "book_explorer",
    "metadata",
    "collections",
    "readlists",
    "bedetheque",
    "manga_news",
    "mangabaka",
    "comicvine",
    "next_releases",
    "release_tracking",
    "kora",
    "posters",
    "csv_bulk",
    "audit",
    "rollback",
    "series_fix",
    "activity",
    "external_automation",
)

EXCLUDED_CAPABILITIES = (
    "google_books",
    "komf",
)


def public_reference() -> dict[str, object]:
    return {
        "id": REFERENCE_ID,
        "name": REFERENCE_NAME,
        "web_api_version": WEB_API_VERSION,
        "capabilities": list(CAPABILITIES),
        "excluded_capabilities": list(EXCLUDED_CAPABILITIES),
    }
