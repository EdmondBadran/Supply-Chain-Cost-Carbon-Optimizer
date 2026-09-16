"""What has to be true before this deploys to Vercel, checked without deploying.

Every failure this file is looking for is quiet. A second file exporting a
Flask `app` deploys the wrong one. A data file the function did not bundle
imports fine and then cannot geocode a single city. An upload limit above the
platform's own limit sends oversized files to a Vercel error page instead of
ours. A stylesheet that moved out from under its URL renders an unstyled page
that still returns 200. None of them raise anything locally.

The rules encoded here come from Vercel's Flask and Functions documentation:
the Flask preset resolves an `app` from one of a fixed list of entrypoints,
a function body is capped at 4.5 MB, and maxDuration is bounded by the plan.

Run with: python -m unittest discover tests
"""

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as application
from optimizer import geo
from tests.test_routes import browser


def config():
    return json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))


class Entrypoint(unittest.TestCase):
    """Vercel's Flask preset picks the app; this is about it picking ours."""

    # app.py first, and every other name the preset will answer to.
    CANDIDATES = ("app.py", "index.py", "server.py", "main.py", "wsgi.py", "asgi.py")

    def test_the_app_is_where_the_configuration_says(self):
        self.assertIn("app.py", config()["functions"])
        self.assertTrue((ROOT / "app.py").exists())

    def test_the_entrypoint_exports_a_wsgi_app(self):
        """The preset looks for a Flask instance named app. Not a factory,
        not a handler, and not under another name."""
        self.assertTrue(callable(application.app.wsgi_app))
        self.assertEqual(application.app.name, "app")

    def test_no_second_file_answers_to_the_same_preset(self):
        """The hazard that renamed wsgi.py.

        The preset resolves the first of its candidate names that exports an
        `app`, so a repository holding two of them deploys whichever the
        resolution order happens to reach. One candidate, and there is nothing
        to resolve.
        """
        found = [name for name in self.CANDIDATES if (ROOT / name).exists()]
        self.assertEqual(found, ["app.py"], f"more than one entrypoint: {found}")

        for parent in ("src", "app"):
            for name in self.CANDIDATES:
                path = ROOT / parent / name
                with self.subTest(path=f"{parent}/{name}"):
                    self.assertFalse(path.exists(), f"{path} shadows app.py")

    def test_the_framework_is_pinned_rather_than_detected(self):
        """So importing the repository into a fresh Vercel project does not
        depend on somebody picking the right preset off a dropdown."""
        self.assertEqual(config().get("framework"), "flask")


class FunctionLimits(unittest.TestCase):
    def test_the_duration_is_set_and_within_the_plan(self):
        """1 to 300 seconds is what a Hobby plan allows with fluid compute.
        Unset means 300, which is a long time to bill for a hung export."""
        duration = config()["functions"]["app.py"]["maxDuration"]
        self.assertIsInstance(duration, int)
        self.assertGreaterEqual(duration, 1)
        self.assertLessEqual(duration, 300)

    def test_the_upload_limit_leaves_room_under_the_body_limit(self):
        """4.5 MB is the platform's cap on a request body, and it is enforced
        before Python is reached. Ours has to be the limit that bites."""
        limit = application.app.config["MAX_CONTENT_LENGTH"]
        self.assertLess(limit, 4.5 * 1024 * 1024)
        self.assertEqual(limit, application.MAX_UPLOAD_MB * 1024 * 1024)

    def test_the_page_quotes_the_configured_limit(self):
        """The number in the copy comes from the config, so the two cannot
        drift apart the way 32 MB did."""
        page = browser().get("/data").get_data(as_text=True)
        self.assertIn(f"up to {application.MAX_UPLOAD_MB} MB", page)


