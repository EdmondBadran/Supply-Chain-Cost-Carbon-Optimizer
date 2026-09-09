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
    "/chain": "step-1",
    "/diagnosis": "step-2",
    "/dashboard": "step-3",
    "/stats": "step-5",
}

TINY_CSV = b"""origin_name,origin_city,origin_country,dest_city,dest_country,weight_kg,mode
Depot,Leeds,GB,Paris,FR,120,road
Depot,Leeds,GB,Madrid,ES,300,road
Depot,Leeds,GB,Sydney,AU,80,air
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
        self.assertTrue(location.endswith("#step-3"), location)

    def test_every_step_is_on_the_page(self):
        body = self.client.get("/report").get_data(as_text=True)
        for step in ("step-1", "step-2", "step-3", "step-4", "step-5"):
            with self.subTest(step=step):
                self.assertIn('id="' + step + '"', body)

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
        rows = response.get_data(as_text=True).strip().split("\n")
        self.assertGreater(len(rows), 1)
        self.assertIn("cost at stake usd per year", rows[0])


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
        self.assertIn(b"missing required columns", response.data)

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
                self.assertIn(b"start to finish", response.data)

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
