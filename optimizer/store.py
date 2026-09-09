"""One workspace per visitor, held in memory and never written to disk.

The first version of this kept a single SQLite file under instance/, which
meant every visitor shared one database. Whoever uploaded last replaced
whatever the person before them was looking at, and their file sat on the
server afterwards. Neither is acceptable once this is on a public address.

So a workspace is an in-memory SQLite database keyed by a signed cookie. It
exists for as long as the visitor keeps using it, it is dropped when they stop,
and it goes with the process on restart. There is no file to leak and nothing
to clean up afterwards.

What this does not claim: the server can read what is in memory while it is
being analysed, because it is the thing doing the analysing. Saying otherwise
would need real client-side encryption, which this project has no reason to
build and could not honestly defend.
"""

import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from flask import session

from . import db

# How long a workspace survives with nobody touching it. Long enough to read
# the whole report and come back to it, short enough that a browser left open
# overnight is not still holding somebody's order file.
IDLE_TIMEOUT_SECONDS = 2 * 60 * 60

# A ceiling so a burst of visitors cannot exhaust memory. Past this the least
# recently used workspace is dropped, which costs that visitor their upload
# and nothing else.
MAX_WORKSPACES = 64

COOKIE_KEY = "wsid"


class Workspace:
    def __init__(self):
        # check_same_thread is off because the dev server and most WSGI hosts
        # answer requests on a pool of threads. The lock below is what keeps
        # that safe: one request at a time per workspace, which is plenty for
        # a session that belongs to a single person.
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Re-entrant on purpose. A request can open its own workspace more
        # than once: the upload handler holds one while it loads the file and
        # then renders an error page, which opens the workspace again to read
        # what is currently loaded. With a plain lock that second acquire
        # waits on the first and the request never finishes.
        self.lock = threading.RLock()
        self.created = time.time()
        self.touched = self.created
        db.init(self.conn)

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


_workspaces = {}
_registry_lock = threading.Lock()


def _sweep(now):
    """Drop anything idle. Called on every acquire, which is often enough:
    a workspace nobody is using cannot be holding anything urgent."""
    stale = [
        key
        for key, ws in _workspaces.items()
        if now - ws.touched > IDLE_TIMEOUT_SECONDS
    ]
    for key in stale:
        _workspaces.pop(key).close()

    while len(_workspaces) > MAX_WORKSPACES:
        oldest = min(_workspaces, key=lambda key: _workspaces[key].touched)
        _workspaces.pop(oldest).close()


def _acquire():
    """The workspace for this visitor, creating one if they have not got one."""
    key = session.get(COOKIE_KEY)
    if not key:
        key = secrets.token_urlsafe(24)
        session[COOKIE_KEY] = key
        session.permanent = False

    now = time.time()
    with _registry_lock:
        _sweep(now)
        ws = _workspaces.get(key)
        if ws is None:
            ws = _workspaces[key] = Workspace()
        ws.touched = now
    return ws


@contextmanager
def workspace():
    """Open this visitor's database for the length of one request."""
    ws = _acquire()
    with ws.lock:
        yield ws.conn


def discard():
    """Forget this visitor's data now rather than waiting for the timeout."""
    key = session.pop(COOKIE_KEY, None)
    if not key:
        return
    with _registry_lock:
        ws = _workspaces.pop(key, None)
    if ws is not None:
        ws.close()


def stats():
    """What the process is currently holding, for the privacy page to state."""
    now = time.time()
    with _registry_lock:
        return {
            "live": len(_workspaces),
            "limit": MAX_WORKSPACES,
            "idle_timeout_minutes": IDLE_TIMEOUT_SECONDS // 60,
            "oldest_minutes": (
                int((now - min(ws.created for ws in _workspaces.values())) // 60)
                if _workspaces
                else 0
            ),
        }
