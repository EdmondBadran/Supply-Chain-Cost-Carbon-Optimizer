"""Claim the port before starting, so there is only ever one server.

Starting a second copy on Windows does not fail. Both processes bind the same
address, both show in netstat, and the older one goes on answering, so a
restart looks like it did nothing and the page does not change. Diagnosing
that from the page is close to impossible: the symptom is a broken layout,
because the old markup is being served under the new stylesheet. It cost an
evening once. See ERRORS.md, 2026-09-16.

So the port is taken rather than shared. Anything already on it that answers
as Overlap is stopped first; anything else is left alone and named, because
taking a port off somebody else's service is not this script's business.

Stdlib only, and the identification works against older builds of this app,
which is the whole point: the server in the way is by definition the one
running yesterday's code.
"""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

WINDOWS = os.name == "nt"

# How long to wait for a port to come free after the process holding it has
# been told to stop. Sockets linger for a moment after the process is gone.
RELEASE_TIMEOUT = 8.0
RELEASE_POLL = 0.25

# What the running server has to say about itself before it is stopped. Both
# strings have been in the page since long before any of this, so an old build
# still identifies itself correctly, which is the case that matters.
FINGERPRINT = ("Overlap", "Cost and carbon screener")


class PortBusy(RuntimeError):
    """Something that is not this app is on the port."""


def is_free(port, host="127.0.0.1"):
    """Whether the port can be bound right now.

    On Windows this asks for the address exclusively. Without that the bind
    succeeds against a port somebody else is already serving, which is the
    behaviour that lets two servers run at once and is the thing being
    prevented here.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if WINDOWS:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def holders(port):
    """The process ids listening on the port, as the operating system sees it."""
    if WINDOWS:
        return _holders_windows(port)
    return _holders_posix(port)


def _holders_windows(port):
    try:
        output = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_netstat(output, port)


def parse_netstat(output, port):
    """Listening process ids for one port, out of netstat -ano.

    Kept separate from running netstat so it can be tested against real
    output rather than against a live machine.
    """
    found = []
    pattern = re.compile(r"^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if match and int(match.group(2)) == port:
            pid = int(match.group(3))
            if pid and pid not in found:
                found.append(pid)
    return found


def _holders_posix(port):
    try:
        output = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(line) for line in output.split() if line.isdigit()]


def answers_as_overlap(port, host="127.0.0.1", timeout=2.0):
    """Whether whatever is on the port is a copy of this app.

    Asked over HTTP rather than by reading the process command line, because
    the command line differs by how it was started and this has to recognise
    a build from any point in the project's history.
    """
    try:
        with urllib.request.urlopen(
            f"http://{host}:{port}/", timeout=timeout
        ) as response:
            body = response.read(8192).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return all(mark in body for mark in FINGERPRINT)


def stop(pid):
    """Stop a process and everything it started.

    The tree matters: the dev server runs as a parent and a reloader child,
    and stopping the parent alone leaves the child holding the socket, which
    is the other half of how two servers end up on one port.
    """
    if WINDOWS:
        command = ["taskkill", "/PID", str(pid), "/F", "/T"]
    else:
        command = ["kill", "-9", str(pid)]
    try:
        subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def wait_until_free(port, timeout=RELEASE_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_free(port):
            return True
        time.sleep(RELEASE_POLL)
    return is_free(port)


def claim(port, log=print):
    """Make this process the only one that can serve on `port`.

    Returns what was done, so the caller can say it out loud: a server that
    silently kills another server is its own kind of confusing.
    """
    if is_free(port):
        return "free"

    pids = holders(port)
    if not answers_as_overlap(port):
        raise PortBusy(
            f"Port {port} is in use by something that is not Overlap"
            + (f" (process {', '.join(str(pid) for pid in pids)})" if pids else "")
            + ". Stop it, or start this with a different port: PORT=5001 python app.py"
        )

    if not pids:
        raise PortBusy(
            f"An older Overlap is on port {port} but the process holding it "
            "could not be identified, so it has been left alone. Close its "
            "window, or start this with PORT=5001."
        )

    log(f"An older Overlap is on port {port}. Stopping it.")
    for pid in pids:
        stop(pid)

    if not wait_until_free(port):
        raise PortBusy(
            f"Port {port} did not come free after stopping process "
            f"{', '.join(str(pid) for pid in pids)}. Try again, or start this "
            "with PORT=5001."
        )
    return "reclaimed"


def claim_or_exit(port, log=print):
    """As claim, but turns a refusal into a message and a clean exit rather
    than a traceback. Nothing here is the user's mistake to read a stack for."""
    try:
        return claim(port, log=log)
    except PortBusy as exc:
        log(str(exc))
        sys.exit(1)
