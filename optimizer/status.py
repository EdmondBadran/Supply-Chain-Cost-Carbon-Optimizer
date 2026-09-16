"""One word for where a recommendation stands, out of five.

The report used to group changes by how sure the statistics were. That is a
property of the analysis, and the question a person actually has is what to do
about this one, which also depends on whether it clears their own service
limits and whether anybody has picked it up yet.

So there are five statuses and no others:

  Ready to act      it held up under the stress test and clears your limits
  Needs validation  it rests on something the file cannot confirm
  Blocked           your own service limit rules it out as it stands
  In progress       somebody owns it and is doing it
  Complete          it has been done

The first three are worked out. The last two are set by a person in the
tracking workspace and win over the first three, because a change somebody is
already doing is not waiting for more evidence.
"""

from . import factors

READY = "ready"
VALIDATE = "validate"
BLOCKED = "blocked"
DOING = "doing"
DONE = "done"

LABELS = {
    READY: "Ready to act",
    VALIDATE: "Needs validation",
    BLOCKED: "Blocked",
    DOING: "In progress",
    DONE: "Complete",
}

# The order they are shown in. Blocked sits above validation because a blocked
# change needs a decision from a person rather than more data, and In progress
# and Complete sit at the bottom because nothing on the results page is being
# asked about them.
ORDER = (READY, VALIDATE, BLOCKED, DOING, DONE)

# What a person is told each group means, in one line.
BLURBS = {
    READY: (
        "Held up every time the rates were redrawn, and within the limits you "
        "have set. Check the operational points, then plan it."
    ),
    VALIDATE: (
        "The size of the saving depends on something this file cannot confirm. "
        "Worth reviewing, and worth confirming before it is planned."
    ),
    BLOCKED: (
        "Ruled out by a limit you set rather than by the numbers. Change the "
        "limit or accept the trade-off and it becomes available again."
    ),
    DOING: "Somebody owns these and they are under way.",
    DONE: "Done. Kept here so the list stays honest about what was acted on.",
}

# Where a confidence label lands when nothing else has anything to say.
FROM_CONFIDENCE = {"high": READY, "moderate": VALIDATE, "low": VALIDATE}


def carrying_cost(route, capital_rate):
    """What the stock costs while it sits on a slower mode.

    Slowing a route down does not only take longer. The goods are paid for and
    are not yet sold for the extra days, and that money costs whatever money
    costs this company. It is only worked out when somebody has said what that
    is, because the alternative is inventing a rate and presenting it as a
    finding.
    """
    if not capital_rate or not route:
        return None
    extra = route.get("extra_days") or 0.0
    value = route.get("value") or 0.0
    if extra <= 0 or value <= 0:
        return None
    return value * (extra / 365.0) * capital_rate


def of(problem, options, action=None):
    """The status of one recommendation, and why it has it.

    `options` is `settings.current(conn)`. `action` is the tracking row for
    this change, if anybody has made one.
    """
    state = (action or {}).get("state")
    if state == DONE:
        return _made(DONE, "Marked complete.")
    if state == DOING:
        owner = (action or {}).get("owner") or ""
        return _made(DOING, f"Owned by {owner}." if owner else "Being worked on.")

    route = problem.get("route")
    limit = options.get("max_extra_days")

    if route and limit is not None:
        extra = route.get("extra_days") or 0.0
        if extra > limit:
            return _made(
                BLOCKED,
                f"Adds {_days(extra)} of transit, past the {_days(limit)} you "
                "allow. Raise the limit or accept the delay to consider it.",
                blocking=True,
            )

    held = carrying_cost(route, options.get("capital_rate"))
    if held is not None and held >= (problem.get("cost_at_stake") or 0.0):
        return _made(
            VALIDATE,
            f"The extra stock in transit costs about ${held:,.0f} a year, "
            "which is more than the freight saving. Worth checking against "
            "your own stock figures before acting.",
        )

    label = problem.get("confidence_label")
    if label is None:
        return _made(VALIDATE, _untested(problem))
    return _made(FROM_CONFIDENCE.get(label, VALIDATE), None)


def _untested(problem):
    """What a figure rests on when it was not stress-tested, in one sentence.

    A change built on an assumption the file cannot confirm never sits beside
    a route change that survived every redraw, so each kind says what would
    settle it.
    """
    kind = problem.get("kind")
    if kind == "warehouse_grid":
        return (
            "Worked out from this site's electricity use and the carbon "
            "intensity of its local grid. Not stress-tested."
        )
    if kind == "supplier_on_time":
        return (
            f"Depends on an assumption that {factors.EXPEDITE_SHARE_OF_LATE:.0%} "
            "of late deliveries are flown in. Your own expedite records would "
            "confirm it."
        )
    if kind == "returns":
        return "Your file shows how often orders on this route come back, but not why."
    if kind == "cost_to_serve":
        return (
            "The CO2e change is not estimated, and the saving depends on a "
            "nearer warehouse having room."
        )
    checks = problem.get("checks") or []
    return checks[0] if checks else "Not stress-tested."


def _made(key, reason, blocking=False):
    return {
        "key": key,
        "label": LABELS[key],
        "reason": reason,
        "blocking": blocking,
        "open": key in (READY, VALIDATE, BLOCKED),
    }


def _days(value):
    whole = round(value)
    if whole < 1:
        return "under a day"
    return f"{whole:,} day{'s' if whole != 1 else ''}"


def group(problems, options, actions=None):
    """Every change with its status, the groups in order, and the counts.

    Nothing is reordered. A change keeps the rank the engine gave it whichever
    group it is shown in, which is the same rule the old grouping followed.
    """
    actions = actions or {}
    by_rank = {}
    for rank, problem in enumerate(problems, start=1):
        by_rank[rank] = of(problem, options, actions.get(problem.get("edge_id")))

    groups = []
    for key in ORDER:
        ranks = [rank for rank, item in by_rank.items() if item["key"] == key]
        if ranks:
            groups.append(
                {
                    "key": key,
                    "label": LABELS[key],
                    "blurb": BLURBS[key],
                    "ranks": ranks,
                }
            )

    counts = {group["key"]: len(group["ranks"]) for group in groups}
    return {
        "by_rank": by_rank,
        "groups": groups,
        "counts": counts,
        # The three a first-time reader should look at, which are the highest
        # ranked changes nobody has picked up and nothing has ruled out.
        "top_ranks": [
            rank
            for rank in sorted(by_rank)
            if by_rank[rank]["key"] in (READY, VALIDATE)
        ][:3],
        "ready": counts.get(READY, 0),
        "blocked": counts.get(BLOCKED, 0),
    }


def modes_in_use(problems):
    """Which modes the recommendations actually move between, so the Improve
    accuracy page can say which rates are worth entering first."""
    seen = set()
    for problem in problems:
        route = problem.get("route")
        if not route:
            continue
        for side in ("now", "proposed"):
            mode = (route.get(side) or {}).get("mode")
            if mode:
                seen.add(factors.normalise_mode(mode))
    return seen
