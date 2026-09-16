"""Cost to serve and emissions per route.

Both sides use the same per-edge pass because they share the same drivers:
tonne-km moved, orders shipped, returns handled, and each lane's share of the
warehouse it ships from.
"""

from . import distance, factors, settings


def tonne_km(weight_kg, straight_km, mode):
    """Tonnes times the distance this mode covers over the route."""
    return (weight_kg / 1000.0) * distance.by_mode(straight_km, mode)


def lane_costs(weight_kg, distance_km, mode, order_count, return_count, rate=None):
    """Cost of one lane. `distance_km` is the route's straight-line distance;
    the modal distance is worked out here for `mode`, so a candidate mode is
    always priced over its own ground. `rate` overrides the published cost
    factor, which is how the uncertainty run in stats.py re-prices the network
    without a second copy of this arithmetic."""
    moved = tonne_km(weight_kg, distance_km, mode)
    transport = moved * (factors.cost_factor(mode) if rate is None else rate)
    packaging = order_count * factors.PACKAGING_COST_PER_ORDER

    returns = 0.0
    if order_count:
        transport_per_order = transport / order_count
        returns = return_count * (
            transport_per_order * factors.RETURN_LEG_MULTIPLIER
            + factors.RETURN_HANDLING_COST
        )

    return {"transport": transport, "packaging": packaging, "returns": returns}


def lane_emissions(weight_kg, distance_km, mode, order_count, return_count, rate=None):
    """Emissions of one lane. Distance and `rate` work as in lane_costs."""
    moved = tonne_km(weight_kg, distance_km, mode)
    transport = moved * (factors.emission_factor(mode) if rate is None else rate)
    packaging = order_count * factors.PACKAGING_KG_CO2E_PER_ORDER

    returns = 0.0
    if order_count:
        returns = (
            (transport / order_count)
            * return_count
            * factors.RETURN_LEG_MULTIPLIER
        )

    return {"transport": transport, "packaging": packaging, "returns": returns}


def expedite_penalty(weight_kg, distance_km, mode, on_time_rate):
    """What a supplier's missed dates cost when the recovery is air freight.

    This is the link between supplier terms and carbon. Lateness on its own is
    a service problem, but the way it gets fixed is a freight decision, and it
    is always the expensive dirty one.
    """
    if on_time_rate is None or on_time_rate >= 1.0:
        return None
    mode = factors.normalise_mode(mode)
    if mode == "air":
        return None

    share = (1.0 - on_time_rate) * factors.EXPEDITE_SHARE_OF_LATE
    weight = weight_kg * share
    # Air and the usual mode cover different distances over the same route,
    # so each side is priced over its own.
    by_air = tonne_km(weight, distance_km, "air")
    usual = tonne_km(weight, distance_km, mode)
    return {
        "share": share,
        "cost": by_air * factors.cost_factor("air") - usual * factors.cost_factor(mode),
        "co2e": by_air * factors.emission_factor("air")
        - usual * factors.emission_factor(mode),
    }


def lane_transit_days(distance_km, mode):
    """Rough door-to-door days for a lane, so a mode switch can be priced in
    lead time as well as money and carbon. Takes the straight-line distance."""
    return (
        distance.by_mode(distance_km, mode) / factors.transit_km_per_day(mode)
        + factors.transit_fixed_days(mode)
    )


