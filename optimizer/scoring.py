"""Rank the lanes where cost and carbon are wasted in the same place.

A lane scoring high on cost alone is a procurement problem. High on carbon
alone is a reporting problem. The ones that score high on both are the only
ones where a single change pays twice, and those are what this ranks.
"""

from statistics import median

from . import analysis, factors, geo

FLAG_THRESHOLD = 0.25

# How much more than its current volume a site is assumed to be able to take.
# Past this the claim that it simply absorbs the extra stops being credible,
# and the overflow goes to the next nearest site instead. A warehouse is a
# building, not a variable, and pretending otherwise is how network studies
# end up recommending things that cannot physically happen.
CAPACITY_HEADROOM = 1.5

# Effort has to be able to outrank size. Tagging something high effort means
# it is not happening this quarter, so it should fall behind a smaller change
# that can actually be done, otherwise the tag looks like it did nothing.
EFFORT_WEIGHT = {"low": 1.6, "med": 1.0, "high": 0.4}

# Without real routing data the safe assumption is that a lane keeps reaching
# its destination the same way it already does. Air already crosses whatever
# is in the way, so it can drop to sea on a long haul or to surface on a short
# one. A road lane is a land route, so rail is the only honest alternative.
# Sea is already the cheapest and cleanest per tonne-km, so nothing beats it.
SWITCHABLE = {
    "air": ("sea", "rail", "road"),
    "road": ("rail",),
    "rail": (),
    "sea": (),
}

# Below this, an air lane is short enough that trucks and trains can take it.
SURFACE_RANGE_KM = 3000

# Below this, shipping it is not a real option regardless of what it costs.
SEA_MINIMUM_KM = 1500


def _normalise(values):
    low, high = min(values), max(values)
    span = high - low
    if span <= 0:
        return [0.0 for _ in values]
    return [(value - low) / span for value in values]


def plausible_modes(edge):
    """Modes this lane could actually run on, given how far it travels."""
    distance = edge["distance_km"]
    modes = []
    for mode in SWITCHABLE.get(edge["mode"], ()):
        if mode == "sea" and distance < SEA_MINIMUM_KM:
            continue
        # Only air needs the range check. A road lane is already a land route,
        # so rail can follow it however long it is.
        if (
            edge["mode"] == "air"
            and mode in ("road", "rail")
            and distance > SURFACE_RANGE_KM
        ):
            continue
        modes.append(mode)
    return modes


def alternatives(edge):
    """What this lane would cost and emit under each other transport mode."""
    options = []
    for mode in plausible_modes(edge):
        costs = analysis.lane_costs(
            edge["total_weight_kg"],
            edge["distance_km"],
            mode,
            edge["order_count"],
            edge["return_count"],
        )
        emissions = analysis.lane_emissions(
            edge["total_weight_kg"],
            edge["distance_km"],
            mode,
            edge["order_count"],
            edge["return_count"],
        )
        # Handling and warehouse share do not move when the mode changes, so
        # only the transport and returns legs are compared.
        options.append(
            {
                "mode": mode,
                "cost": costs["transport"] + costs["returns"],
                "co2e": emissions["transport"] + emissions["returns"],
            }
        )
    return options


def best_switch(edge):
    """The mode change that cuts the most cost and carbon, if any does."""
    current_cost = edge["transport_cost"] + edge["returns_cost"]
    current_co2e = edge["transport_co2e"] + edge["returns_co2e"]

    best = None
    for option in alternatives(edge):
        saved_cost = current_cost - option["cost"]
        saved_co2e = current_co2e - option["co2e"]
        if saved_cost <= 0 or saved_co2e <= 0:
            continue
        # Both sides as a share of what this lane spends and emits today.
        # Adding the raw figures would put dollars next to tonnes and let
        # whichever unit happens to be the bigger number pick the mode alone.
        gain = saved_cost / (current_cost or 1.0) + saved_co2e / (current_co2e or 1.0)
        if best is None or gain > best["gain"]:
            best = {
                "mode": option["mode"],
                "saved_cost": saved_cost,
                "saved_co2e": saved_co2e,
                "gain": gain,
            }
    return best


