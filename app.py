import csv
import io
import os
import secrets
import tempfile
from datetime import date
from functools import lru_cache
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from optimizer import (
    analysis,
    chain,
    db,
    diagnosis,
    distance,
    factors,
    geo,
    ingest,
    scoring,
    stats,
    store,
)

ROOT = Path(__file__).resolve().parent

# Two sample networks, because one dataset only ever proves the tool works on
# that dataset. The roastery is small enough to check by hand, which is what
# makes it the default: somebody can count the routes and agree with the
# answer. The distributor is where the ranking has to do real work, and it is
# now one click away rather than a code change.
SAMPLES = {
    "roastery": {
        "label": "Bristol coffee roastery",
        "blurb": (
            "Five growers, one warehouse, twelve UK and Irish cities. Small "
            "enough to check the arithmetic by hand."
        ),
        "folder": "Sample B Small BB",
        "files": ("orders.csv", "warehouses.csv", "suppliers.csv"),
    },
    "distributor": {
        "label": "Global electronics distributor",
        "blurb": (
            "Nine suppliers, four warehouses on three continents, thirty-two "
            "destination cities. Where the ranking has to earn its keep."
        ),
        "folder": "Sample A",
        "files": (
            "sample_orders.csv",
            "sample_warehouses.csv",
            "sample_suppliers.csv",
        ),
    },
}

DEFAULT_SAMPLE = "roastery"


def sample_paths(key):
    spec = SAMPLES[key]
    folder = ROOT / "data" / spec["folder"]
    return tuple(folder / name for name in spec["files"])


@lru_cache(maxsize=1)
def sample_facts():
    """What each sample actually contains, worked out by loading it.

    These figures used to be typed into the template by hand, and by the time
    anybody looked they described a version of the sample that no longer
    existed: it advertised 1,307 orders across 4 warehouses when the default
    sample had 182 orders and one. A page that argues the tool does honest
    arithmetic cannot open with a number somebody remembered wrong, so the
    numbers come from the files.

    Cached, because it means loading both datasets, and they do not change
    between requests.
    """
    facts = {}
    for key in SAMPLES:
        conn = db.connect()
        try:
            db.init(conn)
            ingest.load(conn, *sample_paths(key))
            analysis.run(conn)
            summary = db.summary(conn)
            totals = analysis.totals(conn)
            report = diagnosis.build(conn)
            facts[key] = {
                "orders": summary["orders"],
                "warehouses": summary["warehouses"],
                "customers": summary["customers"],
                "countries": summary["countries"],
                "routes": summary["edges"],
                "suppliers": conn.execute(
                    "SELECT COUNT(*) FROM nodes WHERE node_type = 'supplier'"
                ).fetchone()[0],
                "cost": totals["cost"],
                "co2e": totals["co2e"],
                "recoverable_cost": report["overview"]["recoverable_cost"],
                "recoverable_co2e": report["overview"]["recoverable_co2e"],
                "problems": len(report["problems"]),
            }
        finally:
            conn.close()
    return facts

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
# A random key when none is set means cookies from one run do not work against
# the next, which is the right way round: a restart should lose the session
# rather than hand it to whoever still holds an old cookie.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("HTTPS_ONLY") == "1"

# In debug, never let the browser hold on to a stylesheet or a script. An
# edit that appears not to have worked, because the page is still running the
# previous version of the file, costs more time than the caching ever saves.
if os.environ.get("FLASK_DEBUG", "1") != "0":
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


def ensure_data(conn):
    """Never let a page open empty. A cold start loads the sample, so the
    first thing anyone sees is a working chain rather than a form.

    Every page that reads data calls this, not just the landing page. Since
    each visitor gets their own empty workspace, whichever page they arrive on
    is the one that has to fill it, and a link shared straight to the map has
    to work as well as the front door.
    """
    if not db.summary(conn):
        load_sample(conn, DEFAULT_SAMPLE)


