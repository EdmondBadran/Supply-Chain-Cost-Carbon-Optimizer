"""The value chain as stages, with the problems sitting in each one.

The map answers "which route is bad". This answers "which part of my business
is bad", which is the question someone asks before they know what a lane is.
Every stage is derived from the same edges and nodes, just grouped by where in
the chain the cost and carbon land rather than by geography.
"""

from statistics import median

from . import scoring

# A warehouse burning this much more carbon per tonne than the median is
# usually sitting on a dirty grid rather than being badly run.
GRID_OUTLIER = 1.4

# Returns above this share of a lane's orders stop being noise.
RETURN_RATE_LIMIT = 0.08

# A destination costing this much more per tonne than the median is worth
# looking at even when its transport mode is already the best available.
COST_TO_SERVE_OUTLIER = 1.5


def _money(lane):
    return lane["cost"]


def build(conn):
    """Return the five stages plus returns, each with its own problems."""
    lanes = scoring.rank(conn)
    if not lanes:
        return []

    nodes = [dict(row) for row in conn.execute("SELECT * FROM nodes")]
    suppliers = [n for n in nodes if n["node_type"] == "supplier"]
    warehouses = [n for n in nodes if n["node_type"] == "warehouse"]
    customers = [n for n in nodes if n["node_type"] == "customer"]

    inbound = [l for l in lanes if l["leg"] == "inbound"]
    outbound = [l for l in lanes if l["leg"] == "outbound"]

    return [
        _suppliers_stage(suppliers, inbound),
        _freight_stage(
            "inbound",
            "Inbound freight",
            "Getting goods from suppliers into your warehouses",
            inbound,
        ),
        _warehousing_stage(warehouses, lanes),
        _freight_stage(
            "outbound",
            "Outbound freight",
            "Getting goods from warehouses out to customers",
            outbound,
        ),
        _customers_stage(customers, outbound),
        _returns_stage(lanes),
    ]


def _stage(key, name, blurb, headline, unit, cost, co2e, problems):
    return {
        "key": key,
        "name": name,
        "blurb": blurb,
        "headline": headline,
        "unit": unit,
        "cost": cost,
        "co2e": co2e,
        "problems": problems,
        "problem_count": len(problems),
        "at_risk_cost": sum(p["cost_at_stake"] for p in problems),
        "at_risk_co2e": sum(p["co2e_at_stake"] for p in problems),
    }


def _problem(title, detail, cost_at_stake, co2e_at_stake, edge_id=None, fix=None):
    return {
        "title": title,
        "detail": detail,
        "cost_at_stake": cost_at_stake,
        "co2e_at_stake": co2e_at_stake,
        "edge_id": edge_id,
        "fix": fix,
    }


def _suppliers_stage(suppliers, inbound):
    countries = {s["country"] for s in suppliers if s["country"]}
    problems = []
    for lane in sorted(inbound, key=lambda l: -l["priority"]):
        if not lane["flagged"]:
            continue
        problems.append(
            _problem(
                lane["origin_name"],
                f"Ships to {lane['dest_name']} by {lane['mode']} over "
                f"{round(lane['distance_km']):,} km",
                lane["switch"]["saved_cost"],
                lane["switch"]["saved_co2e"],
                lane["id"],
                f"Move this supplier to {lane['switch']['mode']}",
            )
        )
    return _stage(
        "suppliers",
        "Suppliers",
        "Where your goods come from before you own them",
        len(suppliers),
        "supplier" if len(suppliers) == 1 else "suppliers",
        0.0,
        0.0,
        problems,
    )


def _freight_stage(key, name, blurb, lanes):
    problems = []
    for lane in sorted(lanes, key=lambda l: -l["priority"]):
        if not lane["flagged"]:
            continue
        switch = lane["switch"]
        problems.append(
            _problem(
                f"{lane['origin_name']} to {lane['dest_name']}",
                f"{round(lane['distance_km']):,} km by {lane['mode']}, "
                f"{round(lane['total_weight_kg'] / 1000):,} t a year",
                switch["saved_cost"],
                switch["saved_co2e"],
                lane["id"],
                f"Ship it by {switch['mode']} instead",
            )
        )
    return _stage(
        key,
        name,
        blurb,
        len(lanes),
        "route" if len(lanes) == 1 else "routes",
        sum(l["transport_cost"] for l in lanes),
        sum(l["transport_co2e"] for l in lanes),
        problems,
    )