def rank(conn):
    """Score every lane, flag the overlap, and attach the best mode switch."""
    rows = conn.execute(
        """
        SELECT e.*,
               o.name AS origin_name, o.lat AS origin_lat, o.lon AS origin_lon,
               d.name AS dest_name, d.lat AS dest_lat, d.lon AS dest_lon,
               d.country AS dest_country,
               o.node_type AS origin_type, d.node_type AS dest_type,
               t.effort AS effort
        FROM edges e
        JOIN nodes o ON o.id = e.origin_id
        JOIN nodes d ON d.id = e.dest_id
        LEFT JOIN effort_tags t ON t.edge_id = e.id
        """
    ).fetchall()

    if not rows:
        return []

    lanes = []
    for row in rows:
        lane = dict(row)
        lane["cost"] = (
            row["transport_cost"] + row["handling_cost"] + row["returns_cost"]
        )
        lane["leg"] = "inbound" if row["origin_type"] == "supplier" else "outbound"
        lane["co2e"] = (
            row["transport_co2e"]
            + row["packaging_co2e"]
            + row["warehouse_co2e"]
            + row["returns_co2e"]
        )
        lanes.append(lane)

    cost_norm = _normalise([lane["cost"] for lane in lanes])
    co2e_norm = _normalise([lane["co2e"] for lane in lanes])

    for lane, cost_score, co2e_score in zip(lanes, cost_norm, co2e_norm):
        lane["cost_score"] = cost_score
        lane["co2e_score"] = co2e_score
        # How much of this lane sits in both problems at once. Taking the
        # smaller of the two is what makes it an overlap rather than a total,
        # so one big number cannot carry a lane on its own.
        lane["overlap"] = min(cost_score, co2e_score)
        lane["switch"] = best_switch(lane)

    # A lane being large is not the same as a lane being fixable. What decides
    # a quick win is how much of itself a lane gives back when the mode
    # changes, so this works in percentages. Normalising against the network
    # instead would let one huge lane flatten every other candidate to zero.
    network_cost = sum(lane["cost"] for lane in lanes) or 1.0
    network_co2e = sum(lane["co2e"] for lane in lanes) or 1.0

    for lane in lanes:
        switch = lane["switch"]
        if switch:
            lane["saving_cost_pct"] = switch["saved_cost"] / (lane["cost"] or 1.0)
            lane["saving_co2e_pct"] = switch["saved_co2e"] / (lane["co2e"] or 1.0)
            lane["network_cost_pct"] = switch["saved_cost"] / network_cost
            lane["network_co2e_pct"] = switch["saved_co2e"] / network_co2e
        else:
            lane["saving_cost_pct"] = 0.0
            lane["saving_co2e_pct"] = 0.0
            lane["network_cost_pct"] = 0.0
            lane["network_co2e_pct"] = 0.0

        # Both sides have to move for this to be a cost and carbon win rather
        # than a trade of one against the other.
        lane["opportunity"] = min(lane["saving_cost_pct"], lane["saving_co2e_pct"])
        lane["flagged"] = bool(switch) and lane["opportunity"] >= FLAG_THRESHOLD
        lane["priority"] = (
            lane["network_cost_pct"] + lane["network_co2e_pct"]
        ) * EFFORT_WEIGHT.get(lane["effort"], 1.0)

    lanes.sort(key=lambda lane: (lane["priority"], lane["overlap"]), reverse=True)
    return lanes


def quick_wins(conn, limit=5):
    return [lane for lane in rank(conn) if lane["flagged"]][:limit]


def set_effort(conn, edge_id, effort):
    if effort not in EFFORT_WEIGHT and effort is not None:
        raise ValueError(f"unknown effort level: {effort}")
    if effort is None:
        conn.execute("DELETE FROM effort_tags WHERE edge_id = ?", (edge_id,))
    else:
        conn.execute(
            """
            INSERT INTO effort_tags (edge_id, effort) VALUES (?, ?)
            ON CONFLICT(edge_id) DO UPDATE SET
                effort = excluded.effort,
                updated_at = CURRENT_TIMESTAMP
            """,
            (edge_id, effort),
        )
    conn.commit()