class BundledData(unittest.TestCase):
    """cities.csv and both samples have to travel with the function."""

    def test_the_configuration_includes_the_data_directory(self):
        included = config()["functions"]["app.py"]["includeFiles"]
        self.assertIn("data", included)

    def test_the_files_the_pattern_has_to_match_are_there(self):
        self.assertTrue(geo.CITIES_PATH.exists())
        for key in application.SAMPLES:
            for path in application.sample_paths(key):
                with self.subTest(path=path.name):
                    self.assertTrue(path.exists(), f"{path} is missing")

    def test_the_data_directory_is_not_ignored(self):
        ignored = (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
        rules = [line.strip() for line in ignored if line.strip() and not line.startswith("#")]
        for rule in rules:
            with self.subTest(rule=rule):
                self.assertFalse(
                    rule.rstrip("/") == "data" or rule.startswith("data/"),
                    f".vercelignore drops the bundled data: {rule}",
                )

    def test_the_city_table_is_found_without_relying_on_the_working_directory(self):
        """A function does not run from the project root. Anything resolving a
        bundled file against the cwd works locally and fails in the cloud, so
        this looks it up from somewhere else entirely."""
        geo._tables.cache_clear()
        here = os.getcwd()
        try:
            os.chdir(ROOT.parent)
            self.assertEqual(len(geo.locate("Bristol", "GB")), 2)
        finally:
            os.chdir(here)
            geo._tables.cache_clear()


class StaticAssets(unittest.TestCase):
    """Vercel serves public/** off the CDN and the function never sees it."""

    def test_the_browser_assets_live_under_public(self):
        served = ROOT / "public" / "static"
        self.assertTrue(served.is_dir())
        self.assertTrue((served / "style.css").exists())

    def test_flask_points_at_the_same_directory(self):
        """So a local run serves the same bytes from the same URL, rather than
        a second copy that drifts."""
        self.assertEqual(
            Path(application.app.static_folder).resolve(),
            (ROOT / "public" / "static").resolve(),
        )
        self.assertEqual(application.app.static_url_path, "/static")

    def test_every_asset_a_template_asks_for_exists(self):
        """A missing script 404s quietly and takes a feature with it."""
        wanted = set()
        for template in (ROOT / "templates").rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            for chunk in text.split("url_for('static', filename='")[1:]:
                wanted.add(chunk.split("'")[0])
        self.assertTrue(wanted, "no static references found to check")
        for name in sorted(wanted):
            with self.subTest(name=name):
                self.assertTrue((ROOT / "public" / "static" / name).exists())


class NothingLeftFromTheOldShape(unittest.TestCase):
    """This deploys to Vercel only. The always-running server files either
    went or are marked as the local convenience they are."""

    def test_no_procfile(self):
        self.assertFalse((ROOT / "Procfile").exists())

    def test_no_always_running_platform_configuration(self):
        for name in ("railway.json", "railway.toml", "render.yaml", "fly.toml"):
            with self.subTest(name=name):
                self.assertFalse((ROOT / name).exists())

    def test_the_local_server_says_it_is_not_the_deployment(self):
        text = (ROOT / "local_server.py").read_text(encoding="utf-8").lower()
        self.assertIn("vercel does not use this file", text)

    def test_nothing_the_app_runs_imports_the_local_server(self):
        for path in [ROOT / "app.py", *(ROOT / "optimizer").glob("*.py")]:
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("import local_server", source)
                self.assertNotIn("import waitress", source)


class Secrets(unittest.TestCase):
    def test_no_environment_file_is_committed(self):
        for name in (".env", ".env.local", ".env.production"):
            with self.subTest(name=name):
                self.assertFalse((ROOT / name).exists())

    def test_the_ignore_list_covers_what_holds_secrets(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".env", ignored)
        self.assertIn(".vercel", ignored)

    def test_no_secret_key_is_written_into_the_source(self):
        """It is read from the environment, and a deployment without one is
        refused in optimizer/security.py rather than given a default."""
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("SECRET_KEY=", source)
        self.assertNotIn('SECRET_KEY"] = "', source)


class LaunchPages(unittest.TestCase):
    """The pages a public launch needs, and the credits they have to carry."""

    def setUp(self):
        self.client = browser()

    def test_the_footer_links_to_both_on_every_page(self):
        for path in ("/", "/report", "/data", "/method", "/privacy"):
            page = self.client.get(path).get_data(as_text=True)
            with self.subTest(path=path):
                self.assertIn("/licences", page)
                self.assertIn("/terms", page)

    def test_the_licences_page_credits_geonames(self):
        page = self.client.get("/licences").get_data(as_text=True)
        self.assertIn("GeoNames", page)
        self.assertIn("https://creativecommons.org/licenses/by/4.0/", page)
        self.assertIn("reduced and modified", page)

    def test_the_terms_say_what_the_figures_are_not(self):
        page = self.client.get("/terms").get_data(as_text=True).lower()
        for claim in ("carrier quote", "financial", "guaranteed saving", "operational"):
            with self.subTest(claim=claim):
                self.assertIn(claim, page)

    def test_the_legal_text_is_marked_as_a_draft(self):
        for path in ("/terms", "/privacy"):
            page = self.client.get(path).get_data(as_text=True).lower()
            with self.subTest(path=path):
                self.assertIn("draft", page)
                self.assertIn("legal review", page)

    def test_no_page_claims_gdpr_compliance(self):
        """Saying it without having done the work is the one sentence here
        that could actually cost somebody something."""
        page = self.client.get("/privacy").get_data(as_text=True).lower()
        self.assertIn("not a gdpr compliance statement", page)
        # Positive claims only. The page denies several of these in so many
        # words, and a banned substring like "certified by" would match the
        # denial as readily as the boast.
        for claim in (
            "gdpr compliant",
            "gdpr-compliant",
            "fully compliant",
            "iso 27001",
            "we are certified",
        ):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, page)

    def test_the_volatility_is_stated_where_somebody_uploads(self):
        page = self.client.get("/data").get_data(as_text=True).lower()
        self.assertIn("serverless", page)
        self.assertIn("memory", page)


if __name__ == "__main__":
    unittest.main()