def load_sample(conn, key):
    ingest.load(conn, *sample_paths(key))
    analysis.run(conn)
    db.set_meta(conn, "source", "sample")
    db.set_meta(conn, "sample", key)


@app.route("/")
def index():
    """The landing page, written for someone deciding whether this is worth
    their time. It runs on real engine output rather than claims, so the
    numbers on it are the same ones the tool would show a customer."""
    with store.workspace() as conn:
        ensure_data(conn)
        return render_template(
            "landing.html",
            summary=db.summary(conn),
            report=diagnosis.build(conn),
            stages=chain.build(conn),
            facts=sample_facts()[DEFAULT_SAMPLE],
        )


def subject_of(conn):
    sample_key = db.get_meta(conn, "sample")
    return SAMPLES[sample_key]["label"] if sample_key in SAMPLES else "Your own order data"


def factor_rows():
    """Every per-mode assumption in one table, for the report and the method."""
    return [
        {
            "mode": mode,
            "cost": factors.COST_FACTORS[mode],
            "emission": factors.EMISSION_FACTORS[mode],
            "circuity": factors.CIRCUITY[mode],
            "speed": factors.TRANSIT_KM_PER_DAY[mode],
            "fixed_days": factors.TRANSIT_FIXED_DAYS[mode],
        }
        for mode in factors.MODES
    ]


def recommendation_payload(report):
    """What the route panel needs for each recommendation, keyed by route id.
    Every figure is the engine's own, so the panel does no arithmetic."""
    if not report:
        return {}
    keep = (
        "title stage kind change checks why action cost_at_stake co2e_at_stake "
        "confidence confidence_label route"
    ).split()
    return {
        str(problem["edge_id"]): {key: problem[key] for key in keep}
        for problem in report["problems"]
        if problem["route"]
    }


@app.route("/report")
def report_page():
    """The whole analysis as one document.

    This used to be four pages: the chain, the map, the written report and the
    statistics. Each opened cold, none of them said why you had arrived, and
    the owner of the tool got lost moving between them. They are five steps of
    one argument, so they are now five steps of one page.
    """
    with store.workspace() as conn:
        ensure_data(conn)
        stages = chain.build(conn)
        report = diagnosis.build(conn)
        return render_template(
            "report.html",
            summary=db.summary(conn),
            ingest=db.ingest_report(conn),
            stages=stages,
            flow=chain.flow_layout(stages),
            report=report,
            recommendations=recommendation_payload(report),
            stats=stats.build(conn),
            totals=analysis.totals(conn),
            network=network_payload(conn),
            regions=analysis.by_region(conn, limit=8),
            warehouses=analysis.by_warehouse(conn),
            using_sample=db.get_meta(conn, "source") == "sample",
            subject=subject_of(conn),
            generated=date.today().strftime("%d %B %Y"),
            stage_settings=chain.settings(conn),
            stage_error=request.args.get("stage_error"),
            factor_table=factor_rows(),
            confidence_checks=stats.CONFIDENCE_CHECKS,
        )


@app.route("/report/summary")
def summary_page():
    """The executive summary: one or two printed pages for somebody who will
    read the headline, the top changes and the catch, and nothing else."""
    with store.workspace() as conn:
        ensure_data(conn)
        report = diagnosis.build(conn)
        return render_template(
            "summary.html",
            summary=db.summary(conn),
            ingest=db.ingest_report(conn),
            report=report,
            uncertainty=stats.uncertainty(scoring.rank(conn)) if report else None,
            subject=subject_of(conn),
            generated=date.today().strftime("%d %B %Y"),
            factor_table=factor_rows(),
            flag_threshold=scoring.FLAG_THRESHOLD,
            confidence_checks=stats.CONFIDENCE_CHECKS,
            expedite_share=factors.EXPEDITE_SHARE_OF_LATE,
            transit_material_days=TRANSIT_MATERIAL_DAYS,
        )


