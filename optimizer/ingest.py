import csv

from . import db, factors, geo

REQUIRED_COLUMNS = {
    "origin_name",
    "origin_city",
    "dest_city",
    "weight_kg",
    "mode",
}

TRUTHY = {"1", "true", "yes", "y", "returned", "t"}

MAX_REPORTED_ERRORS = 50


class ValidationError(Exception):
    pass


def _clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _number(value, default=None, field="value"):
    text = _clean(value)
    if not text:
        return default
    try:
        return float(text.replace(",", ""))
    except ValueError:
        raise ValidationError(f"{field} is not a number: {text}") from None


def _resolve_point(city, country, lat, lon):
    lat_text, lon_text = _clean(lat), _clean(lon)
    if lat_text and lon_text:
        return _number(lat_text, field="lat"), _number(lon_text, field="lon")
    return geo.locate(city, country or None)


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValidationError("the file is empty")
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            raise ValidationError(
                "missing required columns: " + ", ".join(sorted(missing))
            )
        rows = list(reader)
    if not rows:
        raise ValidationError("the file has headers but no rows")
    return rows


def read_warehouse_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "name" not in [
            name.strip().lower() for name in reader.fieldnames
        ]:
            raise ValidationError("warehouse file needs a name column")
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
        return list(reader)


def load(conn, orders_path, warehouses_path=None):
    """Load a CSV into the node/edge graph. Bad rows are reported, not fatal."""
    rows = read_rows(orders_path)
    warehouse_rows = read_warehouse_rows(warehouses_path) if warehouses_path else None

    db.init(conn)
    db.reset(conn)

    errors = []
    nodes = {}
    orders = []

    def register(name, node_type, city, country, point):
        node = nodes.get(name)
        if node is None:
            nodes[name] = {
                "node_type": node_type,
                "city": city,
                "country": country,
                "lat": point[0],
                "lon": point[1],
            }
        elif node["node_type"] == "customer" and node_type == "warehouse":
            node["node_type"] = "warehouse"
        return name

    for position, row in enumerate(rows, start=2):
        try:
            weight = _number(row.get("weight_kg"), field="weight_kg")
            if weight is None or weight <= 0:
                raise ValidationError("weight_kg must be a positive number")

            mode = factors.normalise_mode(row.get("mode"))

            origin_name = _clean(row.get("origin_name"))
            if not origin_name:
                raise ValidationError("origin_name is blank")

            origin_city = _clean(row.get("origin_city"))
            origin_country = _clean(row.get("origin_country"))
            origin_point = _resolve_point(
                origin_city, origin_country, row.get("origin_lat"), row.get("origin_lon")
            )

            dest_city = _clean(row.get("dest_city"))
            dest_country = _clean(row.get("dest_country"))
            dest_point = _resolve_point(
                dest_city, dest_country, row.get("dest_lat"), row.get("dest_lon")
            )
            dest_name = f"{dest_city}, {dest_country}" if dest_country else dest_city

            register(origin_name, "warehouse", origin_city, origin_country, origin_point)
            register(dest_name, "customer", dest_city, dest_country, dest_point)

            orders.append(
                {
                    "order_ref": _clean(row.get("order_ref")) or None,
                    "order_date": _clean(row.get("order_date")) or None,
                    "customer_id": _clean(row.get("customer_id")) or None,
                    "origin": origin_name,
                    "dest": dest_name,
                    "units": int(_number(row.get("units"), 1, field="units")),
                    "weight_kg": weight,
                    "mode": mode,
                    "product_category": _clean(row.get("product_category")) or None,
                    "order_value": _number(row.get("order_value"), 0.0, field="order_value"),
                    "returned": 1 if _clean(row.get("returned")).lower() in TRUTHY else 0,
                }
            )
        except (ValidationError, ValueError, geo.GeocodeError) as exc:
            errors.append({"line": position, "problem": str(exc)})

    if not orders:
        first = errors[0]["problem"] if errors else "file was empty"
        raise ValidationError(f"no usable rows. First problem: {first}")

    if warehouse_rows is not None:
        _apply_warehouse_details(nodes, warehouse_rows, errors)

    node_ids = _write_nodes(conn, nodes)
    _write_orders(conn, orders, node_ids)
    edge_count = _build_edges(conn)
    conn.commit()

    return {
        "orders_loaded": len(orders),
        "rows_skipped": len(errors),
        "nodes": len(node_ids),
        "edges": edge_count,
        "errors": errors[:MAX_REPORTED_ERRORS],
    }


def _apply_warehouse_details(nodes, warehouse_rows, errors):
    for position, row in enumerate(warehouse_rows, start=2):
        name = _clean(row.get("name"))
        node = nodes.get(name)
        if node is None:
            errors.append(
                {
                    "line": position,
                    "problem": f"warehouse {name or '(blank)'} has no orders, ignored",
                }
            )
            continue
        try:
            node["node_type"] = "warehouse"
            node["storage_cost_annual"] = _number(
                row.get("storage_cost_annual"), 0.0, field="storage_cost_annual"
            )
            node["energy_kwh_annual"] = _number(
                row.get("energy_kwh_annual"), 0.0, field="energy_kwh_annual"
            )
            node["grid_intensity"] = _number(
                row.get("grid_intensity"),
                factors.DEFAULT_GRID_INTENSITY,
                field="grid_intensity",
            )
        except ValidationError as exc:
            errors.append({"line": position, "problem": f"{name}: {exc}"})


def _write_nodes(conn, nodes):
    ids = {}
    for name, node in nodes.items():
        cursor = conn.execute(
            """
            INSERT INTO nodes
                (name, node_type, city, country, lat, lon,
                 storage_cost_annual, energy_kwh_annual, grid_intensity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                node["node_type"],
                node["city"],
                node["country"] or None,
                node["lat"],
                node["lon"],
                node.get("storage_cost_annual", 0.0),
                node.get("energy_kwh_annual", 0.0),
                node.get("grid_intensity"),
            ),
        )
        ids[name] = cursor.lastrowid
    return ids


def _write_orders(conn, orders, node_ids):
    conn.executemany(
        """
        INSERT INTO orders
            (order_ref, order_date, customer_id, origin_id, dest_id, units,
             weight_kg, mode, product_category, order_value, returned)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                order["order_ref"],
                order["order_date"],
                order["customer_id"],
                node_ids[order["origin"]],
                node_ids[order["dest"]],
                order["units"],
                order["weight_kg"],
                order["mode"],
                order["product_category"],
                order["order_value"],
                order["returned"],
            )
            for order in orders
        ],
    )


def _build_edges(conn):
    """Roll orders up into lanes. Distance uses haversine registered into SQLite."""
    conn.create_function("haversine", 4, geo.distance_km, deterministic=True)
    conn.execute(
        """
        INSERT INTO edges
            (origin_id, dest_id, mode, order_count, total_weight_kg,
             total_value, return_count, distance_km)
        SELECT o.origin_id,
               o.dest_id,
               o.mode,
               COUNT(*),
               SUM(o.weight_kg),
               SUM(o.order_value),
               SUM(o.returned),
               haversine(origin.lat, origin.lon, dest.lat, dest.lon)
        FROM orders o
        JOIN nodes origin ON origin.id = o.origin_id
        JOIN nodes dest ON dest.id = o.dest_id
        GROUP BY o.origin_id, o.dest_id, o.mode
        """
    )
    return conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
