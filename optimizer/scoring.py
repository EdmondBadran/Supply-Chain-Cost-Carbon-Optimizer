"""Rank the lanes where cost and carbon are wasted in the same place.

A lane scoring high on cost alone is a procurement problem. High on carbon
alone is a reporting problem. The ones that score high on both are the only
ones where a single change pays twice, and those are what this ranks.
"""

from . import analysis, factors

FLAG_THRESHOLD = 0.25

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
        gain = saved_cost + saved_co2e
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


def simulate(conn, edge_id, mode=None, origin_id=None):
    """Recalculate one lane under a different mode or warehouse."""
    from . import geo

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
