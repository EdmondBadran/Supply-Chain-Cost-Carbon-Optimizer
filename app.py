import os
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
    exports,
    factors,
    geo,
    ingest,
    scoring,
    security,
    stats,
    store,
    xlsx,
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
# Debug off, a real secret key where one is set, secure cookies behind HTTPS,
# and a refusal to start a deployment without a key. All of it is in
# optimizer/security.py, which is also where the reasoning is.
security.configure(app)


# Which allowance a request is counted against. An upload rebuilds a whole
# graph, an export runs two thousand reruns, and a page view reads what is
# already there, so one number for all three would either throttle reading or
# let the expensive work run unbounded.
RATE_BUCKETS = {
    "upload": "upload",
    "findings_csv": "export",
    "findings_xlsx": "export",
    "summary_page": "export",
    "change_brief": "export",
}


def rate_bucket():
    if request.path.startswith("/api/"):
        return "api"
    return RATE_BUCKETS.get(request.endpoint, "page")


@app.before_request
def guard():
    """Two checks every request passes before it reaches a view: it is not
    coming faster than the allowance, and if it changes anything it proves it
    came from a page this site served."""
    if request.endpoint == "static":
        return None

    if not security.rate_limit(rate_bucket(), security.client_id()):
        security.audit("rate_limited", who=security.actor(), path=request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "too many requests, slow down"}), 429
        return render_template("429.html"), 429

    if not security.csrf_ok():
        security.audit("csrf_rejected", who=security.actor(), path=request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "this request could not be verified"}), 400
        return render_error(
            "This page had been open too long to be submitted safely. "
            "Reload it and try again."
        ), 400
    return None


@app.after_request
def secure_headers(response):
    return security.apply_headers(response, app.config.get("HTTPS_ONLY"))

# The address of the person who built Overlap. Every page that gives it says
# so, and it is where anybody wanting the full method is sent: the long
# write-up is a document sent on request rather than a page.
CONTACT_EMAIL = "edmondbadran42@gmail.com"


@app.context_processor
def site_details():
    """Details any page may quote about the tool itself. The figures are read
    from the engine, so a threshold changed there is changed on every page."""
    return {
        "csrf_token": security.csrf_token(),
        "contact_email": CONTACT_EMAIL,
        "flag_threshold": scoring.FLAG_THRESHOLD,
        "confidence_checks": stats.CONFIDENCE_CHECKS,
        "confidence_high": diagnosis.CONFIDENCE_HIGH,
        "confidence_moderate": diagnosis.CONFIDENCE_MODERATE,
        "factor_spread": stats.FACTOR_SPREAD,
        "expedite_share": factors.EXPEDITE_SHARE_OF_LATE,
        "idle_hours": store.IDLE_TIMEOUT_SECONDS // 3600,
        "required_fields": REQUIRED_FIELDS,
        # What the upload page needs to spot a missing column before the file
        # is sent: the loader's own names, meanings and aliases, so the two
        # cannot disagree.
        "column_guide": {
            "required": [[name, meaning] for name, meaning, _ in REQUIRED_FIELDS],
            "aliases": {key: list(names) for key, names in ingest.HEADER_ALIASES.items()},
        },
        "transit_material_days": TRANSIT_MATERIAL_DAYS,
        "default_sample": DEFAULT_SAMPLE,
    }


@app.template_filter("nice_date")
def nice_date(value):
    """An order date from the file as a person writes it, 1 Sep 2025. Anything
    that is not an ISO date is shown as the file gave it."""
    try:
        parsed = date.fromisoformat(str(value)[:10])
    except ValueError:
        return value
    return f"{parsed.day} {parsed:%b %Y}"


# The five columns an orders file must have, in the words an operations
# manager would use for them, with an example value from the sample file.
# tests/test_routes.py checks this names exactly what the loader requires.
REQUIRED_FIELDS = (
    ("origin_name", "Who shipped it: your site or supplier name", "Bristol Roastery"),
    ("origin_city", "The city the shipment started in", "Bristol"),
    ("dest_city", "The city it was delivered to", "Edinburgh"),
    ("weight_kg", "Its weight, in kilograms", "52.46"),
    ("mode", "How it travelled: road, rail, sea or air", "road"),
)

FIELD_MEANING = {name: meaning for name, meaning, _ in REQUIRED_FIELDS}


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
        report = diagnosis.build(conn)
        return render_template(
            "landing.html",
            summary=db.summary(conn),
            report=report,
            groups=decision_groups(report),
            using_sample=db.get_meta(conn, "source") == "sample",
            subject=subject_of(conn),
        )


# How long a company name may be before it stops fitting the places it is
# printed: the summary header, the report cover and the running footer.
MAX_COMPANY_NAME = 60


