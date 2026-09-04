from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


AuthorReplacement = str | list[str]


DEFAULT_AUTHOR_DECISIONS_PATH = Path(".komga_db_tool_cache") / "author_cleanup" / "decisions.json"
AUTHOR_REFERENCE_INDEX_SCHEMA = 2
AUTHOR_REFERENCE_FIELDS = {
    "mangabaka": {"authors", "artists"},
    "manga_news": {"authors_story", "authors_art"},
}
AUTHOR_REFERENCE_LABELS = {
    "mangabaka": "MangaBaka",
    "manga_news": "Manga News",
}


def clean_author_name(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or "")).strip())


def author_name_key(value: Any) -> str:
    return clean_author_name(value).casefold()


def author_fold_key(value: Any) -> str:
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", clean_author_name(value))
        if not unicodedata.combining(char)
    ).casefold()
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).strip()


def author_entries(value: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            name = clean_author_name(item.get("name"))
            role = clean_author_name(item.get("role"))
        else:
            name = clean_author_name(item)
            role = "writer"
        if name and role:
            rows.append({"name": name, "role": role})
    return rows


def clean_author_replacement(value: Any) -> AuthorReplacement:
    if isinstance(value, (list, tuple)):
        names = list(dict.fromkeys(clean_author_name(item) for item in value if clean_author_name(item)))
        return names if len(names) > 1 else (names[0] if names else "")
    return clean_author_name(value)


def composite_author_parts(value: Any) -> list[str]:
    text = clean_author_name(value)
    if "/" not in text:
        return []
    return [part for part in (clean_author_name(item) for item in text.split("/")) if part]


def _natural_case_score(value: str) -> tuple[int, int, int, str]:
    words = re.findall(r"[^\s,]+", clean_author_name(value))
    all_caps = sum(1 for word in words if len(word) > 1 and any(char.isalpha() for char in word) and word.isupper())
    mixed = sum(1 for word in words if any(char.isupper() for char in word) and any(char.islower() for char in word))
    comma = 1 if "," in value else 0
    return (-comma, mixed, -all_caps, value.casefold())


def _diacritic_count(value: str) -> int:
    return sum(1 for char in unicodedata.normalize("NFD", value) if unicodedata.combining(char))


def _copy_diacritics(template: str, source: str) -> str:
    """Copy accents from *source* while preserving the natural casing of *template*."""
    template_chars = list(template)
    source_chars = list(source)
    if len(template_chars) != len(source_chars):
        return template
    output: list[str] = []
    for target_char, source_char in zip(template_chars, source_chars):
        target_base = "".join(char for char in unicodedata.normalize("NFD", target_char) if not unicodedata.combining(char))
        source_nfd = unicodedata.normalize("NFD", source_char)
        source_base = "".join(char for char in source_nfd if not unicodedata.combining(char))
        if target_base.casefold() != source_base.casefold():
            return template
        accents = "".join(char for char in source_nfd if unicodedata.combining(char))
        output.append(unicodedata.normalize("NFC", target_char + accents))
    return "".join(output)


def _prefer_diacritics(template: str, candidates: Iterable[str]) -> str:
    result_tokens = clean_author_name(template).split()
    if not result_tokens:
        return template
    for candidate in candidates:
        candidate_tokens = clean_author_name(candidate).split()
        if len(candidate_tokens) != len(result_tokens):
            continue
        for index, candidate_token in enumerate(candidate_tokens):
            if author_fold_key(candidate_token) != author_fold_key(result_tokens[index]):
                continue
            if _diacritic_count(candidate_token) > _diacritic_count(result_tokens[index]):
                result_tokens[index] = _copy_diacritics(result_tokens[index], candidate_token)
    return " ".join(result_tokens)


def _format_given_family(value: str) -> str:
    text = clean_author_name(value)
    if "," in text:
        left, right = (part.strip() for part in text.split(",", 1))
        if left and right:
            text = f"{right} {left}"
    words = text.split()
    if len(words) >= 2 and words[0].isupper() and len(words[0]) > 1 and not all(word.isupper() for word in words[1:]):
        family = "-".join(part[:1].upper() + part[1:].lower() for part in words[0].split("-"))
        text = " ".join(words[1:] + [family])
    return text


def suggest_canonical_name(names: Iterable[str], occurrences: dict[str, int] | Counter[str]) -> str:
    values = list(dict.fromkeys(clean_author_name(name) for name in names if clean_author_name(name)))
    if not values:
        return ""
    formatted = {_format_given_family(value): value for value in values}
    candidates = list(formatted)
    candidates.sort(key=lambda value: (
        tuple(-part if isinstance(part, int) else part for part in _natural_case_score(value)[:3]),
        -int(occurrences.get(formatted[value], 0)),
        value.casefold(),
    ))
    chosen = candidates[0]
    # Prefer an already present mixed-case spelling over an all-caps variant
    # when both represent the same name.
    same_key = [value for value in candidates if author_name_key(value) == author_name_key(chosen)]
    if same_key:
        chosen = max(same_key, key=_natural_case_score)
    equivalent = [value for value in candidates if author_fold_key(value) == author_fold_key(chosen)]
    return clean_author_name(_prefer_diacritics(chosen, equivalent))


def _all_caps_name_token(value: str) -> bool:
    letters = "".join(char for char in value if char.isalpha())
    return len(letters) > 1 and letters.isupper()


def _structured_inversion(left: str, right: str) -> bool:
    left_clean, right_clean = clean_author_name(left), clean_author_name(right)
    left_fold, right_fold = author_fold_key(left_clean).split(), author_fold_key(right_clean).split()
    if len(left_fold) != 2 or right_fold != list(reversed(left_fold)):
        return False
    if "," in left_clean or "," in right_clean:
        return True
    left_tokens, right_tokens = left_clean.split(), right_clean.split()
    if len(left_tokens) != 2 or len(right_tokens) != 2:
        return False
    return (
        _all_caps_name_token(left_tokens[0]) and not _all_caps_name_token(left_tokens[1])
    ) or (
        _all_caps_name_token(right_tokens[0]) and not _all_caps_name_token(right_tokens[1])
    )


def _component_surname_key(values: Iterable[str]) -> str:
    """Infer one family-name token from reliable evidence anywhere in a group."""
    rows = [clean_author_name(value) for value in values]
    token_rows = [author_fold_key(value).split() for value in rows]
    if not token_rows or any(len(tokens) != 2 for tokens in token_rows):
        return ""
    reference = sorted(token_rows[0])
    if any(sorted(tokens) != reference for tokens in token_rows[1:]):
        return ""
    candidates: set[str] = set()
    for value, folded_tokens in zip(rows, token_rows):
        display_tokens = value.replace(",", " ").split()
        if len(display_tokens) != 2:
            continue
        if "," in value:
            candidates.add(folded_tokens[0])
        uppercase = [
            folded_tokens[index]
            for index, token in enumerate(display_tokens)
            if _all_caps_name_token(token)
        ]
        if len(uppercase) == 1:
            candidates.add(uppercase[0])
    return next(iter(candidates)) if len(candidates) == 1 else ""


def _naturalize_name_token(value: str) -> str:
    if not _all_caps_name_token(value):
        return value
    return "-".join(part[:1].upper() + part[1:].lower() for part in value.split("-"))


def _suggest_group_canonical(
    values: Iterable[str],
    occurrences: dict[str, int] | Counter[str],
) -> tuple[str, str]:
    names = [clean_author_name(value) for value in values]
    surname_key = _component_surname_key(names)
    if not surname_key:
        return suggest_canonical_name(names, occurrences), ""
    oriented_occurrences: Counter[str] = Counter()
    for name in names:
        tokens = name.replace(",", " ").split()
        folded_tokens = author_fold_key(name).split()
        if len(tokens) != 2 or len(folded_tokens) != 2:
            continue
        family_index = next((index for index, token in enumerate(folded_tokens) if token == surname_key), -1)
        if family_index < 0:
            continue
        given_index = 1 - family_index
        oriented = f"{_naturalize_name_token(tokens[given_index])} {_naturalize_name_token(tokens[family_index])}"
        oriented_occurrences[oriented] += int(occurrences.get(name, 0))
    if not oriented_occurrences:
        return suggest_canonical_name(names, occurrences), ""
    return suggest_canonical_name(oriented_occurrences, oriented_occurrences), surname_key


def _author_reference_signature(value: Any) -> str:
    tokens = author_fold_key(value).split()
    if len(tokens) != 2 or tokens[0] == tokens[1]:
        return ""
    return "\u241f".join(sorted(tokens))


def _normalize_reference_author(source: str, value: Any) -> str:
    name = clean_author_name(value)
    if not name or "/" in name:
        return ""
    if source == "manga_news":
        name = _format_given_family(name)
    if len(author_fold_key(name).split()) != 2 or len(name.replace(",", " ").split()) != 2:
        return ""
    return clean_author_name(name.replace(",", " "))


def _reference_names(payload: Any, fields: set[str]) -> Iterable[str]:
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in fields and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            yield item
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(item for item in current if isinstance(item, (dict, list)))


def _cache_reference_state(cache_dirs: dict[str, str | Path]) -> tuple[dict[str, Any], dict[str, list[Path]]]:
    state: dict[str, Any] = {}
    files_by_source: dict[str, list[Path]] = {}
    for source in sorted(AUTHOR_REFERENCE_FIELDS):
        root = Path(cache_dirs.get(source) or "")
        files = sorted(root.glob("*.json")) if root.is_dir() else []
        digest = hashlib.sha256()
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(path.name.encode("utf-8", errors="replace"))
            digest.update(f":{stat.st_size}:{stat.st_mtime_ns}\n".encode("ascii"))
        files_by_source[source] = files
        state[source] = {
            "path": str(root.resolve()) if root.exists() else str(root),
            "files": len(files),
            "fingerprint": digest.hexdigest(),
        }
    return state, files_by_source


def load_cached_author_references(
    cache_dirs: dict[str, str | Path],
    *,
    index_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a conservative given-name/family-name index from local source caches only."""
    state, files_by_source = _cache_reference_state(cache_dirs)
    target = Path(index_path) if index_path else None
    if target and target.is_file():
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if (
                cached.get("schema_version") == AUTHOR_REFERENCE_INDEX_SCHEMA
                and cached.get("cache_state") == state
                and isinstance(cached.get("references"), dict)
            ):
                return dict(cached["references"])
        except (OSError, ValueError, TypeError):
            pass

    evidence: dict[str, dict[str, dict[str, Counter[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(Counter))
    )
    for source, files in files_by_source.items():
        fields = AUTHOR_REFERENCE_FIELDS[source]
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, UnicodeError):
                continue
            for raw_name in _reference_names(payload, fields):
                name = _normalize_reference_author(source, raw_name)
                signature = _author_reference_signature(name)
                orientation = author_fold_key(name)
                if signature and len(orientation.split()) == 2:
                    evidence[signature][source][orientation][name] += 1

    references: dict[str, dict[str, Any]] = {}
    for signature, source_rows in evidence.items():
        source_orientations: dict[str, str] = {}
        spellings: Counter[str] = Counter()
        for source, orientations in source_rows.items():
            if len(orientations) != 1:
                continue
            orientation, names = next(iter(orientations.items()))
            source_orientations[source] = orientation
            spellings.update(names)
        agreed = set(source_orientations.values())
        if len(agreed) != 1:
            continue
        orientation = next(iter(agreed))
        oriented_spellings = Counter({
            name: count for name, count in spellings.items() if author_fold_key(name) == orientation
        })
        if not oriented_spellings:
            continue
        canonical = suggest_canonical_name(oriented_spellings, oriented_spellings)
        references[signature] = {
            "canonical": canonical,
            "orientation": orientation,
            "sources": [AUTHOR_REFERENCE_LABELS[source] for source in sorted(source_orientations)],
        }

    token_roles: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "given": 0,
        "family": 0,
        "given_sources": set(),
        "family_sources": set(),
    })
    for reference in references.values():
        tokens = str(reference.get("orientation") or "").split()
        if len(tokens) != 2:
            continue
        sources = set(str(source) for source in reference.get("sources") or [] if str(source))
        token_roles[tokens[0]]["given"] += 1
        token_roles[tokens[0]]["given_sources"].update(sources)
        token_roles[tokens[1]]["family"] += 1
        token_roles[tokens[1]]["family_sources"].update(sources)
    references["__token_roles__"] = {
        "tokens": {
            token: {
                "given": row["given"],
                "family": row["family"],
                "given_sources": sorted(row["given_sources"]),
                "family_sources": sorted(row["family_sources"]),
            }
            for token, row in token_roles.items()
        }
    }

    if target:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(json.dumps({
                "schema_version": AUTHOR_REFERENCE_INDEX_SCHEMA,
                "cache_state": state,
                "references": references,
            }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(target)
        except OSError:
            pass
    return references


def _canonical_from_reference(
    values: Iterable[str],
    occurrences: dict[str, int] | Counter[str],
    references: dict[str, dict[str, Any]] | None,
) -> tuple[str, list[str]]:
    names = [clean_author_name(value) for value in values]
    signature = _author_reference_signature(names[0]) if names else ""
    if not signature or not references:
        return "", []
    reference = references.get(signature)
    if not isinstance(reference, dict):
        token_index = references.get("__token_roles__", {}).get("tokens", {})
        folded = author_fold_key(names[0]).split()
        if len(folded) != 2 or not isinstance(token_index, dict):
            return "", []
        candidates: list[tuple[int, int, list[str], str]] = []
        for given, family in (folded, list(reversed(folded))):
            given_row = token_index.get(given, {})
            family_row = token_index.get(family, {})
            support = int(given_row.get("given", 0)) + int(family_row.get("family", 0))
            opposition = int(given_row.get("family", 0)) + int(family_row.get("given", 0))
            if support < 2 or support < opposition + 2 or support < opposition * 3:
                continue
            sources = sorted(set(
                [str(value) for value in given_row.get("given_sources") or []]
                + [str(value) for value in family_row.get("family_sources") or []]
            ))
            candidates.append((support, opposition, sources, f"{given} {family}"))
        if len(candidates) != 1:
            return "", []
        support, opposition, sources, orientation = candidates[0]
        reference = {
            "canonical": "",
            "orientation": orientation,
            "sources": sources,
            "mode": "token_roles",
            "support": support,
            "opposition": opposition,
        }
    orientation = str(reference.get("orientation") or "")
    if len(orientation.split()) != 2:
        return "", []
    oriented: Counter[str] = Counter()
    expected = orientation.split()
    for name in names:
        tokens = name.replace(",", " ").split()
        folded = author_fold_key(name).split()
        if len(tokens) != 2 or sorted(folded) != sorted(expected):
            continue
        if folded == expected:
            candidate = name.replace(",", " ")
        elif list(reversed(folded)) == expected:
            candidate = " ".join(reversed(tokens))
        else:
            continue
        oriented[clean_author_name(candidate)] += int(occurrences.get(name, 0))
    reference_name = clean_author_name(reference.get("canonical"))
    if reference_name and author_fold_key(reference_name) == orientation:
        oriented[reference_name] += 1
    if not oriented:
        return "", []
    sources = [str(source) for source in reference.get("sources") or [] if str(source)]
    return suggest_canonical_name(oriented, oriented), sources


def suspicious_author_name(value: Any) -> bool:
    text = clean_author_name(value)
    folded = text.casefold()
    if folded in {"?", "a", "n/a", "na", "unknown", "inconnu", "various", "collectif", "tome"}:
        return True
    if "/" in text:
        return True
    if re.fullmatch(r"\d+", text):
        return True
    return bool(re.search(r"\b(?:tome|vol(?:ume)?|chapter|chapitre)\s*\d*\b", text, re.IGNORECASE))


class AuthorCanonicalStore:
    def __init__(self, path: str | Path = DEFAULT_AUTHOR_DECISIONS_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.is_file():
                return {"mappings": {}, "ignored_groups": []}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return {"mappings": {}, "ignored_groups": []}
            mappings = payload.get("mappings") if isinstance(payload, dict) else {}
            ignored = payload.get("ignored_groups") if isinstance(payload, dict) else []
            return {
                "mappings": dict(mappings) if isinstance(mappings, dict) else {},
                "ignored_groups": list(ignored) if isinstance(ignored, list) else [],
            }

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)

    def mappings(self) -> dict[str, AuthorReplacement]:
        payload = self.load()
        return {
            author_name_key(alias): clean_author_replacement(canonical)
            for alias, canonical in payload["mappings"].items()
            if clean_author_name(alias) and clean_author_replacement(canonical)
        }

    def remember(self, aliases: Iterable[str], canonical: AuthorReplacement) -> None:
        replacement = clean_author_replacement(canonical)
        if not replacement:
            return
        payload = self.load()
        mappings = dict(payload["mappings"])
        for alias in aliases:
            clean_alias = clean_author_name(alias)
            if clean_alias and clean_author_replacement(clean_alias) != replacement:
                mappings[clean_alias] = replacement
        payload["mappings"] = mappings
        self.save(payload)

    def normalize_entries(self, value: Any) -> list[dict[str, str]]:
        return normalize_author_entries(value, self.mappings())


def normalize_author_entries(value: Any, mappings: dict[str, AuthorReplacement], *, remove_names: Iterable[str] = ()) -> list[dict[str, str]]:
    normalized_mappings = {author_name_key(key): clean_author_replacement(target) for key, target in mappings.items()}
    removals = {author_name_key(name) for name in remove_names}
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in author_entries(value):
        source_name = entry["name"]
        source_key = author_name_key(source_name)
        if source_key in removals:
            continue
        replacement = normalized_mappings.get(source_key, source_name)
        names = replacement if isinstance(replacement, list) else [replacement]
        for name in names:
            if not name:
                continue
            identity = (author_name_key(name), entry["role"].casefold())
            if identity in seen:
                continue
            seen.add(identity)
            output.append({"name": name, "role": entry["role"]})
    return output


def author_decision_is_remembered(
    aliases: Iterable[str],
    canonical: AuthorReplacement,
    mappings: dict[str, AuthorReplacement],
) -> bool:
    """Return whether every alias already resolves to the requested canonical value."""
    clean_aliases = list(dict.fromkeys(clean_author_name(alias) for alias in aliases if clean_author_name(alias)))
    replacement = clean_author_replacement(canonical)
    if not clean_aliases or not replacement:
        return False
    expected_names = replacement if isinstance(replacement, list) else [replacement]
    for alias in clean_aliases:
        normalized = normalize_author_entries([{"name": alias, "role": "writer"}], mappings)
        if [entry["name"] for entry in normalized] != expected_names:
            return False
    return True


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def scan_author_groups(
    series_rows: Iterable[Any],
    mappings: dict[str, AuthorReplacement] | None = None,
    references: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    mappings = {author_name_key(key): clean_author_replacement(value) for key, value in (mappings or {}).items()}
    occurrences: Counter[str] = Counter()
    roles: dict[str, Counter[str]] = defaultdict(Counter)
    libraries: dict[str, Counter[str]] = defaultdict(Counter)
    series_by_name: dict[str, dict[str, str]] = defaultdict(dict)
    for series in series_rows:
        raw = getattr(series, "raw", {}) if not isinstance(series, dict) else series
        raw = raw if isinstance(raw, dict) else {}
        metadata = raw.get("booksMetadata") if isinstance(raw.get("booksMetadata"), dict) else {}
        series_id = str(getattr(series, "id", "") or raw.get("id") or "")
        series_title = str(getattr(series, "title", "") or (raw.get("metadata") or {}).get("title") or raw.get("name") or "")
        library_id = str(getattr(series, "library_id", "") or raw.get("libraryId") or "")
        for entry in author_entries(metadata.get("authors")):
            name, role = entry["name"], entry["role"]
            occurrences[name] += 1
            roles[name][role] += 1
            libraries[name][library_id] += 1
            if series_id:
                series_by_name[name][series_id] = series_title

    names = list(occurrences)
    union = _UnionFind(names)
    reasons: dict[frozenset[str], set[str]] = defaultdict(set)

    def connect(group: Iterable[str], reason: str) -> None:
        values = list(dict.fromkeys(group))
        for other in values[1:]:
            union.union(values[0], other)
        if len(values) > 1:
            reasons[frozenset(values)].add(reason)

    by_case: dict[str, list[str]] = defaultdict(list)
    by_fold: dict[str, list[str]] = defaultdict(list)
    for name in names:
        by_case[author_name_key(name)].append(name)
        by_fold[author_fold_key(name)].append(name)
    for values in by_case.values():
        connect(values, "case")
    for values in by_fold.values():
        if len({author_name_key(value) for value in values}) > 1:
            connect(values, "accent_punctuation")

    for name in names:
        tokens = author_fold_key(name).split()
        if 2 <= len(tokens) <= 4:
            reversed_key = " ".join(reversed(tokens))
            if reversed_key in by_fold and reversed_key != author_fold_key(name):
                for other in by_fold[reversed_key]:
                    connect(
                        [name, other],
                        "structured_inversion" if _structured_inversion(name, other) else "inversion",
                    )

    canonical_nodes: dict[str, AuthorReplacement] = {}
    for name in names:
        target = mappings.get(author_name_key(name))
        if not target:
            continue
        if isinstance(target, str):
            existing = next((candidate for candidate in names if author_name_key(candidate) == author_name_key(target)), "")
            if existing:
                union.union(name, existing)
        canonical_nodes[name] = target

    components: dict[str, list[str]] = defaultdict(list)
    for name in names:
        components[union.find(name)].append(name)

    output: list[dict[str, Any]] = []
    for values in components.values():
        suspicious = [name for name in values if suspicious_author_name(name)]
        if len(values) < 2 and not suspicious:
            continue
        edge_reasons: set[str] = set()
        for grouped_names, grouped_reasons in reasons.items():
            if len(grouped_names.intersection(values)) >= 2:
                edge_reasons.update(grouped_reasons)
        confirmed_targets = [canonical_nodes[name] for name in values if name in canonical_nodes]
        suggested_canonical, surname_key = _suggest_group_canonical(values, occurrences)
        reference_canonical, reference_sources = _canonical_from_reference(values, occurrences, references)
        if reference_canonical and "inversion" in edge_reasons and not surname_key:
            suggested_canonical = reference_canonical
        replacement = confirmed_targets[0] if confirmed_targets else suggested_canonical
        replacement_names = replacement if isinstance(replacement, list) else [replacement]
        canonical = " / ".join(replacement_names)
        if confirmed_targets:
            confidence = "confirmed"
            reason = "Décision canonique déjà mémorisée"
        elif suspicious:
            confidence = "review"
            reason = "Valeur composite ou suspecte — validation manuelle requise"
        elif edge_reasons <= {"case"}:
            confidence = "safe"
            reason = "Différence de casse ou d'espacement uniquement"
        elif edge_reasons <= {"case", "accent_punctuation"}:
            confidence = "safe"
            reason = "Même nom avec une différence d'accent ou de ponctuation uniquement"
        elif edge_reasons <= {"case", "accent_punctuation", "structured_inversion"}:
            confidence = "safe"
            reason = "Inversion NOM Prénom confirmée par la casse ou la virgule"
        elif "inversion" in edge_reasons and surname_key:
            confidence = "safe"
            reason = "Orientation Prénom Nom confirmée par une autre variante fiable du groupe"
        elif "inversion" in edge_reasons and reference_canonical:
            confidence = "safe"
            reason = "Orientation Prénom Nom confirmée par le cache " + " et ".join(reference_sources)
        elif "inversion" in edge_reasons:
            confidence = "review"
            reason = "Ordre prénom/nom possiblement inversé — validation requise"
        else:
            confidence = "likely"
            reason = "Différence d'accent ou de ponctuation"
        series_map: dict[str, str] = {}
        for name in values:
            series_map.update(series_by_name[name])
        digest = hashlib.sha1("\0".join(sorted(author_name_key(name) for name in values)).encode("utf-8")).hexdigest()[:16]
        output.append({
            "group_id": digest,
            "canonical": canonical,
            "replacement_names": replacement_names,
            "action": "split" if len(replacement_names) > 1 else "normalize",
            "composite_parts": composite_author_parts(values[0]) if len(values) == 1 else [],
            "composite_suggestions": [
                suggest_canonical_name([part], {part: 1})
                for part in (composite_author_parts(values[0]) if len(values) == 1 else [])
            ],
            "variants": [{
                "name": name,
                "occurrences": occurrences[name],
                "roles": dict(roles[name]),
                "libraries": dict(libraries[name]),
                "series_count": len(series_by_name[name]),
            } for name in sorted(values, key=lambda item: (-occurrences[item], item.casefold()))],
            "variant_count": len(values),
            "occurrences": sum(occurrences[name] for name in values),
            "series_count": len(series_map),
            "series": [{"id": key, "title": value} for key, value in sorted(series_map.items(), key=lambda item: item[1].casefold())],
            "roles": sorted({role for name in values for role in roles[name]}),
            "libraries": sorted({library for name in values for library in libraries[name] if library}),
            "confidence": confidence,
            "reason": reason,
            "safe": confidence in {"safe", "confirmed"},
            "suspicious": bool(suspicious),
            "reference_sources": reference_sources,
        })
    rank = {"confirmed": 0, "safe": 1, "likely": 2, "review": 3}
    output.sort(key=lambda row: (rank.get(str(row["confidence"]), 9), -int(row["occurrences"]), str(row["canonical"]).casefold()))
    return output


def author_mapping_for_group(group: dict[str, Any], canonical: AuthorReplacement | None = None) -> dict[str, AuthorReplacement]:
    target = clean_author_replacement(canonical if canonical is not None else group.get("replacement_names") or group.get("canonical"))
    if not target:
        return {}
    return {
        clean_author_name(variant.get("name")): target
        for variant in group.get("variants") or []
        if clean_author_name(variant.get("name"))
    }


def analyze_book_author_change(book: Any, mappings: dict[str, AuthorReplacement], *, remove_names: Iterable[str] = ()) -> dict[str, Any]:
    metadata = getattr(book, "metadata", {}) if not isinstance(book, dict) else book.get("metadata", {})
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    current = author_entries(metadata.get("authors"))
    proposed = normalize_author_entries(current, mappings, remove_names=remove_names)
    raw = getattr(book, "raw", {}) if not isinstance(book, dict) else book
    raw = raw if isinstance(raw, dict) else {}
    return {
        "book_id": str(getattr(book, "id", "") or raw.get("id") or ""),
        "series_id": str(getattr(book, "series_id", "") or raw.get("seriesId") or ""),
        "title": str(getattr(book, "title", "") or metadata.get("title") or raw.get("name") or ""),
        "number": str(getattr(book, "number", "") or metadata.get("number") or ""),
        "current": current,
        "proposed": proposed,
        "changed": current != proposed,
        "locked": bool(metadata.get("authorsLock")),
    }
