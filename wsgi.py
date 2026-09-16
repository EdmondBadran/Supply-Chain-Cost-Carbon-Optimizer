"""The entry point a deployment runs. `python wsgi.py`, and nothing else.

Not `app.py`. That one runs the Werkzeug development server, which says on
every start that it is not for production and means it, and it claims its port
by stopping whatever else is on it, which is right on a laptop and completely
wrong on a server.

Waitress rather than gunicorn, and the reason is the architecture rather than
taste. Every visitor's data lives in this process's memory, and so do the rate
limit counters, so the app is safe across threads (it locks) and unsafe across
processes (they would each hold a different set of visitors). Waitress is one
process with a pool of threads, which is exactly that shape. Gunicorn with
more than one worker would give visitors somebody else's empty workspace at
random, and a deployment that scales this app scales it by making the
workspaces shared, not by adding workers.
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
