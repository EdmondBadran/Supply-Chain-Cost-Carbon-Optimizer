import csv
import math
from functools import lru_cache
from pathlib import Path

CITIES_PATH = Path(__file__).resolve().parent.parent / "data" / "cities.csv"

EARTH_RADIUS_KM = 6371.0


class GeocodeError(Exception):
    pass


@lru_cache(maxsize=1)
def _tables():
    """Return lookup tables keyed by (city, country) and by city alone.

    The city-only table keeps the first match, and cities.csv is sorted by
    population descending, so a bare "Springfield" resolves to the biggest one
    rather than an arbitrary village.
    """
    by_pair = {}
    by_city = {}
    with open(CITIES_PATH, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            city = row["city"].strip().lower()
            country = row["country"].strip().upper()
            point = (float(row["lat"]), float(row["lon"]))
            by_pair.setdefault((city, country), point)
            by_city.setdefault(city, point)
    return by_pair, by_city


def locate(city, country=None):
    """Look up a city anywhere in the world and return (lat, lon)."""
    if not city or not str(city).strip():
        raise GeocodeError("missing city name")

    by_pair, by_city = _tables()
    key = str(city).strip().lower()

    if country and str(country).strip():
        code = str(country).strip().upper()
        point = by_pair.get((key, code))
        if point:
            return point
        raise GeocodeError(f"no match for {city}, {country}")

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
