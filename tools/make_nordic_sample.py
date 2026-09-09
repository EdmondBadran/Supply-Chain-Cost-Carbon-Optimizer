"""Generate a Swedish test dataset: Nordvik Industri AB.

An invented company, not a real one. Nothing here is anyone's actual trading
data. What is real is the shape: a mid-size Swedish engineering supply
distributor, bearings and fasteners and hydraulics, sourcing from Asia and
eastern Europe, holding stock in the Jonkoping corridor, distributing across
the Nordics by road and serving continental Europe through a Dutch 3PL.

Industrial components rather than the outdoor clothing this started as, for
one reason: the engine prices freight per tonne-kilometre, which is the right
measure for dense palletised goods and the wrong one for anything light and
bulky. Apparel would be charged on volumetric weight in reality, so a
tonne-km factor understates its freight badly and the sample came out with
nine tenths of its cost in the warehouse. Dense goods keep the arithmetic
honest.

It exists to test the tool against a chain that is genuinely different from
the two shipped samples, in three ways that matter:

  * The Swedish grid is almost carbon free, about 0.03 kg CO2e per kWh against
    a world average of 0.35. A warehouse that would dominate the carbon report
    anywhere else contributes almost nothing here, which pushes the entire
    footprint onto freight. If the tool is working, warehousing should come
    out clean and it should say so rather than finding something to flag.
  * Distances are short. Most Nordic distribution runs a few hundred
    kilometres, against thousands in the two shipped samples, so cost per
    tonne-kilometre on a domestic lane is inflated by the per-order costs it
    has almost no distance to spread over. The tool needs to not mistake a
    short lane for a badly priced one, which is what the distance column on
    the outlier table is for.
  * The city names are spelled properly. Malmo is Malmoe in the reference
    table and Jonkoping is Joenkoeping, so a real Swedish export misses on
    every accented name unless the lookup folds them, which is what the
    variants in geo.py are for.

Written in the same style as make_sample.py: deterministic, and every city is
checked before a single row is generated.

Costs are US dollars, because that is what the factors in the engine are in.
A Swedish company would work in kronor, and converting the output is a
multiplication the tool deliberately does not do for you.
"""

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from optimizer import geo

SEED = 20260909
START = date(2025, 9, 1)
DAYS = 365

OUT_DIR = ROOT / "data" / "Nordic test"

WAREHOUSES = [
    {
        "name": "Jönköping DC",
        "city": "Jönköping",
        "country": "SE",
        # About 7,000 square metres in the Jonkoping logistics corridor, at
        # roughly 850 SEK a square metre a year. Dense goods need less floor
        # for the same tonnage, which is why this is smaller than it looks.
        "storage_cost_annual": 520_000,
        # Heating is most of it. A Nordic warehouse burns far more per square
        # metre than a Spanish one, and almost none of it shows up as carbon.
        "energy_kwh_annual": 700_000,
        # Swedish grid: hydro and nuclear, about 0.03 against a world average
        # of 0.35. This is the number that makes the dataset worth running.
        "grid_intensity": 0.03,
        "capacity_kg": 11_000_000,
    },
    {
        "name": "Venlo 3PL",
        "city": "Venlo",
        "country": "NL",
        "storage_cost_annual": 180_000,
        "energy_kwh_annual": 200_000,
        "grid_intensity": 0.33,
        "capacity_kg": 3_400_000,
    },
]

# city, country, warehouse, orders a year, average kg, mode, return rate,
# average order value in USD. One to two pallets a drop, which is what a
# distributor sends a machine shop or a plant maintenance store.
LANES = [
    # Sweden, from Jonkoping by road. Short, dense, and where the volume is.
    ("Stockholm", "SE", "Jönköping DC", 620, 1650, "road", 0.03, 8200),
    ("Göteborg", "SE", "Jönköping DC", 480, 1580, "road", 0.03, 7900),
    ("Malmö", "SE", "Jönköping DC", 430, 1600, "road", 0.03, 8000),
    ("Uppsala", "SE", "Jönköping DC", 190, 1490, "road", 0.03, 7400),
    ("Örebro", "SE", "Jönköping DC", 175, 1460, "road", 0.03, 7200),
    # The far north. Nine hundred and eleven hundred kilometres by road, which
    # is exactly the distance where rail becomes an honest question.
    ("Umeå", "SE", "Jönköping DC", 145, 1520, "road", 0.03, 7500),
    ("Luleå", "SE", "Jönköping DC", 95, 1580, "road", 0.03, 7700),
    # Norway and Denmark.
    ("Oslo", "NO", "Jönköping DC", 330, 1560, "road", 0.04, 7800),
    ("Bergen", "NO", "Jönköping DC", 120, 1440, "road", 0.04, 7300),
    ("Trondheim", "NO", "Jönköping DC", 110, 1420, "road", 0.04, 7200),
    ("København", "DK", "Jönköping DC", 350, 1610, "road", 0.03, 8000),
    ("Århus", "DK", "Jönköping DC", 165, 1510, "road", 0.03, 7600),
    # Finland and the Baltics go by sea, because they do.
    ("Helsinki", "FI", "Jönköping DC", 225, 1550, "sea", 0.04, 7700),
    ("Tampere", "FI", "Jönköping DC", 95, 1470, "sea", 0.04, 7300),
    ("Tallinn", "EE", "Jönköping DC", 85, 1430, "sea", 0.04, 7100),
    # Continental Europe, served from the Dutch 3PL.
    ("Hamburg", "DE", "Venlo 3PL", 280, 1720, "road", 0.04, 8600),
    ("Berlin", "DE", "Venlo 3PL", 255, 1690, "road", 0.04, 8500),
    ("München", "DE", "Venlo 3PL", 215, 1740, "road", 0.04, 8700),
    ("Amsterdam", "NL", "Venlo 3PL", 200, 1660, "road", 0.04, 8300),
    ("Antwerpen", "BE", "Venlo 3PL", 150, 1640, "road", 0.04, 8200),
    ("Paris", "FR", "Venlo 3PL", 225, 1780, "road", 0.05, 8900),
    ("London", "GB", "Venlo 3PL", 250, 1820, "road", 0.05, 9100),
    ("Zürich", "CH", "Venlo 3PL", 120, 1700, "road", 0.04, 8500),
    ("Wien", "AT", "Venlo 3PL", 110, 1730, "road", 0.04, 8700),
    ("Warszawa", "PL", "Venlo 3PL", 135, 1790, "road", 0.04, 8900),
    # Export. The two Asian accounts are flown because they were set up as
    # urgent spares cover and nobody has revisited it, which is how these
    # usually happen.
    ("Tokyo", "JP", "Jönköping DC", 60, 980, "air", 0.05, 11500),
    ("Seoul", "KR", "Jönköping DC", 40, 940, "air", 0.05, 11200),
    ("New York City", "US", "Jönköping DC", 105, 1560, "sea", 0.05, 9400),
    ("Toronto", "CA", "Jönköping DC", 45, 1500, "sea", 0.05, 9200),
]

