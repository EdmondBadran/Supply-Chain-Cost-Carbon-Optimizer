"""What period a file covers, and what the tool is allowed to call a year.

The rules these pin are the ones a figure depends on: a full year is left
exactly as it arrived, a part year is stretched and says so, and a file too
short or too undated to measure is never stretched at all. The last one
matters most, because stretching three weeks into a year is the failure that
looks like a working answer.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from optimizer import analysis, coverage, db, ingest

HEADINGS = (
    "order_ref,order_date,origin_name,origin_city,origin_country,"
    "dest_city,dest_country,weight_kg,mode"
)


def dates_over(span_days, count=60, start=date(2026, 1, 5)):
    """`count` order dates spread evenly across `span_days`, first and last
    on the ends, so the span is exactly what the test asked for."""
    if count < 2:
        return [start.isoformat()]
    step = (span_days - 1) / (count - 1)
    return [(start + timedelta(days=round(i * step))).isoformat() for i in range(count)]


def orders_csv(tmp, order_dates, weight=100):
    """An orders file with one row per date given, all on the same two lanes
    so the scaling can be read off the routes."""
    cities = (("Paris", "FR"), ("Madrid", "ES"))
    lines = [HEADINGS]
    for index, when in enumerate(order_dates):
        city, country = cities[index % 2]
        lines.append(
            f"R{index},{when},Depot,Leeds,GB,{city},{country},{weight},road"
        )
    path = tmp / "orders.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class Measuring(unittest.TestCase):
    def test_a_full_year_is_left_alone(self):
        span = coverage.measure(dates_over(364))
        self.assertEqual(span["basis"], "full_year")
        self.assertEqual(span["factor"], 1.0)
        self.assertFalse(span["scaled"])

    def test_a_year_and_a_week_is_still_a_year(self):
        """The tolerance exists so an export that runs a few days either side
        of twelve months is not scaled by a percent and a half."""
        for days in (350, 365, 371, 400):
            with self.subTest(days=days):
                self.assertEqual(coverage.measure(dates_over(days))["factor"], 1.0)

    def test_a_quarter_is_scaled_up_to_a_year(self):
        span = coverage.measure(dates_over(91))
        self.assertEqual(span["basis"], "scaled")
        self.assertTrue(span["scaled"])
        self.assertAlmostEqual(span["factor"], 365 / 91, places=6)

    def test_two_years_are_brought_down_to_one(self):
        span = coverage.measure(dates_over(730))
        self.assertEqual(span["basis"], "scaled")
        self.assertLess(span["factor"], 1.0)

    def test_three_weeks_is_too_short_to_stretch(self):
        span = coverage.measure(dates_over(21))
        self.assertEqual(span["basis"], "too_short")
        self.assertEqual(span["factor"], 1.0)
        self.assertIn("not as a year", span["detail"])

    def test_no_dates_at_all_scales_nothing(self):
        span = coverage.measure(["", None, ""])
        self.assertEqual(span["basis"], "undated")
        self.assertEqual(span["factor"], 1.0)
        self.assertIsNone(span["period"])

    def test_dates_on_a_handful_of_rows_scale_nothing(self):
        """A span read off three rows out of a hundred describes those three
        rows, so it is reported and not acted on."""
        span = coverage.measure(dates_over(91, count=3) + [""] * 97)
        self.assertEqual(span["basis"], "sparse_dates")
        self.assertEqual(span["factor"], 1.0)

    def test_a_short_span_says_how_much_to_trust_it(self):
        self.assertEqual(coverage.measure(dates_over(300))["strength"], "high")
        self.assertEqual(coverage.measure(dates_over(120))["strength"], "moderate")
        self.assertEqual(coverage.measure(dates_over(40))["strength"], "low")

    def test_a_part_year_warns_that_it_carries_no_season(self):
        self.assertIn("season", coverage.measure(dates_over(91))["detail"])

    def test_an_empty_month_inside_the_span_is_named(self):
        days = [date(2026, 1, 5), date(2026, 1, 20), date(2026, 4, 2)]
        span = coverage.measure([day.isoformat() for day in days])
        self.assertEqual(span["missing_months"], ["Feb 2026", "Mar 2026"])

    def test_a_continuous_span_names_no_missing_month(self):
        self.assertEqual(coverage.measure(dates_over(364))["missing_months"], [])


class Scaling(unittest.TestCase):
    """The scaling has to land on the routes, because that is what every cost
    and carbon figure is computed from."""

    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_coverage_tmp"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        for child in self.tmp.iterdir():
            child.unlink()
        self.tmp.rmdir()

    def load(self, order_dates):
        conn = db.connect()
        db.init(conn)
        report = ingest.load(conn, orders_csv(self.tmp, order_dates))
        analysis.run(conn)
        return conn, report

    def test_a_quarter_of_orders_produces_about_four_times_the_cost(self):
        quarter, _ = self.load(dates_over(91, count=60))
        year, _ = self.load(dates_over(364, count=60))
        # Transport scales with weight, so it scales exactly. Total cost does
        # not, because packaging is charged per order and order counts are
        # kept whole: thirty orders times 4.011 is 120.33, and a route cannot
        # carry a third of a shipment.
        self.assertAlmostEqual(
            analysis.totals(quarter)["transport_cost"]
            / analysis.totals(year)["transport_cost"],
            365 / 91,
            places=6,
        )
        self.assertAlmostEqual(
            analysis.totals(quarter)["cost"] / analysis.totals(year)["cost"],
            365 / 91,
            places=2,
        )
        quarter.close()
        year.close()

    def test_the_orders_themselves_are_never_rewritten(self):
        """The data check reads the orders table and has to keep reporting
        what the file actually contained."""
        conn, report = self.load(dates_over(91, count=60))
        self.assertEqual(report["orders_loaded"], 60)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 60
        )
        conn.close()

    def test_route_order_counts_stay_whole(self):
        conn, _ = self.load(dates_over(91, count=60))
        for row in conn.execute("SELECT order_count, return_count FROM edges"):
            self.assertEqual(row["order_count"], int(row["order_count"]))
            self.assertEqual(row["return_count"], int(row["return_count"]))
        conn.close()

    def test_a_route_that_ran_is_never_rounded_down_to_nothing(self):
        conn = db.connect()
        db.init(conn)
        ingest.load(conn, orders_csv(self.tmp, dates_over(730, count=2)))
        for row in conn.execute("SELECT order_count FROM edges"):
            self.assertGreaterEqual(row["order_count"], 1)
        conn.close()

    def test_the_loader_reports_what_it_measured(self):
        conn, report = self.load(dates_over(91, count=60))
        self.assertEqual(report["coverage"]["basis"], "scaled")
        self.assertEqual(db.coverage(conn)["basis"], "scaled")
        conn.close()

    def test_a_file_too_short_to_stretch_is_not_stretched(self):
        short, _ = self.load(dates_over(20, count=60))
        undated, _ = self.load(["" for _ in range(60)])
        self.assertAlmostEqual(
            analysis.totals(short)["cost"], analysis.totals(undated)["cost"], places=6
        )
        short.close()
        undated.close()


class SamplesAreAYear(unittest.TestCase):
    """Both samples are a year of orders, so nothing about them moved when
    measuring arrived. If this fails, every published sample figure has."""

    def test_both_samples_measure_as_a_full_year(self):
        import app as application

        for key in application.SAMPLES:
            with self.subTest(sample=key):
                conn = db.connect()
                db.init(conn)
                report = ingest.load(conn, *application.sample_paths(key))
                self.assertEqual(report["coverage"]["basis"], "full_year")
                self.assertEqual(report["coverage"]["factor"], 1.0)
                conn.close()


if __name__ == "__main__":
    unittest.main()
