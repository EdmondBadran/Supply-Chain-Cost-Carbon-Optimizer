import csv
import io
import json
import re
from pathlib import Path

from . import coverage, db, factors, geo

REQUIRED_COLUMNS = {
    "origin_name",
    "origin_city",
    "dest_city",
    "weight_kg",
    "mode",
}

TRUTHY = {"1", "true", "yes", "y", "returned", "t"}

MAX_REPORTED_ERRORS = 50

# Order references go by more than one name in real exports, and a repeated
# reference is only noticed if its column is read under the name it has.
ORDER_REF_COLUMNS = ("order_ref", "order_id", "order_number")

# Straight-line distances past which a road or rail order is far more likely
# to be a city matched to the wrong place than a real service. A city given
# without a country resolves to the largest place with that name, so London,
# Ontario arrives as London, England. Rail is allowed much further because
# China to Europe rail freight runs past 10,000 km in a straight line.
SURFACE_LIMIT_KM = {"road": 5000, "rail": 11000}

# Headings other systems give the columns the loader reads, compared after
# header_key. Each one names its column and nothing else: a heading that could
# mean two things is left for the person to match on the upload page.
HEADER_ALIASES = {
    "origin_name": (
        "shipper", "shipper_name", "ship_from", "ship_from_name", "sender",
        "sender_name", "origin_site", "origin_site_name", "from_name",
    ),
    "origin_city": (
        "ship_from_city", "from_city", "shipper_city", "sender_city",
        "pickup_city", "collection_city", "departure_city", "origin_town",
    ),
    "origin_country": (
        "ship_from_country", "from_country", "shipper_country",
        "sender_country", "pickup_country", "origin_country_code",
    ),
    "dest_city": (
        "destination_city", "ship_to_city", "to_city", "delivery_city",
        "consignee_city", "receiver_city", "recipient_city", "customer_city",
        "dest_town", "destination_town",
    ),
    "dest_country": (
        "destination_country", "ship_to_country", "to_country",
        "delivery_country", "consignee_country", "receiver_country",
        "recipient_country", "customer_country", "dest_country_code",
        "destination_country_code",
    ),
    "weight_kg": (
        "weight_in_kg", "weight_kgs", "gross_weight_kg", "shipment_weight_kg",
        "total_weight_kg", "kg", "kgs",
    ),
    "mode": (
        "transport_mode", "shipping_mode", "shipment_mode", "freight_mode",
        "mode_of_transport", "transport_type", "ship_mode",
    ),
}

DELIMITERS = (",", ";", "\t", "|")

DECIMAL_COMMA = re.compile(r"^\s*-?\d{1,3}(\.\d{3})*,\d+\s*$|^\s*-?\d+,\d+\s*$")


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


def _rate(value, field):
    """A rate written either as 0.92 or as 92, both of which people use."""
    number = _number(value, None, field=field)
    if number is None:
        return None
    if number > 1.0:
        number = number / 100.0
    if not 0.0 <= number <= 1.0:
        raise ValidationError(f"{field} must be between 0 and 100 percent")
    return number


def _order_ref(row):
    """The order's reference, under whichever of the usual names it has."""
    for name in ORDER_REF_COLUMNS:
        ref = _clean(row.get(name))
        if ref:
            return ref
    return ""


def _resolve_point(city, country, lat, lon):
    lat_text, lon_text = _clean(lat), _clean(lon)
    if lat_text and lon_text:
        return _number(lat_text, field="lat"), _number(lon_text, field="lon")
    return geo.locate(city, country or None)


def header_key(name):
    """A column heading reduced to the form the loader uses: Weight (kg),
    weight-kg and WEIGHT_KG all become weight_kg."""
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")


