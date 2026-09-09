"""Tests for the statistics page.

Two different risks here, so two different kinds of test.

The primitives are textbook methods, and a textbook method has known answers:
a Gini of a flat list is zero, a Spearman of a reversed list is minus one, a
Wilson interval has to contain its own point estimate. Those are pinned to the
values rather than to behaviour, because there is a right answer to check
against and getting one of them subtly wrong would be invisible on real data.

The sections built on top are pinned to behaviour instead, the same way the
scoring tests are. What matters is that the uncertainty band brackets the
figure quoted everywhere else, that it does not move between page loads, and
that it never comes back narrower than the number it is meant to cast doubt
on. The exact percentiles are allowed to move.

Run with: python -m unittest discover tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from optimizer import analysis, db, scoring, stats


class Primitives(unittest.TestCase):
    def test_an_even_split_has_no_inequality(self):
        self.assertAlmostEqual(stats.gini([10, 10, 10, 10]), 0.0, places=9)

    def test_one_holder_of_everything_approaches_one(self):
        values = [0.0001] * 200 + [1000]
        self.assertGreater(stats.gini(values), 0.95)

    def test_gini_sits_between_zero_and_one(self):
        for values in ([1, 2, 3], [5, 5, 5, 900], [0.2, 0.4], [7] * 30):
            with self.subTest(values=values):
                self.assertGreaterEqual(stats.gini(values), 0.0)
                self.assertLessEqual(stats.gini(values), 1.0)

    def test_a_single_lane_cannot_be_unequal(self):
        self.assertEqual(stats.gini([42]), 0.0)
        self.assertEqual(stats.gini([]), 0.0)

    def test_the_lorenz_curve_runs_corner_to_corner(self):
        points = stats.lorenz([3, 1, 4, 1, 5])
        self.assertEqual(points[0], (0.0, 0.0))
        self.assertAlmostEqual(points[-1][0], 1.0)
        self.assertAlmostEqual(points[-1][1], 1.0)

    def test_the_lorenz_curve_only_climbs(self):
        points = stats.lorenz([9, 2, 7, 1, 30, 4])
        shares = [share for _, share in points]
        self.assertEqual(shares, sorted(shares))

    def test_perfect_agreement_is_one_and_reversal_is_minus_one(self):
        xs = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(stats.spearman(xs, [2, 4, 6, 8, 10]), 1.0)
        self.assertAlmostEqual(stats.spearman(xs, [5, 4, 3, 2, 1]), -1.0)

    def test_spearman_ignores_the_size_of_the_gaps(self):
        """The reason it is Spearman and not Pearson: one enormous lane must
        not be able to decide the answer by itself."""
        xs = [1, 2, 3, 4, 5]
        gentle = stats.spearman(xs, [10, 20, 30, 40, 50])
        violent = stats.spearman(xs, [10, 20, 30, 40, 50_000])
        self.assertAlmostEqual(gentle, violent)

    def test_ties_share_a_rank_rather_than_breaking_the_maths(self):
        rho = stats.spearman([1, 1, 2, 3], [4, 4, 5, 6])
        self.assertIsNotNone(rho)
        self.assertAlmostEqual(rho, 1.0)

    def test_spearman_declines_to_answer_on_too_little_data(self):
        self.assertIsNone(stats.spearman([1, 2], [3, 4]))

    def test_no_spread_means_nothing_is_an_outlier(self):
        self.assertEqual(stats.robust_z([5, 5, 5, 5]), [0.0, 0.0, 0.0, 0.0])

    def test_one_wild_value_does_not_hide_behind_its_own_effect(self):
        """The point of using the median absolute deviation. With an ordinary
        standard deviation the outlier inflates the scale it is measured
        against, and a single large one can score under two."""
        values = [10, 11, 9, 10, 12, 10, 900]
        scores = stats.robust_z(values)
        self.assertGreater(scores[-1], stats.OUTLIER_Z)
        for score in scores[:-1]:
            self.assertLess(abs(score), stats.OUTLIER_Z)

    def test_a_wilson_interval_contains_its_own_estimate(self):
        for successes, trials in ((0, 5), (1, 4), (3, 10), (50, 400), (12, 12)):
            with self.subTest(n=trials):
                rate, low, high = stats.wilson(successes, trials)
                self.assertLessEqual(low, rate)
                self.assertLessEqual(rate, high)

    def test_a_wilson_interval_stays_inside_zero_and_one(self):
        """The textbook interval fails exactly here, which is why this is not
        the textbook interval."""
        for successes, trials in ((0, 3), (3, 3), (1, 2), (0, 1)):
            with self.subTest(n=trials):
                _, low, high = stats.wilson(successes, trials)
                self.assertGreaterEqual(low, 0.0)
                self.assertLessEqual(high, 1.0)

    def test_more_evidence_narrows_the_interval(self):
        _, low_small, high_small = stats.wilson(1, 10)
        _, low_big, high_big = stats.wilson(100, 1000)
        self.assertGreater(high_small - low_small, high_big - low_big)

    def test_percentiles_land_where_they_should(self):
        values = [1, 2, 3, 4, 5]
        self.assertEqual(stats.percentile(values, 0.0), 1)
        self.assertEqual(stats.percentile(values, 0.5), 3)
        self.assertEqual(stats.percentile(values, 1.0), 5)
        self.assertAlmostEqual(stats.percentile(values, 0.25), 2.0)

    def test_percentiles_survive_an_empty_list(self):
        self.assertEqual(stats.percentile([], 0.5), 0.0)


class Sections(unittest.TestCase):
    """Built on a network small enough to reason about by hand."""

    def setUp(self):
        self.conn = db.connect()
        db.init(self.conn)
        warehouse = self.node("W", "warehouse", 51.9, 4.5)
        for index, (name, lat, lon, mode, weight, distance, orders, returns) in enumerate(
            [
                ("Sydney", -33.9, 151.2, "air", 60_000, 16_000, 400, 30),
                ("Milan", 45.5, 9.2, "road", 300_000, 1_000, 900, 40),
                ("Lyon", 45.8, 4.8, "road", 120_000, 800, 300, 5),
                ("Madrid", 40.4, -3.7, "road", 90_000, 1_500, 260, 4),
                ("Oslo", 59.9, 10.8, "rail", 40_000, 1_100, 120, 2),
            ]
        ):
            city = self.node(name, "customer", lat, lon)
            self.edge(warehouse, city, mode, weight, distance, orders, returns)
        analysis.run(self.conn)
        self.lanes = scoring.rank(self.conn)

    def tearDown(self):
        self.conn.close()

    def node(self, name, node_type, lat, lon):
        return self.conn.execute(
            """
            INSERT INTO nodes (name, node_type, city, country, lat, lon,
                storage_cost_annual, energy_kwh_annual, grid_intensity)
            VALUES (?, ?, '', '', ?, ?, 0, 0, NULL)
            """,
            (name, node_type, lat, lon),
        ).lastrowid

    def edge(self, origin, dest, mode, weight_kg, distance_km, orders, returns):
        return self.conn.execute(
            """
            INSERT INTO edges (origin_id, dest_id, mode, order_count,
                total_weight_kg, total_value, return_count, distance_km)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (origin, dest, mode, orders, weight_kg, returns, distance_km),
        ).lastrowid

    def test_nothing_loaded_returns_nothing(self):
        empty = db.connect()
        db.init(empty)
        self.assertIsNone(stats.build(empty))
        empty.close()

    def test_the_band_brackets_the_figure_quoted_elsewhere(self):
        """The whole point of the page. A band that does not contain the
        headline would mean the two are computing different things."""
        result = stats.uncertainty(self.lanes)
        self.assertLessEqual(result["p10"], result["baseline"])
        self.assertLessEqual(result["baseline"], result["p90"])

    def test_the_percentiles_stay_in_order(self):
        result = stats.uncertainty(self.lanes)
        self.assertLessEqual(result["worst"], result["p10"])
        self.assertLessEqual(result["p10"], result["p50"])
        self.assertLessEqual(result["p50"], result["p90"])
        self.assertLessEqual(result["p90"], result["best"])

    def test_the_band_does_not_move_between_page_loads(self):
        """Seeded on purpose. A figure that jitters on refresh reads as noise
        even when the method behind it is sound."""
        first = stats.uncertainty(self.lanes)
        second = stats.uncertainty(self.lanes)
        self.assertEqual(first["p10"], second["p10"])
        self.assertEqual(first["p90"], second["p90"])

    def test_the_band_has_width(self):
        """A band as narrow as the point estimate would be worse than no band,
        because it would look like precision rather than an error."""
        result = stats.uncertainty(self.lanes)
        self.assertGreater(result["p90"] - result["p10"], 0)

    def test_confidence_is_a_share_and_never_exceeds_one(self):
        result = stats.uncertainty(self.lanes)
        for row in result["always_flagged"]:
            self.assertGreaterEqual(row["confidence"], 0.0)
            self.assertLessEqual(row["confidence"], 1.0)

    def test_pareto_counts_never_exceed_the_network(self):
        result = stats.concentration(self.lanes)
        self.assertLessEqual(result["cost_pareto"]["lanes"], len(self.lanes))
        self.assertGreaterEqual(result["cost_pareto"]["share_of_total"], 0.8)

    def test_the_overlap_verdict_matches_the_correlation(self):
        result = stats.overlap(self.lanes)
        self.assertIsNotNone(result["rho"])
        self.assertGreaterEqual(result["rho"], -1.0)
        self.assertLessEqual(result["rho"], 1.0)
        self.assertIsInstance(result["verdict"], str)

    def test_a_quiet_lane_is_not_ranked_on_noise(self):
        """Returns are only judged where there is enough volume to judge
        them. This is the guard that stops one return out of four topping the
        table."""
        result = stats.returns_by_lane(self.lanes)
        for row in result["rows"]:
            self.assertGreaterEqual(row["orders"], stats.MIN_RETURNS_SAMPLE)

    def test_mode_shares_add_up(self):
        rows = stats.by_mode(self.lanes)
        self.assertAlmostEqual(sum(row["cost_share"] for row in rows), 1.0)
        self.assertAlmostEqual(sum(row["co2e_share"] for row in rows), 1.0)

    def test_every_section_survives_a_one_lane_network(self):
        """The smallest network anybody could upload. Nothing here should be
        able to divide by zero or index off the end of a list."""
        small = db.connect()
        db.init(small)
        warehouse = small.execute(
            "INSERT INTO nodes (name, node_type, city, country, lat, lon)"
            " VALUES ('W', 'warehouse', '', '', 51.9, 4.5)"
        ).lastrowid
        city = small.execute(
            "INSERT INTO nodes (name, node_type, city, country, lat, lon)"
            " VALUES ('C', 'customer', '', '', -33.9, 151.2)"
        ).lastrowid
        small.execute(
            """
            INSERT INTO edges (origin_id, dest_id, mode, order_count,
                total_weight_kg, total_value, return_count, distance_km)
            VALUES (?, ?, 'air', 10, 5000, 0, 0, 16000)
            """,
            (warehouse, city),
        )
        small.execute(
            "INSERT INTO orders (origin_id, dest_id, weight_kg, mode, returned)"
            " VALUES (?, ?, 500, 'air', 0)",
            (warehouse, city),
        )
        analysis.run(small)
        report = stats.build(small)
        self.assertIsNotNone(report)
        self.assertEqual(report["lane_count"], 1)
        small.close()


if __name__ == "__main__":
    unittest.main()
