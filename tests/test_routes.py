"""Tests that go through the app rather than around it.

The other two files build a connection and call the engine directly, which is
the right way to test arithmetic and the wrong way to catch what actually
broke when workspaces became per visitor: every page still worked in
isolation, and two of them redirected to the front door for anybody who
arrived on them first. Nothing that talks to the engine could have seen it.

So these drive the Flask test client with a cookie jar, one client standing in
for one visitor. They are slower than the rest and there are fewer of them,
and they cover the things that only exist once a request is involved: session
isolation, that no page opens empty, and that nothing reaches the disk.

Run with: python -m unittest discover tests
"""

import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as application
from optimizer import store

PAGES = ("/", "/report", "/method", "/privacy", "/data")

# The four addresses the report used to be split across. Kept as redirects so
# a link somebody has already sent still lands on the right part of it.
LEGACY = {
    "/chain": "network",
    "/diagnosis": "recommendations",
    "/dashboard": "network",
    "/stats": "data",
}

TINY_CSV = b"""origin_name,origin_city,origin_country,dest_city,dest_country,weight_kg,mode
Depot,Leeds,GB,Paris,FR,120,road
Depot,Leeds,GB,Madrid,ES,300,road
Depot,Leeds,GB,Sydney,AU,80,air
"""

# One sea route into one city: nothing to switch to, nothing to compare.
NOTHING_CSV = b"""origin_name,origin_city,origin_country,dest_city,dest_country,weight_kg,mode
Harbour Co,Rotterdam,NL,Stockholm,SE,9000,sea
Harbour Co,Rotterdam,NL,Stockholm,SE,8000,sea
"""


def _orders_shown(response):
    """The order count the data page is reporting, as an integer."""
    match = re.search(
        r"<span>Orders</span><strong>([\d,]+)</strong>",
        response.get_data(as_text=True),
    )
    return int(match.group(1).replace(",", "")) if match else None


def _databases():
    """Every SQLite file in the project, skipping the virtualenv, which has
    its own and would make this walk take longer than the rest of the suite."""
    found = set()
    for path in ROOT.rglob("*.db"):
        if ".venv" in path.parts or ".git" in path.parts:
            continue
        found.add(path.relative_to(ROOT).as_posix())
    return found