# name, city, country, warehouse fed, mode, annual kg, shipments a year,
# annual cost USD, lead time days, minimum order qty, on time rate.
SUPPLIERS = [
    ("Suzhou Castings", "Suzhou", "CN", "Jönköping DC", "sea",
     3_100_000, 30, 4_600_000, 45, 20_000, 0.93),
    # The long lead time and the weakest on time rate sit on the same
    # supplier, which is where an expedite problem actually comes from.
    ("Pune Forgings", "Pune", "IN", "Jönköping DC", "sea",
     1_850_000, 22, 2_400_000, 58, 30_000, 0.87),
    ("Kaunas Machining", "Kaunas", "LT", "Jönköping DC", "road",
     1_240_000, 60, 2_900_000, 14, 4_000, 0.98),
    ("Porto Seals", "Porto", "PT", "Venlo 3PL", "road",
     680_000, 40, 1_500_000, 21, 3_000, 0.97),
    ("Izmir Fasteners", "Izmir", "TR", "Jönköping DC", "sea",
     920_000, 24, 1_300_000, 32, 12_000, 0.94),
    # Small, light, expensive and flown: sensors and controllers.
    ("Taipei Controls", "Taipei", "TW", "Jönköping DC", "air",
     46_000, 30, 1_050_000, 12, 500, 0.95),
]

CATEGORIES = [
    "bearings",
    "fasteners",
    "hydraulics",
    "seals and gaskets",
    "drive components",
    "tooling",
    "sensors",
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
    for _, city, country, *_ in SUPPLIERS:
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

    for city, country, warehouse_name, orders, avg_weight, mode, returns, value in LANES:
        warehouse = warehouse_lookup[warehouse_name]
        for _ in range(orders):
            weight = max(20.0, rng.gauss(avg_weight, avg_weight * 0.4))
            amount = max(120, rng.gauss(value, value * 0.32))
            units = max(1, int(round(weight / rng.uniform(3.0, 14.0))))
            # Industrial demand dips through the Swedish summer shutdown, when
            # half the country's factories close for July. A flat year would
            # hide whether the seasonality check on the statistics page works.
            day = rng.randrange(DAYS)
            month = (START + timedelta(days=day)).month
            if month == 7 and rng.random() < 0.6:
                day = rng.randrange(DAYS)
            rows.append(
                {
                    "order_ref": f"NI-{counter:05d}",
                    "order_date": (START + timedelta(days=day)).isoformat(),
                    "customer_id": f"K-{city[:3].upper()}-{rng.randrange(1, 26):03d}",
                    "origin_name": warehouse["name"],
                    "origin_city": warehouse["city"],
                    "origin_country": warehouse["country"],
                    "dest_city": city,
                    "dest_country": country,
                    "units": units,
                    "weight_kg": round(weight, 2),
                    "mode": mode,
                    "product_category": rng.choice(CATEGORIES),
                    "order_value": round(amount, 2),
                    "returned": 1 if rng.random() < returns else 0,
                }
            )
            counter += 1

    rows.sort(key=lambda row: row["order_date"])
    return rows


def main():
    problems = check_cities()
    if problems:
        print("city lookup failed:")
        for problem in problems:
            print(" ", problem)
        return 1

    rows = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    orders_path = OUT_DIR / "orders.csv"
    with open(orders_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    warehouses_path = OUT_DIR / "warehouses.csv"
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
                "capacity_kg",
            ],
        )
        writer.writeheader()
        writer.writerows(WAREHOUSES)

    suppliers_path = OUT_DIR / "suppliers.csv"
    with open(suppliers_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "name",
                "city",
                "country",
                "supplies",
                "mode",
                "annual_weight_kg",
                "shipments_per_year",
                "annual_cost",
                "lead_time_days",
                "min_order_qty",
                "on_time_rate",
            ]
        )
        writer.writerows(SUPPLIERS)

    weight = sum(row["weight_kg"] for row in rows)
    print(
        f"{len(rows):,} orders across {len(LANES)} lanes, {len(WAREHOUSES)} "
        f"warehouses and {len(SUPPLIERS)} suppliers"
    )
    print(f"{weight / 1000:,.0f} tonnes shipped over the year")
    print(f"wrote three files into {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
