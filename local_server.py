"""Optional. Runs Overlap on a machine you control, under waitress.

Vercel does not use this file and does not install waitress. The deployment
is `vercel.json`, which hands `app.py` straight to the Python runtime; see the
Deploying it section of the README. Nothing here is on the deployment path,
and nothing in the app imports it.

It is kept because the architecture still has a self-hosted shape and this is
the file that knows it. Every visitor's data lives in one process's memory,
and so do the rate limit counters, so the app is safe across threads (it
locks) and unsafe across processes (they would each hold a different set of
visitors). Waitress is one process with a pool of threads, which is exactly
that. Gunicorn with more than one worker would give visitors somebody else's
empty workspace at random, and a host that scales this app scales it by
making the workspaces shared, not by adding workers. Vercel is the same
constraint wearing different clothes, and the README says what it costs
there.

It used to be called wsgi.py, which was a hazard rather than a name: Vercel's
Flask preset looks for an `app` in app.py, index.py, server.py, main.py,
wsgi.py or asgi.py, and this file exports one. Two candidate entry points in
one repository is the kind of ambiguity that deploys the wrong thing quietly.

    pip install -r requirements-local.txt
    python local_server.py
"""

import logging
import os
import sys

from app import app

logger = logging.getLogger("overlap.audit")

# Threads, not processes. See the note above before changing this to anything
# that forks.
DEFAULT_THREADS = 8


def settings_from_environment():
    return {
        "host": os.environ.get("HOST", "0.0.0.0"),
        "port": int(os.environ.get("PORT", "5000")),
        "threads": int(os.environ.get("THREADS", DEFAULT_THREADS)),
    }


def check_environment():
    """What a deployment has to have set, and what it is losing if it has not.

    `security.configure` already refuses to start with HTTPS_ONLY and no
    SECRET_KEY. This catches the quieter mistake: a public deployment that
    never set HTTPS_ONLY at all, where the cookie is not marked secure and
    every restart signs everybody out.
    """
    problems = []
    if not os.environ.get("SECRET_KEY"):
        problems.append(
            "SECRET_KEY is not set, so every restart ends every visitor's "
            "session and loses what they uploaded."
        )
    if os.environ.get("HTTPS_ONLY") != "1":
        problems.append(
            "HTTPS_ONLY is not set, so the session cookie is not marked "
            "secure and no HSTS header is sent. Set it once TLS is in front."
        )
    return problems


def main():
    from waitress import serve

    for problem in check_environment():
        logger.warning('{"event": "config.warning", "detail": "%s"}', problem)

    options = settings_from_environment()
    logger.warning(
        '{"event": "serving", "host": "%s", "port": %d, "threads": %d}',
        options["host"],
        options["port"],
        options["threads"],
    )
    serve(app, **options)


if __name__ == "__main__":
    sys.exit(main())
