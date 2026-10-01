"""Work-level matching for genre suggestions only; no remote requests."""
from collections import defaultdict
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from ..series_identity import normalize_title, title_evidence, match_titles

_FUZZY_CANDIDATE_LIMIT = 512
_COMMON_TOKEN_LIMIT = 10_000
_TITLE_STOPWORDS = {"the", "and", "for", "with", "from", "des", "les", "dans", "une", "sur"}


def _title_tokens(values):
    tokens = {
        token
        for value in values
        for token in normalize_title(value).split()
        if len(token) >= 3 and token not in _TITLE_STOPWORDS
    }
    return tokens


def work_title(value):
    # Physical editions share genres. Do NOT remove story/subseries qualifiers.
    text = re.sub(r"\s*\(chap\)\s*$", "", str(value), flags=re.I).strip()
    text = re.sub(r"\s*\((?:EN|FR|JP|JA|INT|OS)\)\s*$", "", text, flags=re.I).strip()
    physical = r"(?:perfect|deluxe|collector|kanzenban|bunkoban|omnibus|int[ée]grale)"
    text = re.sub(rf"\s*(?:[-–:]\s*|\(\s*)?\b(?:{physical}\s+[ée]dition|[ée]dition\s+{physical})\s*\)?\s*$", "", text, flags=re.I).strip()
    text = re.sub(rf"\s*(?:[-–:]\s*|\(\s*){physical}\s*\)?\s*$", "", text, flags=re.I).strip()
    return text


def canonical_url(value):
    parts = urlsplit(str(value or ""))
    host = (parts.hostname or "").lower().removeprefix("www.")
    return urlunsplit(("https", host, unquote(parts.path).rstrip("/").casefold(), parts.query, ""))


class NautiljonGenreMatcher:
    def __init__(self, client):
        self.client = client
        self.rows = client._rows()
        self.by_url = {}
        self.by_title = {}
        self.by_token = defaultdict(set)
        self.targets = []
        for row in self.rows:
            url = row.get("url_fiche", "")
            values = [work_title(value) for value, _ in title_evidence(row)]
            candidate = {"title": work_title(row.get("titre", "")), "alternateTitles": values}
            index = len(self.targets)
            self.targets.append((row, candidate))
            self.by_url[canonical_url(url)] = row
            for token in _title_tokens(values):
                self.by_token[token].add(index)
            for value in values:
                key = normalize_title(value)
                if key:
                    self.by_title.setdefault(key, {})[url] = (row, candidate)

    def _fuzzy_pool(self, titles):
        # A full CSV scan for every selected series made bulk searches grow as
        # series x catalog size. Rank a bounded token shortlist instead.
        weights = defaultdict(float)
        for token in _title_tokens(titles):
            indexes = self.by_token.get(token)
            if not indexes or len(indexes) > _COMMON_TOKEN_LIMIT:
                continue
            weight = 1.0 / len(indexes)
            for index in indexes:
                weights[index] += weight
        indexes = sorted(weights, key=lambda index: (-weights[index], index))[:_FUZZY_CANDIDATE_LIMIT]
        return [self.targets[index] for index in indexes]

    def find(self, record):
        metadata = dict(record.raw.get("metadata") or {})
        # Komga raw.name is a filesystem name, often literally "Chap".
        target = {"title": record.title, "alternateTitles": metadata.get("alternateTitles", [])}
        titles = [work_title(value) for value, _ in title_evidence(target)]
        titles = [value for value in titles if normalize_title(value) not in {"chap", "chapter", "chapters", "chapitres"}]
        target = {"title": titles[0] if titles else "", "alternateTitles": titles[1:]}
        links = [link.get("url", "") for link in metadata.get("links", []) if isinstance(link, dict)
                 and (urlsplit(str(link.get("url", ""))).hostname or "").lower().removeprefix("www.") == "nautiljon.com"]
        linked = {canonical_url(url) for url in links}
        if len(linked) == 1 and next(iter(linked)) in self.by_url:
            row = self.by_url[next(iter(linked))]
            return row, {"safe": True, "score": 1.0, "reason": "Lien Nautiljon existant retrouvé dans le CSV", "conflicts": []}

        exact = {}
        for title in titles:
            exact.update(self.by_title.get(normalize_title(title), {}))
        pool = list(exact.values()) if exact else self._fuzzy_pool(titles)
        ranked = sorted(((match_titles(target, candidate), row) for row, candidate in pool),
                        key=lambda item: (-item[0]["score"], item[1].get("url_fiche", "")))
        ranked = [item for item in ranked if item[0]["score"] >= 0.60]
        if not ranked:
            return None, {"safe": False, "score": 0.0, "reason": "Aucune correspondance dans le CSV", "conflicts": []}
        identity, row = ranked[0]
        ambiguous = len(ranked) > 1 and ranked[1][0]["score"] >= 0.90 and ranked[1][0]["score"] >= identity["score"] - 0.08
        safe = identity["score"] >= 0.90 and not ambiguous and len(linked) <= 1
        reason = "Correspondance trouvée dans le CSV (titre ou titre alternatif)" if safe else "Plusieurs correspondances proches" if ambiguous else "Score insuffisant"
        if len(linked) > 1:
            reason = "Plusieurs liens Nautiljon : choix à confirmer"
        return row, {**identity, "safe": safe, "reason": reason, "conflicts": [] if safe else [reason],
                     "choices": [{"title": item[1].get("titre", ""), "url": item[1].get("url_fiche", ""), "score": item[0]["score"]} for item in ranked[:5]]}
