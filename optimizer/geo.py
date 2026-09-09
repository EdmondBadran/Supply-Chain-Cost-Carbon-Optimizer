import csv
import math
import unicodedata
from functools import lru_cache
from pathlib import Path

CITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "cities.csv"

EARTH_RADIUS_KM = 6371.0

# The GeoNames extract stores names in ASCII, and it is not consistent about
# how. Malmö is Malmoe and Västerås is Vaesteras, so ä becomes ae but å becomes
# a. Tromsø is Tromso, so ø becomes o. Zürich is Zuerich, so ü becomes ue. An
# export from a Swedish, Danish or German system spells all of them properly
# and misses every one.
#
# Rather than guess which rule the table used for a given name, each accented
# character is tried both ways and the name is indexed under every combination.
# City names carry two or three of these at most, so the cross product stays
# small, and the cap below stops a pathological name from exploding it.
FOLDS = {
    "ä": ("ae", "a"),
    "ö": ("oe", "o"),
    "ü": ("ue", "u"),
    "å": ("aa", "a"),
    "ø": ("oe", "o"),
    "æ": ("ae", "a"),
    "ß": ("ss", "s"),
    "é": ("e",),
    "è": ("e",),
    "ñ": ("n",),
    "ç": ("c",),
}

MAX_VARIANTS = 32

# Cities the table lists under a different word rather than a different
# spelling, which no amount of folding will bridge. These are tried in
# addition to the folds, never instead of them: the table keeps Munich but
# also keeps Koeln, so a map that replaced the name outright would fix one and
# break the other.
EXONYMS = {
    "göteborg": "gothenburg",
    "københavn": "copenhagen",
    "helsingfors": "helsinki",
    "åbo": "turku",
    "wien": "vienna",
    "münchen": "munich",
    "nürnberg": "nuremberg",
    "praha": "prague",
    "warszawa": "warsaw",
    "lisboa": "lisbon",
    "milano": "milan",
    "roma": "rome",
    "firenze": "florence",
    "torino": "turin",
    "napoli": "naples",
    "genève": "geneva",
    "den haag": "the hague",
    "antwerpen": "antwerp",
    "bruxelles": "brussels",
    "brussel": "brussels",
    "moskva": "moscow",
    "beograd": "belgrade",
    "bucuresti": "bucharest",
    "athina": "athens",
}


class GeocodeError(Exception):
    pass


def _strip_accents(text):
    """Decompose and drop the combining marks, so Århus becomes Arhus."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _fold_variants(text):
    """Every spelling of this name the reference table might have used."""
    variants = [""]
    for char in text:
        options = FOLDS.get(char, (char,))
        variants = [
            prefix + option for prefix in variants for option in options
        ][:MAX_VARIANTS]
    return variants


def _keys(name):
    """Every key this name should be findable under, best guess first."""
    base = " ".join(str(name).strip().lower().split())
    if not base:
        return []

    found = []

    def add(candidate):
        if candidate and candidate not in found:
            found.append(candidate)

    for word in (base, EXONYMS.get(base)):
        if not word:
            continue
        add(word)
        for variant in _fold_variants(word):
            add(variant)
        add(_strip_accents(word))
    return found


@lru_cache(maxsize=1)
def _tables():
    """Return lookup tables keyed by (city, country) and by city alone.

    The city-only table keeps the first match, and cities.csv is sorted by
    population descending, so a bare "Springfield" resolves to the biggest one
    rather than an arbitrary village. Each name is registered under every fold
    of itself, so a file spelling it either way finds the same place.
    """
    by_pair = {}
    by_city = {}
    with open(CITIES_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            country = row["country"].strip().upper()
            point = (float(row["lat"]), float(row["lon"]))
            for key in _keys(row["city"]):
                by_pair.setdefault((key, country), point)
                by_city.setdefault(key, point)
    return by_pair, by_city


def locate(city, country=None):
    """Look up a city anywhere in the world and return (lat, lon)."""
    if not city or not str(city).strip():
        raise GeocodeError("missing city name")

    by_pair, by_city = _tables()
    candidates = _keys(str(city))

    if country and str(country).strip():
        code = str(country).strip().upper()
        for key in candidates:
            point = by_pair.get((key, code))
            if point:
                return point
        raise GeocodeError(f"no match for {city}, {country}")

    for key in candidates:
        point = by_city.get(key)
        if point:
            return point
    raise GeocodeError(f"no match for {city}")


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def known_countries():
    by_pair, _ = _tables()
    return sorted({country for _, country in by_pair})