def _read_table(path):
    """The file's heading row and rows, whichever way it was saved.

    Excel saves "CSV" as Windows-1252 unless told otherwise, and in much of
    Europe it separates fields with semicolons and writes 52,46 for 52.46.
    All three used to stop the upload or, worse, read 52,46 as 5,246. A
    decimal comma is only rewritten in a file that does not use commas to
    separate fields, where it cannot mean anything else.
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    first = text.split("\n", 1)[0]
    delimiter = max(DELIMITERS, key=first.count) if first.strip() else ","
    if first.count(delimiter) == 0:
        delimiter = ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    headings = next(reader, None)
    rows = [row for row in reader if row]
    if delimiter != ",":
        rows = [[_decimal_point(cell) for cell in row] for row in rows]
    return headings, rows


def _decimal_point(cell):
    return cell.replace(".", "").replace(",", ".") if DECIMAL_COMMA.match(cell) else cell


def _as_dicts(names, rows):
    return [dict(zip(names, row)) for row in rows]


def match_columns(headings, chosen=None):
    """Which heading in the file stands for each column the loader reads.

    A heading already named as the loader expects wins. Then anything the
    person picked on the upload page, then a recognised alias. Only aliases
    with one plausible meaning are listed, so a bare "weight", which might be
    pounds, or "origin", which might be a site or a city, is asked about
    rather than guessed.
    """
    keys = [header_key(name) for name in headings]
    names = list(keys)
    matched = []
    wanted = set(REQUIRED_COLUMNS) | set(HEADER_ALIASES)
    for column in sorted(wanted):
        if column in keys:
            continue
        position, how = None, None
        pick = header_key((chosen or {}).get(column))
        if pick and pick in keys:
            position, how = keys.index(pick), "chosen"
        else:
            for alias in HEADER_ALIASES.get(column, ()):
                if alias in keys:
                    position, how = keys.index(alias), "recognised"
                    break
        if position is None or names[position] in wanted:
            continue
        names[position] = column
        matched.append({"column": column, "heading": headings[position].strip(), "how": how})
    return names, matched


def read_rows(path, chosen=None):
    headings, rows = _read_table(path)
    if not headings or not any(name.strip() for name in headings):
        raise ValidationError("the file is empty")
    names, matched = match_columns(headings, chosen)
    missing = REQUIRED_COLUMNS - set(names)
    if missing:
        error = ValidationError("missing required columns: " + ", ".join(sorted(missing)))
        error.headings = [name.strip() for name in headings if name.strip()]
        raise error
    if not rows:
        raise ValidationError("the file has headers but no rows")
    return _as_dicts(names, rows), matched


def read_supplier_rows(path):
    headings, rows = _read_table(path)
    names = [header_key(name) for name in (headings or [])]
    missing = {"name", "city", "supplies", "mode", "annual_weight_kg"} - set(names)
    if missing:
        raise ValidationError(
            "supplier file is missing columns: " + ", ".join(sorted(missing))
        )
    return _as_dicts(names, rows)


def read_warehouse_rows(path):
    headings, rows = _read_table(path)
    names = [header_key(name) for name in (headings or [])]
    if "name" not in names:
        raise ValidationError("warehouse file needs a name column")
    return _as_dicts(names, rows)


def _error(file, line, field, exc):
    """One row that could not be used, saying which file, line and field."""
    problem = str(exc)
    if field and field not in problem:
        problem = f"{field}: {problem}"
    return {"file": file, "line": line, "field": field, "problem": problem}


def load(conn, orders_path, warehouses_path=None, suppliers_path=None, columns=None):
    """Load a CSV into the node/edge graph.

    A bad row is reported, never fatal and never silent: every row in the
    orders file ends up either loaded or listed with its line, field and
    reason, and the totals are checked against the file before anything is
    handed on to be analysed.
    """
    rows, columns_matched = read_rows(orders_path, columns)
    warehouse_rows = read_warehouse_rows(warehouses_path) if warehouses_path else None
    supplier_rows = read_supplier_rows(suppliers_path) if suppliers_path else None

    db.init(conn)
    db.reset(conn)

    errors = []
    warnings = []
    nodes = {}
    orders = []
    weight_in_file = 0.0
    first_seen = {}
    repeats = []
    far = []

    def register(label, node_type, city, country, point):
        """Record a place and return the key that identifies it.

        A place is its name and its city together, never the name on its own.
        A file that ships from ten cities under one company name describes ten
        origins, and keying on the name alone folded all of them into whichever
        one happened to be read first: every lane then inherited that city's
        coordinates, so road freight between two German cities was priced as
        the distance from Shanghai. Nothing about that is visible in the
        output, which is what makes it worth spelling out here.
        """
        key = (label, city)
        node = nodes.get(key)
        if node is None:
            nodes[key] = {
                "label": label,
                "node_type": node_type,
                "city": city,
                "country": country,
                "lat": point[0],
                "lon": point[1],
            }
        elif node["node_type"] == "customer" and node_type == "warehouse":
            node["node_type"] = "warehouse"
        return key

    for position, row in enumerate(rows, start=2):
        # Weight is added up for every row whose weight can be read, whether
        # or not the rest of the row is usable, so the report can say how
        # much weight the excluded rows took with them.
        try:
            readable = _number(row.get("weight_kg"), field="weight_kg")
        except ValidationError:
            readable = None
        if readable and readable > 0:
            weight_in_file += readable

        field = "weight_kg"
        try:
            weight = _number(row.get("weight_kg"), field="weight_kg")
            if weight is None or weight <= 0:
                raise ValidationError("weight_kg must be a positive number")

            field = "mode"
            mode = factors.normalise_mode(row.get("mode"))

            field = "origin_name"
            origin_name = _clean(row.get("origin_name"))
            if not origin_name:
                raise ValidationError("origin_name is blank")

            field = "origin_city"
            origin_city = _clean(row.get("origin_city"))
            origin_country = _clean(row.get("origin_country"))
            origin_point = _resolve_point(
                origin_city, origin_country, row.get("origin_lat"), row.get("origin_lon")
            )

            field = "dest_city"
            dest_city = _clean(row.get("dest_city"))
            dest_country = _clean(row.get("dest_country"))
            dest_point = _resolve_point(
                dest_city, dest_country, row.get("dest_lat"), row.get("dest_lon")
            )
            dest_name = f"{dest_city}, {dest_country}" if dest_country else dest_city

            origin_key = register(
                origin_name, "warehouse", origin_city, origin_country, origin_point
            )
            dest_key = register(
                dest_name, "customer", dest_city, dest_country, dest_point
            )

            orders.append(
                {
                    "order_ref": _order_ref(row) or None,
                    "order_date": _clean(row.get("order_date")) or None,
                    "customer_id": _clean(row.get("customer_id")) or None,
                    "origin": origin_key,
                    "dest": dest_key,
                    "units": int(_number(row.get("units"), 1, field="units")),
                    "weight_kg": weight,
                    "mode": mode,
                    "product_category": _clean(row.get("product_category")) or None,
                    "order_value": _number(row.get("order_value"), 0.0, field="order_value"),
                    "returned": 1 if _clean(row.get("returned")).lower() in TRUTHY else 0,
                }
            )
        except (ValidationError, ValueError, geo.GeocodeError) as exc:
            errors.append(_error("orders", position, field, exc))
            continue

        # A repeated row is kept, because two identical orders are entirely
        # possible, but it is pointed out, because an export run twice is
        # more likely still.
        ref = _order_ref(row)
        key = ("ref", ref) if ref else tuple(
            sorted((name, _clean(value)) for name, value in row.items() if name)
        )
        if key in first_seen:
            repeats.append({"line": position, "same_as": first_seen[key]})
        else:
            first_seen[key] = position

        # Kept, because it could be real, and pointed out, because it is far
        # more often a city that matched the wrong place.
        limit = SURFACE_LIMIT_KM.get(mode)
        if limit:
            straight = geo.distance_km(*origin_point, *dest_point)
            if straight > limit:
                far.append({"line": position, "km": round(straight)})

    order_errors = len(errors)

    if not orders:
        first = errors[0]["problem"] if errors else "file was empty"
        raise ValidationError(f"no usable rows. First problem: {first}")

    if repeats:
        many = len(repeats) != 1
        warnings.append(
            {
                "kind": "duplicates",
                "count": len(repeats),
                "lines": repeats[:10],
                "message": (
                    f"{len(repeats)} row{'s repeat' if many else ' repeats'} an "
                    "earlier row or order reference. "
                    + (
                        "They were counted as separate orders. Remove them if "
                        "they are duplicates."
                        if many
                        else "It was counted as a separate order. Remove it if it "
                        "is a duplicate."
                    )
                ),
            }
        )

    if far:
        many = len(far) != 1
        warnings.append(
            {
                "kind": "distance",
                "count": len(far),
                "lines": far[:10],
                "message": (
                    f"{len(far)} road or rail order{'s run' if many else ' runs'} "
                    "further than any regular road or rail service. A city given "
                    "without a country is matched to the largest place with that "
                    "name, which may not be the one meant. "
                    + ("They were" if many else "It was")
                    + " counted as given. Check the cities, or add origin_country "
                    "and dest_country."
                ),
            }
        )

    if warehouse_rows is not None:
        _apply_warehouse_details(nodes, warehouse_rows, errors)

    inbound = []
    if supplier_rows is not None:
        inbound = _apply_suppliers(nodes, supplier_rows, errors, warnings)

    node_ids = _write_nodes(conn, nodes)
    _write_orders(conn, orders, node_ids)
    lane_count = _build_edges(conn)
    _reconcile(conn, orders, lane_count)
    # Measured and applied before the supplier lanes are written. Those come
    # from a column called annual_weight_kg and are already a year, so
    # stretching them with everything else would count the same year twice.
    span = coverage.measure([order["order_date"] for order in orders])
    coverage.apply(conn, span["factor"])
    edge_count = lane_count + _write_inbound_edges(conn, inbound, node_ids, nodes)
    conn.commit()

    weight_loaded = sum(order["weight_kg"] for order in orders)
    fields = {error["field"] for error in errors if error["file"] == "orders"}
    report = {
        "rows_in_file": len(rows),
        "orders_loaded": len(orders),
        "rows_skipped": order_errors,
        "weight_in_file_kg": weight_in_file,
        "weight_loaded_kg": weight_loaded,
        "weight_excluded_kg": max(weight_in_file - weight_loaded, 0.0),
        "lanes": lane_count,
        "nodes": len(node_ids),
        "edges": edge_count,
        "suppliers": len(inbound),
        "columns_matched": columns_matched,
        "coverage": span,
        "error_count": len(errors),
        "errors": errors[:MAX_REPORTED_ERRORS],
        "warnings": warnings,
        "checks": {
            "weights_readable": "weight_kg" not in fields,
            "modes_recognised": "mode" not in fields,
            "origins_found": not fields & {"origin_name", "origin_city"},
            "destinations_found": "dest_city" not in fields,
        },
    }
    db.set_meta(conn, "ingest", json.dumps(report))
    return report


def _reconcile(conn, orders, lane_count):
    """Check the routes still add up to the orders they were built from.

    Grouping is where records have gone missing before: routes keyed on too
    little collapse into each other, and nothing downstream can tell. So
    before anything is analysed, the order count, the weight and the number
    of distinct origin, destination and mode combinations are read back out of
    the routes and compared with what was loaded. A mismatch stops the load
    rather than producing a report built on it.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(order_count), 0) AS orders, "
        "COALESCE(SUM(total_weight_kg), 0) AS weight FROM edges"
    ).fetchone()
    expected_lanes = len({(o["origin"], o["dest"], o["mode"]) for o in orders})
    expected_weight = sum(order["weight_kg"] for order in orders)

    if (
        row["orders"] != len(orders)
        or abs(row["weight"] - expected_weight) > 1e-6 * max(expected_weight, 1.0)
        or lane_count != expected_lanes
    ):
        raise ValidationError(
            f"the loaded orders did not reconcile with the routes built from "
            f"them ({len(orders)} orders and {expected_lanes} routes expected, "
            f"{row['orders']} orders and {lane_count} routes found), so no "
            f"report was produced"
        )


