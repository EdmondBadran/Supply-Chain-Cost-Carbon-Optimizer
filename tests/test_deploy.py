"""What has to be true of a deployment, checked without deploying one.

The dangerous mistakes here are quiet. A health check pointed at the front
page mints a workspace and a copy of the sample on every poll. A server that
forks gives visitors somebody else's empty workspace, because every workspace
lives in one process's memory. A deployment with no secret key signs everybody
out on each restart. None of those raise anything.

Run with: python -m unittest discover tests
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as application
import local_server
from optimizer import store
from tests.test_routes import browser


class HealthCheck(unittest.TestCase):
    """A monitor polls this every thirty seconds forever."""

    def test_it_answers(self):
        response = browser().get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertIn("ok", response.get_data(as_text=True))

    def test_it_creates_no_workspace(self):
        """The front page fills a new workspace with the sample so it never
        opens empty. A monitor pointed at it would do that on every poll and
        push real visitors out of the registry."""
        client = browser()
        before = store.stats()["live"]
        for _ in range(10):
            client.get("/healthz")
        self.assertEqual(store.stats()["live"], before)

    def test_it_sets_no_cookie(self):
        response = browser().get("/healthz")
        self.assertNotIn("Set-Cookie", response.headers)

    def test_the_front_page_does_create_one(self):
        """The contrast that makes the test above worth having."""
        before = store.stats()["live"]
        browser().get("/")
        self.assertEqual(store.stats()["live"], before + 1)


class Entrypoint(unittest.TestCase):
    def test_it_serves_the_same_app(self):
        self.assertIs(local_server.app, application.app)

    def test_it_binds_every_interface_by_default(self):
        """Localhost is the right default for app.py and useless on a host."""
        original = dict(os.environ)
        for key in ("HOST", "PORT", "THREADS"):
            os.environ.pop(key, None)
        try:
            options = local_server.settings_from_environment()
        finally:
            os.environ.clear()
            os.environ.update(original)
        self.assertEqual(options["host"], "0.0.0.0")
        self.assertEqual(options["port"], 5000)

    def test_the_platform_chooses_the_port(self):
        original = dict(os.environ)
        os.environ["PORT"] = "8080"
        os.environ["HOST"] = "127.0.0.1"
        try:
            options = local_server.settings_from_environment()
        finally:
            os.environ.clear()
            os.environ.update(original)
        self.assertEqual(options["port"], 8080)
        self.assertEqual(options["host"], "127.0.0.1")

    def test_it_serves_with_threads_rather_than_processes(self):
        """Every visitor's data is in this process's memory, so a second
        worker process would hand visitors an empty workspace at random. The
        thread count is the knob; there is no worker count on purpose."""
        options = local_server.settings_from_environment()
        self.assertIn("threads", options)
        self.assertGreater(options["threads"], 1)
        self.assertNotIn("workers", options)

    def test_it_does_not_claim_the_port_off_anything(self):
        """app.py stops an older copy of itself to take port 5000, which is
        right on a laptop and would be a server restarting loop in
        production."""
        source = (ROOT / "local_server.py").read_text(encoding="utf-8")
        self.assertNotIn("serve.claim", source)
        # "from waitress import serve" contains "import serve", so this
        # matches whole lines rather than substrings.
        for line in source.splitlines():
            stripped = line.strip()
            with self.subTest(line=stripped):
                self.assertNotEqual(stripped, "import serve")
                self.assertFalse(stripped.startswith("from serve import"))


class EnvironmentWarnings(unittest.TestCase):
    def setUp(self):
        self.original = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original)

    def test_a_missing_secret_key_is_called_out(self):
        os.environ.pop("SECRET_KEY", None)
        os.environ["HTTPS_ONLY"] = "1"
        problems = " ".join(local_server.check_environment())
        self.assertIn("SECRET_KEY", problems)

    def test_a_deployment_without_https_is_called_out(self):
        os.environ["SECRET_KEY"] = "a" * 32
        os.environ.pop("HTTPS_ONLY", None)
        problems = " ".join(local_server.check_environment())
        self.assertIn("HTTPS_ONLY", problems)

    def test_a_complete_environment_has_nothing_to_say(self):
        os.environ["SECRET_KEY"] = "a" * 32
        os.environ["HTTPS_ONLY"] = "1"
        self.assertEqual(local_server.check_environment(), [])


class StaticFiles(unittest.TestCase):
    def test_the_stylesheet_is_served_from_the_same_url_as_before(self):
        """The files moved to public/static so the CDN serves them. The URL
        must not have moved with them, or every page loses its styling."""
        response = browser().get("/static/style.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response.headers["Content-Type"])

    def test_the_scripts_are_there_too(self):
        for name in ("csrf.js", "dashboard.js", "workspace.js", "motion.js"):
            with self.subTest(name=name):
                self.assertEqual(browser().get("/static/" + name).status_code, 200)

    def test_there_is_only_one_copy_of_each(self):
        self.assertFalse((ROOT / "static").exists(), "static/ was left behind")
        self.assertTrue((ROOT / "public" / "static" / "style.css").exists())


class Packaging(unittest.TestCase):
    def test_the_deployment_installs_only_what_it_runs(self):
        """Vercel installs requirements.txt and never reads the local one, so
        waitress belongs in the local file and nothing else does."""
        deployed = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        local = (ROOT / "requirements-local.txt").read_text(encoding="utf-8").lower()
        self.assertIn("flask", deployed)
        self.assertNotIn("waitress", deployed)
        self.assertIn("waitress", local)

    def test_the_bundled_data_keeps_its_attribution(self):
        """cities.csv is GeoNames under CC BY 4.0. Shipping it without the
        attribution is the one licence mistake this repo can actually make."""
        licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GeoNames", licence)
        self.assertIn("CC BY 4.0", licence)

    def test_there_is_a_licence_at_all(self):
        self.assertTrue((ROOT / "LICENSE").exists())

    def test_the_platform_is_told_which_file_is_the_app(self):
        import json

        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertIn("app.py", config["functions"])
        # The city table is read at import time. Without it the function boots
        # and then cannot geocode anything.
        self.assertIn("data", config["functions"]["app.py"]["includeFiles"])

    def test_the_upload_limit_sits_under_the_platform_limit(self):
        """A Vercel function refuses a body over 4.5 MB before Python sees it,
        with its own error page. The app's own limit has to be the lower one or
        an oversized file leaves the product entirely."""
        import app as application

        limit_mb = application.app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024)
        self.assertLess(limit_mb, 4.5)


if __name__ == "__main__":
    unittest.main()