def _warehousing_stage(warehouses, lanes):
    handling_cost = sum(l["handling_cost"] for l in lanes)
    warehouse_co2e = sum(l["warehouse_co2e"] for l in lanes)

    # Carbon per tonne handled, so a big warehouse is not flagged just for
    # being big. What this catches is a site on a dirty grid.
    per_site = []
    for site in warehouses:
        site_lanes = [l for l in lanes if l["origin_id"] == site["id"]]
        tonnes = sum(l["total_weight_kg"] for l in site_lanes) / 1000
        if tonnes <= 0:
            continue
        co2e = sum(l["warehouse_co2e"] for l in site_lanes)
        per_site.append((site, co2e / tonnes, co2e, tonnes))

    problems = []
    if per_site:
        typical = median(x[1] for x in per_site)
        for site, intensity, co2e, tonnes in sorted(per_site, key=lambda x: -x[1]):
            if typical <= 0 or intensity < typical * GRID_OUTLIER:
                continue
            # What the site would emit at the typical intensity is what is
            # recoverable here, not the whole figure.
            avoidable = co2e - (typical * tonnes)
            problems.append(
                _problem(
                    site["name"],
                    f"{intensity:,.0f} kg CO2e per tonne handled, against "
                    f"{typical:,.0f} across your other sites",
                    0.0,
                    max(avoidable, 0.0),
                    None,
                    "Cleaner power at this site, or move volume to another one",
                )
            )

    return _stage(
        "warehousing",
        "Warehousing",
        "Holding, handling and powering the buildings in between",
        len(warehouses),
        "site" if len(warehouses) == 1 else "sites",
        handling_cost,
        warehouse_co2e,
        problems,
    )


def _customers_stage(customers, outbound):
    countries = {c["country"] for c in customers if c["country"]}

    per_dest = []
    for lane in outbound:
        tonnes = lane["total_weight_kg"] / 1000
        if tonnes > 0:
            per_dest.append((lane, lane["cost"] / tonnes))

    problems = []
    if per_dest:
        typical = median(rate for _, rate in per_dest)
        for lane, rate in sorted(per_dest, key=lambda x: -x[1]):
            if typical <= 0 or rate < typical * COST_TO_SERVE_OUTLIER:
                continue
            tonnes = lane["total_weight_kg"] / 1000
            problems.append(
                _problem(
                    lane["dest_name"],
                    f"${rate:,.0f} per tonne to serve, against ${typical:,.0f} "
                    f"across your other destinations",
                    max(lane["cost"] - typical * tonnes, 0.0),
                    0.0,
                    lane["id"],
                    "Serve it from a nearer warehouse, or on a cheaper mode",
                )
            )

    return _stage(
        "customers",
        "Customers",
        "Everywhere you deliver to, and what each one costs you",
        len(customers),
        "city" if len(customers) == 1 else "cities",
        0.0,
        0.0,
        problems[:6],
    )


def _returns_stage(lanes):
    returns_cost = sum(l["returns_cost"] for l in lanes)
    returns_co2e = sum(l["returns_co2e"] for l in lanes)
    returned = sum(l["return_count"] for l in lanes)

    problems = []
    for lane in sorted(lanes, key=lambda l: -l["returns_cost"]):
        if not lane["order_count"]:
            continue
        rate = lane["return_count"] / lane["order_count"]
        if rate < RETURN_RATE_LIMIT:
            continue
        problems.append(
            _problem(
                f"{lane['origin_name']} to {lane['dest_name']}",
                f"{rate:.0%} of orders come back, against a "
                f"{RETURN_RATE_LIMIT:.0%} threshold",
                lane["returns_cost"],
                lane["returns_co2e"],
                lane["id"],
                "Returns here are a product or fulfilment problem, not a freight one",
            )
        )

    return _stage(
        "returns",
        "Returns",
        "Everything that comes back, and the second trip it pays for",
        returned,
        "order returned" if returned == 1 else "orders returned",
        returns_cost,
        returns_co2e,
        problems[:6],
    )


