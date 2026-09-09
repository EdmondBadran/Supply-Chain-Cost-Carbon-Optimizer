"""Tests for the parts of the engine that were tuned by hand.

The thresholds in scoring.py and chain.py decide which routes get flagged and
in what order, and they have been adjusted twice by eye. Nothing about them is
derivable from first principles, so the risk is not that they are wrong. It is
that somebody changes one and nothing complains until the output looks strange
weeks later. These tests pin the behaviour the thresholds are there to produce,
not the numbers themselves, so moving a threshold on purpose is easy and moving
one by accident is loud.

Run with: python -m unittest discover tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimizer import analysis, chain, db, factors, scoring


def lane(mode="air", distance_km=8000, weight_kg=100_000, orders=100, returns=0):
    """A lane dict shaped like the rows rank() works on."""
    costs = analysis.lane_costs(weight_kg, distance_km, mode, orders, returns)
    emissions = analysis.lane_emissions(weight_kg, distance_km, mode, orders, returns)
    return {
        "mode": mode,
        "distance_km": distance_km,
        "total_weight_kg": weight_kg,
        "order_count": orders,
        "return_count": returns,
        "transport_cost": costs["transport"],
        "returns_cost": costs["returns"],
        "transport_co2e": emissions["transport"],
        "returns_co2e": emissions["returns"],
    }


class PlausibleModes(unittest.TestCase):
    """What a lane is allowed to switch to, given how far it goes."""

    def test_air_can_drop_to_sea_on_a_long_haul(self):
        self.assertIn("sea", scoring.plausible_modes(lane(distance_km=12_000)))

    def test_sea_is_refused_below_the_minimum(self):
        short = lane(distance_km=scoring.SEA_MINIMUM_KM - 1)
        self.assertNotIn("sea", scoring.plausible_modes(short))

    def test_sea_is_offered_at_the_minimum(self):
        at_limit = lane(distance_km=scoring.SEA_MINIMUM_KM)
        self.assertIn("sea", scoring.plausible_modes(at_limit))

    def test_surface_is_refused_beyond_its_range(self):
        far = scoring.plausible_modes(lane(distance_km=scoring.SURFACE_RANGE_KM + 1))
        self.assertNotIn("road", far)
        self.assertNotIn("rail", far)

    def test_surface_is_offered_inside_its_range(self):
        near = scoring.plausible_modes(lane(distance_km=scoring.SURFACE_RANGE_KM - 1))
        self.assertIn("rail", near)

    def test_road_can_only_become_rail(self):
        # A road lane is already a land route, so the range gate does not
        # apply to it however long it is.
        self.assertEqual(scoring.plausible_modes(lane("road", 9000)), ["rail"])

    def test_nothing_beats_sea_or_rail(self):
        self.assertEqual(scoring.plausible_modes(lane("sea", 12_000)), [])
        self.assertEqual(scoring.plausible_modes(lane("rail", 2000)), [])


class BestSwitch(unittest.TestCase):
    """Picking the recommended mode."""

    def test_a_switch_is_found_for_an_expensive_air_lane(self):
        switch = scoring.best_switch(lane("air", 12_000))
        self.assertIsNotNone(switch)
        self.assertEqual(switch["mode"], "sea")
        self.assertGreater(switch["saved_cost"], 0)
        self.assertGreater(switch["saved_co2e"], 0)

    def test_nothing_is_offered_when_there_is_no_alternative(self):
        self.assertIsNone(scoring.best_switch(lane("sea", 12_000)))

    def test_the_gain_is_unit_free(self):
        """The bug this guards against.

        gain used to be saved_cost + saved_co2e, adding dollars to kilograms.
        Scaling the whole lane by weight scales both savings identically, so a
        unit-free gain is unchanged by it. A gain that adds raw figures is not,
        because the two units grow at different absolute rates.
        """
        small = scoring.best_switch(lane("air", 12_000, weight_kg=10_000))
        large = scoring.best_switch(lane("air", 12_000, weight_kg=10_000_000))
        self.assertEqual(small["mode"], large["mode"])
        self.assertAlmostEqual(small["gain"], large["gain"], places=6)

    def test_the_gain_is_a_share_of_the_lane(self):
        switch = scoring.best_switch(lane("air", 12_000))
        # Two fractions, each at most 1, so the ceiling is 2. Anything larger
        # means raw money has leaked back into the score.
        self.assertLessEqual(switch["gain"], 2.0)
        self.assertGreater(switch["gain"], 0.0)


class EffortWeighting(unittest.TestCase):
    def test_easy_work_outranks_hard_work(self):
        self.assertGreater(scoring.EFFORT_WEIGHT["low"], scoring.EFFORT_WEIGHT["med"])
        self.assertGreater(scoring.EFFORT_WEIGHT["med"], scoring.EFFORT_WEIGHT["high"])

    def test_untagged_work_sits_in_the_middle(self):
        self.assertEqual(scoring.EFFORT_WEIGHT["med"], 1.0)


class Fixture(unittest.TestCase):
    """A small network built directly, so the figures are controlled."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self.tmp.name) / "test.db")
        db.init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def node(self, name, node_type, lat, lon, **extra):
        cursor = self.conn.execute(
            """
            INSERT INTO nodes (name, node_type, city, country, lat, lon,
                storage_cost_annual, energy_kwh_annual, grid_intensity,
                lead_time_days, min_order_qty, on_time_rate, capacity_kg)
            VALUES (?, ?, '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                node_type,
                lat,
                lon,
                extra.get("storage_cost_annual", 0.0),
                extra.get("energy_kwh_annual", 0.0),
                extra.get("grid_intensity"),
                extra.get("lead_time_days"),
                extra.get("min_order_qty"),
                extra.get("on_time_rate"),
                extra.get("capacity_kg"),
            ),
        )
        return cursor.lastrowid

    def edge(self, origin, dest, mode, weight_kg, distance_km, orders=100, returns=0):
        cursor = self.conn.execute(
            """
            INSERT INTO edges (origin_id, dest_id, mode, order_count,
                total_weight_kg, total_value, return_count, distance_km)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (origin, dest, mode, orders, weight_kg, returns, distance_km),
        )
        return cursor.lastrowid


