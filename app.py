import os
import tempfile
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from optimizer import analysis, chain, db, factors, ingest, scoring

ROOT = Path(__file__).resolve().parent
SAMPLE_ORDERS = ROOT / "data" / "sample_orders.csv"
SAMPLE_WAREHOUSES = ROOT / "data" / "sample_warehouses.csv"
SAMPLE_SUPPLIERS = ROOT / "data" / "sample_suppliers.csv"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "dev")


def get_conn():
    conn = db.connect()
    db.init(conn)
    return conn


@app.route("/")
def index():
    """The value chain is the front door. Load the sample if nothing is here,
    so the first thing anyone sees is a working chain rather than a form."""
    conn = get_conn()
    try:
        if not db.summary(conn):
            ingest.load(conn, SAMPLE_ORDERS, SAMPLE_WAREHOUSES, SAMPLE_SUPPLIERS)
            analysis.run(conn)
            session["using_sample"] = True
        return render_template(
            "chain.html",
            summary=db.summary(conn),
            stages=chain.build(conn),
            using_sample=session.get("using_sample", False),
        )
    finally:
        conn.close()


@app.route("/data")
def upload_page():
    conn = get_conn()
    try:
        return render_template("index.html", summary=db.summary(conn))
    finally:
        conn.close()


@app.route("/method")
def method():
    return render_template(
        "method.html",
        emissions=factors.EMISSION_FACTORS,
        costs=factors.COST_FACTORS,
        packaging_co2e=factors.PACKAGING_KG_CO2E_PER_ORDER,
        packaging_cost=factors.PACKAGING_COST_PER_ORDER,
        return_multiplier=factors.RETURN_LEG_MULTIPLIER,
        return_handling=factors.RETURN_HANDLING_COST,
        grid_default=factors.DEFAULT_GRID_INTENSITY,
        flag_threshold=scoring.FLAG_THRESHOLD,
        effort_weights=scoring.EFFORT_WEIGHT,
        sea_minimum=scoring.SEA_MINIMUM_KM,
        surface_range=scoring.SURFACE_RANGE_KM,
    )


@app.route("/dashboard")
def dashboard():
    conn = get_conn()
    try:
        summary = db.summary(conn)
        if not summary:
            return redirect(url_for("index"))
        return render_template(
            "dashboard.html",
            summary=summary,
            totals=analysis.totals(conn),
            network=network_payload(conn),
            regions=analysis.by_region(conn, limit=8),
            warehouses=analysis.by_warehouse(conn),
        )
    finally:
        conn.close()


@app.post("/api/effort")
def api_effort():
    payload = request.get_json(silent=True) or {}
    edge_id = payload.get("edge_id")
    effort = payload.get("effort") or None
    if not edge_id:
        return jsonify({"error": "edge_id is required"}), 400

    conn = get_conn()
    try:
        scoring.set_effort(conn, int(edge_id), effort)
        return jsonify({"network": network_payload(conn)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.post("/api/simulate")
def api_simulate():
    payload = request.get_json(silent=True) or {}
    edge_id = payload.get("edge_id")
    if not edge_id:
        return jsonify({"error": "edge_id is required"}), 400

    conn = get_conn()
    try:
        result = scoring.simulate(
            conn,
            int(edge_id),
            mode=payload.get("mode"),
            origin_id=int(payload["origin_id"]) if payload.get("origin_id") else None,
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()


@app.post("/upload")
def upload():
    uploaded = request.files.get("orders")
    if uploaded is None or not uploaded.filename:
        return render_error("Choose a CSV file first.")
    if not uploaded.filename.lower().endswith(".csv"):
        return render_error("That is not a CSV file.")

    warehouses = request.files.get("warehouses")
    suppliers = request.files.get("suppliers")
    tmpdir = tempfile.mkdtemp(prefix="sco-")
    try:
        orders_path = Path(tmpdir) / "orders.csv"
        uploaded.save(orders_path)

        warehouses_path = None
        if warehouses is not None and warehouses.filename:
            if not warehouses.filename.lower().endswith(".csv"):
                return render_error("The warehouse file is not a CSV.")
            warehouses_path = Path(tmpdir) / "warehouses.csv"
            warehouses.save(warehouses_path)

        suppliers_path = None
        if suppliers is not None and suppliers.filename:
            if not suppliers.filename.lower().endswith(".csv"):
                return render_error("The supplier file is not a CSV.")
            suppliers_path = Path(tmpdir) / "suppliers.csv"
            suppliers.save(suppliers_path)

        conn = get_conn()
        try:
            report = ingest.load(conn, orders_path, warehouses_path, suppliers_path)
            analysis.run(conn)
            summary = db.summary(conn)
            session["using_sample"] = False
        except ingest.ValidationError as exc:
            return render_error(str(exc))
        except UnicodeDecodeError:
            return render_error("That file is not readable as UTF-8 text.")
        finally:
            conn.close()
    finally:
        _cleanup(tmpdir)

    return render_template("index.html", summary=summary, report=report)


@app.post("/sample")
def sample():
    conn = get_conn()
    try:
        ingest.load(conn, SAMPLE_ORDERS, SAMPLE_WAREHOUSES, SAMPLE_SUPPLIERS)
        analysis.run(conn)
        session["using_sample"] = True
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.post("/clear")
def clear():
    conn = get_conn()
    try:
        db.reset(conn)
        session.pop("using_sample", None)
    finally:
        conn.close()
    return redirect(url_for("upload_page"))


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


@app.errorhandler(413)
def too_large(_):
    return render_error("That file is bigger than the 32 MB limit."), 413


def network_payload(conn):
    """Everything the map needs: nodes, scored lanes, and the ranked wins."""
    nodes = [
        dict(row)
        for row in conn.execute(
            "SELECT id, name, node_type, city, country, lat, lon FROM nodes"
        )
    ]
    lanes = scoring.rank(conn)
    keep = (
        "id origin_id dest_id origin_name dest_name mode distance_km order_count leg "
        "total_weight_kg return_count cost co2e overlap flagged opportunity "
        "priority effort saving_cost_pct saving_co2e_pct network_cost_pct "
        "network_co2e_pct switch transport_cost handling_cost returns_cost "
        "transport_co2e packaging_co2e warehouse_co2e returns_co2e"
    ).split()
    return {
        "nodes": nodes,
        "lanes": [{key: lane.get(key) for key in keep} for lane in lanes],
        "warehouses": [n for n in nodes if n["node_type"] == "warehouse"],
    }


def render_error(message):
    conn = get_conn()
    try:
        return render_template("index.html", summary=db.summary(conn), error=message)
    finally:
        conn.close()


def _cleanup(tmpdir):
    for child in Path(tmpdir).iterdir():
        child.unlink(missing_ok=True)
    Path(tmpdir).rmdir()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