def run(conn):
    """Fill in cost and emission columns for every edge.

    Priced at the company's own rates wherever it has given them on the
    Improve accuracy page, and at the published defaults everywhere else. A
    workspace that has given none is priced exactly as it always was.
    """
    rates = settings.rates(conn)
    edges = conn.execute(
        """
        SELECT e.*, w.storage_cost_annual, w.energy_kwh_annual, w.grid_intensity
        FROM edges e
        JOIN nodes w ON w.id = e.origin_id
        """
    ).fetchall()

    outbound_weight = {}
    for edge in edges:
        outbound_weight[edge["origin_id"]] = (
            outbound_weight.get(edge["origin_id"], 0.0) + edge["total_weight_kg"]
        )

    updates = []
    for edge in edges:
        costs = lane_costs(
            edge["total_weight_kg"],
            edge["distance_km"],
            edge["mode"],
            edge["order_count"],
            edge["return_count"],
            rate=rates[factors.normalise_mode(edge["mode"])],
        )
        emissions = lane_emissions(
            edge["total_weight_kg"],
            edge["distance_km"],
            edge["mode"],
            edge["order_count"],
            edge["return_count"],
        )

        # Warehouse storage and energy are shared out by weight, which is the
        # closest honest proxy for how much of the building a lane uses up.
        total_weight = outbound_weight.get(edge["origin_id"]) or 0.0
        share = edge["total_weight_kg"] / total_weight if total_weight else 0.0
        handling = costs["packaging"] + share * (edge["storage_cost_annual"] or 0.0)
        grid = edge["grid_intensity"]
        if grid is None:
            grid = factors.DEFAULT_GRID_INTENSITY
        warehouse_co2e = share * (edge["energy_kwh_annual"] or 0.0) * grid

        updates.append(
            (
                costs["transport"],
                handling,
                costs["returns"],
                emissions["transport"],
                emissions["packaging"],
                warehouse_co2e,
                emissions["returns"],
                edge["id"],
            )
        )

    conn.executemany(
        """
        UPDATE edges SET
            transport_cost = ?, handling_cost = ?, returns_cost = ?,
            transport_co2e = ?, packaging_co2e = ?, warehouse_co2e = ?,
            returns_co2e = ?
        WHERE id = ?
        """,
        updates,
    )
    conn.commit()
    return len(updates)


def totals(conn):
    # Tonne-km is summed over each route's modal distance, the same distance
    # its cost and carbon were priced over.
    circuity = " ".join(
        f"WHEN '{mode}' THEN {value}" for mode, value in factors.CIRCUITY.items()
    )
    row = conn.execute(
        f"""
        SELECT
            SUM(transport_cost + handling_cost + returns_cost) AS cost,
            SUM(transport_co2e + packaging_co2e + warehouse_co2e + returns_co2e) AS co2e,
            SUM(transport_cost) AS transport_cost,
            SUM(handling_cost) AS handling_cost,
            SUM(returns_cost) AS returns_cost,
            SUM(transport_co2e) AS transport_co2e,
            SUM(packaging_co2e) AS packaging_co2e,
            SUM(warehouse_co2e) AS warehouse_co2e,
            SUM(returns_co2e) AS returns_co2e,
            SUM(total_weight_kg * distance_km / 1000.0
                * CASE mode {circuity} ELSE 1.0 END) AS tonne_km
        FROM edges
        """
    ).fetchone()
    return {key: row[key] or 0.0 for key in row.keys()}


def by_region(conn, limit=None):
    """Cost and carbon rolled up to the destination."""
    sql = """
        SELECT d.name AS name,
               d.country AS country,
               SUM(e.order_count) AS orders,
               SUM(e.transport_cost + e.handling_cost + e.returns_cost) AS cost,
               SUM(e.transport_co2e + e.packaging_co2e + e.warehouse_co2e
                   + e.returns_co2e) AS co2e
        FROM edges e
        JOIN nodes d ON d.id = e.dest_id
        GROUP BY e.dest_id
        ORDER BY cost DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql)]


def by_warehouse(conn):
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT w.name AS name,
                   COUNT(*) AS lanes,
                   SUM(e.order_count) AS orders,
                   SUM(e.transport_cost + e.handling_cost + e.returns_cost) AS cost,
                   SUM(e.transport_co2e + e.packaging_co2e + e.warehouse_co2e
                       + e.returns_co2e) AS co2e
            FROM edges e
            JOIN nodes w ON w.id = e.origin_id
            GROUP BY e.origin_id
            ORDER BY cost DESC
            """
        )
    ]