class Ranking(Fixture):
    def test_overlap_takes_the_smaller_side(self):
        """A lane huge on cost and tiny on carbon must not score as an overlap.

        This is the rule the whole tool rests on. If overlap ever became a sum
        or an average, a purely expensive lane would outrank a genuinely dual
        problem and the product would be a cost tool wearing a carbon badge.
        """
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        near = self.node("Near", "customer", 52.4, 4.9)
        far = self.node("Far", "customer", -33.9, 151.2)
        self.edge(warehouse, near, "road", 5_000_000, 60)
        self.edge(warehouse, far, "air", 20_000, 16_000)
        analysis.run(self.conn)

        lanes = scoring.rank(self.conn)
        for row in lanes:
            self.assertLessEqual(
                row["overlap"], min(row["cost_score"], row["co2e_score"]) + 1e-9
            )

    def test_flagging_needs_both_sides_past_the_threshold(self):
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        city = self.node("C", "customer", -33.9, 151.2)
        self.edge(warehouse, city, "air", 200_000, 16_000)
        analysis.run(self.conn)

        found = scoring.rank(self.conn)[0]
        self.assertTrue(found["flagged"])
        self.assertGreaterEqual(found["saving_cost_pct"], scoring.FLAG_THRESHOLD)
        self.assertGreaterEqual(found["saving_co2e_pct"], scoring.FLAG_THRESHOLD)
        self.assertEqual(
            found["opportunity"],
            min(found["saving_cost_pct"], found["saving_co2e_pct"]),
        )

    def test_a_lane_with_no_switch_is_never_flagged(self):
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        city = self.node("C", "customer", -33.9, 151.2)
        self.edge(warehouse, city, "sea", 200_000, 16_000)
        analysis.run(self.conn)

        found = scoring.rank(self.conn)[0]
        self.assertIsNone(found["switch"])
        self.assertFalse(found["flagged"])
        self.assertEqual(found["opportunity"], 0.0)

    def test_high_effort_falls_behind_a_smaller_easy_win(self):
        """The tag has to be able to outrank size or it looks decorative."""
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        big_city = self.node("Big", "customer", -33.9, 151.2)
        small_city = self.node("Small", "customer", -37.8, 144.9)
        big = self.edge(warehouse, big_city, "air", 300_000, 16_000)
        small = self.edge(warehouse, small_city, "air", 200_000, 16_000)
        analysis.run(self.conn)

        self.assertEqual(scoring.rank(self.conn)[0]["id"], big)
        scoring.set_effort(self.conn, big, "high")
        scoring.set_effort(self.conn, small, "low")
        self.assertEqual(scoring.rank(self.conn)[0]["id"], small)

    def test_an_unknown_effort_is_refused(self):
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        city = self.node("C", "customer", -33.9, 151.2)
        edge_id = self.edge(warehouse, city, "air", 200_000, 16_000)
        with self.assertRaises(ValueError):
            scoring.set_effort(self.conn, edge_id, "trivial")


