"""Who is doing what about each change.

This is the only part of the tool that is about people rather than freight,
and it lives on its own page for that reason. The results page answers what to
do; this answers whether anybody is doing it. Mixing the two puts a column of
empty owner fields in front of somebody who came to read three numbers.

It is kept in the same in-memory workspace as everything else, so it lasts as
long as the session and no longer, and it can be exported as a CSV that
outlives it. That is a deliberate limit rather than an oversight: the privacy
page promises nothing is written to disk, and a tracker is not worth breaking
that promise for.
"""

import csv
import io
from datetime import date

from . import status

STATES = {
    "": "Not started",
    status.DOING: "In progress",
    status.DONE: "Complete",
}

MAX_OWNER = 60
MAX_NOTE = 400


class ActionError(ValueError):
    pass


def init(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS actions (
            edge_id INTEGER PRIMARY KEY,
            owner TEXT NOT NULL DEFAULT '',
            due TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def all_by_edge(conn):
    """Every tracking row, keyed by the route it belongs to."""
    init(conn)
    return {
        row["edge_id"]: dict(row)
        for row in conn.execute("SELECT * FROM actions")
    }


def save(conn, edge_id, owner=None, due=None, state=None, note=None):
    """Record what somebody said about one change.

    Only the fields given are changed, so setting a status from the results
    page does not wipe an owner set on the tracking page.
    """
    init(conn)
    try:
        edge_id = int(edge_id)
    except (TypeError, ValueError):
        raise ActionError("that is not a route") from None

    if state is not None and state not in STATES:
        raise ActionError("that is not a status")
    if due:
        try:
            date.fromisoformat(due)
        except ValueError:
            raise ActionError("the due date has to be a date, like 2026-03-31") from None

    existing = conn.execute(
        "SELECT * FROM actions WHERE edge_id = ?", (edge_id,)
    ).fetchone()
    row = dict(existing) if existing else {"owner": "", "due": "", "state": "", "note": ""}

    if owner is not None:
        row["owner"] = " ".join(owner.split())[:MAX_OWNER]
    if due is not None:
        row["due"] = due.strip()
    if state is not None:
        row["state"] = state
    if note is not None:
        row["note"] = " ".join(note.split())[:MAX_NOTE]

    if not any((row["owner"], row["due"], row["state"], row["note"])):
        conn.execute("DELETE FROM actions WHERE edge_id = ?", (edge_id,))
        conn.commit()
        return None

    conn.execute(
        """
        INSERT INTO actions (edge_id, owner, due, state, note, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(edge_id) DO UPDATE SET
            owner = excluded.owner, due = excluded.due,
            state = excluded.state, note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
        """,
        (edge_id, row["owner"], row["due"], row["state"], row["note"]),
    )
    conn.commit()
    return row


def clear(conn, edge_id):
    init(conn)
    conn.execute("DELETE FROM actions WHERE edge_id = ?", (int(edge_id),))
    conn.commit()


def board(problems, options, tracked):
    """The tracking page: every change with whoever owns it, in status order.

    Built from the same problems and the same statuses the results page uses,
    so the two can never disagree about what a change is called or what it is
    worth.
    """
    rows = []
    for rank, problem in enumerate(problems, start=1):
        edge_id = problem.get("edge_id")
        action = tracked.get(edge_id) or {}
        state = status.of(problem, options, action)
        rows.append(
            {
                "rank": rank,
                "edge_id": edge_id,
                "title": problem["title"],
                "change": problem.get("change"),
                "cost": problem.get("cost_at_stake") or 0.0,
                "co2e": problem.get("co2e_at_stake") or 0.0,
                "owner": action.get("owner", ""),
                "due": action.get("due", ""),
                "note": action.get("note", ""),
                "state": action.get("state", ""),
                "status": state,
            }
        )

    order = {key: index for index, key in enumerate(status.ORDER)}
    rows.sort(key=lambda row: (order.get(row["status"]["key"], 99), row["rank"]))

    return {
        "rows": rows,
        "counts": {
            key: sum(1 for row in rows if row["status"]["key"] == key)
            for key in status.ORDER
        },
        "owned": sum(1 for row in rows if row["owner"]),
        "tracked": sum(1 for row in rows if row["state"]),
        "committed_cost": sum(
            row["cost"] for row in rows if row["state"] == status.DOING
        ),
        "delivered_cost": sum(
            row["cost"] for row in rows if row["state"] == status.DONE
        ),
    }


def to_csv(board_data, subject, generated):
    """The tracker as a file somebody keeps, because the workspace will not."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"Overlap action tracker: {subject}, {generated}"])
    writer.writerow([])
    writer.writerow(
        [
            "Priority",
            "Change",
            "What to do",
            "Cost saving (USD a year)",
            "CO2e avoided (kg a year)",
            "Status",
            "Owner",
            "Due",
            "Note",
        ]
    )
    for row in board_data["rows"]:
        writer.writerow(
            [
                row["rank"],
                row["title"],
                row["change"] or "",
                round(row["cost"], 2),
                round(row["co2e"], 2),
                row["status"]["label"],
                row["owner"],
                row["due"],
                row["note"],
            ]
        )
    return "﻿" + buffer.getvalue()
