from __future__ import annotations

import re
from typing import Any, Iterable, List

SEARCH_TAG_RE = re.compile(r"\s*[\(\[\{]\s*(?:EN|INT|OS)\s*[\)\]\}]\s*", re.IGNORECASE)
SEARCH_SEPARATOR_RE = re.compile(r"[!?:;\-_–—]+")
SEARCH_QUOTES_RE = re.compile(r"[\"“”‘’`]+")
SUMMARY_MIN_SIGNIFICANT_CHARS = 80
CRITICAL_SERIES_UPDATE_FIELDS = {"status", "totalBookCount"}
SUPPORTED_WRITE_LANGUAGES = {"fr", "en"}
CHAP_SCAN_EXCLUDED_SEGMENTS = {"chap", "scan"}

LOW_VALUE_SUMMARY_PATTERNS = [
    re.compile(r"^(?:tout\s+sur\s+la\s+s[ée]rie|all\s+about\s+the\s+series)\b", re.IGNORECASE),
    re.compile(r"^(?:retrouvez\s+tous\s+les\s+albums\s+de|retrouvez\s+toute\s+la\s+s[ée]rie)\b", re.IGNORECASE),
    re.compile(r"^(?:fiche\s+de\s+la\s+s[ée]rie|fiche\s+s[ée]rie)\b", re.IGNORECASE),
    re.compile(r"^(?:r[ée]sum[ée]\s+indisponible|aucun\s+r[ée]sum[ée]|no\s+summary)\b", re.IGNORECASE),
]


def compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()



def normalize_write_language(value: Any) -> str:
    """Return a Komga language value only when it is allowed for writing.

    User rule: automatic metadata updates may write only FR or EN. Any other
    language code from external sources (ko, ja, es, etc.) must be ignored even
    when Komga is blank.
    """
    text = scalar_metadata_text(value).strip().casefold().replace("_", "-")
    aliases = {
        "fre": "fr",
        "fra": "fr",
        "french": "fr",
        "français": "fr",
        "francais": "fr",
        "eng": "en",
        "english": "en",
        "anglais": "en",
    }
    text = aliases.get(text, text)
    if text.startswith("fr-"):
        text = "fr"
    elif text.startswith("en-"):
        text = "en"
    return text if text in SUPPORTED_WRITE_LANGUAGES else ""


def is_supported_write_language(value: Any) -> bool:
    return bool(normalize_write_language(value))


def path_has_chap_scan_segment(value: Any) -> bool:
    """Return True only for exact /Chap/ or /Scan/ path segments.

    The check is intentionally narrow to avoid hiding legitimate series whose
    names merely contain words such as Chaplin, Chapter, Scans, or Scanlation.
    """
    raw = scalar_metadata_text(value)
    if not raw:
        return False
    text = raw.replace("\\", "/")
    # Remove query/fragment while preserving local paths without a scheme.
    text = text.split("?", 1)[0].split("#", 1)[0]
    try:
        from urllib.parse import unquote, urlparse
        parsed = urlparse(text)
        if parsed.scheme or parsed.netloc:
            text = parsed.path
        text = unquote(text)
    except Exception:
        pass
    segments = [segment.strip().casefold() for segment in text.split("/") if segment.strip()]
    return any(segment in CHAP_SCAN_EXCLUDED_SEGMENTS for segment in segments)

def should_auto_apply_changed_metadata_field(field: str, target_type: str = "series") -> bool:
    """Return True for changed fields that should be auto-included.

    `status` and `totalBookCount` are critical series fields for refresh
    workflows such as Update with link. They must be refreshed when the source
    changed them, even if Komga already has a non-empty value.
    """
    if field in {"title", "titleSort"}:
        return True
    if target_type == "series" and field in CRITICAL_SERIES_UPDATE_FIELDS:
        return True
    return False


def bedetheque_main_album_count(albums: Any) -> int:
    """Return a conservative count of main Bedetheque albums.

    Bedetheque pages can include reedition/admin rows. Prefer unique numeric
    album numbers when present; otherwise count unique non-generic titles.
    """
    if not isinstance(albums, list):
        return 0
    numeric_numbers: set[int] = set()
    fallback_titles: set[str] = set()
    for item in albums:
        if not isinstance(item, dict):
            continue
        raw_number = str(item.get("number") or "").strip()
        raw_title = scalar_metadata_text(item.get("title") or "").strip()
        folded_title = clean_title_for_search(raw_title).casefold()
        if not raw_title or folded_title.startswith(("reeditions", "rééditions", "voir la fiche", "identifiant")):
            continue
        number_match = re.search(r"\d+", raw_number)
        if number_match:
            try:
                numeric_numbers.add(int(number_match.group(0)))
            except ValueError:
                pass
        else:
            fallback_titles.add(folded_title or raw_title.casefold())
    if numeric_numbers:
        return len(numeric_numbers)
    return len(fallback_titles)