class NetworkSimulation(Fixture):
    def build(self):
        self.rotterdam = self.node(
            "Rotterdam", "warehouse", 51.9, 4.5,
            storage_cost_annual=400_000, energy_kwh_annual=900_000,
            grid_intensity=0.3,
        )
        self.madrid = self.node(
            "Madrid", "warehouse", 40.4, -3.7,
            storage_cost_annual=250_000, energy_kwh_annual=600_000,
            grid_intensity=0.2,
        )
        self.lisbon = self.node("Lisbon", "customer", 38.7, -9.1)
        self.berlin = self.node("Berlin", "customer", 52.5, 13.4)
        self.edge(self.rotterdam, self.berlin, "road", 400_000, 580)
        self.edge(self.madrid, self.lisbon, "road", 300_000, 500)
        analysis.run(self.conn)

    def test_changing_nothing_changes_nothing(self):
        """The baseline has to be exactly zero.

        If simply running the simulation re-plans the network, every closure
        result carries an unrelated saving inside it and gets the credit.
        """
        self.build()
        result = scoring.simulate_network(self.conn)
        self.assertEqual(result["moved_lanes"], 0)
        self.assertAlmostEqual(result["saved"]["cost"], 0.0, places=4)
        self.assertAlmostEqual(result["saved"]["co2e"], 0.0, places=4)

    def test_closing_a_site_moves_its_work(self):
        self.build()
        result = scoring.simulate_network(self.conn, closed_ids=[self.madrid])
        self.assertEqual(result["moved_lanes"], 1)

        sites = {site["name"]: site for site in result["sites"]}
        self.assertEqual(sites["Madrid"]["status"], "closed")
        self.assertEqual(sites["Madrid"]["tonnes_after"], 0.0)
        self.assertGreater(
            sites["Rotterdam"]["tonnes_after"], sites["Rotterdam"]["tonnes_before"]
        )

    def test_closing_a_site_stops_paying_for_it(self):
        self.build()
        result = scoring.simulate_network(self.conn, closed_ids=[self.madrid])
        # Freight gets longer, but the building stops costing anything, and
        # that trade is the whole reason anyone asks the question.
        self.assertGreater(result["saved"]["co2e"], 0)

    def test_everything_cannot_be_closed(self):
        self.build()
        with self.assertRaises(ValueError):
            scoring.simulate_network(
                self.conn, closed_ids=[self.rotterdam, self.madrid]
            )

    def test_capacity_is_reported_when_it_runs_out(self):
        self.build()
        result = scoring.simulate_network(self.conn, closed_ids=[self.rotterdam])
        madrid = next(s for s in result["sites"] if s["name"] == "Madrid")
        # Madrid handles 300 t and would take Rotterdam's 400 t on top, which
        # is past its headroom, so the answer has to admit that.
        self.assertTrue(madrid["full"])
        self.assertTrue(result["over_capacity"])

    def test_a_new_site_only_takes_what_is_nearer_to_it(self):
        self.build()
        result = scoring.simulate_network(
            self.conn, added={"name": "Porto", "lat": 41.1, "lon": -8.6}
        )
        sites = {site["name"]: site for site in result["sites"]}
        self.assertEqual(sites["Porto"]["status"], "added")
        # Berlin is nowhere near Porto, so Rotterdam keeps it.
        self.assertEqual(
            sites["Rotterdam"]["tonnes_after"], sites["Rotterdam"]["tonnes_before"]
        )
        self.assertGreater(sites["Porto"]["tonnes_after"], 0)

    def test_volume_is_conserved(self):
        self.build()
        result = scoring.simulate_network(self.conn, closed_ids=[self.madrid])
        before = sum(s["tonnes_before"] for s in result["sites"])
        after = sum(s["tonnes_after"] for s in result["sites"])
        self.assertAlmostEqual(before, after, places=6)