# How many extra days of transit the summary calls out as a trade-off to check.
# A presentation rule only: it decides what is highlighted, not what is
# recommended.
TRANSIT_MATERIAL_DAYS = 2


# The addresses these pages used to live at. Kept so a link already sent to
# somebody still lands somewhere sensible, and pointed at the step that
# replaced them rather than at the top.
LEGACY_STEPS = {
    "/chain": "step-1",
    "/diagnosis": "step-2",
    "/dashboard": "step-3",
    "/stats": "step-5",
}


@app.route("/chain")
@app.route("/diagnosis")
@app.route("/dashboard")
@app.route("/stats")
def legacy_page():
    step = LEGACY_STEPS.get(request.path, "step-1")
    query = request.query_string.decode()
    target = url_for("report_page") + ("?" + query if query else "") + "#" + step
    return redirect(target, code=301)


@app.post("/chain/stages")
def edit_stages():
    """Rename, reorder, hide or add a stage.

    A plain form post rather than an API call, because every one of these
    changes the picture the page is built from. Re-rendering the whole page
    is both simpler and more honest than patching half of it in the browser.
    """
    action = request.form.get("action")
    key = request.form.get("key")
    error = None

    with store.workspace() as conn:
        try:
            if action == "rename":
                chain.rename_stage(
                    conn, key, request.form.get("name"), request.form.get("blurb")
                )
            elif action in ("up", "down"):
                chain.move_stage(conn, key, action)
            elif action == "hide":
                chain.set_stage_hidden(conn, key, True)
            elif action == "show":
                chain.set_stage_hidden(conn, key, False)
            elif action == "add":
                chain.add_stage(
                    conn, request.form.get("name"), request.form.get("blurb")
                )
            elif action == "remove":
                chain.remove_stage(conn, key)
            elif action == "reset":
                chain.reset_stages(conn)
            else:
                error = "unknown action"
        except ValueError as exc:
            error = str(exc)

    return redirect(
        url_for("report_page", stage_error=error) + "#stage-editor"
    )


@app.route("/data")
def upload_page():
    with store.workspace() as conn:
        return render_template(
            "index.html",
            summary=db.summary(conn),
            samples=SAMPLES,
            facts=sample_facts(),
            loaded_sample=db.get_meta(conn, "sample"),
        )


@app.route("/data/start")
def start_upload():
    """Where 'Upload my orders CSV' goes. The landing page always loads the
    sample so it never opens empty, but someone who followed this link came
    to upload their own file and should not land on a page that says the
    sample is already loaded."""
    store.discard()
    return redirect(url_for("upload_page"))


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
        expedite_share=factors.EXPEDITE_SHARE_OF_LATE,
        on_time_target=chain.ON_TIME_TARGET,
        lead_time_limit=chain.LEAD_TIME_LONG_DAYS,
        moq_months=chain.MOQ_MONTHS_LIMIT,
        capacity_headroom=scoring.CAPACITY_HEADROOM,
        factor_spread=stats.FACTOR_SPREAD,
        trials=stats.TRIALS,
        transit_speed=factors.TRANSIT_KM_PER_DAY,
        transit_fixed=factors.TRANSIT_FIXED_DAYS,
        circuity=factors.CIRCUITY,
        confidence_high=diagnosis.CONFIDENCE_HIGH,
        confidence_moderate=diagnosis.CONFIDENCE_MODERATE,
        confidence_checks=stats.CONFIDENCE_CHECKS,
    )


@app.route("/privacy")
def privacy():
    """What happens to a file somebody uploads. Short, and specific enough to
    be checked against the code rather than taken on trust."""
    return render_template(
        "privacy.html",
        store_stats=store.stats(),
        max_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
    )


