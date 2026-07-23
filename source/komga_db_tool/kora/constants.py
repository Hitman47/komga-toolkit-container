from __future__ import annotations

APP_NAME = "Kora Komga Genre Manager"
APP_VERSION = "1.2.3"
APP_USER_AGENT = f"kora-komga-genre-manager/{APP_VERSION}"

KORA_GENRE_PREFIX = "kora:genre:"
KORA_TAG_PREFIX = "kora:tag:"
KORA_TAXONOMY_PREFIX = "kora:taxonomy:"

MAX_KORA_GENRES = 4
LOCAL_EXCLUSIONS_FILENAME = ".kora_local_exclusions.json"

KORA_GENRES: tuple[str, ...] = (
    "action",
    "aventure",
    "comedie",
    "documentaire-biographie",
    "drame",
    "espionnage",
    "fantastique-surnaturel",
    "fantasy",
    "guerre-militaire",
    "historique",
    "horreur",
    "jeunesse",
    "mystere",
    "policier-crime",
    "romance",
    "science-fiction",
    "societe",
    "sport-arts-martiaux",
    "super-heros",
    "thriller-suspense",
    "tranche-de-vie",
    "western",
)

KORA_GENRE_LABELS: dict[str, str] = {
    "action": "Action",
    "aventure": "Aventure",
    "comedie": "Comédie",
    "documentaire-biographie": "Documentaire / Biographie",
    "drame": "Drame",
    "espionnage": "Espionnage",
    "fantastique-surnaturel": "Fantastique / Surnaturel",
    "fantasy": "Fantasy",
    "guerre-militaire": "Guerre / Militaire",
    "historique": "Historique",
    "horreur": "Horreur",
    "jeunesse": "Jeunesse",
    "mystere": "Mystère",
    "policier-crime": "Policier / Crime",
    "romance": "Romance",
    "science-fiction": "Science-fiction",
    "societe": "Société",
    "sport-arts-martiaux": "Sport / Arts martiaux",
    "super-heros": "Super-héros",
    "thriller-suspense": "Thriller / Suspense",
    "tranche-de-vie": "Tranche de vie",
    "western": "Western",
}

EXCLUDED_LIBRARY_NAMES_DEFAULT: tuple[str, ...] = ("Divers", "Magazines")

CSV_LIST_SEPARATORS = ("|", ";", ",")