def subject_of(conn):
    """Whose data this is, in the words it should be printed in. A sample says
    which company it invents; an upload says the name given with the file, and
    falls back to a description rather than a name nobody chose."""
    sample_key = db.get_meta(conn, "sample")
    if sample_key in SAMPLES:
        return SAMPLES[sample_key]["label"]
    return db.get_meta(conn, "company") or "Your own order data"


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


# The three groups the report sorts its ranked changes into. Presentation
# only: the ranking, the figures and the confidence are the engine's, and a
# change keeps its rank whichever group it is shown in.
GROUP_LABELS = {
    "now": "Recommended now",
    "review": "Worth reviewing",
    "more_data": "Needs more data to confirm",
}


def _untested_basis(problem):
    """The group for a change that was not stress-tested, and what its figure
    rests on, in one sentence. A figure built on an assumption the file cannot
    confirm never sits beside a route change that survived every redraw."""
    kind = problem["kind"]
    if kind == "warehouse_grid":
        return "review", (
            "Worked out from this site's electricity use and the carbon "
            "intensity of its local grid. Not stress-tested."
        )
    if kind == "supplier_on_time":
        return "more_data", (
            f"Depends on an assumption that {factors.EXPEDITE_SHARE_OF_LATE:.0%} "
            "of late deliveries are flown in. Your own expedite records would "
            "confirm it."
        )
    if kind == "returns":
        return "more_data", (
            "Your file shows how often orders on this route come back, but not why."
        )
    if kind == "cost_to_serve":
        return "more_data", (
            "The CO₂e change is not estimated, and the saving depends on a "
            "nearer warehouse having room."
        )
    return "more_data", (
        problem["checks"][0] if problem["checks"] else "Not stress-tested."
    )


def decision_groups(report):
    """Which group each ranked change is shown in, keyed by its rank.

    A change that was stress-tested goes by its confidence label: high is
    recommended now, moderate is worth reviewing, low needs more data. One
    that was not goes by what its figure rests on. Nothing is reordered.
    """
    if not report:
        return {"by_rank": {}, "groups": [], "counts": {}, "all_cut_both": False}
    by_rank = {}
    for rank, problem in enumerate(report["problems"], start=1):
        label = problem["confidence_label"]
        if label is None:
            key, basis = _untested_basis(problem)
        else:
            key = {"high": "now", "moderate": "review"}.get(label, "more_data")
            basis = None
        by_rank[rank] = {"key": key, "label": GROUP_LABELS[key], "basis": basis}
    groups = []
    for key, label in GROUP_LABELS.items():
        ranks = [rank for rank, group in by_rank.items() if group["key"] == key]
        if ranks:
            groups.append({"key": key, "label": label, "ranks": ranks})
    return {
        "by_rank": by_rank,
        "groups": groups,
        "counts": {group["key"]: len(group["ranks"]) for group in groups},
        # Whether the overview may say every change cuts both. A cleaner
        # power supply saves carbon and no money, so it is not always true.
        "all_cut_both": all(
            problem["cost_at_stake"] > 0
            and problem["co2e_at_stake"] > 0
            and not problem["co2e_line"]
            for problem in report["problems"]
        ),
    }


@app.route("/report")
def report_page():
    """The whole analysis as one page, in the order a decision is made: what
    was found and what to do first, every change grouped by how ready it is to
    act on, where the routes run, how far to trust the figures, and how to
    share them."""
    with store.workspace() as conn:
        ensure_data(conn)
        stages = chain.pictured(chain.build(conn))
        report = diagnosis.build(conn)
        regions = analysis.by_region(conn)
        return render_template(
            "report.html",
            summary=db.summary(conn),
            ingest=db.ingest_report(conn),
            stages=stages,
            flow=chain.flow_layout(stages),
            report=report,
            groups=decision_groups(report),
            recommendations=recommendation_payload(report),
            stats=stats.build(conn),
            totals=analysis.totals(conn),
            network=network_payload(conn),
            regions=regions[:REGIONS_SHOWN],
            region_count=len(regions),
            transit_material_days=TRANSIT_MATERIAL_DAYS,
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
            coverage=db.coverage(conn),
            summary=db.summary(conn),
            ingest=db.ingest_report(conn),
            report=report,
            groups=decision_groups(report),
            uncertainty=stats.uncertainty(scoring.rank(conn)) if report else None,
            subject=subject_of(conn),
            using_sample=db.get_meta(conn, "source") == "sample",
            generated=date.today().strftime("%d %B %Y"),
            factor_table=factor_rows(),
            flag_threshold=scoring.FLAG_THRESHOLD,
            confidence_checks=stats.CONFIDENCE_CHECKS,
            expedite_share=factors.EXPEDITE_SHARE_OF_LATE,
            transit_material_days=TRANSIT_MATERIAL_DAYS,
        )