class ExpeditePenalty(unittest.TestCase):
    """Lateness turning into air freight, which is the supplier-carbon link."""

    def test_a_perfect_supplier_costs_nothing(self):
        self.assertIsNone(analysis.expedite_penalty(100_000, 8000, "sea", 1.0))

    def test_an_unrated_supplier_is_not_guessed_at(self):
        self.assertIsNone(analysis.expedite_penalty(100_000, 8000, "sea", None))

    def test_air_cannot_be_expedited_further(self):
        self.assertIsNone(analysis.expedite_penalty(100_000, 8000, "air", 0.5))

    def test_lateness_costs_money_and_carbon(self):
        penalty = analysis.expedite_penalty(100_000, 8000, "sea", 0.9)
        self.assertGreater(penalty["cost"], 0)
        self.assertGreater(penalty["co2e"], 0)
        self.assertAlmostEqual(
            penalty["share"], 0.1 * factors.EXPEDITE_SHARE_OF_LATE, places=9
        )

    def test_worse_suppliers_cost_more(self):
        better = analysis.expedite_penalty(100_000, 8000, "sea", 0.95)
        worse = analysis.expedite_penalty(100_000, 8000, "sea", 0.80)
        self.assertGreater(worse["cost"], better["cost"])
        self.assertGreater(worse["co2e"], better["co2e"])


class SupplierTerms(Fixture):
    def stage(self, **terms):
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        city = self.node("C", "customer", 52.4, 4.9)
        supplier = self.node("S", "supplier", 9.0, 38.7, **terms)
        self.edge(warehouse, city, "road", 100_000, 60)
        self.edge(supplier, warehouse, "sea", 100_000, 6000, orders=12)
        analysis.run(self.conn)
        stages = {s["key"]: s for s in chain.build(self.conn)}
        return stages["suppliers"]

    def kinds(self, stage):
        return {problem["kind"] for problem in stage["problems"]}

    def test_good_terms_raise_nothing(self):
        found = self.stage(lead_time_days=20, min_order_qty=1000, on_time_rate=0.99)
        self.assertNotIn("on_time", self.kinds(found))
        self.assertNotIn("lead_time", self.kinds(found))
        self.assertNotIn("min_order", self.kinds(found))

    def test_a_late_supplier_is_flagged_with_money_on_it(self):
        found = self.stage(on_time_rate=0.80)
        late = next(p for p in found["problems"] if p["kind"] == "on_time")
        self.assertGreater(late["cost_at_stake"], 0)
        self.assertGreater(late["co2e_at_stake"], 0)

    def test_a_supplier_on_the_target_is_not_flagged(self):
        found = self.stage(on_time_rate=chain.ON_TIME_TARGET)
        self.assertNotIn("on_time", self.kinds(found))

    def test_a_long_lead_time_is_flagged_without_money(self):
        found = self.stage(lead_time_days=chain.LEAD_TIME_LONG_DAYS + 1)
        slow = next(p for p in found["problems"] if p["kind"] == "lead_time")
        self.assertEqual(slow["cost_at_stake"], 0.0)
        self.assertIsNotNone(slow["note"])

    def test_a_lead_time_on_the_limit_is_not_flagged(self):
        found = self.stage(lead_time_days=chain.LEAD_TIME_LONG_DAYS)
        self.assertNotIn("lead_time", self.kinds(found))

    def test_a_minimum_order_past_the_limit_is_flagged(self):
        # The lane carries 100 t a year, so a month is 8.33 t.
        months = chain.MOQ_MONTHS_LIMIT + 1
        found = self.stage(min_order_qty=100_000 / 12 * months)
        self.assertIn("min_order", self.kinds(found))

    def test_a_minimum_order_inside_the_limit_is_not_flagged(self):
        months = chain.MOQ_MONTHS_LIMIT - 1
        found = self.stage(min_order_qty=100_000 / 12 * months)
        self.assertNotIn("min_order", self.kinds(found))


