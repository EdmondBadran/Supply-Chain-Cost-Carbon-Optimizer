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
        "storage_cost_annual": 1_120_000,
        "energy_kwh_annual": 1_640_000,
        "grid_intensity": 0.27,
    },
    {
        "name": "Shenzhen DC",
        "city": "Shenzhen",
        "country": "CN",
        "storage_cost_annual": 840_000,
        "energy_kwh_annual": 1_950_000,
        "grid_intensity": 0.58,
    },
    {
        "name": "Memphis DC",
        "city": "Memphis",
        "country": "US",
        "storage_cost_annual": 1_310_000,
        "energy_kwh_annual": 1_780_000,
        "grid_intensity": 0.39,
    },
    {
        "name": "Dubai DC",
        "city": "Dubai",
        "country": "AE",
        "storage_cost_annual": 690_000,
        "energy_kwh_annual": 1_240_000,
        "grid_intensity": 0.49,
    },
]

# Pallet-scale B2B distribution: a few hundred shipments a year per lane,
# roughly a tonne each. orders, avg weight kg, mode, return rate, avg value.
LANES = [
    ("London", "GB", "Rotterdam DC", 410, 1150, "road", 0.07, 24000),
    ("Berlin", "DE", "Rotterdam DC", 340, 1240, "road", 0.05, 26500),
    ("Paris", "FR", "Rotterdam DC", 370, 1180, "road", 0.06, 25200),
    ("Madrid", "ES", "Rotterdam DC", 250, 1320, "road", 0.06, 27800),
    ("Milan", "IT", "Rotterdam DC", 225, 1210, "road", 0.05, 26100),
    ("Warsaw", "PL", "Rotterdam DC", 190, 1390, "road", 0.04, 29400),
    ("Stockholm", "SE", "Rotterdam DC", 155, 1090, "road", 0.05, 24800),
    ("Dublin", "IE", "Rotterdam DC", 120, 640, "air", 0.09, 17500),
    ("Tokyo", "JP", "Shenzhen DC", 330, 980, "sea", 0.04, 31000),
    ("Seoul", "KR", "Shenzhen DC", 265, 1020, "sea", 0.04, 29800),
    ("Singapore", "SG", "Shenzhen DC", 290, 940, "sea", 0.05, 28600),
    ("Bangkok", "TH", "Shenzhen DC", 210, 1150, "sea", 0.06, 26400),
    ("Jakarta", "ID", "Shenzhen DC", 180, 1240, "sea", 0.07, 24900),
    ("Manila", "PH", "Shenzhen DC", 160, 1080, "sea", 0.08, 24100),
    ("Mumbai", "IN", "Shenzhen DC", 240, 1310, "sea", 0.09, 23000),
    ("Sydney", "AU", "Rotterdam DC", 195, 820, "air", 0.06, 31500),
    ("Auckland", "NZ", "Rotterdam DC", 110, 710, "air", 0.06, 30600),
    ("New York City", "US", "Memphis DC", 490, 1030, "road", 0.08, 26000),
    ("Chicago", "US", "Memphis DC", 400, 1170, "road", 0.07, 26800),
    ("Los Angeles", "US", "Memphis DC", 375, 1090, "road", 0.09, 25700),
    ("Toronto", "CA", "Memphis DC", 245, 1140, "road", 0.06, 27200),
    ("Mexico City", "MX", "Memphis DC", 215, 1280, "road", 0.10, 22300),
    ("Sao Paulo", "BR", "Memphis DC", 195, 1420, "sea", 0.08, 23400),
    ("Bogota", "CO", "Memphis DC", 135, 690, "air", 0.11, 21200),
    ("Santiago", "CL", "Memphis DC", 120, 1350, "sea", 0.07, 24200),
    ("Buenos Aires", "AR", "Memphis DC", 150, 1390, "sea", 0.08, 23800),
    ("Riyadh", "SA", "Dubai DC", 235, 1230, "road", 0.05, 28000),
    ("Cairo", "EG", "Dubai DC", 190, 1310, "sea", 0.07, 21900),
    ("Nairobi", "KE", "Dubai DC", 140, 760, "air", 0.09, 19600),
    ("Lagos", "NG", "Dubai DC", 130, 800, "air", 0.12, 18900),
    ("Johannesburg", "ZA", "Dubai DC", 165, 1360, "sea", 0.07, 23100),
    ("Karachi", "PK", "Dubai DC", 175, 1150, "sea", 0.08, 21200),
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