CSV_COLUMNS = [
    "rank",
    "type",
    "stage",
    "route or item",
    "origin",
    "destination",
    "change",
    "current mode",
    "proposed mode",
    "straight line km",
    "current mode km",
    "proposed mode km",
    "weight tonnes per year",
    "orders per year",
    "current cost usd per year",
    "proposed cost usd per year",
    "cost at stake usd per year",
    "cost reduction share",
    "current co2e kg per year",
    "proposed co2e kg per year",
    "co2e at stake kg per year",
    "co2e reduction share",
    "current transit days",
    "proposed transit days",
    "confidence",
    "share of simulations",
    "share of chain cost",
    "share of chain carbon",
    "check before acting",
    "what to do",
    "effort",
]


def _csv_number(value, places=2):
    return "" if value is None else round(value, places)


@app.route("/findings.csv")
def findings_csv():
    """The route opportunities as a spreadsheet.

    A report somebody agrees with is still a web page. This is the same
    findings in the form the next conversation actually happens in, which is
    a spreadsheet with a column for who is doing it. Route changes carry the
    current and proposed figures side by side, so the saving in each row can be
    checked with a subtraction.
    """
    with store.workspace() as conn:
        ensure_data(conn)
        report = diagnosis.build(conn)
        if report is None:
            return redirect(url_for("report_page"))

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_COLUMNS)
        for index, problem in enumerate(report["problems"], start=1):
            route = problem["route"] or {}
            now = route.get("now") or {}
            proposed = route.get("proposed") or {}
            writer.writerow(
                [
                    index,
                    problem["kind"] or "",
                    problem["stage"],
                    problem["title"],
                    route.get("origin", ""),
                    route.get("dest", ""),
                    problem["change"] or "",
                    now.get("mode", ""),
                    proposed.get("mode", ""),
                    _csv_number(route.get("straight_km"), 0),
                    _csv_number(now.get("route_km"), 0),
                    _csv_number(proposed.get("route_km"), 0),
                    _csv_number(route.get("weight_t"), 3),
                    route.get("orders", ""),
                    _csv_number(now.get("cost")),
                    _csv_number(proposed.get("cost")),
                    round(problem["cost_at_stake"] or 0, 2),
                    _csv_number(route.get("cost_pct"), 4),
                    _csv_number(now.get("co2e")),
                    _csv_number(proposed.get("co2e")),
                    round(problem["co2e_at_stake"] or 0, 2),
                    _csv_number(route.get("co2e_pct"), 4),
                    _csv_number(now.get("days"), 1),
                    _csv_number(proposed.get("days"), 1),
                    problem["confidence_label"] or "not simulated",
                    _csv_number(problem["confidence"], 3),
                    "{:.4f}".format(problem["cost_share"]),
                    "{:.4f}".format(problem["co2e_share"]),
                    "; ".join(problem["checks"]),
                    problem["action"],
                    problem["effort"] or "",
                ]
            )

    name = "route-opportunities-" + date.today().isoformat() + ".csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="' + name + '"'},
    )


@app.post("/api/effort")
def api_effort():
    payload = request.get_json(silent=True) or {}
    edge_id = payload.get("edge_id")
    effort = payload.get("effort") or None
    if not edge_id:
        return jsonify({"error": "edge_id is required"}), 400

    with store.workspace() as conn:
        try:
            scoring.set_effort(conn, int(edge_id), effort)
            return jsonify({"network": network_payload(conn)})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@app.post("/api/simulate")
