"""Tests for what a file turns into once it is loaded.

These exist because of a bug that produced a report nobody could tell was
wrong. Places were identified by name alone, so a company shipping from ten
cities under one company name arrived as a single origin sitting in whichever
city was read first. Every lane then inherited that city's coordinates: road
freight between two German cities was priced over the distance from Shanghai,
and twelve lanes were reported as six. Every figure downstream was wrong and
every one of them looked plausible.

So these check the things that have to hold before any arithmetic is worth
reading: that every order is accounted for, that lanes are separated by where
they start as well as where they end, and that each lane is measured over its
own ground.

Run with: python -m unittest discover tests
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimizer import analysis, db, geo, ingest

# One company name, ten origin cities, two orders on each lane. Twelve lanes,
# twenty-four orders, and four lanes into Stockholm by road that differ only by
# where they set off.
ONE_NAME_MANY_CITIES = """\
origin_name,origin_city,dest_city,weight_kg,mode
Northstar Electronics,Shanghai,Stockholm,12000,air
Northstar Electronics,Shanghai,Stockholm,8500,air
Northstar Electronics,Shenzhen,Berlin,9000,air
Northstar Electronics,Shenzhen,Berlin,6000,air
Northstar Electronics,New York,Stockholm,5000,air
Northstar Electronics,New York,Stockholm,3000,air
Northstar Electronics,Munich,Stockholm,4000,road
Northstar Electronics,Munich,Stockholm,3500,road
Northstar Electronics,Hamburg,Stockholm,10000,road
Northstar Electronics,Hamburg,Stockholm,7000,road
Northstar Electronics,Oslo,Stockholm,6000,road
Northstar Electronics,Oslo,Stockholm,5000,road
Northstar Electronics,Warsaw,Stockholm,7000,rail
Northstar Electronics,Warsaw,Stockholm,5000,rail
Northstar Electronics,Hamburg,Stockholm,8000,rail
Northstar Electronics,Hamburg,Stockholm,6000,rail
Northstar Electronics,Shanghai,Gothenburg,30000,sea
Northstar Electronics,Shanghai,Gothenburg,22000,sea
Northstar Electronics,Rotterdam,Stockholm,18000,sea
Northstar Electronics,Rotterdam,Stockholm,12000,sea
Northstar Electronics,Paris,Stockholm,2500,road
Northstar Electronics,Paris,Stockholm,2000,road
Northstar Electronics,Helsinki,Stockholm,3500,sea
Northstar Electronics,Helsinki,Stockholm,2500,sea
"""


def load(text, name="orders.csv"):
    """Load a CSV written as a string and return the connection and report."""
    folder = Path(tempfile.mkdtemp(prefix="sco-test-"))
    path = folder / name
    path.write_text(text, encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init(conn)
    report = ingest.load(conn, path)
    analysis.run(conn)
    return conn, report


def lanes(conn):
    """Every lane as (origin city, destination city, mode) with its figures."""
    return {
        (row["origin_city"], row["dest_city"], row["mode"]): row
        for row in conn.execute(
            """
            SELECT o.city AS origin_city, d.city AS dest_city, e.mode,
                   e.order_count, e.total_weight_kg, e.distance_km
            FROM edges e
            JOIN nodes o ON o.id = e.origin_id
            JOIN nodes d ON d.id = e.dest_id
            """
        )
    }


class EveryOrderIsAccountedFor(unittest.TestCase):
    """Loaded plus skipped has to equal what was in the file."""

    def test_every_row_is_either_loaded_or_reported(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        rows = len(ONE_NAME_MANY_CITIES.strip().splitlines()) - 1
        self.assertEqual(report["orders_loaded"] + report["rows_skipped"], rows)

    def test_this_file_loses_nothing(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        self.assertEqual(report["orders_loaded"], 24)
        self.assertEqual(report["rows_skipped"], 0)
        self.assertEqual(report["errors"], [])

    def test_a_skipped_row_says_which_line_and_why(self):
        broken = ONE_NAME_MANY_CITIES.replace(
            "Northstar Electronics,Munich,Stockholm,4000,road",
            "Northstar Electronics,Munich,Stockholm,not a number,road",
        )
        conn, report = load(broken)
        self.assertEqual(report["orders_loaded"], 23)
        self.assertEqual(report["rows_skipped"], 1)
        problem = report["errors"][0]
        self.assertEqual(problem["line"], 8)
        self.assertIn("weight_kg", problem["problem"])

    def test_the_weight_that_arrives_is_the_weight_in_the_file(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        in_file = sum(
            float(line.split(",")[3])
            for line in ONE_NAME_MANY_CITIES.strip().splitlines()[1:]
        )
        in_lanes = conn.execute("SELECT SUM(total_weight_kg) FROM edges").fetchone()[0]
        self.assertAlmostEqual(in_lanes, in_file, places=3)


class LanesAreSeparatedByWhereTheyStart(unittest.TestCase):
    """The bug this file exists for: origins folded together by name."""

    def test_one_lane_per_origin_destination_and_mode(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        self.assertEqual(report["edges"], 12)
        self.assertEqual(len(lanes(conn)), 12)

    def test_two_origins_into_one_destination_by_air_stay_apart(self):
        found = lanes(load(ONE_NAME_MANY_CITIES)[0])
        self.assertIn(("Shanghai", "Stockholm", "air"), found)
        self.assertIn(("New York", "Stockholm", "air"), found)

    def test_four_origins_into_one_destination_by_road_stay_apart(self):
        found = lanes(load(ONE_NAME_MANY_CITIES)[0])
        for city in ("Munich", "Hamburg", "Oslo", "Paris"):
            self.assertIn((city, "Stockholm", "road"), found)

    def test_one_origin_on_two_modes_stays_apart(self):
        found = lanes(load(ONE_NAME_MANY_CITIES)[0])
        self.assertIn(("Hamburg", "Stockholm", "road"), found)
        self.assertIn(("Hamburg", "Stockholm", "rail"), found)

    def test_orders_on_the_same_lane_are_added_together(self):
        found = lanes(load(ONE_NAME_MANY_CITIES)[0])
        hamburg_road = found[("Hamburg", "Stockholm", "road")]
        self.assertEqual(hamburg_road["order_count"], 2)
        self.assertAlmostEqual(hamburg_road["total_weight_kg"], 17_000)

    def test_a_name_used_for_one_city_is_left_alone(self):
        """The ordinary case has to keep reading the way it was written."""
        conn, report = load(
            "origin_name,origin_city,dest_city,weight_kg,mode\n"
            "Bristol Roastery,Bristol,London,900,road\n"
        )
        names = [row["name"] for row in conn.execute(
            "SELECT name FROM nodes WHERE node_type = 'warehouse'"
        )]
        self.assertEqual(names, ["Bristol Roastery"])

    def test_a_name_used_for_several_cities_says_which_is_which(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        names = sorted(
            row["name"] for row in conn.execute(
                "SELECT name FROM nodes WHERE node_type = 'warehouse'"
            )
        )
        self.assertIn("Northstar Electronics, Hamburg", names)
        self.assertIn("Northstar Electronics, Shanghai", names)


class EveryLaneIsMeasuredOverItsOwnGround(unittest.TestCase):
    """No lane may inherit another lane's distance."""

    def test_each_lane_matches_the_distance_between_its_own_two_cities(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        for (origin_city, dest_city, mode), row in lanes(conn).items():
            with self.subTest(lane=f"{origin_city} to {dest_city} by {mode}"):
                start = geo.locate(origin_city)
                end = geo.locate(dest_city)
                expected = geo.distance_km(start[0], start[1], end[0], end[1])
                self.assertAlmostEqual(row["distance_km"], expected, places=3)

    def test_a_short_european_lane_is_not_priced_as_a_long_asian_one(self):
        """Oslo to Stockholm is about 400km. It was being priced at 7,765."""
        found = lanes(load(ONE_NAME_MANY_CITIES)[0])
        oslo = found[("Oslo", "Stockholm", "road")]["distance_km"]
        shanghai = found[("Shanghai", "Stockholm", "air")]["distance_km"]
        self.assertLess(oslo, 600)
        self.assertGreater(shanghai, 7_000)

    def test_the_same_origin_pair_agrees_across_modes(self):
        found = lanes(load(ONE_NAME_MANY_CITIES)[0])
        self.assertAlmostEqual(
            found[("Hamburg", "Stockholm", "road")]["distance_km"],
            found[("Hamburg", "Stockholm", "rail")]["distance_km"],
        )


class NamesPeopleActuallyType(unittest.TestCase):
    """Cities the reference table files under a different name.

    Each of these was an order dropped from the analysis with "no match"
    against it, which is the quiet version of getting the answer wrong.
    """

    def test_common_aliases_resolve(self):
        for typed, expected_city in [
            ("New York", "New York City"),
            ("Bombay", "Mumbai"),
            ("Bangalore", "Bengaluru"),
            ("Saigon", "Ho Chi Minh City"),
        ]:
            with self.subTest(city=typed):
                self.assertEqual(geo.locate(typed), geo.locate(expected_city))


if __name__ == "__main__":
    unittest.main()