def _sites(conn, closed_ids, added):
    """Every warehouse available after the closures and additions.

    A site that is not there any more takes its storage cost and its energy
    with it, which is most of the reason closing one looks attractive. A site
    that does not exist yet has neither, so it has to be given both, and the
    only defensible source for them is what this company's own sites cost.
    """
    # What a site handles is everything that passes through it, arriving as
    # well as leaving. Counting only what it ships out would leave the
    # receiving work off the books, and a site's capacity has to cover the
    # same movements the reassignment is going to charge against it.
    throughput = {}
    for row in conn.execute(
        """
        SELECT node_id, SUM(kg) AS kg FROM (
            SELECT origin_id AS node_id, SUM(total_weight_kg) AS kg
            FROM edges GROUP BY origin_id
            UNION ALL
            SELECT dest_id AS node_id, SUM(total_weight_kg) AS kg
            FROM edges GROUP BY dest_id
        ) GROUP BY node_id
        """
    ):
        throughput[row["node_id"]] = row["kg"] or 0.0

    sites = []
    for row in conn.execute("SELECT * FROM nodes WHERE node_type = 'warehouse'"):
        handled = throughput.get(row["id"], 0.0)
        grid = row["grid_intensity"]
        sites.append(
            {
                "id": row["id"],
                "name": row["name"],
                "lat": row["lat"],
                "lon": row["lon"],
                "storage_cost_annual": row["storage_cost_annual"] or 0.0,
                "energy_kwh_annual": row["energy_kwh_annual"] or 0.0,
                "grid_intensity": (
                    grid if grid is not None else factors.DEFAULT_GRID_INTENSITY
                ),
                "handled_kg": handled,
                "capacity_kg": handled * CAPACITY_HEADROOM,
                "closed": row["id"] in closed_ids,
                "added": False,
            }
        )

    if added:
        sites.append(_hypothetical(sites, added))
    return sites


def _hypothetical(sites, added):
    """A site that does not exist yet, priced off the ones that do.

    Inventing a storage cost would make the whole answer arbitrary, so a new
    site is assumed to cost, burn and hold what this company's existing sites
    do per tonne. It is their own network, which makes it the fairest guess
    available, and it is stated on the page rather than buried here.
    """
    real = [s for s in sites if s["handled_kg"] > 0]
    if real:
        cost_rate = median(
            s["storage_cost_annual"] / (s["handled_kg"] / 1000) for s in real
        )
        energy_rate = median(
            s["energy_kwh_annual"] / (s["handled_kg"] / 1000) for s in real
        )
        typical_kg = median(s["handled_kg"] for s in real)
    else:
        cost_rate = energy_rate = typical_kg = 0.0

    tonnes = typical_kg / 1000
    return {
        "id": -1,
        "name": added["name"],
        "lat": added["lat"],
        "lon": added["lon"],
        "storage_cost_annual": cost_rate * tonnes,
        "energy_kwh_annual": energy_rate * tonnes,
        # Nothing in the data says what a grid looks like at a place that has
        # no site on it yet, so this is the world default rather than a guess
        # dressed up as local knowledge.
        "grid_intensity": factors.DEFAULT_GRID_INTENSITY,
        "handled_kg": 0.0,
        "capacity_kg": typical_kg * CAPACITY_HEADROOM,
        "closed": False,
        "added": True,
    }


