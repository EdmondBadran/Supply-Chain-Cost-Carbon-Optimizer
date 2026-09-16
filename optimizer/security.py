"""What has to be true before this runs on a public address.

Four things, none of which the app had: a form post has to prove it came from
a page this site served, a visitor cannot ask for the expensive work as fast
as their machine can issue requests, the browser is told what the page is
allowed to load, and every state change leaves a line somebody can read
afterwards.

All of it is stdlib. A rate limiter that lives in memory holds for one
process, which is what this deployment is; the moment it runs behind more than
one worker the counters want a shared store, and `rate_limit` is the one
function that has to change.
"""

import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections import deque

from flask import request, session

from . import store

logger = logging.getLogger("overlap.audit")

SESSION_KEY = "_csrf"
FORM_FIELD = "csrf_token"
HEADER = "X-CSRF-Token"

# GET, HEAD, OPTIONS and TRACE change nothing, so they carry no token.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


# ------------------------------------------------------------------- CSRF


def csrf_token():
    """This visitor's token, minted on first use and held in their session."""
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def csrf_ok(req=None):
    """Whether this request proved it came from a page this site served."""
    req = req or request
    if req.method in SAFE_METHODS:
        return True
    expected = session.get(SESSION_KEY)
    if not expected:
        return False
    sent = req.headers.get(HEADER) or req.form.get(FORM_FIELD) or ""
    return hmac.compare_digest(str(sent), str(expected))


# ------------------------------------------------------------- rate limits

# Per window, per visitor. Uploads and the JSON endpoints are separated
# because they cost different amounts: an upload parses a file and rebuilds a
# whole graph, a simulate call re-prices one lane, and an export runs two
# thousand reruns before it writes anything.
LIMITS = {
    "upload": (10, 60),
    "api": (120, 60),
    "export": (20, 60),
    "page": (240, 60),
}

# A visitor who is not touching the site should not keep a queue alive.
_LIMIT_IDLE_SECONDS = 15 * 60
_MAX_TRACKED = 4096

_hits = {}
_hits_lock = threading.Lock()


def _sweep(now):
    stale = [key for key, seen in _hits.items() if not seen or now - seen[-1] > _LIMIT_IDLE_SECONDS]
    for key in stale:
        _hits.pop(key, None)
    while len(_hits) > _MAX_TRACKED:
        oldest = min(_hits, key=lambda key: _hits[key][-1] if _hits[key] else 0)
        _hits.pop(oldest, None)


def rate_limit(bucket, who):
    """True when this visitor is inside the allowance for this bucket.

    A sliding window rather than a fixed one, because a fixed window lets
    somebody spend a whole minute of allowance in the last second of one
    window and the first of the next.
    """
    allowed, window = LIMITS.get(bucket, LIMITS["page"])
    now = time.monotonic()
    key = (bucket, who)
    with _hits_lock:
        _sweep(now)
        seen = _hits.setdefault(key, deque())
        while seen and now - seen[0] > window:
            seen.popleft()
        if len(seen) >= allowed:
            return False
        seen.append(now)
        return True


def client_id():
    """Who to count against.

    The session id where there is one, so two people behind one office
    address are not each other's problem, falling back to the address itself
    for a first request that has no session yet. The proxy header is only
    trusted when the deployment says it is behind a proxy, because otherwise
    anybody can set it.
    """
    known = session.get(store.COOKIE_KEY)
    if known:
        return f"s:{known}"
    address = request.remote_addr or "unknown"
    if os.environ.get("BEHIND_PROXY") == "1":
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            address = forwarded.split(",")[0].strip()
    return f"a:{address}"


def reset_limits():
    """Drop every counter. For tests, which would otherwise inherit each
    other's requests."""
    with _hits_lock:
        _hits.clear()


# ------------------------------------------------------------------ headers

# Everything the pages actually load, and nothing else. Inline styles are
# allowed because the map and the printed report set widths and offsets as
# style attributes, which no nonce can cover; inline scripts are not, and the
# JSON the map reads is a data block rather than a script that runs.
CSP = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "img-src 'self' data:",
        "script-src 'self' https://cdnjs.cloudflare.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "connect-src 'self' https://cdn.jsdelivr.net",
    )
)

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), interest-cohort=()",
}

# Two years, because a shorter max-age is ignored by the preload lists and a
# header nobody honours is not a defence.
HSTS = "max-age=63072000; includeSubDomains"


def apply_headers(response, https_only=False):
    for name, value in HEADERS.items():
        response.headers.setdefault(name, value)
    if https_only:
        response.headers.setdefault("Strict-Transport-Security", HSTS)
    return response


# -------------------------------------------------------------------- audit


def audit(event, **fields):
    """One line per state change, as JSON, on the application log.

    Deliberately thin on what it records. This tool exists because somebody
    uploaded their shipping data to it, so the log says what happened and how
    much of it, never a city, a customer, a company name or a filename.
    """
    entry = {"event": event, "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())}
    entry.update({key: value for key, value in fields.items() if value is not None})
    logger.info(json.dumps(entry, sort_keys=True, default=str))


def start_logging():
    """Give the audit log somewhere to go.

    A logger with no handler drops everything under WARNING, so without this
    every audit line written in production disappears and the tests still pass,
    because assertLogs attaches a handler of its own. One stream handler on
    stdout, and only if the deployment has not already configured its own.
    """
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # The app's own logging config owns anything above this; an audit line is
    # a record of a state change, not part of the request log.
    logger.propagate = False
    return logger


def actor():
    """The visitor an audit line belongs to, short enough to read and not
    reversible into a session anybody could reuse."""
    return client_id()[:10]


def configure(app):
    """Everything the app has to turn on, in one call it can be read from.

    Debug is off unless somebody asks for it. It used to be on unless somebody
    asked for it off, which means the first accidental production start served
    an interactive console on a stack trace.
    """
    start_logging()
    debug = os.environ.get("FLASK_DEBUG") == "1"
    https_only = os.environ.get("HTTPS_ONLY") == "1"
    secret = os.environ.get("SECRET_KEY")

    if https_only and not secret:
        raise RuntimeError(
            "HTTPS_ONLY is set, so this is a deployment: set SECRET_KEY as well, "
            "or every restart signs out everybody who was using it."
        )
    if not secret and not debug:
        logger.warning(
            json.dumps(
                {
                    "event": "config.no_secret_key",
                    "detail": "SECRET_KEY is unset, so sessions end at restart",
                }
            )
        )

    app.config.update(
        DEBUG=debug,
        SECRET_KEY=secret or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=https_only,
        HTTPS_ONLY=https_only,
        # Caching is keyed on whether this is a deployment, not on debug.
        #
        # These two used to follow the debug flag, and turning debug off by
        # default turned them off with it: a local server then held its
        # compiled templates and served a browser an hour of stale CSS, so an
        # edit looked like it had done nothing. The page that showed it was
        # the old markup under the new stylesheet, which reads as a broken
        # layout rather than as a stale process, and that is what makes it
        # worth this many lines. Nothing about caching a template in a local
        # run is a security property, so it is not tied to the security flag.
        TEMPLATES_AUTO_RELOAD=not https_only,
        SEND_FILE_MAX_AGE_DEFAULT=3600 if https_only else 0,
    )
    return app