def _resolve_label(nodes, label):
    """The node a warehouse or supplier file means when it names a site.

    Sites are keyed by name and city, so a bare name is only unambiguous while
    the orders file uses it for one place. Where it is used for several, this
    says so rather than attaching the costs of one site to another.
    """
    matches = [key for key in nodes if key[0] == label]
    if not matches:
        return None
    if len(matches) > 1:
        cities = ", ".join(sorted(key[1] for key in matches))
        raise ValidationError(
            f"the orders file uses the name {label} for more than one place "
            f"({cities}), so it is not clear which one this row is about"
        )
    return matches[0]


def _display_names(nodes):
    """What each node is called once every place is known.

    A name used for one city stays as it is, which is the ordinary case and
    keeps a file that names its sites properly reading the way it was written.
    A name used for several gets the city attached, because on a map and in a
    ranked list those are different places and have to be told apart.
    """
    cities = {}
    for label, city in nodes:
        cities.setdefault(label, set()).add(city)

    return {
        (label, city): (label if len(cities[label]) == 1 else f"{label}, {city}")
        for label, city in nodes
    }


def _apply_suppliers(nodes, supplier_rows, errors, warnings):
    """Add supplier nodes and the inbound lanes feeding each warehouse.

    Two rows describing the same supplier, warehouse and mode are one route,
    so their weight, shipments and spend are added together. The second row
    used to be dropped by the database without a word.
    """
    inbound = {}
    combined = []
    for position, row in enumerate(supplier_rows, start=2):
        try:
            name = _clean(row.get("name"))
            if not name:
                raise ValidationError("supplier name is blank")

            feeds = _clean(row.get("supplies"))
            feeds_key = _resolve_label(nodes, feeds)
            if feeds_key is None:
                raise ValidationError(
                    f"supplies {feeds or '(blank)'}, which has no orders in the "
                    "orders file"
                )

            city = _clean(row.get("city"))
            country = _clean(row.get("country"))
            point = _resolve_point(city, country, row.get("lat"), row.get("lon"))

            weight = _number(row.get("annual_weight_kg"), field="annual_weight_kg")
            if weight is None or weight <= 0:
                raise ValidationError("annual_weight_kg must be a positive number")

            existing = nodes.get((name, city))
            if existing is not None and existing["node_type"] != "supplier":
                raise ValidationError(
                    f"{name} in {city} is already a site in the orders file, so "
                    "it cannot also be a supplier under the same name"
                )

            mode = factors.normalise_mode(row.get("mode"))
            shipments = int(
                _number(row.get("shipments_per_year"), 12, field="shipments_per_year")
            )
            value = _number(row.get("annual_cost"), 0.0, field="annual_cost")

            nodes[(name, city)] = {
                "label": name,
                "node_type": "supplier",
                "city": city,
                "country": country or None,
                "lat": point[0],
                "lon": point[1],
                "lead_time_days": _number(
                    row.get("lead_time_days"), None, field="lead_time_days"
                ),
                "min_order_qty": _number(
                    row.get("min_order_qty"), None, field="min_order_qty"
                ),
                "on_time_rate": _rate(row.get("on_time_rate"), "on_time_rate"),
            }
            lane_key = ((name, city), feeds_key, mode)
            lane = inbound.get(lane_key)
            if lane is None:
                inbound[lane_key] = {
                    "supplier": (name, city),
                    "warehouse": feeds_key,
                    "mode": mode,
                    "weight_kg": weight,
                    "shipments": shipments,
                    "value": value,
                }
            else:
                lane["weight_kg"] += weight
                lane["shipments"] += shipments
                lane["value"] += value
                combined.append(position)
        except (ValidationError, ValueError, geo.GeocodeError) as exc:
            errors.append(_error("suppliers", position, None, f"supplier row: {exc}"))

    if combined:
        warnings.append(
            {
                "kind": "supplier_rows_combined",
                "count": len(combined),
                "lines": [{"line": line} for line in combined[:10]],
                "message": (
                    f"{len(combined)} supplier row{'s' if len(combined) != 1 else ''} "
                    "described a supplier, warehouse and mode already listed. "
                    "Their weight was added to that route."
                ),
            }
        )
    return list(inbound.values())