def simulate_network(conn, closed_ids=(), added=None):
    """Recalculate the whole network with sites closed or a new one opened.

    Changing one lane is arithmetic. Removing a warehouse is not: its work has
    to go somewhere, the somewhere has to be near enough and big enough, and
    the building it used to sit in stops costing money. This does all three,
    and reports where it had to give up on capacity rather than quietly
    assuming a site can swallow anything.
    """
    closed_ids = set(closed_ids or ())
    sites = _sites(conn, closed_ids, added)
    live = [s for s in sites if not s["closed"]]
    if not live:
        raise ValueError("something has to stay open")

    edges = [
        dict(row)
        for row in conn.execute(
            """
            SELECT e.*, o.node_type AS origin_type, o.lat AS origin_lat,
                   o.lon AS origin_lon, o.name AS origin_name,
                   d.node_type AS dest_type, d.lat AS dest_lat,
                   d.lon AS dest_lon, d.name AS dest_name
            FROM edges e
            JOIN nodes o ON o.id = e.origin_id
            JOIN nodes d ON d.id = e.dest_id
            """
        )
    ]

    by_id = {site["id"]: site for site in sites}
    new_site = next((s for s in sites if s["added"]), None)
    used = {site["id"]: 0.0 for site in live}
    lanes = {site["id"]: 0 for site in live}
    moved = 0
    strained = False

    # Only lanes that are actually affected are reassigned. A lane whose site
    # is still open and still the nearest one stays exactly where it is, so
    # the answer to "change nothing" is zero. Re-planning the whole network on
    # every run would fold unrelated savings into whatever the user just did
    # and credit them to it.
    staying, moving = [], []
    for edge in edges:
        if edge["origin_type"] == "supplier":
            anchor = (edge["origin_lat"], edge["origin_lon"])
            current = edge["dest_id"]
        else:
            anchor = (edge["dest_lat"], edge["dest_lon"])
            current = edge["origin_id"]

        site = by_id.get(current)
        if site is None or site["closed"]:
            moving.append((edge, anchor, current, "closed"))
            continue

        # A new site only takes work off an existing one when it is genuinely
        # nearer to that end of the lane. Opening a warehouse does not make
        # every route reconsider itself.
        if new_site is not None:
            here = geo.distance_km(anchor[0], anchor[1], site["lat"], site["lon"])
            there = geo.distance_km(
                anchor[0], anchor[1], new_site["lat"], new_site["lon"]
            )
            if there < here:
                moving.append((edge, anchor, current, "nearer"))
                continue

        staying.append((edge, anchor, current))

    after_transport_cost = after_returns_cost = 0.0
    after_transport_co2e = after_returns_co2e = 0.0

    def price(edge, distance):
        nonlocal after_transport_cost, after_returns_cost
        nonlocal after_transport_co2e, after_returns_co2e
        costs = analysis.lane_costs(
            edge["total_weight_kg"],
            distance,
            edge["mode"],
            edge["order_count"],
            edge["return_count"],
        )
        emissions = analysis.lane_emissions(
            edge["total_weight_kg"],
            distance,
            edge["mode"],
            edge["order_count"],
            edge["return_count"],
        )
        after_transport_cost += costs["transport"]
        after_returns_cost += costs["returns"]
        after_transport_co2e += emissions["transport"]
        after_returns_co2e += emissions["returns"]

    for edge, _anchor, current in staying:
        used[current] += edge["total_weight_kg"]
        lanes[current] += 1
        price(edge, edge["distance_km"])

    # Heaviest first, so the biggest volumes get first claim on the nearest
    # site. Placing the light ones first would let them fill a site and push
    # the heavy ones somewhere far away, which is the expensive mistake.
    for edge, anchor, current, reason in sorted(
        moving, key=lambda item: -item[0]["total_weight_kg"]
    ):
        weight = edge["total_weight_kg"]

        if reason == "nearer":
            # This lane is only in play because the new site is closer to it.
            # If the new site cannot take it, nothing happens: it does not go
            # shopping around the rest of the network on the strength of a
            # change that turned out not to apply to it.
            if used[new_site["id"]] + weight <= new_site["capacity_kg"]:
                pick = new_site
            else:
                pick = by_id[current]
                strained = True
        else:
            ranked = sorted(
                live,
                key=lambda s: geo.distance_km(anchor[0], anchor[1], s["lat"], s["lon"]),
            )
            pick = next(
                (s for s in ranked if used[s["id"]] + weight <= s["capacity_kg"]), None
            )
            if pick is None:
                # Nowhere has room. The volume still has to go somewhere, so
                # it goes to the nearest site and the answer says it is over.
                pick = ranked[0]
                strained = True

        used[pick["id"]] += weight
        lanes[pick["id"]] += 1
        if pick["id"] != current:
            moved += 1
        price(edge, geo.distance_km(anchor[0], anchor[1], pick["lat"], pick["lon"]))

    totals = analysis.totals(conn)
    # Packaging does not move: the same orders ship either way. What changes
    # on the fixed side is which buildings are still being paid for.
    packaging_cost = totals["handling_cost"] - sum(
        s["storage_cost_annual"] for s in sites if not s["added"]
    )
    open_storage = sum(s["storage_cost_annual"] for s in live)
    open_energy_co2e = sum(
        s["energy_kwh_annual"] * s["grid_intensity"] for s in live
    )

    before = {
        "cost": totals["cost"],
        "co2e": totals["co2e"],
    }
    after = {
        "cost": (
            after_transport_cost
            + after_returns_cost
            + packaging_cost
            + open_storage
        ),
        "co2e": (
            after_transport_co2e
            + after_returns_co2e
            + totals["packaging_co2e"]
            + open_energy_co2e
        ),
    }

    return {
        "before": before,
        "after": after,
        "saved": {
            "cost": before["cost"] - after["cost"],
            "co2e": before["co2e"] - after["co2e"],
        },
        "moved_lanes": moved,
        "over_capacity": strained,
        "sites": [
            {
                "id": site["id"],
                "name": site["name"],
                "status": (
                    "closed"
                    if site["closed"]
                    else ("added" if site["added"] else "open")
                ),
                "tonnes_before": site["handled_kg"] / 1000,
                "tonnes_after": used.get(site["id"], 0.0) / 1000,
                "capacity_tonnes": site["capacity_kg"] / 1000,
                "lanes": lanes.get(site["id"], 0),
                "full": used.get(site["id"], 0.0) > site["capacity_kg"],
            }
            for site in sites
        ],
    }


