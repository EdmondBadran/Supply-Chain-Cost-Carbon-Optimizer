"""One server on the port, and never somebody else's.

The failure this prevents is not an error anybody sees. Two servers bind the
same port on Windows, the older one keeps answering, and a restart appears to
do nothing while the page renders as old markup under a new stylesheet. So
these pin the two halves: an older Overlap is stopped, and anything else is
left alone and named.

Run with: python -m unittest discover tests
"""

import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import serve

# Real netstat -ano output, which is the format the parser has to survive.
NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1268
  TCP    127.0.0.1:5000         0.0.0.0:0              LISTENING       7100
  TCP    127.0.0.1:5000         0.0.0.0:0              LISTENING       11392
  TCP    127.0.0.1:5000         127.0.0.1:52233        ESTABLISHED     7100
  TCP    127.0.0.1:50001        0.0.0.0:0              LISTENING       9999
  TCP    [::]:445               [::]:0                 LISTENING       4
"""


class Parsing(unittest.TestCase):
    def test_every_listener_on_the_port_is_found(self):
        """Both of them. Finding only the first is how one gets left behind,
        which is the whole bug."""
        self.assertEqual(serve.parse_netstat(NETSTAT, 5000), [7100, 11392])

    def test_an_established_connection_is_not_a_listener(self):
        self.assertNotIn(52233, serve.parse_netstat(NETSTAT, 52233))

    def test_a_port_that_merely_starts_the_same_is_not_matched(self):
        """5000 and 50001 share four characters and nothing else."""
        self.assertEqual(serve.parse_netstat(NETSTAT, 50001), [9999])

    def test_a_quiet_port_has_no_holders(self):
        self.assertEqual(serve.parse_netstat(NETSTAT, 6000), [])


class Binding(unittest.TestCase):
    def test_a_quiet_port_reads_as_free(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertTrue(serve.is_free(port))

    def test_a_port_in_use_reads_as_taken(self):
        """On Windows this needs an exclusive bind. A plain one succeeds
        against a port somebody else is serving, which is exactly how two
        servers end up running at once."""
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        try:
            self.assertFalse(serve.is_free(port))
        finally:
            held.close()


class Claiming(unittest.TestCase):
    def test_a_free_port_is_taken_without_stopping_anything(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        said = []
        self.assertEqual(serve.claim(port, log=said.append), "free")
        self.assertEqual(said, [], "nothing should be announced for a free port")

    def test_something_that_is_not_overlap_is_left_alone(self):
        """The important half. A port held by somebody else's service is not
        this script's to take."""
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        try:
            with self.assertRaises(serve.PortBusy) as caught:
                serve.claim(port, log=lambda _: None)
            self.assertIn("not Overlap", str(caught.exception))
            self.assertIn(str(port), str(caught.exception))
        finally:
            held.close()

    def test_the_refusal_says_what_to_do_instead(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        try:
            with self.assertRaises(serve.PortBusy) as caught:
                serve.claim(port, log=lambda _: None)
            self.assertIn("PORT=", str(caught.exception))
        finally:
            held.close()

    def test_a_refusal_exits_rather_than_raising_at_the_user(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        try:
            with self.assertRaises(SystemExit) as caught:
                serve.claim_or_exit(port, log=lambda _: None)
            self.assertEqual(caught.exception.code, 1)
        finally:
            held.close()


class Fingerprint(unittest.TestCase):
    def test_nothing_listening_does_not_answer_as_overlap(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.assertFalse(serve.answers_as_overlap(port, timeout=0.5))

    def test_the_marks_it_looks_for_are_on_the_page(self):
        """Identification has to work against a build from any point in this
        project's history, because the server in the way is by definition the
        one running yesterday's code. So it looks for the two strings that
        have been in the page markup the longest."""
        from tests.test_routes import browser

        body = browser().get("/").get_data(as_text=True)
        for mark in serve.FINGERPRINT:
            with self.subTest(mark=mark):
                self.assertIn(mark, body)


if __name__ == "__main__":
    unittest.main()
