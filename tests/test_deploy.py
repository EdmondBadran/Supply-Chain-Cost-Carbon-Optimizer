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
import wsgi
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
        self.assertIs(wsgi.app, application.app)

    def test_it_binds_every_interface_by_default(self):
        """Localhost is the right default for app.py and useless on a host."""
        original = dict(os.environ)
        for key in ("HOST", "PORT", "THREADS"):
            os.environ.pop(key, None)
        try:
            options = wsgi.settings_from_environment()
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
            options = wsgi.settings_from_environment()
        finally:
            os.environ.clear()
            os.environ.update(original)
        self.assertEqual(options["port"], 8080)
        self.assertEqual(options["host"], "127.0.0.1")

    def test_it_serves_with_threads_rather_than_processes(self):
        """Every visitor's data is in this process's memory, so a second
        worker process would hand visitors an empty workspace at random. The
        thread count is the knob; there is no worker count on purpose."""
        options = wsgi.settings_from_environment()
        self.assertIn("threads", options)
        self.assertGreater(options["threads"], 1)
        self.assertNotIn("workers", options)

    def test_it_does_not_claim_the_port_off_anything(self):
        """app.py stops an older copy of itself to take port 5000, which is
        right on a laptop and would be a server restarting loop in
        production."""
        source = (ROOT / "wsgi.py").read_text(encoding="utf-8")
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
        problems = " ".join(wsgi.check_environment())
        self.assertIn("SECRET_KEY", problems)

    def test_a_deployment_without_https_is_called_out(self):
        os.environ["SECRET_KEY"] = "a" * 32
        os.environ.pop("HTTPS_ONLY", None)
        problems = " ".join(wsgi.check_environment())
        self.assertIn("HTTPS_ONLY", problems)

    def test_a_complete_environment_has_nothing_to_say(self):
        os.environ["SECRET_KEY"] = "a" * 32
        os.environ["HTTPS_ONLY"] = "1"
        self.assertEqual(wsgi.check_environment(), [])


class Packaging(unittest.TestCase):
    def test_the_server_is_a_stated_dependency(self):
        needs = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn("flask", needs)
        self.assertIn("waitress", needs)

    def test_the_bundled_data_keeps_its_attribution(self):
        """cities.csv is GeoNames under CC BY 4.0. Shipping it without the
        attribution is the one licence mistake this repo can actually make."""
        licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GeoNames", licence)
        self.assertIn("CC BY 4.0", licence)

    def test_there_is_a_licence_at_all(self):
        self.assertTrue((ROOT / "LICENSE").exists())

    def test_the_start_command_points_at_the_production_entrypoint(self):
        self.assertIn("wsgi.py", (ROOT / "Procfile").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
