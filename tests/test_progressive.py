"""The rule the whole design rests on: an advanced feature you ignore costs
you nothing.

Two halves. The first pins the default experience, which is what a first-time
visitor gets: three actions, each with what it saves, how sure to be, where it
stands and one button, and no input to fill in before any of it appears. The
second pins what the optional inputs do when somebody does use them, and that
clearing them puts the original answer back exactly.

Run with: python -m unittest discover tests
"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as application
from optimizer import actions, analysis, db, diagnosis, ingest, settings, status
from tests.test_routes import browser


def loaded(sample=None):
    conn = db.connect()
    db.init(conn)
    ingest.load(conn, *application.sample_paths(sample or application.DEFAULT_SAMPLE))
    analysis.run(conn)
    return conn


class DefaultExperience(unittest.TestCase):
    """Nothing here may need a setting, a file beyond the orders, or a click
    into anything advanced."""

    def setUp(self):
        self.client = browser()
        self.body = self.client.get("/report").get_data(as_text=True)
        self.overview = self.body[
            self.body.index('id="overview"') : self.body.index('id="recommendations"')
        ]

    def test_the_first_screen_is_three_actions(self):
        self.assertEqual(self.overview.count('class="top-action"'), 3)

    def test_each_action_carries_the_four_things_a_decision_needs(self):
        """Cost, carbon, confidence and where it stands. A saving on its own
        is a number; these four are a decision."""
        for card in re.findall(r'<li class="top-action".*?</li>', self.overview, re.S):
            with self.subTest(card=card[:60]):
                self.assertIn("Cost saving", card)
                self.assertIn("CO₂e reduction", card)
                self.assertIn('class="status status-', card)
                self.assertTrue(
                    "confidence" in card or "Not stress-tested" in card,
                    "an action with no confidence answer",
                )
                self.assertIn("Review this change", card)

    def test_the_first_screen_asks_for_nothing(self):
        for unwanted in ("<input", "<select", "<textarea", "<fieldset"):
            with self.subTest(unwanted=unwanted):
                self.assertNotIn(unwanted, self.overview)

    def test_the_results_are_there_before_any_setting_is_touched(self):
        """A cold visitor, no upload, no configuration: a full answer."""
        fresh = browser()
        body = fresh.get("/report").get_data(as_text=True)
        self.assertIn("We found", body)
        self.assertIn("top-action", body)

    def test_nothing_advanced_is_reachable_only_as_a_dead_end(self):
        self.assertIn("/improve", self.body)
        self.assertIn("/actions", self.body)

    def test_the_advanced_work_sits_below_the_changes(self):
        """Assumptions and the network are not the first thing anybody meets."""
        self.assertLess(self.body.index('id="recommendations"'), self.body.index('id="advanced"'))
        self.assertLess(self.body.index('id="advanced"'), self.body.index('id="data"'))

    def test_every_status_shown_is_one_of_the_five(self):
        shown = set(re.findall(r'class="status status-(\w+)"', self.body))
        self.assertTrue(shown)
        self.assertTrue(shown <= set(status.ORDER), shown)

    def test_the_words_are_the_five_agreed_ones(self):
        for key, label in status.LABELS.items():
            with self.subTest(key=key):
                self.assertIn(label, application.status.LABELS.values())
        self.assertEqual(
            sorted(status.LABELS.values()),
            sorted(
                ["Ready to act", "Needs validation", "Blocked", "In progress", "Complete"]
            ),
        )


class OptionalInputs(unittest.TestCase):
    def setUp(self):
        self.conn = loaded()

    def tearDown(self):
        self.conn.close()

    def test_an_untouched_workspace_uses_the_published_factors(self):
        self.assertEqual(settings.rates(self.conn), dict(application.factors.COST_FACTORS))
        self.assertFalse(settings.overridden(self.conn))
        self.assertIsNone(settings.summary(self.conn))

    def test_a_real_rate_changes_what_the_changes_are_worth(self):
        before = diagnosis.build(self.conn)["overview"]["recoverable_cost"]
        settings.save(self.conn, {"rate_air": "0.06"})
        analysis.run(self.conn)
        after = diagnosis.build(self.conn)["overview"]["recoverable_cost"]
        self.assertNotAlmostEqual(before, after, places=2)

    def test_clearing_the_rates_puts_the_first_answer_back_exactly(self):
        """The important half of the promise: an advanced input is reversible,
        so trying one costs nothing either."""
        before = diagnosis.build(self.conn)
        settings.save(self.conn, {"rate_air": "0.06", "rate_road": "0.4"})
        analysis.run(self.conn)
        settings.clear(self.conn)
        analysis.run(self.conn)
        after = diagnosis.build(self.conn)
        self.assertEqual(
            [(p["title"], round(p["cost_at_stake"], 6)) for p in before["problems"]],
            [(p["title"], round(p["cost_at_stake"], 6)) for p in after["problems"]],
        )

    def test_a_rate_that_cannot_be_a_rate_is_refused(self):
        for bad in ("per kg", "-2", "9000"):
            with self.subTest(bad=bad):
                with self.assertRaises(settings.SettingError):
                    settings.save(self.conn, {"rate_air": bad})

    def test_a_rate_in_dollars_per_kg_is_caught_as_a_unit_mistake(self):
        """Somebody entering 3.50 a kg rather than per tonne-km is the most
        likely way to get a wrong answer that looks right."""
        with self.assertRaises(settings.SettingError) as caught:
            settings.save(self.conn, {"rate_air": "3500"})
        self.assertIn("per kg", str(caught.exception))

    def test_the_audit_still_prints_the_rate_it_priced_at(self):
        settings.save(self.conn, {"rate_air": "0.28"})
        analysis.run(self.conn)
        report = diagnosis.build(self.conn)
        air = [
            p["route"]["now"]
            for p in report["problems"]
            if p["route"] and p["route"]["now"]["mode"] == "air"
        ]
        self.assertTrue(air)
        self.assertAlmostEqual(air[0]["cost_factor"], 0.28, places=6)


class ServiceLimit(unittest.TestCase):
    def setUp(self):
        self.conn = loaded()
        self.report = diagnosis.build(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_no_limit_blocks_nothing(self):
        grouped = status.group(self.report["problems"], settings.current(self.conn), {})
        self.assertEqual(grouped["counts"].get("blocked", 0), 0)

    def test_a_limit_blocks_what_breaks_it_and_says_why(self):
        settings.save(self.conn, {"max_extra_days": "10"})
        grouped = status.group(self.report["problems"], settings.current(self.conn), {})
        self.assertEqual(grouped["counts"].get("blocked"), 1)
        blocked = [g for g in grouped["by_rank"].values() if g["key"] == "blocked"][0]
        self.assertIn("past the 10 days you allow", blocked["reason"])

    def test_a_blocked_change_is_not_one_of_the_three_to_start_with(self):
        settings.save(self.conn, {"max_extra_days": "10"})
        grouped = status.group(self.report["problems"], settings.current(self.conn), {})
        blocked = {r for r, g in grouped["by_rank"].items() if g["key"] == "blocked"}
        self.assertFalse(blocked & set(grouped["top_ranks"]))

    def test_raising_the_limit_gives_it_back(self):
        settings.save(self.conn, {"max_extra_days": "10"})
        settings.save(self.conn, {"max_extra_days": "60"})
        grouped = status.group(self.report["problems"], settings.current(self.conn), {})
        self.assertEqual(grouped["counts"].get("blocked", 0), 0)

    def test_stock_in_transit_is_only_counted_when_somebody_gives_a_rate(self):
        route = {"extra_days": 30, "value": 100000}
        self.assertIsNone(status.carrying_cost(route, None))
        self.assertAlmostEqual(
            status.carrying_cost(route, 0.1), 100000 * (30 / 365) * 0.1, places=6
        )


class Tracking(unittest.TestCase):
    def setUp(self):
        self.conn = loaded()
        self.report = diagnosis.build(self.conn)
        self.edge = self.report["problems"][0]["edge_id"]

    def tearDown(self):
        self.conn.close()

    def grouped(self):
        return status.group(
            self.report["problems"],
            settings.current(self.conn),
            actions.all_by_edge(self.conn),
        )

    def test_an_owner_moves_a_change_to_in_progress(self):
        actions.save(self.conn, self.edge, owner="Priya", state="doing")
        self.assertEqual(self.grouped()["by_rank"][1]["label"], "In progress")

    def test_a_change_being_done_drops_out_of_the_three(self):
        self.assertIn(1, self.grouped()["top_ranks"])
        actions.save(self.conn, self.edge, state="doing")
        self.assertNotIn(1, self.grouped()["top_ranks"])

    def test_complete_wins_over_everything_the_engine_says(self):
        actions.save(self.conn, self.edge, state="done")
        self.assertEqual(self.grouped()["by_rank"][1]["label"], "Complete")

    def test_clearing_every_field_removes_the_row_rather_than_keeping_a_blank(self):
        actions.save(self.conn, self.edge, owner="Priya", state="doing")
        actions.save(self.conn, self.edge, owner="", state="", due="", note="")
        self.assertEqual(actions.all_by_edge(self.conn), {})
        self.assertEqual(self.grouped()["by_rank"][1]["label"], "Ready to act")

    def test_setting_a_status_does_not_wipe_an_owner(self):
        actions.save(self.conn, self.edge, owner="Priya")
        actions.save(self.conn, self.edge, state="doing")
        self.assertEqual(actions.all_by_edge(self.conn)[self.edge]["owner"], "Priya")

    def test_a_date_that_is_not_a_date_is_refused(self):
        with self.assertRaises(actions.ActionError):
            actions.save(self.conn, self.edge, due="next tuesday")

    def test_the_board_and_the_results_never_disagree(self):
        actions.save(self.conn, self.edge, state="doing")
        board = actions.board(
            self.report["problems"], settings.current(self.conn), actions.all_by_edge(self.conn)
        )
        by_rank = self.grouped()["by_rank"]
        for row in board["rows"]:
            with self.subTest(rank=row["rank"]):
                self.assertEqual(row["status"]["label"], by_rank[row["rank"]]["label"])

    def test_the_tracker_can_be_taken_away_as_a_file(self):
        actions.save(self.conn, self.edge, owner="Priya", state="doing", note="quoting")
        board = actions.board(
            self.report["problems"], settings.current(self.conn), actions.all_by_edge(self.conn)
        )
        written = actions.to_csv(board, "Bristol coffee roastery", "2026-09-16")
        self.assertIn("Priya", written)
        self.assertIn("In progress", written)
        self.assertIn("quoting", written)


class Pages(unittest.TestCase):
    def test_both_optional_pages_open_and_offer_a_way_back(self):
        client = browser()
        for path in ("/improve", "/actions"):
            with self.subTest(path=path):
                body = client.get(path).get_data(as_text=True)
                self.assertEqual(client.get(path).status_code, 200)
                self.assertIn("Back to results", body)

    def test_every_field_on_the_improve_page_is_pre_filled_or_optional(self):
        """Nothing here may be required, because the page is optional."""
        body = browser().get("/improve").get_data(as_text=True)
        self.assertNotIn("required", body)
        self.assertIn('placeholder="0.19"', body)
        self.assertIn('placeholder="no limit"', body)

    def test_a_rate_box_accepts_a_real_rate(self):
        """A fixed step is measured from min, so step="0.001" with
        min="0.0001" makes 0.28 invalid and the browser refuses to submit
        without saying anything. It cost an afternoon once."""
        body = browser().get("/improve").get_data(as_text=True)
        for field in re.findall(r'<input id="rate-\w+"[^>]*>', body):
            with self.subTest(field=field[:40]):
                self.assertIn('step="any"', field)

    def test_saving_a_rate_reprices_the_results(self):
        client = browser()
        before = client.get("/report").get_data(as_text=True)
        client.post("/improve", data={"rate_air": "0.4"})
        after = client.get("/report").get_data(as_text=True)
        self.assertNotEqual(
            re.search(r"could save an estimated.*?</p>", before, re.S).group(0),
            re.search(r"could save an estimated.*?</p>", after, re.S).group(0),
        )
        self.assertIn("Using your own air rates", after)

    def test_putting_the_defaults_back_restores_the_first_answer(self):
        client = browser()
        before = client.get("/report").get_data(as_text=True)
        client.post("/improve", data={"rate_air": "0.4"})
        client.post("/improve/reset")
        self.assertEqual(before, client.get("/report").get_data(as_text=True))

    def test_a_bad_rate_comes_back_as_a_sentence_not_a_stack_trace(self):
        client = browser()
        client.post("/improve", data={"rate_air": "elephants"}, follow_redirects=True)
        body = client.get("/improve?error=x").get_data(as_text=True)
        self.assertIn("could not be saved", body)

    def test_the_tracker_says_it_will_not_outlive_the_session(self):
        body = browser().get("/actions").get_data(as_text=True)
        self.assertIn("dropped after", body)
        self.assertIn("Download the CSV to keep it", body)

    def test_the_tracker_downloads(self):
        response = browser().get("/actions.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("Owner", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