def simulate(conn, edge_id, mode=None, origin_id=None):
    """Recalculate one lane under a different mode or warehouse."""
    edge = conn.execute(
        """
        SELECT e.*, o.lat AS origin_lat, o.lon AS origin_lon
        FROM edges e JOIN nodes o ON o.id = e.origin_id
        WHERE e.id = ?
        """,
        (edge_id,),
    ).fetchone()
    if edge is None:
        raise ValueError("no such lane")

    edge = dict(edge)
    new_mode = factors.normalise_mode(mode) if mode else edge["mode"]
    distance = edge["distance_km"]

    if origin_id and origin_id != edge["origin_id"]:
        origin = conn.execute(
            "SELECT lat, lon FROM nodes WHERE id = ?", (origin_id,)
        ).fetchone()
        dest = conn.execute(
            "SELECT lat, lon FROM nodes WHERE id = ?", (edge["dest_id"],)
        ).fetchone()
        if origin is None:
            raise ValueError("no such warehouse")
        distance = geo.distance_km(
            origin["lat"], origin["lon"], dest["lat"], dest["lon"]
        )

    costs = analysis.lane_costs(
        edge["total_weight_kg"],
        distance,
        new_mode,
        edge["order_count"],
        edge["return_count"],
    )
    emissions = analysis.lane_emissions(
        edge["total_weight_kg"],
        distance,
        new_mode,
        edge["order_count"],
        edge["return_count"],
    )

    before_cost = edge["transport_cost"] + edge["returns_cost"]
    before_co2e = edge["transport_co2e"] + edge["returns_co2e"]
    after_cost = costs["transport"] + costs["returns"]
    after_co2e = emissions["transport"] + emissions["returns"]

    return {
        "edge_id": edge_id,
        "mode": new_mode,
        "distance_km": distance,
        "before": {"cost": before_cost, "co2e": before_co2e},
        "after": {"cost": after_cost, "co2e": after_co2e},
        "saved": {
            "cost": before_cost - after_cost,
            "co2e": before_co2e - after_co2e,
        },
    }
