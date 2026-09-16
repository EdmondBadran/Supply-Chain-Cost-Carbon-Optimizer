"""The defences that only matter once this is on a public address.

Each of these pins a decision that is invisible when it is working and
expensive when it is not: a form post that cannot prove where it came from is
refused, a visitor cannot ask for the expensive work as fast as they like, the
browser is told what the page may load, and debug is off unless somebody asked
for it.
"""

import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as application
from optimizer import security
from tests.test_routes import TINY_CSV, browser, raw_browser


class Csrf(unittest.TestCase):
    def setUp(self):
        security.reset_limits()

    def test_a_post_with_no_token_is_refused(self):
        client = raw_browser()
        client.get("/data")
        response = client.post("/sample", data={"sample": "roastery"})
        self.assertEqual(response.status_code, 400)

    def test_a_post_with_the_wrong_token_is_refused(self):
        client = raw_browser()
        client.get("/data")
        response = client.post(
            "/sample", data={"sample": "roastery", "csrf_token": "not-the-one"}
        )
        self.assertEqual(response.status_code, 400)

    def test_a_json_endpoint_with_no_token_is_refused(self):
        client = raw_browser()
        client.get("/report")
        response = client.post("/api/simulate", json={"edge_id": 1})
        self.assertEqual(response.status_code, 400)
        self.assertIn("could not be verified", response.get_json()["error"])

    def test_an_upload_with_no_token_is_refused(self):
        client = raw_browser()
        client.get("/data")
        response = client.post(
            "/upload",
            data={"orders": (io.BytesIO(TINY_CSV), "orders.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_the_same_post_with_a_token_is_accepted(self):
        response = browser().post(
            "/sample", data={"sample": "roastery"}, follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)

    def test_every_form_carries_a_token(self):
        client = browser()
        for path in ("/", "/data", "/report"):
            with self.subTest(path=path):
                body = client.get(path).get_data(as_text=True)
                forms = body.count('method="post"')
                fields = body.count('name="csrf_token"')
                self.assertGreaterEqual(fields, forms, "a form is missing its token")

    def test_the_page_publishes_the_token_for_the_json_calls(self):
        body = browser().get("/report").get_data(as_text=True)
        self.assertIn('name="csrf-token"', body)

    def test_reading_a_page_needs_no_token(self):
        client = raw_browser()
        for path in ("/", "/report", "/method", "/privacy", "/data"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)


class RateLimits(unittest.TestCase):
    def setUp(self):
        security.reset_limits()

    def tearDown(self):
        security.reset_limits()

    def test_a_visitor_inside_the_allowance_is_served(self):
        allowed, _ = security.LIMITS["upload"]
        for _ in range(allowed):
            self.assertTrue(security.rate_limit("upload", "somebody"))

    def test_one_request_past_the_allowance_is_refused(self):
        allowed, _ = security.LIMITS["upload"]
        for _ in range(allowed):
            security.rate_limit("upload", "somebody")
        self.assertFalse(security.rate_limit("upload", "somebody"))

    def test_one_visitor_does_not_spend_another_visitors_allowance(self):
        allowed, _ = security.LIMITS["upload"]
        for _ in range(allowed):
            security.rate_limit("upload", "first")
        self.assertTrue(security.rate_limit("upload", "second"))

    def test_the_buckets_are_counted_apart(self):
        allowed, _ = security.LIMITS["upload"]
        for _ in range(allowed):
            security.rate_limit("upload", "somebody")
        self.assertTrue(security.rate_limit("page", "somebody"))

    def test_a_flood_of_uploads_gets_a_page_saying_so(self):
        client = browser()
        # A first request has no session yet and is counted against the
        # address instead, so the visitor is given one before the flood.
        client.get("/data")
        allowed, _ = security.LIMITS["upload"]
        last = None
        for _ in range(allowed + 1):
            last = client.post(
                "/upload",
                data={"orders": (io.BytesIO(TINY_CSV), "orders.csv")},
                content_type="multipart/form-data",
            )
        self.assertEqual(last.status_code, 429)
        self.assertIn("Slow down", last.get_data(as_text=True))

    def test_a_flooded_json_endpoint_answers_in_json(self):
        client = browser()
        client.get("/report")
        allowed, _ = security.LIMITS["api"]
        last = None
        for _ in range(allowed + 1):
            last = client.post("/api/effort", json={"edge_id": 1, "effort": "low"})
        self.assertEqual(last.status_code, 429)
        self.assertIn("too many requests", last.get_json()["error"])


class Headers(unittest.TestCase):
    def setUp(self):
        security.reset_limits()
        self.response = browser().get("/report")

    def test_the_page_says_what_it_may_load(self):
        policy = self.response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)

    def test_the_policy_allows_exactly_what_the_pages_use(self):
        """The map is drawn with d3 and topojson off one CDN and reads a world
        outline from another. A policy that forgets either leaves a blank map
        and no error anybody sees."""
        policy = self.response.headers["Content-Security-Policy"]
        self.assertIn("https://cdnjs.cloudflare.com", policy)
        self.assertIn("https://cdn.jsdelivr.net", policy)
        self.assertIn("https://fonts.googleapis.com", policy)
        self.assertIn("https://fonts.gstatic.com", policy)

    def test_the_usual_three_are_set(self):
        self.assertEqual(self.response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Referrer-Policy", self.response.headers)
        self.assertIn("Permissions-Policy", self.response.headers)

    def test_https_is_only_demanded_when_the_site_is_on_https(self):
        self.assertNotIn("Strict-Transport-Security", self.response.headers)


class Defaults(unittest.TestCase):
    def test_debug_is_off(self):
        """It used to be on unless FLASK_DEBUG=0 said otherwise, which means
        the first production start without that variable served an interactive
        console on a stack trace."""
        self.assertFalse(application.app.config["DEBUG"])

    def test_the_session_cookie_is_not_readable_from_script(self):
        self.assertTrue(application.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(application.app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    def test_a_deployment_without_a_secret_key_refuses_to_start(self):
        import os

        from flask import Flask

        original = dict(os.environ)
        os.environ["HTTPS_ONLY"] = "1"
        os.environ.pop("SECRET_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                security.configure(Flask(__name__))
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_a_deployment_with_a_secret_key_starts_and_locks_the_cookie(self):
        import os

        from flask import Flask

        original = dict(os.environ)
        os.environ["HTTPS_ONLY"] = "1"
        os.environ["SECRET_KEY"] = "a" * 32
        try:
            configured = security.configure(Flask(__name__))
            self.assertTrue(configured.config["SESSION_COOKIE_SECURE"])
            self.assertEqual(configured.config["SECRET_KEY"], "a" * 32)
        finally:
            os.environ.clear()
            os.environ.update(original)


class AuditLog(unittest.TestCase):
    def setUp(self):
        security.reset_limits()

    def test_a_sample_load_is_recorded(self):
        with self.assertLogs("overlap.audit", level="INFO") as caught:
            browser().post("/sample", data={"sample": "roastery"})
        self.assertTrue(any("sample_loaded" in line for line in caught.output))

    def test_an_upload_records_counts_and_not_the_file(self):
        with self.assertLogs("overlap.audit", level="INFO") as caught:
            browser().post(
                "/upload",
                data={"orders": (io.BytesIO(TINY_CSV), "a-real-company.csv")},
                content_type="multipart/form-data",
            )
        written = "\n".join(caught.output)
        self.assertIn("upload_loaded", written)
        self.assertIn('"orders": 3', written)
        self.assertNotIn("a-real-company", written)
        self.assertNotIn("Leeds", written)

    def test_the_log_has_somewhere_to_go(self):
        """A logger with no handler drops everything under WARNING, and the
        other tests here would not notice: assertLogs attaches one of its own.
        Without this the audit log exists only in the tests."""
        import logging

        self.assertTrue(security.logger.handlers)
        self.assertLessEqual(security.logger.getEffectiveLevel(), logging.INFO)

    def test_a_refused_request_is_recorded(self):
        client = raw_browser()
        client.get("/data")
        with self.assertLogs("overlap.audit", level="INFO") as caught:
            client.post("/sample", data={"sample": "roastery"})
        self.assertTrue(any("csrf_rejected" in line for line in caught.output))


if __name__ == "__main__":
    unittest.main()