def api_simulate():
    payload = request.get_json(silent=True) or {}
    edge_id = payload.get("edge_id")
    if not edge_id:
        return jsonify({"error": "edge_id is required"}), 400

    with store.workspace() as conn:
        try:
            result = scoring.simulate(
                conn,
                int(edge_id),
                mode=payload.get("mode"),
                origin_id=(
                    int(payload["origin_id"]) if payload.get("origin_id") else None
                ),
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@app.post("/api/network")
def api_network():
    """Reshape the network: close sites, open one, reassign everything."""
    payload = request.get_json(silent=True) or {}

    try:
        closed = [int(value) for value in payload.get("closed") or []]
    except (TypeError, ValueError):
        return jsonify({"error": "closed must be a list of site ids"}), 400

    added = None
    city = (payload.get("city") or "").strip()
    if city:
        country = (payload.get("country") or "").strip() or None
        try:
            lat, lon = geo.locate(city, country)
        except geo.GeocodeError as exc:
            return jsonify({"error": str(exc)}), 400
        added = {"name": f"{city.title()} (new)", "lat": lat, "lon": lon}

    with store.workspace() as conn:
        try:
            return jsonify(scoring.simulate_network(conn, closed, added))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400


@app.post("/upload")
def upload():
    uploaded = request.files.get("orders")
    if uploaded is None or not uploaded.filename:
        return render_error("Choose a CSV file first.")
    if not uploaded.filename.lower().endswith(".csv"):
        return render_error("That is not a CSV file.")

    warehouses = request.files.get("warehouses")
    suppliers = request.files.get("suppliers")

    # The upload is spooled to a temporary file only long enough to parse it,
    # then removed in the finally below. What survives the request is the
    # graph, in this visitor's own in-memory database. See optimizer/store.py.
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

        with store.workspace() as conn:
            try:
                report = ingest.load(
                    conn, orders_path, warehouses_path, suppliers_path
                )
                analysis.run(conn)
                summary = db.summary(conn)
                db.set_meta(conn, "source", "upload")
                db.set_meta(conn, "sample", "")
            except ingest.ValidationError as exc:
                return render_error(str(exc))
            except UnicodeDecodeError:
                return render_error("That file is not readable as UTF-8 text.")
    finally:
        _cleanup(tmpdir)

    return render_template(
        "index.html",
        summary=summary,
        report=report,
        samples=SAMPLES,
        facts=sample_facts(),
        loaded_sample=None,
    )


@app.post("/sample")
def sample():
    key = request.form.get("sample") or DEFAULT_SAMPLE
    if key not in SAMPLES:
        return render_error("There is no sample by that name.")
    with store.workspace() as conn:
        load_sample(conn, key)
    return redirect(url_for("report_page"))


@app.post("/clear")
def clear():
    """Drop everything this visitor loaded, workspace included."""
    store.discard()
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
    held = {row["edge_id"]: row["confidence"] for row in stats.confidence(lanes)}
    keep = (
        "id origin_id dest_id origin_name dest_name mode distance_km order_count leg "
        "total_weight_kg return_count cost co2e overlap flagged opportunity "
        "priority effort saving_cost_pct saving_co2e_pct network_cost_pct "
        "network_co2e_pct switch transport_cost handling_cost returns_cost "
        "transport_co2e packaging_co2e warehouse_co2e returns_co2e"
    ).split()
    payload = []
    for lane in lanes:
        row = {key: lane.get(key) for key in keep}
        row["route_km"] = distance.by_mode(lane["distance_km"], lane["mode"])
        row["days"] = analysis.lane_transit_days(lane["distance_km"], lane["mode"])
        row["confidence"] = held.get(lane["id"])
        row["confidence_label"] = diagnosis.confidence_label(held.get(lane["id"]))
        payload.append(row)
    return {
        "nodes": nodes,
        "lanes": payload,
        "warehouses": [n for n in nodes if n["node_type"] == "warehouse"],
        "threshold": scoring.FLAG_THRESHOLD,
    }


def render_error(message):
    with store.workspace() as conn:
        return render_template(
            "index.html",
            summary=db.summary(conn),
            samples=SAMPLES,
            facts=sample_facts(),
            loaded_sample=db.get_meta(conn, "sample"),
            error=message,
        )


def _cleanup(tmpdir):
    for child in Path(tmpdir).iterdir():
        child.unlink(missing_ok=True)
    Path(tmpdir).rmdir()


if __name__ == "__main__":
    # The reloader runs a second process, which makes the server awkward to
    # stop by port. Set FLASK_DEBUG=0 when starting it from something that
    # needs to shut it down again cleanly.
    debug = os.environ.get("FLASK_DEBUG", "1") != "0"
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=debug, port=port)
