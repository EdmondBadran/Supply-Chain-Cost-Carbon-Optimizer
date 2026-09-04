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