def clean_title_for_search(value: Any) -> str:
    """Aggressive title cleanup for external search queries only.

    This must not be used for writing metadata back to Komga.
    """
    text = str(value or "")
    text = SEARCH_TAG_RE.sub(" ", text)
    text = SEARCH_SEPARATOR_RE.sub(" ", text)
    text = SEARCH_QUOTES_RE.sub("'", text)
    text = re.sub(r"\s+/\s+", " / ", text)
    return compact_spaces(text).strip(" .,/\t\r\n")


def _drop_parenthetical_search_parts(value: Any) -> str:
    """Remove short parenthetical parts for fallback search only.

    External sites often fail on edition tags such as "(Perfect Edition)".
    This is intentionally not used for metadata writing.
    """
    text = SEARCH_TAG_RE.sub(" ", str(value or ""))
    text = re.sub(r"\s*[\(\[\{][^\)\]\}]{1,80}[\)\]\}]\s*", " ", text)
    return clean_title_for_search(text)


def _drop_search_apostrophes(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[’'`´]", "", text)
    return compact_spaces(text)


def _space_search_apostrophes(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"[’'`´]", " ", text)
    return compact_spaces(text)


def _drop_low_value_search_words(value: Any) -> str:
    text = str(value or "")
    # Words that often block Bedetheque/MangaBaka searches but rarely identify
    # the series alone. Keep source order otherwise.
    text = re.sub(r"\b(?:it\s*s|its|s|an|a|the|le|la|les|un|une|des|du|de|d|l)\b", " ", text, flags=re.IGNORECASE)
    return compact_spaces(text)


def _drop_edition_search_words(value: Any) -> str:
    text = str(value or "")
    text = re.sub(
        r"\b(?:perfect|deluxe|collector'?s?|collectors?|complete|ultimate|black|white|kanzenban|wideban|omnibus|édition|edition)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return compact_spaces(text)


def build_search_queries(value: Any, *, max_queries: int = 10) -> List[str]:
    """Return ordered fallback queries for external metadata searches.

    The first query stays close to the user/Komga title. Later queries are
    progressively looser and are used only if previous variants return no
    result. These strings must never be written back to Komga metadata.
    """
    variants: List[str] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        query = clean_title_for_search(candidate)
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            variants.append(query)

    primary = clean_title_for_search(value)
    no_parens = _drop_parenthetical_search_parts(value)

    for candidate in (
        primary,
        _drop_search_apostrophes(primary),
        _space_search_apostrophes(primary),
        no_parens,
        _drop_search_apostrophes(no_parens),
        _space_search_apostrophes(no_parens),
        _drop_edition_search_words(no_parens),
        _drop_low_value_search_words(_space_search_apostrophes(_drop_edition_search_words(no_parens))),
        _drop_low_value_search_words(_drop_search_apostrophes(_drop_edition_search_words(no_parens))),
    ):
        add(candidate)

    # Last-resort broad query: the first meaningful token. This handles cases
    # where Bedetheque is very sensitive to apostrophes/edition suffixes, e.g.
    # "Eden It's An Endless World (Perfect Edition)" -> "Eden".
    tokens = [t for t in re.split(r"\s+", clean_title_for_search(no_parens)) if len(t) >= 3]
    if tokens:
        add(tokens[0])

    return variants[:max_queries]


def clean_title_for_compare(value: Any) -> str:
    """Stable comparison key for fuzzy/exact title matching."""
    text = clean_title_for_search(value).casefold()
    text = re.sub(r"[\"'.,/\\()\[\]{}]", " ", text)
    return compact_spaces(text)


def clean_title_for_write(value: Any) -> str:
    """Conservative cleanup allowed before writing a title to Komga.

    It removes only local workflow tags and repeated whitespace. It deliberately
    keeps punctuation such as ':' or '-' because those may be part of the title.
    """
    text = str(value or "")
    text = SEARCH_TAG_RE.sub(" ", text)
    return compact_spaces(text)


def one_line(value: Any) -> str:
    if value is None:
        return "<NULL>"
    if isinstance(value, (list, tuple, set)):
        return "\n".join(one_line(x) for x in value if str(x).strip())
    if isinstance(value, dict):
        import json
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def normalized_summary_text(value: Any) -> str:
    text = one_line(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return compact_spaces(text)


def significant_summary_length(value: Any) -> int:
    text = normalized_summary_text(value)
    return len(re.sub(r"\s+", "", text))


def is_blank_metadata_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _summary_repeats_title(summary: str, title: str | None = None) -> bool:
    if not title:
        return False
    summary_key = clean_title_for_compare(summary)
    title_key = clean_title_for_compare(title)
    if not summary_key or not title_key:
        return False
    return summary_key in {title_key, f"serie {title_key}", f"série {title_key}"}


def is_low_value_summary(value: Any, *, title: str | None = None) -> bool:
    """Return True for empty, generic, or too-short summaries.

    The goal is to prevent automatic pollution. Users can still manually include
    a rejected summary if they explicitly want it.
    """
    if is_blank_metadata_value(value):
        return True
    raw = one_line(value)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    text = normalized_summary_text(value)
    if not text:
        return True
    if len(lines) <= 1 and any(pattern.match(text) for pattern in LOW_VALUE_SUMMARY_PATTERNS):
        return True
    if _summary_repeats_title(text, title):
        return True
    return significant_summary_length(text) < SUMMARY_MIN_SIGNIFICANT_CHARS


def scalar_metadata_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        parts: List[str] = []
        for item in value:
            text = one_line(item).strip()
            if text and text != "<NULL>":
                parts.append(text)
        return "\n".join(parts).strip()
    return one_line(value).strip()


def _isbn10_is_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dXx]", value or ""):
        return False
    total = 0
    for index, char in enumerate(value[:9], start=1):
        total += index * int(char)
    check = 10 if value[-1].upper() == "X" else int(value[-1])
    total += 10 * check
    return total % 11 == 0


def _isbn13_is_valid(value: str) -> bool:
    if not re.fullmatch(r"\d{13}", value or ""):
        return False
    total = 0
    for index, char in enumerate(value[:12]):
        total += int(char) * (1 if index % 2 == 0 else 3)
    check = (10 - (total % 10)) % 10
    return check == int(value[-1])


def normalize_isbn_value(value: Any) -> str:
    raw = scalar_metadata_text(value)
    if not raw:
        return ""
    raw = raw.replace("ISBN", " ").replace("isbn", " ")
    raw = re.sub(r"[–—−]", "-", raw)
    candidates = re.findall(r"[0-9Xx][0-9Xx\-\s]{8,20}[0-9Xx]", raw)
    candidates.append(raw)
    for candidate in candidates:
        cleaned = re.sub(r"[^0-9Xx]", "", candidate).upper()
        if len(cleaned) == 13 and _isbn13_is_valid(cleaned):
            return cleaned
        if len(cleaned) == 10 and _isbn10_is_valid(cleaned):
            return cleaned
    return ""


def dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = scalar_metadata_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def metadata_field_update_report(field: str, current_metadata: dict[str, Any] | None, source_metadata: dict[str, Any] | None, payload: dict[str, Any] | None) -> dict[str, str]:
    """Return a user-facing before/source/after summary for one metadata field.

    This is intentionally display-oriented. It must distinguish the operation
    status from the domain field named ``status`` used by Komga series
    metadata.
    """
    current_metadata = current_metadata or {}
    source_metadata = source_metadata or {}
    payload = payload or {}

    current_available = field in current_metadata and not is_blank_metadata_value(current_metadata.get(field))
    source_available = field in source_metadata and not is_blank_metadata_value(source_metadata.get(field))
    payload_has_field = field in payload and not is_blank_metadata_value(payload.get(field))

    current_text = one_line(current_metadata.get(field, "")) if current_available else "<vide>"
    source_text = one_line(source_metadata.get(field, "")) if source_available else "<indisponible>"
    proposed_text = one_line(payload.get(field, "")) if payload_has_field else "<aucune modification>"

    if payload_has_field:
        action = "sera modifié"
    elif not source_available:
        action = "source indisponible"
    else:
        current_key = one_line(current_metadata.get(field, "")).strip()
        source_key = one_line(source_metadata.get(field, "")).strip()
        action = "aucun changement" if current_key == source_key else "non inclus"

    return {
        "current": current_text,
        "source": source_text,
        "proposed": proposed_text,
        "action": action,
    }


STATUS_ALIASES = {
    "ONGOING": "ONGOING",
    "IN_PROGRESS": "ONGOING",
    "IN PROGRESS": "ONGOING",
    "EN_COURS": "ONGOING",
    "EN COURS": "ONGOING",
    "ONGOING_PUBLICATION": "ONGOING",
    "ENDED": "ENDED",
    "COMPLETE": "ENDED",
    "COMPLETED": "ENDED",
    "FINISHED": "ENDED",
    "TERMINE": "ENDED",
    "TERMINÉ": "ENDED",
    "HIATUS": "HIATUS",
    "ON_HIATUS": "HIATUS",
    "PAUSED": "HIATUS",
    "ABANDONED": "ABANDONED",
    "CANCELLED": "ABANDONED",
    "CANCELED": "ABANDONED",
    "DROPPED": "ABANDONED",
}
RISK_ORDER = {"": 0, "Ignoré": 0, "Faible": 1, "Moyen": 2, "Fort": 3, "Erreur": 4}


def normalize_series_status_for_tracking(value: Any) -> str:
    text = scalar_metadata_text(value).strip()
    if not text:
        return ""
    key = text.upper().replace("-", "_").replace(" ", "_")
    key = re.sub(r"_+", "_", key)
    return STATUS_ALIASES.get(key, key if key in {"ONGOING", "ENDED", "HIATUS", "ABANDONED"} else "")


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", scalar_metadata_text(value))
        if not match:
            return None
        try:
            number = int(match.group(0))
        except ValueError:
            return None
    return number


def release_tracking_total_decision(current: Any, source: Any) -> dict[str, Any]:
    current_int = _int_or_none(current)
    source_int = _int_or_none(source)
    if source_int is None or source_int <= 0:
        return {"current": current_int, "source": source_int, "proposed": None, "action": "source indisponible", "risk": "Ignoré", "selected": False, "note": "totalBookCount source absent, invalide ou égal à 0"}
    if current_int is None or current_int <= 0:
        return {"current": current_int, "source": source_int, "proposed": source_int, "action": f"vide → {source_int}", "risk": "Faible", "selected": True, "note": "remplissage d'un totalBookCount vide"}
    if source_int == current_int:
        return {"current": current_int, "source": source_int, "proposed": None, "action": "aucun changement", "risk": "Ignoré", "selected": False, "note": "totalBookCount identique"}
    if source_int < current_int:
        return {"current": current_int, "source": source_int, "proposed": source_int, "action": f"diminution suspecte {current_int} → {source_int}", "risk": "Fort", "selected": False, "note": "une série n'est normalement pas censée perdre des tomes"}
    delta = source_int - current_int
    if delta <= 3:
        return {"current": current_int, "source": source_int, "proposed": source_int, "action": f"+{delta} tome(s)", "risk": "Faible", "selected": True, "note": "augmentation normale"}
    if delta <= 9:
        return {"current": current_int, "source": source_int, "proposed": source_int, "action": f"+{delta} tome(s) à vérifier", "risk": "Moyen", "selected": False, "note": "augmentation importante, validation manuelle requise"}
    return {"current": current_int, "source": source_int, "proposed": source_int, "action": f"+{delta} tome(s) suspect", "risk": "Fort", "selected": False, "note": "augmentation très importante, validation manuelle requise"}


def release_tracking_status_decision(current: Any, source: Any) -> dict[str, Any]:
    current_status = normalize_series_status_for_tracking(current)
    source_status = normalize_series_status_for_tracking(source)
    if not source_status:
        return {"current": current_status, "source": source_status, "proposed": None, "action": "source indisponible", "risk": "Ignoré", "selected": False, "note": "status source absent ou non mappable"}
    if current_status == source_status:
        return {"current": current_status, "source": source_status, "proposed": None, "action": "aucun changement", "risk": "Ignoré", "selected": False, "note": "status identique"}
    if not current_status:
        risk = "Faible" if source_status in {"ONGOING", "ENDED"} else "Moyen"
        return {"current": current_status, "source": source_status, "proposed": source_status, "action": f"vide → {source_status}", "risk": risk, "selected": risk == "Faible", "note": "remplissage du status vide"}
    if current_status == "ONGOING" and source_status == "ENDED":
        return {"current": current_status, "source": source_status, "proposed": source_status, "action": "ONGOING → ENDED", "risk": "Faible", "selected": True, "note": "fin de série plausible"}
    if current_status == "ONGOING" and source_status == "HIATUS":
        return {"current": current_status, "source": source_status, "proposed": source_status, "action": "ONGOING → HIATUS", "risk": "Moyen", "selected": False, "note": "passage en pause à vérifier"}
    if current_status == "ONGOING" and source_status == "ABANDONED":
        return {"current": current_status, "source": source_status, "proposed": source_status, "action": "ONGOING → ABANDONED", "risk": "Fort", "selected": False, "note": "abandon à valider manuellement"}
    if current_status == "ENDED" and source_status in {"ONGOING", "HIATUS", "ABANDONED"}:
        return {"current": current_status, "source": source_status, "proposed": source_status, "action": f"{current_status} → {source_status} suspect", "risk": "Fort", "selected": False, "note": "réouverture/changement après fin de série suspect"}
    risk = "Moyen" if source_status in {"HIATUS"} else "Fort"
    return {"current": current_status, "source": source_status, "proposed": source_status, "action": f"{current_status} → {source_status}", "risk": risk, "selected": False, "note": "transition status à valider"}


def combine_release_tracking_risk(*risks: Any) -> str:
    best = "Ignoré"
    for risk in risks:
        text = scalar_metadata_text(risk) or "Ignoré"
        if RISK_ORDER.get(text, 0) > RISK_ORDER.get(best, 0):
            best = text
    return best