# The chain drawn as one river of money. Geometry lives here rather than in
# the template because a bezier path is not something Jinja should be asked
# to work out.
FLOW_WIDTH = 1000
FLOW_HEIGHT = 400
FLOW_SPINE = 150
FLOW_MAX_HALF = 54
FLOW_MIN_HALF = 5


def _curve(points):
    """A path through points with flat tangents, so the river reads as flow."""
    d = f"M {points[0][0]:.1f} {points[0][1]:.1f}"
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        grip = (x1 - x0) / 2
        d += (
            f" C {x0 + grip:.1f} {y0:.1f}, {x1 - grip:.1f} {y1:.1f}, "
            f"{x1:.1f} {y1:.1f}"
        )
    return d


def flow_layout(stages):
    """Where each stage sits, and how thick the river is when it gets there.

    The band widens at every stage that adds cost, because that is what a
    supply chain does to a product: each leg makes it more expensive. Stages
    that add no cost of their own, suppliers and customers, leave the band
    flat, which answers the "why is this one blank" question in the picture
    instead of in a footnote.
    """
    forward = [s for s in stages if s["key"] != "returns"]
    returns = next((s for s in stages if s["key"] == "returns"), None)
    if not forward:
        return None

    step = FLOW_WIDTH / (len(forward) + 1)
    total = sum(s["cost"] for s in forward) or 1.0

    running = 0.0
    marks = []
    for index, stage in enumerate(forward):
        running += stage["cost"]
        span = FLOW_MAX_HALF - FLOW_MIN_HALF
        half = FLOW_MIN_HALF + span * (running / total)
        marks.append(
            {
                "key": stage["key"],
                "name": stage["name"],
                "x": step * (index + 1),
                "half": half,
                "running": running,
                "stage": stage,
            }
        )

    top = [(m["x"], FLOW_SPINE - m["half"]) for m in marks]
    bottom = [(m["x"], FLOW_SPINE + m["half"]) for m in marks]
    band = _curve(top) + " L " + _curve(list(reversed(bottom)))[1:] + " Z"

    loop = None
    if returns and returns["cost"] > 0:
        # The return leg runs flat underneath the whole chain rather than
        # curving out of the band. It has to clear every stage label, and a
        # line going right to left with an arrow on it reads as "backwards"
        # more plainly than a graceful curve does.
        start = marks[-1]["x"]
        end = next(
            (m["x"] for m in marks if m["key"] == "warehousing"), marks[0]["x"]
        )
        depth = FLOW_SPINE + FLOW_MAX_HALF + 110
        lift = 22
        corner = 34
        thickness = max(6.0, FLOW_MAX_HALF * (returns["cost"] / total) * 2)
        loop = {
            "path": (
                f"M {start:.1f} {depth - lift:.1f} "
                f"Q {start:.1f} {depth:.1f}, {start - corner:.1f} {depth:.1f} "
                f"L {end + corner:.1f} {depth:.1f} "
                f"Q {end:.1f} {depth:.1f}, {end:.1f} {depth - lift:.1f}"
            ),
            "width": thickness,
            "arrow": f"{end + corner + 16:.1f},{depth - 7:.1f} "
                     f"{end + corner - 4:.1f},{depth:.1f} "
                     f"{end + corner + 16:.1f},{depth + 7:.1f}",
            "label_x": (start + end) / 2,
            "label_y": depth + 24,
            "stage": returns,
        }

    return {
        "width": FLOW_WIDTH,
        "height": FLOW_HEIGHT,
        "spine": FLOW_SPINE,
        "band": band,
        "marks": marks,
        "loop": loop,
        "total": total,
    }