class StageSettings(Fixture):
    def build(self):
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        city = self.node("C", "customer", 52.4, 4.9)
        self.edge(warehouse, city, "road", 100_000, 60)
        analysis.run(self.conn)

    def names(self):
        return [stage["name"] for stage in chain.build(self.conn)]

    def test_the_defaults_are_seeded_once(self):
        self.build()
        self.assertEqual(len(chain.settings(self.conn)), len(chain.BUILTIN_STAGES))
        self.assertEqual(len(chain.settings(self.conn)), len(chain.BUILTIN_STAGES))

    def test_a_stage_can_be_renamed(self):
        self.build()
        chain.rename_stage(self.conn, "warehousing", "Depots", "Where it sits")
        self.assertIn("Depots", self.names())
        self.assertNotIn("Warehousing", self.names())

    def test_a_stage_cannot_be_left_nameless(self):
        self.build()
        with self.assertRaises(ValueError):
            chain.rename_stage(self.conn, "warehousing", "  ", "")

    def test_hiding_a_stage_removes_it_from_the_picture(self):
        self.build()
        chain.set_stage_hidden(self.conn, "customers", True)
        self.assertNotIn("Customers", self.names())
        chain.set_stage_hidden(self.conn, "customers", False)
        self.assertIn("Customers", self.names())

    def test_a_stage_can_be_moved(self):
        self.build()
        before = self.names()
        chain.move_stage(self.conn, "warehousing", "up")
        after = self.names()
        self.assertEqual(before[1], after[2])
        self.assertEqual(before[2], after[1])

    def test_moving_past_the_end_does_nothing(self):
        self.build()
        before = self.names()
        chain.move_stage(self.conn, "suppliers", "up")
        self.assertEqual(before, self.names())

    def test_a_custom_stage_measures_nothing(self):
        self.build()
        key = chain.add_stage(self.conn, "Assembly", "Parts become products")
        added = next(s for s in chain.build(self.conn) if s["key"] == key)
        self.assertIsNone(added["headline"])
        self.assertEqual(added["problems"], [])
        self.assertEqual(added["cost"], 0.0)

    def test_custom_stage_keys_do_not_collide(self):
        self.build()
        first = chain.add_stage(self.conn, "Assembly")
        second = chain.add_stage(self.conn, "Assembly")
        self.assertNotEqual(first, second)

    def test_a_built_in_stage_cannot_be_deleted(self):
        self.build()
        with self.assertRaises(ValueError):
            chain.remove_stage(self.conn, "returns")

    def test_a_custom_stage_can_be_deleted(self):
        self.build()
        key = chain.add_stage(self.conn, "Assembly")
        chain.remove_stage(self.conn, key)
        self.assertNotIn("Assembly", self.names())

    def test_resetting_restores_the_defaults(self):
        self.build()
        chain.rename_stage(self.conn, "returns", "Reverse logistics", "")
        chain.add_stage(self.conn, "Assembly")
        chain.reset_stages(self.conn)
        self.assertEqual(
            self.names(), [name for _, name, _ in chain.BUILTIN_STAGES]
        )

    def test_stages_survive_a_new_upload(self):
        """Renaming a stage describes the business, not the file."""
        self.build()
        chain.rename_stage(self.conn, "warehousing", "Depots", "Where it sits")
        db.reset(self.conn)
        self.assertEqual(
            [row["name"] for row in chain.settings(self.conn)][2], "Depots"
        )


if __name__ == "__main__":
    unittest.main()


class Capacity(Fixture):
    """A stated capacity has to beat the assumed one, and has to bite.

    The headroom constant was the only limit for a long time, so the risk with
    the column is not that it is read wrongly. It is that it is read and then
    quietly ignored somewhere downstream, which would look exactly like it
    working on a network that happens to have room.
    """

    def build(self, capacities):
        first = self.node("A", "warehouse", 51.9, 4.5, capacity_kg=capacities[0])
        second = self.node("B", "warehouse", 52.4, 4.9, capacity_kg=capacities[1])
        near = self.node("Near", "customer", 52.0, 4.6)
        far = self.node("Far", "customer", 52.5, 5.0)
        self.edge(first, near, "road", 100_000, 40)
        self.edge(second, far, "road", 100_000, 40)
        analysis.run(self.conn)
        return first, second

    def test_a_stated_capacity_is_used_instead_of_the_headroom(self):
        self.build([900_000, 900_000])
        result = scoring.simulate_network(self.conn, [], None)
        self.assertTrue(result["stated_capacity"])
        for site in result["sites"]:
            self.assertEqual(site["capacity_basis"], "stated")
            self.assertEqual(site["capacity_tonnes"], 900.0)

    def test_without_a_column_the_headroom_still_applies(self):
        self.build([None, None])
        result = scoring.simulate_network(self.conn, [], None)
        self.assertFalse(result["stated_capacity"])
        for site in result["sites"]:
            self.assertEqual(site["capacity_basis"], "assumed")
            self.assertAlmostEqual(
                site["capacity_tonnes"],
                site["tonnes_before"] * scoring.CAPACITY_HEADROOM,
            )

    def test_a_tight_stated_capacity_reports_the_strain(self):
        """Closing a site into a neighbour with no room has to say so.

        Under the headroom assumption both sites would look able to take 50%
        more and the move would come back clean. The column is the difference
        between an answer and a plausible-looking one.
        """
        _, second = self.build([110_000, 110_000])
        result = scoring.simulate_network(self.conn, [second], None)
        self.assertTrue(result["over_capacity"])

    def test_a_generous_stated_capacity_absorbs_the_move(self):
        _, second = self.build([500_000, 500_000])
        result = scoring.simulate_network(self.conn, [second], None)
        self.assertFalse(result["over_capacity"])
        self.assertEqual(result["moved_lanes"], 1)
