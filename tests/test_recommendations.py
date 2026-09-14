"""Tests for what the report is allowed to say about a route.

test_ingest.py checks that the routes are built right. These check what is
worked out from them: that a candidate mode is measured over its own ground,
that nothing is recommended unless cost and carbon both fall by the threshold,
that the figures on a recommendation reconcile with the route they came from,
and that the load report accounts for every row.

Run with: python -m unittest discover tests
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from optimizer import analysis, db, diagnosis, distance, factors, geo, ingest, scoring
from test_ingest import ONE_NAME_MANY_CITIES

HEADER = "origin_name,origin_city,dest_city,weight_kg,mode\n"


def write(text, name):
    folder = Path(tempfile.mkdtemp(prefix="sco-test-"))
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def load(text, suppliers=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init(conn)
    paths = [write(text, "orders.csv"), None]
    if suppliers is not None:
        paths.append(write(suppliers, "suppliers.csv"))
    report = ingest.load(conn, *paths)
    analysis.run(conn)
    return conn, report


class DistanceBelongsToTheRouteAndTheMode(unittest.TestCase):
    def setUp(self):
        self.conn, _ = load(ONE_NAME_MANY_CITIES)
        self.lanes = {
            (lane["origin_name"], lane["dest_name"], lane["mode"]): lane
            for lane in scoring.rank(self.conn)
        }

    def test_the_stored_distance_is_the_straight_line_between_its_own_ends(self):
        for key, lane in self.lanes.items():
            origin = self.conn.execute(
                "SELECT lat, lon FROM nodes WHERE id = ?", (lane["origin_id"],)
            ).fetchone()
            dest = self.conn.execute(
                "SELECT lat, lon FROM nodes WHERE id = ?", (lane["dest_id"],)
            ).fetchone()
            with self.subTest(route=key):
                self.assertAlmostEqual(
                    lane["distance_km"],
                    geo.distance_km(origin["lat"], origin["lon"], dest["lat"], dest["lon"]),
                    places=6,
                )

    def test_freight_is_priced_over_the_distance_of_its_own_mode(self):
        for key, lane in self.lanes.items():
            expected = (
                lane["total_weight_kg"] / 1000
                * lane["distance_km"]
                * factors.circuity(lane["mode"])
                * factors.cost_factor(lane["mode"])
            )
            with self.subTest(route=key):
                self.assertAlmostEqual(lane["transport_cost"], expected, places=6)

    def test_the_proposed_mode_uses_its_own_multiplier_on_the_same_route(self):
        report = diagnosis.build(self.conn)
        for problem in report["problems"]:
            route = problem["route"]
            if not route:
                continue
            lane = next(l for l in self.lanes.values() if l["id"] == route["edge_id"])
            with self.subTest(route=problem["title"]):
                self.assertEqual(route["straight_km"], lane["distance_km"])
                self.assertAlmostEqual(
                    route["proposed"]["route_km"],
                    lane["distance_km"] * factors.circuity(route["proposed"]["mode"]),
                )
                self.assertAlmostEqual(
                    route["now"]["route_km"],
                    lane["distance_km"] * factors.circuity(lane["mode"]),
                )

    def test_two_routes_into_one_city_are_not_given_the_same_distance(self):
        shanghai = self.lanes[("Northstar Electronics, Shanghai", "Stockholm", "air")]
        new_york = self.lanes[("Northstar Electronics, New York", "Stockholm", "air")]
        self.assertNotAlmostEqual(shanghai["distance_km"], new_york["distance_km"], places=0)

    def test_a_scenario_from_another_warehouse_is_measured_from_that_warehouse(self):
        lane = self.lanes[("Northstar Electronics, Oslo", "Stockholm", "road")]
        other = self.conn.execute(
            "SELECT id, lat, lon FROM nodes WHERE name = 'Northstar Electronics, Shanghai'"
        ).fetchone()
        dest = self.conn.execute(
            "SELECT lat, lon FROM nodes WHERE id = ?", (lane["dest_id"],)
        ).fetchone()
        result = scoring.simulate(self.conn, lane["id"], mode="rail", origin_id=other["id"])
        straight = geo.distance_km(other["lat"], other["lon"], dest["lat"], dest["lon"])
        self.assertAlmostEqual(result["after"]["straight_km"], straight, places=6)
        self.assertAlmostEqual(
            result["after"]["route_km"], distance.by_mode(straight, "rail"), places=6
        )
        self.assertAlmostEqual(result["before"]["straight_km"], lane["distance_km"])

    def test_a_scenario_that_changes_nothing_saves_nothing(self):
        lane = self.lanes[("Northstar Electronics, Shanghai", "Stockholm", "air")]
        result = scoring.simulate(self.conn, lane["id"], mode="air")
        self.assertFalse(result["changed"])
        self.assertAlmostEqual(result["saved"]["cost"], 0.0, places=6)
        self.assertAlmostEqual(result["saved"]["co2e"], 0.0, places=6)
        self.assertAlmostEqual(result["before"]["cost"], lane["cost"], places=6)

    def test_a_scenario_says_when_a_mode_may_not_exist_on_the_route(self):
        lane = self.lanes[("Northstar Electronics, Oslo", "Stockholm", "road")]
        self.assertIsNotNone(scoring.simulate(self.conn, lane["id"], mode="sea")["note"])
        far = self.lanes[("Northstar Electronics, Shanghai", "Stockholm", "air")]
        self.assertIsNotNone(scoring.simulate(self.conn, far["id"], mode="road")["note"])


class OnlyRoutesWhereBothFallAreRecommended(unittest.TestCase):
    def test_every_flag_clears_both_thresholds_and_every_non_flag_does_not(self):
        conn, _ = load(ONE_NAME_MANY_CITIES)
        for lane in scoring.rank(conn):
            both = (
                lane["switch"] is not None
                and lane["saving_cost_pct"] >= scoring.FLAG_THRESHOLD
                and lane["saving_co2e_pct"] >= scoring.FLAG_THRESHOLD
            )
            with self.subTest(route=(lane["origin_name"], lane["mode"])):
                self.assertEqual(lane["flagged"], both)

    def test_sea_and_rail_routes_are_never_told_to_switch(self):
        conn, _ = load(ONE_NAME_MANY_CITIES)
        for lane in scoring.rank(conn):
            if lane["mode"] in ("sea", "rail"):
                with self.subTest(route=lane["origin_name"]):
                    self.assertFalse(lane["flagged"])

    def test_a_route_carried_by_its_packaging_does_not_qualify(self):
        """Hundreds of tiny parcels flown a short way. A mode switch cuts the
        freight, but the freight is a sliver of what the route costs, so it
        cannot give back a quarter of the whole and must not be recommended."""
        rows = "".join(
            "Depot,Hamburg,Berlin,1,air\n" for _ in range(300)
        )
        conn, _ = load(HEADER + rows)
        (lane,) = scoring.rank(conn)
        self.assertFalse(lane["flagged"])
        self.assertLess(
            min(lane["saving_cost_pct"], lane["saving_co2e_pct"]), scoring.FLAG_THRESHOLD
        )


class RecommendationsReconcile(unittest.TestCase):
    def setUp(self):
        self.conn, _ = load(ONE_NAME_MANY_CITIES)
        self.report = diagnosis.build(self.conn)
        self.lanes = {lane["id"]: lane for lane in scoring.rank(self.conn)}

    def test_the_saving_on_a_card_is_current_minus_proposed(self):
        for problem in self.report["problems"]:
            route = problem["route"]
            if not route:
                continue
            with self.subTest(route=problem["title"]):
                self.assertAlmostEqual(
                    problem["cost_at_stake"],
                    route["now"]["cost"] - route["proposed"]["cost"],
                    places=6,
                )
                self.assertAlmostEqual(
                    problem["co2e_at_stake"],
                    route["now"]["co2e"] - route["proposed"]["co2e"],
                    places=6,
                )

    def test_current_figures_are_the_routes_own_figures(self):
        for problem in self.report["problems"]:
            route = problem["route"]
            if not route:
                continue
            lane = self.lanes[route["edge_id"]]
            with self.subTest(route=problem["title"]):
                self.assertAlmostEqual(route["now"]["cost"], lane["cost"], places=6)
                self.assertAlmostEqual(route["now"]["co2e"], lane["co2e"], places=6)

    def test_the_audit_lines_add_up_to_the_totals(self):
        for problem in self.report["problems"]:
            route = problem["route"]
            if not route:
                continue
            for side in ("now", "proposed"):
                figures = route[side]
                with self.subTest(route=problem["title"], side=side):
                    self.assertAlmostEqual(
                        figures["freight_cost"],
                        figures["tonne_km"] * figures["cost_factor"],
                        places=6,
                    )
                    self.assertAlmostEqual(
                        figures["cost"],
                        figures["freight_cost"] + figures["returns_cost"] + figures["fixed_cost"],
                        places=6,
                    )
                    self.assertAlmostEqual(
                        figures["co2e"],
                        figures["freight_co2e"] + figures["returns_co2e"] + figures["fixed_co2e"],
                        places=6,
                    )

    def test_the_headline_is_the_sum_of_the_list(self):
        overview = self.report["overview"]
        self.assertAlmostEqual(
            overview["recoverable_cost"],
            sum(p["cost_at_stake"] for p in self.report["problems"]),
        )

    def test_the_same_simulation_on_the_proposed_mode_matches_the_card(self):
        for problem in self.report["problems"]:
            route = problem["route"]
            if not route:
                continue
            result = scoring.simulate(
                self.conn, route["edge_id"], mode=route["proposed"]["mode"]
            )
            with self.subTest(route=problem["title"]):
                self.assertAlmostEqual(result["saved"]["cost"], problem["cost_at_stake"], places=6)
                self.assertAlmostEqual(result["after"]["days"], route["proposed"]["days"], places=6)

    def test_each_route_has_its_own_confidence(self):
        """Confidence used to be keyed by origin and destination names, which
        gave a road route and a rail route between the same places one entry."""
        from optimizer import stats

        rows = stats.confidence(scoring.rank(self.conn))
        ids = [row["edge_id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)))
        for row in rows:
            self.assertTrue(0.0 <= row["confidence"] <= 1.0)

    def test_every_route_recommendation_carries_a_confidence_and_checks(self):
        for problem in self.report["problems"]:
            with self.subTest(route=problem["title"]):
                self.assertTrue(problem["checks"])
                if problem["kind"] == "mode_switch":
                    self.assertIn(problem["confidence_label"], ("high", "moderate", "low"))


class TheLoadReportAccountsForEveryRow(unittest.TestCase):
    def test_a_clean_file(self):
        conn, report = load(ONE_NAME_MANY_CITIES)
        self.assertEqual(report["rows_in_file"], 24)
        self.assertEqual(report["orders_loaded"], 24)
        self.assertEqual(report["rows_skipped"], 0)
        self.assertEqual(report["lanes"], 12)
        self.assertAlmostEqual(report["weight_in_file_kg"], report["weight_loaded_kg"])
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(db.ingest_report(conn)["orders_loaded"], 24)

    def test_bad_rows_are_counted_and_explained_by_field(self):
        text = (
            HEADER
            + "Depot,Hamburg,Berlin,100,road\n"
            + "Depot,Nowhereville Xyz,Berlin,200,road\n"
            + "Depot,Hamburg,Atlantis Qqq,300,road\n"
            + "Depot,Hamburg,Berlin,400,teleport\n"
            + "Depot,Hamburg,Berlin,heavy,road\n"
        )
        conn, report = load(text)
        self.assertEqual(report["rows_in_file"], 5)
        self.assertEqual(report["orders_loaded"], 1)
        self.assertEqual(report["rows_skipped"], 4)
        self.assertEqual(report["orders_loaded"] + report["rows_skipped"], report["rows_in_file"])
        fields = {error["line"]: error["field"] for error in report["errors"]}
        self.assertEqual(fields, {3: "origin_city", 4: "dest_city", 5: "mode", 6: "weight_kg"})
        self.assertFalse(report["checks"]["origins_found"])
        self.assertFalse(report["checks"]["destinations_found"])
        # The unreadable weight cannot be counted, the other three can.
        self.assertAlmostEqual(report["weight_in_file_kg"], 1000)
        self.assertAlmostEqual(report["weight_loaded_kg"], 100)
        self.assertAlmostEqual(report["weight_excluded_kg"], 900)

    def test_repeated_orders_are_kept_and_pointed_out(self):
        text = (
            "order_ref," + HEADER
            + "A1,Depot,Hamburg,Berlin,100,road\n"
            + "A1,Depot,Hamburg,Berlin,100,road\n"
            + "A2,Depot,Hamburg,Berlin,100,road\n"
        )
        conn, report = load(text)
        self.assertEqual(report["orders_loaded"], 3)
        (warning,) = report["warnings"]
        self.assertEqual(warning["kind"], "duplicates")
        self.assertEqual(warning["count"], 1)
        self.assertEqual(warning["lines"][0], {"line": 3, "same_as": 2})

    def test_identical_rows_without_a_reference_are_pointed_out_too(self):
        conn, report = load(HEADER + "Depot,Hamburg,Berlin,100,road\n" * 2)
        self.assertEqual(report["orders_loaded"], 2)
        self.assertEqual(report["warnings"][0]["count"], 1)

    def test_two_supplier_rows_for_one_route_are_added_not_dropped(self):
        suppliers = (
            "name,city,supplies,mode,annual_weight_kg\n"
            "Mill,Porto,Depot,road,1000\n"
            "Mill,Porto,Depot,road,500\n"
        )
        conn, report = load(HEADER + "Depot,Hamburg,Berlin,100,road\n", suppliers)
        weight = conn.execute(
            """
            SELECT e.total_weight_kg FROM edges e
            JOIN nodes o ON o.id = e.origin_id
            WHERE o.node_type = 'supplier'
            """
        ).fetchall()
        self.assertEqual([row[0] for row in weight], [1500])
        self.assertEqual(report["edges"], 2)
        self.assertEqual(report["warnings"][0]["kind"], "supplier_rows_combined")

    def test_many_origins_into_one_destination_keep_every_order(self):
        cities = ["Munich", "Hamburg", "Oslo", "Paris", "Warsaw", "Rotterdam"]
        text = HEADER + "".join(
            f"Acme,{city},Stockholm,{100 + index},road\n" for index, city in enumerate(cities)
        )
        conn, report = load(text)
        self.assertEqual(report["orders_loaded"], 6)
        self.assertEqual(report["lanes"], 6)
        self.assertEqual(
            conn.execute("SELECT COUNT(DISTINCT distance_km) FROM edges").fetchone()[0], 6
        )


if __name__ == "__main__":
    unittest.main()
