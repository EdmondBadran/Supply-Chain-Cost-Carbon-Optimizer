"""Generate the sample dataset shipped with the app.

Deterministic, so re-running it produces the same CSV. Every city is checked
against data/cities.csv first, since a sample dataset that fails to geocode
would be an embarrassing thing to ship.
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from optimizer import geo

SEED = 20260905
START = date(2025, 9, 1)
DAYS = 365

WAREHOUSES = [
    {
        "name": "Rotterdam DC",
        "city": "Rotterdam",
        "country": "NL",
        "storage_cost_annual": 1_850_000,
        "energy_kwh_annual": 2_400_000,
        "grid_intensity": 0.27,
    },
    {
        "name": "Shenzhen DC",
        "city": "Shenzhen",
        "country": "CN",
        "storage_cost_annual": 1_260_000,
        "energy_kwh_annual": 3_100_000,
        "grid_intensity": 0.58,
    },
    {
        "name": "Memphis DC",
        "city": "Memphis",
        "country": "US",
        "storage_cost_annual": 2_050_000,
        "energy_kwh_annual": 2_900_000,
        "grid_intensity": 0.39,
    },
    {
        "name": "Dubai DC",
        "city": "Dubai",
        "country": "AE",
        "storage_cost_annual": 980_000,
        "energy_kwh_annual": 2_600_000,
        "grid_intensity": 0.49,
    },
]

# orders, avg weight kg, mode, return rate, avg order value
LANES = [
    ("London", "GB", "Rotterdam DC", 74, 18, "road", 0.07, 640),
    ("Berlin", "DE", "Rotterdam DC", 61, 22, "road", 0.05, 700),
    ("Paris", "FR", "Rotterdam DC", 68, 20, "road", 0.06, 660),
    ("Madrid", "ES", "Rotterdam DC", 44, 24, "road", 0.06, 720),
    ("Milan", "IT", "Rotterdam DC", 39, 21, "road", 0.05, 690),
    ("Warsaw", "PL", "Rotterdam DC", 33, 26, "road", 0.04, 750),
    ("Stockholm", "SE", "Rotterdam DC", 27, 19, "road", 0.05, 680),
    ("Dublin", "IE", "Rotterdam DC", 22, 9, "air", 0.09, 410),
    ("Tokyo", "JP", "Shenzhen DC", 58, 16, "sea", 0.04, 820),
    ("Seoul", "KR", "Shenzhen DC", 46, 17, "sea", 0.04, 790),
    ("Singapore", "SG", "Shenzhen DC", 51, 15, "sea", 0.05, 760),
    ("Bangkok", "TH", "Shenzhen DC", 37, 19, "sea", 0.06, 700),
    ("Jakarta", "ID", "Shenzhen DC", 31, 21, "sea", 0.07, 660),
    ("Manila", "PH", "Shenzhen DC", 28, 18, "sea", 0.08, 640),
    ("Mumbai", "IN", "Shenzhen DC", 42, 23, "sea", 0.09, 610),
    ("Sydney", "AU", "Rotterdam DC", 35, 14, "air", 0.06, 830),
    ("Auckland", "NZ", "Rotterdam DC", 19, 12, "air", 0.06, 810),
    ("New York City", "US", "Memphis DC", 88, 17, "road", 0.08, 690),
    ("Chicago", "US", "Memphis DC", 71, 20, "road", 0.07, 710),
    ("Los Angeles", "US", "Memphis DC", 66, 18, "road", 0.09, 680),
    ("Toronto", "CA", "Memphis DC", 43, 19, "road", 0.06, 720),
    ("Mexico City", "MX", "Memphis DC", 38, 22, "road", 0.10, 590),
    ("Sao Paulo", "BR", "Memphis DC", 34, 25, "sea", 0.08, 620),
    ("Bogota", "CO", "Memphis DC", 24, 20, "air", 0.11, 560),
    ("Santiago", "CL", "Memphis DC", 21, 23, "sea", 0.07, 640),
    ("Buenos Aires", "AR", "Memphis DC", 26, 24, "sea", 0.08, 630),
    ("Riyadh", "SA", "Dubai DC", 41, 21, "road", 0.05, 740),
    ("Cairo", "EG", "Dubai DC", 33, 23, "sea", 0.07, 580),
    ("Nairobi", "KE", "Dubai DC", 25, 19, "air", 0.09, 520),
    ("Lagos", "NG", "Dubai DC", 23, 22, "air", 0.12, 500),
    ("Johannesburg", "ZA", "Dubai DC", 29, 24, "sea", 0.07, 610),
    ("Karachi", "PK", "Dubai DC", 30, 20, "sea", 0.08, 560),
]

CATEGORIES = [
    "audio",
    "cables",
    "chargers",
    "cases",
    "wearables",
    "storage",
]


def check_cities():
    problems = []
    for warehouse in WAREHOUSES:
        try:
            geo.locate(warehouse["city"], warehouse["country"])
        except geo.GeocodeError as exc:
            problems.append(str(exc))
    for city, country, *_ in LANES:
        try:
            geo.locate(city, country)
        except geo.GeocodeError as exc:
            problems.append(str(exc))
    return problems


def build():
    rng = random.Random(SEED)
    warehouse_lookup = {w["name"]: w for w in WAREHOUSES}
    rows = []
    counter = 1

    for city, country, warehouse_name, order_count, avg_weight, mode, return_rate, avg_value in LANES:
        warehouse = warehouse_lookup[warehouse_name]
        for _ in range(order_count):
            weight = max(0.5, rng.gauss(avg_weight, avg_weight * 0.35))
            value = max(40, rng.gauss(avg_value, avg_value * 0.3))
            units = max(1, int(round(weight / rng.uniform(1.4, 4.0))))
            rows.append(
                {
                    "order_ref": f"SO-{counter:05d}",
                    "order_date": (START + timedelta(days=rng.randrange(DAYS))).isoformat(),
                    "customer_id": f"C-{city[:3].upper()}-{rng.randrange(1, 40):03d}",
                    "origin_name": warehouse["name"],
                    "origin_city": warehouse["city"],
                    "origin_country": warehouse["country"],
                    "dest_city": city,
                    "dest_country": country,
                    "units": units,
                    "weight_kg": round(weight, 2),
                    "mode": mode,
                    "product_category": rng.choice(CATEGORIES),
                    "order_value": round(value, 2),
                    "returned": 1 if rng.random() < return_rate else 0,
                }
            )
            counter += 1

    rows.sort(key=lambda r: r["order_date"])
    return rows


def main():
    problems = check_cities()
    if problems:
        print("city lookup failed:")
        for problem in problems:
            print(" ", problem)
        return 1

    rows = build()
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    orders_path = data_dir / "sample_orders.csv"
    with open(orders_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    warehouses_path = data_dir / "sample_warehouses.csv"
    with open(warehouses_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "name",
                "city",
                "country",
                "storage_cost_annual",
                "energy_kwh_annual",
                "grid_intensity",
            ],
        )
        writer.writeheader()
        writer.writerows(WAREHOUSES)

    print(f"{len(rows)} orders across {len(LANES)} lanes and {len(WAREHOUSES)} warehouses")
    print(f"wrote {orders_path.name} and {warehouses_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