class Routes(unittest.TestCase):
    def setUp(self):
        application.app.config["TESTING"] = True
        self.client = application.app.test_client()

    def test_every_page_answers(self):
        for path in PAGES:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_no_page_opens_empty(self):
        """The regression that started this file.

        A visitor arriving straight on the map or the report has an empty
        workspace, and both used to bounce them to the chain. Any page that
        reads data has to load the sample itself.
        """
        for path in ("/report", "/"):
            with self.subTest(path=path):
                fresh = application.app.test_client()
                response = fresh.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertNotIn(b"Redirecting", response.data)

    def test_the_old_addresses_land_on_the_step_that_replaced_them(self):
        """Four pages became five steps of one. A link already sent to
        somebody has to keep working, and has to arrive at the part they were
        pointed at rather than at the top of a long page."""
        for path, step in LEGACY.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301, path)
                self.assertTrue(
                    response.headers["Location"].endswith("/report#" + step),
                    response.headers["Location"],
                )

    def test_a_lane_link_survives_the_redirect(self):
        """Opening a problem on the map is a query string plus an anchor, and
        dropping either half would land the reader on the map with nothing
        selected."""
        response = self.client.get("/dashboard?lane=3")
        location = response.headers["Location"]
        self.assertIn("lane=3", location)
        self.assertTrue(location.endswith("#network"), location)

    def test_every_section_is_on_the_page(self):
        """The results are an overview and four sections, each named in the
        section navigation, and the old numbered parts are gone."""
        body = self.client.get("/report").get_data(as_text=True)
        for section in ("overview", "recommendations", "network", "data", "export"):
            with self.subTest(section=section):
                self.assertIn('id="' + section + '"', body)
                self.assertIn('data-rail="' + section + '"', body)
        self.assertNotIn('id="step-1"', body)

    def test_the_overview_leads_with_the_decision(self):
        """The first thing on the results is how many changes were found, what
        they are worth, and one button into the top one."""
        body = self.client.get("/report").get_data(as_text=True)
        overview = body[body.index('id="overview"'):body.index('id="recommendations"')]
        self.assertIn("We found 5 changes worth reviewing", overview)
        self.assertIn("$2,297", overview)
        self.assertIn("7.0 t CO₂e", overview)
        self.assertIn("Review the top recommendation", overview)
        self.assertEqual(overview.count("btn-primary"), 1)
        for label in ("Potential annual cost saving", "Potential annual CO₂e reduction", "Recommended changes"):
            with self.subTest(label=label):
                self.assertIn(label, overview)

    def test_every_change_has_one_card_and_one_decision_view(self):
        """Each change is written up once as a card in the list and once as
        the decision view its card opens, and nowhere else."""
        body = self.client.get("/report").get_data(as_text=True)
        cards = body.count('class="rec-card')
        self.assertGreater(cards, 0)
        self.assertEqual(cards, body.count('<template id="change-detail-'))
        self.assertEqual(cards, body.count("Why this is recommended"))
        self.assertEqual(cards, body.count('data-open-change="') - 1)
        self.assertNotIn("Not simulated", body)

    def test_how_it_works_is_two_boxes_and_an_email(self):
        body = self.client.get("/method").get_data(as_text=True)
        self.assertEqual(body.count('class="hiw-box"'), 2)
        self.assertIn("mailto:" + application.CONTACT_EMAIL, body)
        self.assertIn("Overlap's creator", body)

    def test_the_data_page_offers_the_upload_before_the_samples(self):
        body = self.client.get("/data").get_data(as_text=True)
        self.assertLess(body.index('name="orders"'), body.index('name="sample"'))

    def test_the_map_still_has_everything_its_script_needs(self):
        """The map moved from its own page into a section of this one. Its
        script finds its pieces by id, so a rename during the merge would
        leave a blank rectangle and no error anybody would see."""
        body = self.client.get("/report").get_data(as_text=True)
        for element in (
            'id="network-data"',
            'id="map"',
            'id="tip"',
            'id="detail"',
            'id="wins"',
            'id="finding"',
            'id="site-toggles"',
            'id="run-network"',
            'id="network-result"',
        ):
            with self.subTest(element=element):
                self.assertIn(element, body)

    def test_a_missing_page_is_a_404_not_a_crash(self):
        self.assertEqual(self.client.get("/no-such-page").status_code, 404)

    def test_the_findings_download_as_a_csv(self):
        response = self.client.get("/findings.csv")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.mimetype.startswith("text/csv"))
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("sample", response.headers["Content-Disposition"])
        # Excel only reads a CSV as UTF-8 when it starts with a byte order mark.
        self.assertTrue(response.get_data().startswith(b"\xef\xbb\xbf"))
        rows = response.get_data(as_text=True).strip().split("\n")
        self.assertGreater(len(rows), 1)
        for column in (
            "Cost saving (USD a year)",
            "Current mode",
            "Proposed mode",
            "Current cost (USD a year)",
            "Proposed cost (USD a year)",
            "Simulations where it still paid (%)",
            "Check before acting",
        ):
            self.assertIn(column, rows[0])

    def test_the_findings_download_as_a_workbook(self):
        """The workbook is a real one: a zip with the sheets the summary
        lists, and every formula written with the value it comes to, so a
        previewer that does not calculate still shows the figure."""
        import zipfile

        response = self.client.get("/findings.xlsx")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, application.xlsx.MIMETYPE)
        self.assertIn("sample", response.headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertIsNone(archive.testzip())
            sheets = re.findall(
                r'<sheet name="([^"]+)"', archive.read("xl/workbook.xml").decode()
            )
            self.assertEqual(
                sheets, ["Summary", "Opportunities", "Routes", "Data check", "Assumptions"]
            )
            formulas = 0
            for number in range(1, len(sheets) + 1):
                xml = archive.read(f"xl/worksheets/sheet{number}.xml").decode()
                for cell in re.findall(r"<f>.*?</f>(?:<v>.*?</v>)?", xml):
                    formulas += 1
                    self.assertIn("<v>", cell)
            self.assertGreater(formulas, 0)

    def test_the_executive_summary_opens(self):
        response = self.client.get("/report/summary")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for text in ("Executive summary", "Top changes to review", "What to check before acting",
                     "How confident we are", "Important assumptions", "Data check"):
            with self.subTest(text=text):
                self.assertIn(text, body)
        self.assertNotIn("Not simulated", body)

    def test_the_report_carries_the_workspace(self):
        body = self.client.get("/report").get_data(as_text=True)
        for text in ('id="overview"', 'id="drawer"', 'id="recs-data"', 'id="export"',
                     "Data check", "Review change", "Mark for review",
                     "Export this recommendation", "Back to all changes",
                     "See calculation details", "Try a different transport mode or warehouse",
                     "Rename or reorder your supply chain stages", "Full uncertainty analysis",
                     "Recommended now", "Needs more data to confirm"):
            with self.subTest(text=text):
                self.assertIn(text, body)

    def test_the_trust_questions_are_answered(self):
        body = self.client.get("/report").get_data(as_text=True)
        for question in ("How much of your data was usable?",
                         "How reliable are the recommendations?",
                         "Which assumptions affect the estimates?",
                         "What does this analysis not include?"):
            with self.subTest(question=question):
                self.assertIn(question, body)
        self.assertIn("182 of 182", body)
        self.assertIn("these are planning estimates", body)

    def test_the_groups_follow_confidence_and_never_reorder(self):
        """Grouping is presentation only. A stress-tested change goes by its
        confidence label, one that was not goes by what its figure rests on,
        and every change keeps the rank the engine gave it."""
        from optimizer import analysis, db, ingest

        conn = db.connect()
        try:
            db.init(conn)
            ingest.load(conn, *application.sample_paths(application.DEFAULT_SAMPLE))
            analysis.run(conn)
            report = application.diagnosis.build(conn)
        finally:
            conn.close()
        before = [(p["title"], p["cost_at_stake"], p["co2e_at_stake"]) for p in report["problems"]]
        groups = application.decision_groups(report)
        self.assertEqual(before, [(p["title"], p["cost_at_stake"], p["co2e_at_stake"]) for p in report["problems"]])
        self.assertEqual(sorted(groups["by_rank"]), list(range(1, len(report["problems"]) + 1)))
        for rank, problem in enumerate(report["problems"], start=1):
            group = groups["by_rank"][rank]
            with self.subTest(rank=rank):
                if problem["confidence_label"] == "high":
                    self.assertEqual(group["key"], "now")
                    self.assertIsNone(group["basis"])
                elif problem["confidence_label"] is None:
                    self.assertNotEqual(group["key"], "now")
                    self.assertTrue(group["basis"])
        ranks = [rank for g in groups["groups"] for rank in g["ranks"]]
        self.assertEqual(sorted(ranks), list(range(1, len(report["problems"]) + 1)))

    def test_the_required_columns_guide_matches_the_loader(self):
        from optimizer import ingest

        self.assertEqual(
            {name for name, _, _ in application.REQUIRED_FIELDS}, ingest.REQUIRED_COLUMNS
        )

    def test_the_example_orders_download(self):
        response = self.client.get("/data/example-orders.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("sample", response.headers["Content-Disposition"])
        header = response.get_data(as_text=True).splitlines()[0]
        for name, _, _ in application.REQUIRED_FIELDS:
            self.assertIn(name, header)

    def test_a_change_brief_opens_and_a_missing_one_goes_back(self):
        response = self.client.get("/report/change/1")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Recommendation brief", body)
        self.assertIn("What to check before acting", body)
        self.assertIn("not a real company", body)
        self.assertEqual(self.client.get("/report/change/99").status_code, 302)

    def test_a_scenario_answers_with_both_sides(self):
        import json

        body = self.client.get("/report").get_data(as_text=True)
        recs = json.loads(
            re.search(r'<script id="recs-data" type="application/json">(.*?)</script>', body, re.S).group(1)
        )
        edge_id, rec = next(iter(recs.items()))
        response = self.client.post(
            "/api/simulate", json={"edge_id": int(edge_id), "mode": rec["route"]["proposed"]["mode"]}
        )
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertAlmostEqual(result["saved"]["cost"], rec["cost_at_stake"], places=4)
        for side in ("before", "after"):
            for key in ("cost", "co2e", "days", "route_km", "mode"):
                self.assertIn(key, result[side])


class Isolation(unittest.TestCase):
    """One visitor's file must never reach another visitor.

    This is the whole reason store.py exists, and it is not something the
    engine can be asked about: both visitors are correct on their own, and the
    bug is only visible when there are two of them.
    """

    def setUp(self):
        application.app.config["TESTING"] = True

    def upload(self, client, data=TINY_CSV):
        return client.post(
            "/upload",
            data={"orders": (io.BytesIO(data), "orders.csv")},
            content_type="multipart/form-data",
        )

    def test_an_upload_does_not_reach_another_visitor(self):
        first = application.app.test_client()
        second = application.app.test_client()
        first.get("/")
        second.get("/")

        self.upload(first)
        self.assertEqual(_orders_shown(first.get("/data")), 3)
        # The sample the second visitor loaded is untouched. If the workspaces
        # were shared this would now be 3 as well, which is exactly the bug
        # this whole arrangement exists to prevent.
        self.assertEqual(_orders_shown(second.get("/data")), 182)

    def test_clearing_forgets_this_visitor_and_nobody_else(self):
        first = application.app.test_client()
        second = application.app.test_client()
        first.get("/")
        second.get("/")
        self.upload(second)

        first.post("/clear")
        self.assertIsNone(_orders_shown(first.get("/data")))
        self.assertEqual(_orders_shown(second.get("/data")), 3)

    def test_a_bad_upload_is_reported_not_raised(self):
        client = application.app.test_client()
        response = self.upload(client, b"nothing,useful\n1,2\n")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("missing required columns", body)
        # Said as a person would say it, with each missing column explained.
        self.assertIn("Your file is missing 5 required columns", body)
        self.assertIn("<code>dest_city</code>", body)
        self.assertIn("the city it was delivered to", body)

    def test_a_missing_column_error_lists_the_file_s_own_columns(self):
        client = application.app.test_client()
        body = self.upload(client, b"nothing,useful\n1,2\n").get_data(as_text=True)
        self.assertIn("Columns in your file: <code>nothing</code>, <code>useful</code>", body)

    def test_columns_matched_on_the_page_reach_the_loader(self):
        client = application.app.test_client()
        response = client.post(
            "/upload",
            data={
                "orders": (
                    io.BytesIO(TINY_CSV.replace(b"weight_kg", b"gross")),
                    "orders.csv",
                ),
                "column_weight_kg": "gross",
            },
            content_type="multipart/form-data",
        )
        body = response.get_data(as_text=True)
        self.assertIn("Your analysis is ready", body)
        self.assertIn("gross as <code>weight_kg</code>", body)

    def test_a_company_name_given_with_the_file_is_what_the_report_says(self):
        """Without one an upload is described rather than named, which is what
        a printed copy used to carry instead of whose report it is."""
        client = application.app.test_client()
        client.post(
            "/upload",
            data={
                "orders": (io.BytesIO(TINY_CSV), "orders.csv"),
                "company": "  Nordic Supply AB  ",
            },
            content_type="multipart/form-data",
        )
        for path in ("/report", "/report/summary"):
            with self.subTest(path=path):
                self.assertIn("Nordic Supply AB", client.get(path).get_data(as_text=True))

        plain = application.app.test_client()
        self.upload(plain)
        self.assertIn("Your own order data", plain.get("/report/summary").get_data(as_text=True))

    def test_the_upload_page_carries_the_column_guide(self):
        body = self.upload(application.app.test_client(), b"x\n").get_data(as_text=True)
        self.assertIn("data-matcher", body)
        self.assertIn("ship_from_city", body)

    def test_an_upload_with_bad_rows_says_what_was_excluded(self):
        client = application.app.test_client()
        response = self.upload(
            client,
            TINY_CSV + b"Depot,Leeds,GB,Atlantis Qqq,XX,50,road\n",
        )
        body = response.get_data(as_text=True)
        self.assertIn("Your analysis is ready", body)
        self.assertIn("1 order could not be used", body)
        self.assertIn("dest_city", body)
        report = client.get("/report").get_data(as_text=True)
        self.assertIn("1 order could not be used", report)

    def test_sample_figures_say_they_are_not_real(self):
        """Anywhere the sample's figures appear, the page says the company is
        invented. Once a real file is loaded, it stops saying so."""
        client = application.app.test_client()
        for path in ("/", "/report", "/report/summary"):
            with self.subTest(path=path):
                self.assertIn("not a real company", client.get(path).get_data(as_text=True))
        self.upload(client)
        for path in ("/", "/report", "/report/summary"):
            with self.subTest(path=path, loaded="upload"):
                self.assertNotIn("not a real company", client.get(path).get_data(as_text=True))

    def test_a_network_with_nothing_to_change_leaves_that_part_out(self):
        """With nothing to change, the report says so, and the part, the CSV
        and the workbook sheet that would have been empty are left out."""
        import zipfile

        client = application.app.test_client()
        self.upload(client, NOTHING_CSV)
        body = client.get("/report").get_data(as_text=True)
        self.assertIn("We found no changes that cut both cost and carbon", body)
        self.assertNotIn('id="recommendations"', body)
        self.assertIn('id="network"', body)
        self.assertNotIn('href="/findings.csv"', body)
        self.assertNotIn("1 routes", body)
        self.assertNotIn("data-open-change", body)

        book = client.get("/findings.xlsx")
        self.assertEqual(book.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(book.data)) as archive:
            sheets = re.findall(
                r'<sheet name="([^"]+)"', archive.read("xl/workbook.xml").decode()
            )
        self.assertEqual(sheets, ["Summary", "Routes", "Data check", "Assumptions"])

    def test_stages_without_data_are_not_drawn(self):
        """An orders file on its own has no suppliers, no inbound freight and
        here no returns, so the chain picture does not draw them as zeros."""
        client = application.app.test_client()
        self.upload(client)
        body = client.get("/report").get_data(as_text=True)
        for missing in ("suppliers", "inbound", "returns"):
            with self.subTest(stage=missing):
                self.assertNotIn(f'data-stage="{missing}"', body)
        self.assertIn('data-stage="outbound"', body)

    def test_a_non_csv_is_refused(self):
        client = application.app.test_client()
        response = client.post(
            "/upload",
            data={"orders": (io.BytesIO(b"x"), "orders.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertIn(b"not a CSV", response.data)

    def test_nothing_is_written_to_disk(self):
        """The claim the privacy page makes, checked rather than trusted."""
        before = _databases()
        client = application.app.test_client()
        client.get("/")
        self.upload(client)
        client.get("/stats")
        self.assertEqual(before, _databases())


class Samples(unittest.TestCase):
    def setUp(self):
        application.app.config["TESTING"] = True
        self.client = application.app.test_client()

    def test_both_samples_load(self):
        for key in application.SAMPLES:
            with self.subTest(sample=key):
                response = self.client.post(
                    "/sample", data={"sample": key}, follow_redirects=True
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("changes worth reviewing", response.get_data(as_text=True))

    def test_an_unknown_sample_is_refused(self):
        response = self.client.post("/sample", data={"sample": "nope"})
        self.assertIn(b"no sample by that name", response.data)

    def test_the_default_sample_is_one_of_the_two(self):
        self.assertIn(application.DEFAULT_SAMPLE, application.SAMPLES)

    def test_every_sample_file_is_actually_there(self):
        """A sample listed but not shipped would only fail when clicked."""
        for key in application.SAMPLES:
            for path in application.sample_paths(key):
                with self.subTest(path=path.name):
                    self.assertTrue(path.exists(), path)


class Workspaces(unittest.TestCase):
    def test_the_registry_has_a_ceiling(self):
        self.assertGreater(store.MAX_WORKSPACES, 0)
        self.assertGreater(store.IDLE_TIMEOUT_SECONDS, 0)

    def test_stats_describe_the_registry(self):
        report = store.stats()
        for key in ("live", "limit", "idle_timeout_minutes", "oldest_minutes"):
            self.assertIn(key, report)
        self.assertLessEqual(report["live"], report["limit"])


if __name__ == "__main__":
    unittest.main()
