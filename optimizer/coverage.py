"""How much time the loaded orders actually cover.

Every figure this tool prints is annual, and until now that was an assumption
rather than a measurement: whatever was in the file was treated as a year, so
a quarter of orders produced a quarter of a year of cost under a heading that
said "a year". A file covering two years had the opposite problem.

So the order dates are measured, and the routes built from them are scaled to
a year. Scaling happens once, on the routes, which is where the model already
says a year of weight lives. Warehouse rent and electricity arrive annual on
their own file and are never touched by it.

Nothing here scales silently. Every result carries the span it was measured
over and how far it was stretched, and a file too short or too undated to
measure is left alone and said to be.
"""

from datetime import date

YEAR_DAYS = 365

# A year of trading rarely spans exactly 365 days: the first and last order of
# a twelve month export land wherever they land. Inside this band the file is
# a year and is left exactly as it arrived, which also keeps a full year of
# data producing the same figures it produced before any of this existed.
FULL_YEAR_MIN_DAYS = 350
FULL_YEAR_MAX_DAYS = 400

# Below this, one month of orders would be multiplied by thirteen or more, and
# whatever that month happened to contain becomes the whole year. The figures
# stay as the period's own and say so, rather than being stretched into a
# claim the file cannot support.
MIN_SPAN_DAYS = 28

# Dates have to be on most of the rows before the span between the earliest
# and the latest describes the file rather than a handful of rows in it.
MIN_DATED_SHARE = 0.8

# How much of a year has to be present before an extrapolation carries a
# season. Under this, one quarter is being asked to speak for four.
STRONG_SPAN_DAYS = 180
MODERATE_SPAN_DAYS = 90


def _parse(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _nice(day):
    return f"{day.day} {day:%b %Y}"


def _span_words(span):
    if span >= 60:
        return f"{span / 30.44:.0f} months"
    return f"{span} days"


def measure(order_dates, total_orders=None):
    """What span the orders cover and what to do about it.

    `order_dates` is every order's date as it was loaded, blanks included, so
    the share carrying a date is reported rather than guessed at.
    """
    values = list(order_dates)
    total = len(values) if total_orders is None else total_orders
    days = sorted(day for day in (_parse(value) for value in values) if day)
    dated = len(days)
    share = dated / total if total else 0.0

    result = {
        "orders": total,
        "dated": dated,
        "dated_share": share,
        "first": days[0].isoformat() if days else None,
        "last": days[-1].isoformat() if days else None,
        "period": f"{_nice(days[0])} to {_nice(days[-1])}" if days else None,
        "span_days": (days[-1] - days[0]).days + 1 if days else None,
        "active_days": len(set(days)),
        "months": len({(day.year, day.month) for day in days}),
        "missing_months": _missing_months(days),
        "factor": 1.0,
        "basis": "undated",
        "strength": "none",
        "scaled": False,
        "headline": "No order dates, so the file is read as a year",
        "detail": (
            "The orders carry no readable date, so there is no way to tell "
            "what period they cover. Every figure treats the file as one "
            "year. Add an order_date column to have that checked."
        ),
    }

    if not days:
        return result

    span = result["span_days"]

    if share < MIN_DATED_SHARE:
        result["basis"] = "sparse_dates"
        result["headline"] = (
            f"Only {share:.0%} of orders carry a date, so the file is read as "
            "a year"
        )
        result["detail"] = (
            f"The {dated:,} dated orders run {result['period']}, but the rest "
            "could be from any period, so nothing was scaled. Figures are the "
            "file's own totals under an annual heading."
        )
        return result

    if span < MIN_SPAN_DAYS:
        result["basis"] = "too_short"
        result["headline"] = f"{span} days of orders, too short to read as a year"
        result["detail"] = (
            f"These orders cover {result['period']}, which is {span} days. "
            "Stretching that to a year would multiply one short period by "
            f"{YEAR_DAYS / span:.0f}, so it was left alone. Read every figure "
            "as this period's own, not as a year."
        )
        return result

    if FULL_YEAR_MIN_DAYS <= span <= FULL_YEAR_MAX_DAYS:
        result["basis"] = "full_year"
        result["strength"] = "high"
        result["headline"] = f"A full year of orders, {result['period']}"
        result["detail"] = (
            f"These orders span {span} days, so they are read as a year as "
            "they arrived, with nothing scaled."
        )
        return result

    factor = YEAR_DAYS / span
    result["factor"] = factor
    result["scaled"] = True
    result["basis"] = "scaled"
    result["strength"] = (
        "high"
        if span >= STRONG_SPAN_DAYS
        else "moderate"
        if span >= MODERATE_SPAN_DAYS
        else "low"
    )

    if span > FULL_YEAR_MAX_DAYS:
        result["headline"] = f"{span / YEAR_DAYS:.1f} years of orders, reduced to one"
        result["detail"] = (
            f"These orders cover {result['period']}, which is "
            f"{span / YEAR_DAYS:.1f} years. Every route figure is divided by "
            f"{1 / factor:.2f} so it describes a single year."
        )
        return result

    result["headline"] = f"{_span_words(span)} of orders, scaled up to a year"
    detail = (
        f"These orders cover {result['period']}, which is {_span_words(span)}. "
        f"Every route figure is multiplied by {factor:.2f} so it describes a "
        "year."
    )
    if result["months"] < 12:
        detail += (
            " A part year cannot carry a season: if these months are busier or "
            "quieter than the rest, the annual figures move with them."
        )
    result["detail"] = detail
    return result


def _missing_months(days):
    """Calendar months inside the span with no orders at all.

    A gap is worth naming because it decides whether the span is a period of
    trading or two exports stapled together with half a year in between.
    """
    if not days:
        return []
    seen = {(day.year, day.month) for day in days}
    first, last = days[0], days[-1]
    missing = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        if (year, month) not in seen:
            missing.append(f"{date(year, month, 1):%b %Y}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return missing


def apply(conn, factor):
    """Scale the routes built from orders to a year.

    Order counts stay whole, because everywhere they are shown reads them as a
    number of shipments. A route that carried orders keeps at least one, so a
    file covering several years cannot round a real route down to one that
    never ran.
    """
    if not factor or abs(factor - 1.0) < 1e-9:
        return 0

    rows = conn.execute("SELECT id, order_count, return_count FROM edges").fetchall()
    updates = []
    for row in rows:
        orders = max(1, round(row["order_count"] * factor)) if row["order_count"] else 0
        returns = min(orders, round(row["return_count"] * factor))
        updates.append((factor, factor, orders, returns, row["id"]))

    conn.executemany(
        """
        UPDATE edges SET
            total_weight_kg = total_weight_kg * ?,
            total_value = total_value * ?,
            order_count = ?,
            return_count = ?
        WHERE id = ?
        """,
        updates,
    )
    conn.commit()
    return len(updates)