def _write_inbound_edges(conn, inbound, node_ids, nodes):
    if not inbound:
        return 0
    conn.create_function("haversine", 4, geo.distance_km, deterministic=True)
    rows = []
    for lane in inbound:
        supplier = nodes[lane["supplier"]]
        warehouse = nodes[lane["warehouse"]]
        rows.append(
            (
                node_ids[lane["supplier"]],
                node_ids[lane["warehouse"]],
                lane["mode"],
                lane["shipments"],
                lane["weight_kg"],
                lane["value"],
                0,
                geo.distance_km(
                    supplier["lat"], supplier["lon"], warehouse["lat"], warehouse["lon"]
                ),
            )
        )
    conn.executemany(
        """
        INSERT INTO edges
            (origin_id, dest_id, mode, order_count, total_weight_kg,
             total_value, return_count, distance_km)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _apply_warehouse_details(nodes, warehouse_rows, errors):
    for position, row in enumerate(warehouse_rows, start=2):
        name = _clean(row.get("name"))
        try:
            key = _resolve_label(nodes, name)
        except ValidationError as exc:
            errors.append(_error("warehouses", position, None, f"warehouse {exc}"))
            continue

        node = nodes.get(key) if key else None
        if node is None:
            errors.append(
                _error(
                    "warehouses",
                    position,
                    None,
                    f"warehouse {name or '(blank)'} has no orders, ignored",
                )
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
            # What the building can actually hold in a year, if the company
            # knows. Left as None when the column is absent, which is the
            # signal for scoring.py to fall back to its headroom assumption
            # rather than treat an unknown site as a site of size zero.
            capacity = _number(row.get("capacity_kg"), None, field="capacity_kg")
            if capacity is not None and capacity <= 0:
                raise ValidationError("capacity_kg must be a positive number")
            node["capacity_kg"] = capacity
        except ValidationError as exc:
            errors.append(_error("warehouses", position, None, f"{name}: {exc}"))


def _write_nodes(conn, nodes):
    names = _display_names(nodes)
    ids = {}
    for key, node in nodes.items():
        cursor = conn.execute(
            """
            INSERT INTO nodes
                (name, node_type, city, country, lat, lon,
                 storage_cost_annual, energy_kwh_annual, grid_intensity,
                 lead_time_days, min_order_qty, on_time_rate, capacity_kg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                names[key],
                node["node_type"],
                node["city"],
                node["country"] or None,
                node["lat"],
                node["lon"],
                node.get("storage_cost_annual", 0.0),
                node.get("energy_kwh_annual", 0.0),
                node.get("grid_intensity"),
                node.get("lead_time_days"),
                node.get("min_order_qty"),
                node.get("on_time_rate"),
                node.get("capacity_kg"),
            ),
        )
        ids[key] = cursor.lastrowid
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
