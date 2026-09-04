import os
import tempfile
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

from optimizer import db, ingest

ROOT = Path(__file__).resolve().parent
SAMPLE_ORDERS = ROOT / "data" / "sample_orders.csv"
SAMPLE_WAREHOUSES = ROOT / "data" / "sample_warehouses.csv"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
app.secret_key = os.environ.get("SECRET_KEY", "dev")


def get_conn():
    conn = db.connect()
    db.init(conn)
    return conn


@app.route("/")
def index():
    conn = get_conn()
    try:
        return render_template("index.html", summary=db.summary(conn))
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
    tmpdir = tempfile.mkdtemp(prefix="sco-")
    orders_path = Path(tmpdir) / "orders.csv"
    uploaded.save(orders_path)

    warehouses_path = None
    if warehouses is not None and warehouses.filename:
        if not warehouses.filename.lower().endswith(".csv"):
            return render_error("The warehouse file is not a CSV.")
        warehouses_path = Path(tmpdir) / "warehouses.csv"
        warehouses.save(warehouses_path)

    conn = get_conn()
    try:
        report = ingest.load(conn, orders_path, warehouses_path)
        summary = db.summary(conn)
    except ingest.ValidationError as exc:
        return render_error(str(exc))
    except UnicodeDecodeError:
        return render_error("That file is not readable as UTF-8 text.")
    finally:
        conn.close()
        _cleanup(tmpdir)

    return render_template("index.html", summary=summary, report=report)


@app.post("/sample")
def sample():
    conn = get_conn()
    try:
        report = ingest.load(conn, SAMPLE_ORDERS, SAMPLE_WAREHOUSES)
        summary = db.summary(conn)
    finally:
        conn.close()
    return render_template("index.html", summary=summary, report=report, sample=True)


@app.post("/clear")
def clear():
    conn = get_conn()
    try:
        db.reset(conn)
    finally:
        conn.close()
    return redirect(url_for("index"))


@app.errorhandler(413)
def too_large(_):
    return render_error("That file is bigger than the 32 MB limit."), 413


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