@app.route("/report/change/<int:rank>")
def change_brief(rank):
    """One change on a page of its own, to print or save as a PDF and hand to
    whoever has to act on it. Every figure is the report's own."""
    with store.workspace() as conn:
        ensure_data(conn)
        report = diagnosis.build(conn)
        if not report or not 1 <= rank <= len(report["problems"]):
            return redirect(url_for("report_page"))
        return render_template(
            "change.html",
            coverage=db.coverage(conn),
            report=report,
            problem=report["problems"][rank - 1],
            rank=rank,
            group=decision_groups(report)["by_rank"][rank],
            summary=db.summary(conn),
            subject=subject_of(conn),
            using_sample=db.get_meta(conn, "source") == "sample",
            generated=date.today().strftime("%d %B %Y"),
            transit_material_days=TRANSIT_MATERIAL_DAYS,
        )


# How many extra days of transit the summary calls out as a trade-off to check.
# A presentation rule only: it decides what is highlighted, not what is
# recommended.
TRANSIT_MATERIAL_DAYS = 2

# How many destinations part one lists by cost to serve. Past this the table
# stops being read, and the workbook carries every route anyway.
REGIONS_SHOWN = 8


# The addresses these pages used to live at. Kept so a link already sent to
# somebody still lands somewhere sensible, and pointed at the section that
# replaced them rather than at the top.
LEGACY_STEPS = {
    "/chain": "network",
    "/diagnosis": "recommendations",
    "/dashboard": "network",
    "/stats": "data",
}


@app.route("/chain")
@app.route("/diagnosis")
@app.route("/dashboard")
@app.route("/stats")
def legacy_page():
    step = LEGACY_STEPS.get(request.path, "overview")
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

    security.audit(
        "stage_edited", who=security.actor(), action=action, rejected=error
    )
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


@app.route("/data/example-orders.csv")
def example_orders():
    """The roastery sample's orders file, to copy the columns from. It is
    invented data, and its filename says so."""
    path = sample_paths(DEFAULT_SAMPLE)[0]
    return attachment(path.read_bytes(), "text/csv", "overlap-sample-orders.csv")


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
    """How it works, in two boxes. The full method is a document sent on
    request, so the page stays short enough to be read."""
    return render_template("method.html")


@app.route("/privacy")
def privacy():
    """What happens to a file somebody uploads. Short, and specific enough to
    be checked against the code rather than taken on trust."""
    return render_template(
        "privacy.html",
        store_stats=store.stats(),
        max_mb=app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
    )


def export_context(conn, band=False):
    """Everything an export needs, read while the workspace is held, so
    writing the file afterwards does not keep this visitor's database locked.
    The uncertainty band is two thousand reruns, so only the workbook, which
    prints it, asks for it."""
    report = diagnosis.build(conn)
    lanes = scoring.rank(conn)
    tested = bool(report and report["confidence"]["tested"])
    return {
        "report": report,
        "lanes": lanes,
        "summary": db.summary(conn),
        "ingest": db.ingest_report(conn),
        "totals": analysis.totals(conn),
        "subject": subject_of(conn),
        "sample": db.get_meta(conn, "source") == "sample",
        "coverage": db.coverage(conn),
        "uncertainty": stats.uncertainty(lanes) if band and tested else None,
        "generated": date.today(),
        "contact_email": CONTACT_EMAIL,
    }


def export_name(context, what, extension):
    return "overlap-{}{}-{}.{}".format(
        "sample-" if context["sample"] else "",
        what,
        context["generated"].isoformat(),
        extension,
    )


def attachment(body, mimetype, name):
    return Response(
        body,
        mimetype=mimetype,
        headers={"Content-Disposition": 'attachment; filename="' + name + '"'},
    )


@app.route("/findings.csv")
def findings_csv():
    """Every opportunity as plain data, one row each, for whatever system
    reads it next. The version laid out for a person is the workbook."""
    with store.workspace() as conn:
        ensure_data(conn)
        context = export_context(conn)
    if context["report"] is None:
        return redirect(url_for("report_page"))
    return attachment(
        exports.opportunities_csv(context),
        "text/csv",
        export_name(context, "opportunities", "csv"),
    )


@app.route("/findings.xlsx")
def findings_xlsx():
    """The findings as a workbook that is ready to use when it opens: a
    summary sheet, every opportunity and every route with units, formats,
    totals and filters, the data check and the assumptions."""
    with store.workspace() as conn:
        ensure_data(conn)
        context = export_context(conn, band=True)
    if context["report"] is None:
        return redirect(url_for("report_page"))
    return attachment(
        exports.workbook(context),
        xlsx.MIMETYPE,
        export_name(context, "cost-and-carbon", "xlsx"),
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
            security.audit("effort_set", who=security.actor(), effort=effort or "none")
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
    # Columns matched by hand on the upload page, where the file's own
    # headings did not say which was which.
    company = " ".join((request.form.get("company") or "").split())[:MAX_COMPANY_NAME]
    columns = {
        name[len("column_"):]: value
        for name, value in request.form.items()
        if name.startswith("column_") and value
    }

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
                    conn, orders_path, warehouses_path, suppliers_path, columns
                )
                analysis.run(conn)
                summary = db.summary(conn)
                db.set_meta(conn, "source", "upload")
                db.set_meta(conn, "sample", "")
                db.set_meta(conn, "company", company)
                security.audit(
                    "upload_loaded",
                    who=security.actor(),
                    orders=report["orders_loaded"],
                    skipped=report["rows_skipped"],
                    routes=report["edges"],
                    coverage=report["coverage"]["basis"],
                    scaled_by=round(report["coverage"]["factor"], 4),
                    extra_files=sum(
                        1 for path in (warehouses_path, suppliers_path) if path
                    ),
                )
            except ingest.ValidationError as exc:
                security.audit(
                    "upload_rejected", who=security.actor(), reason=str(exc)[:120]
                )
                return render_error(str(exc), getattr(exc, "headings", None))
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
    security.audit("sample_loaded", who=security.actor(), sample=key)
    return redirect(url_for("report_page"))


@app.post("/clear")
def clear():
    """Drop everything this visitor loaded, workspace included."""
    security.audit("workspace_cleared", who=security.actor())
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


def friendly_error(message, headings=None):
    """An upload problem said the way a person would say it, with what to do
    next. The loader's own words stay underneath as the detail, for whoever
    ends up fixing the file."""
    text = message.strip()
    lowered = text.lower()
    missing = []
    if lowered.startswith("missing required columns:"):
        missing = [name.strip() for name in text.split(":", 1)[1].split(",") if name.strip()]
        title = "Your file is missing {} required column{}".format(
            len(missing), "" if len(missing) == 1 else "s"
        )
        advice = (
            "Choose the file again and match these to your own columns, or "
            "rename them in your spreadsheet and upload it again."
        )
    elif "not a csv" in lowered:
        title = "That file is not a CSV"
        advice = (
            "Save your spreadsheet as CSV (comma-separated values) and upload "
            "that. In Excel: File, Save As, CSV UTF-8."
        )
    elif lowered.startswith("choose a csv"):
        title = "Choose a file to upload"
        advice = "Pick your orders CSV, or drag it onto the upload area."
    elif "utf-8" in lowered:
        title = "We could not read the text in this file"
        advice = "Save it as CSV UTF-8 and upload it again."
    elif "mb limit" in lowered:
        title = "This file is too large"
        advice = "Split it into smaller files, or remove columns the analysis does not use."
    elif lowered == "the file is empty":
        title = "This file is empty"
        advice = "Check you exported the orders, not a blank sheet."
    elif lowered.startswith("the file has headers but no rows"):
        title = "This file has column headings but no orders"
        advice = "Export the order lines themselves and upload again."
    elif lowered.startswith("no usable rows"):
        title = "None of the orders could be used"
        advice = "Every row had a problem. The first one is described below."
    elif "did not reconcile" in lowered:
        title = "We could not check this file's totals"
        advice = (
            "The orders did not add up to the routes built from them, so no "
            "results were produced. Look for unusual rows and try again."
        )
    elif lowered.startswith("supplier file") or lowered.startswith("warehouse file"):
        title = "There is a problem with an optional file"
        advice = "Fix the file described below, or upload the orders on their own."
    elif "no sample by that name" in lowered:
        title = "That example does not exist"
        advice = "Choose one of the examples listed on this page."
    else:
        title = "We could not analyze this file"
        advice = "The problem is described below."
    return {
        "title": title,
        "advice": advice,
        "detail": text,
        "missing": [(name, FIELD_MEANING.get(name)) for name in missing],
        "headings": headings or [],
    }


def render_error(message, headings=None):
    with store.workspace() as conn:
        return render_template(
            "index.html",
            summary=db.summary(conn),
            samples=SAMPLES,
            facts=sample_facts(),
            loaded_sample=db.get_meta(conn, "sample"),
            error=friendly_error(message, headings),
        )


def _cleanup(tmpdir):
    for child in Path(tmpdir).iterdir():
        child.unlink(missing_ok=True)
    Path(tmpdir).rmdir()


if __name__ == "__main__":
    # Debug is off unless FLASK_DEBUG=1 is set, and the reloader it turns on
    # runs a second process, which makes the server awkward to stop by port.
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=app.config["DEBUG"], port=port)
