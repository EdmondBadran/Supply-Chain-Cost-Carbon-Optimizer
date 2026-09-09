"""The statistics behind the headline number.

The rest of the tool answers "what should we change first". This answers the
three questions somebody sensible asks straight afterwards:

    Is this network actually concentrated, or is the waste spread everywhere?
    How wrong could the figure be, given the factors it rests on?
    Do cost and carbon really land on the same lanes, or is that just the
    premise of the tool talking?

The last one matters most. The whole argument for this project is that money
and emissions overlap. If a particular network turns out not to work that way,
saying so is worth more than a chart that implies it does.

Everything here is stdlib. No numpy, no scipy: the sample sizes are small,
the methods are standard, and a dependency would buy nothing but a wheel to
install.
"""

import math
import random
from collections import Counter, defaultdict
from statistics import median

from . import analysis, factors, scoring

# How far each published factor is allowed to wander in the uncertainty run.
# Freight rates move with fuel, lane, contract and season, and the emission
# factors are themselves published as ranges. Plus or minus a third is a
# defensible width for both: it is wider than the year-on-year drift in the
# DEFRA figures and narrower than the gap between spot and contract freight.
FACTOR_SPREAD = 0.33

TRIALS = 2000

# Fixed, so the same data gives the same band every time the page is opened.
# A number that jitters on refresh reads as noise even when the method is
# sound, and there is nothing here worth re-rolling per visitor.
SEED = 20250907

# A robust z above this is not a rounding difference. 3.5 is the usual working
# threshold for the median-absolute-deviation version of the test.
OUTLIER_Z = 3.5

# Wilson intervals go wide below this many shipments, which is the point: a
# lane with four orders and one return has not told you anything yet.
MIN_RETURNS_SAMPLE = 12


def build(conn):
    """Everything the statistics page shows, or None with nothing loaded."""
    lanes = scoring.rank(conn)
    if not lanes:
        return None

    orders = [
        dict(row)
        for row in conn.execute(
            "SELECT weight_kg, order_value, order_date, returned, mode FROM orders"
        )
    ]

    return {
        "lane_count": len(lanes),
        "order_count": len(orders),
        "concentration": concentration(lanes),
        "overlap": overlap(lanes),
        "uncertainty": uncertainty(lanes),
        "outliers": outliers(lanes),
        "order_sizes": order_sizes(orders),
        "seasonality": seasonality(orders),
        "returns": returns_by_lane(lanes),
        "modes": by_mode(lanes),
    }


# ---------------------------------------------------------------- primitives


def gini(values):
    """How unequally a total is shared out. 0 is every lane carrying the same
    amount, 1 is one lane carrying all of it.

    Written as the mean absolute difference over twice the mean, which is the
    definition rather than the Lorenz-area shortcut, because with a few dozen
    lanes it is fast enough and there is no discretisation error to explain.
    """
    values = sorted(value for value in values if value > 0)
    n = len(values)
    if n < 2:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    # Sorted, so the usual rank-weighted form applies.
    weighted = sum((index + 1) * value for index, value in enumerate(values))
    return (2 * weighted) / (n * total) - (n + 1) / n


def lorenz(values):
    """Points on the cumulative share curve, poorest lane first."""
    values = sorted(value for value in values if value > 0)
    total = sum(values) or 1.0
    points = [(0.0, 0.0)]
    running = 0.0
    for index, value in enumerate(values, start=1):
        running += value
        points.append((index / len(values), running / total))
    return points


def spearman(xs, ys):
    """Rank correlation, ties averaged.

    Rank rather than Pearson because both cost and carbon are heavy-tailed:
    one enormous lane would otherwise decide the answer on its own, and the
    question here is whether the ordering agrees, not whether the magnitudes
    line up on a straight line.
    """
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mean_x, mean_y = sum(rx) / n, sum(ry) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    var_x = sum((a - mean_x) ** 2 for a in rx)
    var_y = sum((b - mean_y) ** 2 for b in ry)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        stop = index
        while (
            stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]
        ):
            stop += 1
        shared = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[order[position]] = shared
        index = stop + 1
    return ranks


def robust_z(values):
    """Distance from the median in median-absolute-deviations.

    The ordinary z-score is no use here: a handful of lanes and one genuine
    outlier is enough to drag the mean and inflate the standard deviation
    until the outlier stops looking like one. The median and the MAD do not
    move when a single value goes wild, which is the whole point.
    """
    if len(values) < 3:
        return [0.0] * len(values)
    mid = median(values)
    mad = median([abs(value - mid) for value in values])
    if mad <= 0:
        return [0.0] * len(values)
    # 0.6745 is the MAD of a standard normal, so dividing by it puts the score
    # back on the same scale as an ordinary z.
    return [0.6745 * (value - mid) / mad for value in values]


def wilson(successes, trials, z=1.96):
    """A 95 percent interval for a proportion that behaves at small n.

    The textbook interval built on the normal approximation gives nonsense
    below about thirty observations, including bounds under zero, which is
    exactly the range most lanes in a small network sit in.
    """
    if trials <= 0:
        return (0.0, 0.0, 1.0)
    phat = successes / trials
    denom = 1 + z * z / trials
    centre = (phat + z * z / (2 * trials)) / denom
    spread = (
        z
        * math.sqrt(phat * (1 - phat) / trials + z * z / (4 * trials * trials))
        / denom
    )
    return (phat, max(0.0, centre - spread), min(1.0, centre + spread))


def percentile(values, fraction):
    """Linear-interpolated percentile, the same convention as numpy's default,
    so a figure quoted from here matches one worked out elsewhere."""
    if not values:
        return 0.0
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    position = fraction * (len(values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return values[low]
    return values[low] + (values[high] - values[low]) * (position - low)


# -------------------------------------------------------------- the sections


def concentration(lanes):
    """How much of the network sits in how few lanes.

    Concentration is the difference between a problem with a fix and a problem
    with a programme. If eighty percent of the spend is on four lanes, four
    conversations change the number. If it is spread evenly across sixty, no
    single change moves anything and the honest advice is different.
    """
    costs = [lane["cost"] for lane in lanes]
    co2es = [lane["co2e"] for lane in lanes]

    return {
        "cost_gini": gini(costs),
        "co2e_gini": gini(co2es),
        "cost_curve": _svg_curve(lorenz(costs)),
        "co2e_curve": _svg_curve(lorenz(co2es)),
        "cost_pareto": _pareto(costs),
        "co2e_pareto": _pareto(co2es),
        "top_lanes": [
            {
                "name": f"{lane['origin_name']} to {lane['dest_name']}",
                "mode": lane["mode"],
                "cost": lane["cost"],
                "co2e": lane["co2e"],
                "cost_share": lane["cost"] / (sum(costs) or 1.0),
                "co2e_share": lane["co2e"] / (sum(co2es) or 1.0),
            }
            for lane in sorted(lanes, key=lambda item: -item["cost"])[:6]
        ],
    }


def _pareto(values, target=0.8):
    """How few lanes it takes to reach `target` of the total."""
    values = sorted(values, reverse=True)
    total = sum(values) or 1.0
    running = 0.0
    for count, value in enumerate(values, start=1):
        running += value
        if running / total >= target:
            return {
                "lanes": count,
                "share_of_lanes": count / len(values),
                "share_of_total": running / total,
            }
    return {"lanes": len(values), "share_of_lanes": 1.0, "share_of_total": 1.0}


def overlap(lanes):
    """Whether cost and carbon really do land on the same lanes.

    This is the premise of the whole tool put back under test on the loaded
    data rather than assumed. A high rank correlation says the two problems
    are the same problem here and one set of changes fixes both. A low one
    says they are not, and that this network needs the cost work and the
    carbon work planned separately, which is a more useful thing to be told
    than a reassuring number.
    """
    costs = [lane["cost"] for lane in lanes]
    co2es = [lane["co2e"] for lane in lanes]
    rho = spearman(costs, co2es)

    cost_cut = percentile(costs, 0.75)
    co2e_cut = percentile(co2es, 0.75)
    both = [
        lane
        for lane in lanes
        if lane["cost"] >= cost_cut and lane["co2e"] >= co2e_cut
    ]
    # If the two rankings were unrelated, a quarter of the top quarter would
    # land in both by chance, so a quarter of a quarter of all lanes.
    expected = len(lanes) * 0.25 * 0.25

    intensity = [
        (lane, lane["co2e"] / lane["cost"]) for lane in lanes if lane["cost"] > 0
    ]
    intensity.sort(key=lambda item: -item[1])

    return {
        "rho": rho,
        "verdict": _rho_verdict(rho),
        "both_count": len(both),
        "expected_by_chance": expected,
        "lift": (len(both) / expected) if expected > 0 else None,
        "scatter": _svg_scatter(lanes),
        "dirtiest": [
            {
                "name": f"{lane['origin_name']} to {lane['dest_name']}",
                "mode": lane["mode"],
                "kg_per_dollar": ratio,
            }
            for lane, ratio in intensity[:5]
        ],
        "cleanest": [
            {
                "name": f"{lane['origin_name']} to {lane['dest_name']}",
                "mode": lane["mode"],
                "kg_per_dollar": ratio,
            }
            for lane, ratio in intensity[-3:][::-1]
        ],
    }


def _rho_verdict(rho):
    if rho is None:
        return "too few lanes to say"
    if rho >= 0.7:
        return "strong, so one set of changes moves both"
    if rho >= 0.4:
        return "real but partial, so some lanes need choosing between"
    if rho >= 0.1:
        return "weak, so cost and carbon need planning separately here"
    return "none, so treating them as one problem would mislead"


def uncertainty(lanes):
    """How far the recoverable figure could be out.

    Every saving in this tool is a difference between two factor-driven
    estimates, and the factors are published as ranges rather than constants.
    Quoting a single number to the dollar implies a precision the inputs do
    not have. So the whole ranking is re-run against factors drawn from those
    ranges, and what comes back is a band.

    The factors are drawn once per trial and applied to every lane, because
    that is how the real uncertainty behaves: if diesel is dearer than assumed
    it is dearer on every road lane at once. Drawing per lane would let the
    errors cancel each other out and produce a band far too narrow to be true.
    """
    rng = random.Random(SEED)
    results = []
    flagged_counts = []

    for _ in range(TRIALS):
        cost_rates = {
            mode: value * _triangular(rng)
            for mode, value in factors.COST_FACTORS.items()
        }
        emission_rates = {
            mode: value * _triangular(rng)
            for mode, value in factors.EMISSION_FACTORS.items()
        }
        saved, flagged = _rerun(lanes, cost_rates, emission_rates)
        results.append(saved)
        flagged_counts.append(flagged)

    baseline = sum(
        lane["switch"]["saved_cost"] for lane in lanes if lane["flagged"]
    )
    counts = Counter(flagged_counts)

    return {
        "baseline": baseline,
        "p10": percentile(results, 0.10),
        "p50": percentile(results, 0.50),
        "p90": percentile(results, 0.90),
        "worst": min(results),
        "best": max(results),
        "spread": FACTOR_SPREAD,
        "trials": TRIALS,
        "histogram": _histogram(results, baseline),
        "always_flagged": _always_flagged(lanes, rng),
        "typical_flagged": counts.most_common(1)[0][0] if counts else 0,
        "flag_range": (min(flagged_counts), max(flagged_counts))
        if flagged_counts
        else (0, 0),
    }


def _triangular(rng):
    """A multiplier around 1, most likely to be 1 and never outside the range.

    Triangular rather than normal because a normal has no bounds, and a rate
    three times the published one is not a tail worth modelling, it is a
    different lane. Triangular says: probably about right, possibly a third
    out either way, never more.
    """
    return rng.triangular(1 - FACTOR_SPREAD, 1 + FACTOR_SPREAD, 1.0)


def _rerun(lanes, cost_rates, emission_rates):
    """Re-price and re-rank every lane under one draw of the factors."""
    saved = 0.0
    flagged = 0
    for lane in lanes:
        current_cost = _transport_cost(lane, lane["mode"], cost_rates)
        current_co2e = _transport_co2e(lane, lane["mode"], emission_rates)
        best = None
        for mode in scoring.plausible_modes(lane):
            option_cost = _transport_cost(lane, mode, cost_rates)
            option_co2e = _transport_co2e(lane, mode, emission_rates)
            cut_cost = current_cost - option_cost
            cut_co2e = current_co2e - option_co2e
            if cut_cost <= 0 or cut_co2e <= 0:
                continue
            gain = cut_cost / (current_cost or 1.0) + cut_co2e / (current_co2e or 1.0)
            if best is None or gain > best[0]:
                best = (gain, cut_cost, cut_co2e)
        if best is None:
            continue
        # Flagging uses the same gate as the live ranking, against the lane's
        # full cost and carbon, so the band answers the same question the
        # headline does rather than a looser version of it.
        opportunity = min(
            best[1] / (lane["cost"] or 1.0), best[2] / (lane["co2e"] or 1.0)
        )
        if opportunity >= scoring.FLAG_THRESHOLD:
            saved += best[1]
            flagged += 1
    return saved, flagged


def _transport_cost(lane, mode, rates):
    costs = analysis.lane_costs(
        lane["total_weight_kg"],
        lane["distance_km"],
        mode,
        lane["order_count"],
        lane["return_count"],
        rate=rates[factors.normalise_mode(mode)],
    )
    return costs["transport"] + costs["returns"]


def _transport_co2e(lane, mode, rates):
    emissions = analysis.lane_emissions(
        lane["total_weight_kg"],
        lane["distance_km"],
        mode,
        lane["order_count"],
        lane["return_count"],
        rate=rates[factors.normalise_mode(mode)],
    )
    return emissions["transport"] + emissions["returns"]


def _always_flagged(lanes, rng):
    """Lanes that stay worth changing under every draw of the factors.

    These are the recommendations that do not depend on the numbers being
    right, only on the ordering being right, and they are the ones to open a
    conversation with.
    """
    survivors = {
        f"{lane['origin_name']} to {lane['dest_name']}": lane
        for lane in lanes
        if lane["flagged"]
    }
    counts = defaultdict(int)
    checks = 200
    for _ in range(checks):
        cost_rates = {
            mode: value * _triangular(rng)
            for mode, value in factors.COST_FACTORS.items()
        }
        emission_rates = {
            mode: value * _triangular(rng)
            for mode, value in factors.EMISSION_FACTORS.items()
        }
        for name, lane in survivors.items():
            current_cost = _transport_cost(lane, lane["mode"], cost_rates)
            current_co2e = _transport_co2e(lane, lane["mode"], emission_rates)
            for mode in scoring.plausible_modes(lane):
                cut_cost = current_cost - _transport_cost(lane, mode, cost_rates)
                cut_co2e = current_co2e - _transport_co2e(lane, mode, emission_rates)
                if cut_cost <= 0 or cut_co2e <= 0:
                    continue
                if (
                    min(cut_cost / (lane["cost"] or 1.0), cut_co2e / (lane["co2e"] or 1.0))
                    >= scoring.FLAG_THRESHOLD
                ):
                    counts[name] += 1
                    break
    return sorted(
        (
            {
                "name": name,
                "mode": survivors[name]["mode"],
                "switch": survivors[name]["switch"]["mode"],
                "confidence": counts[name] / checks,
            }
            for name in survivors
        ),
        key=lambda item: -item["confidence"],
    )


def outliers(lanes):
    """Lanes that cost or emit unlike everything else per tonne moved.

    Cost per tonne-km strips out size, so a lane cannot appear here just for
    being big. Two things put a lane on this list. Either it runs on an
    expensive mode, which is the answer the rest of the tool is already built
    to act on, or it is short enough that the per-order costs dominate: a
    twenty kilometre delivery carries the same packaging and handling as a
    two thousand kilometre one and has almost no tonne-km to spread it over.
    The distance column is there so the two are told apart at a glance rather
    than a short lane being mistaken for a badly priced one.
    """
    priced = [
        lane
        for lane in lanes
        if lane["total_weight_kg"] > 0 and lane["distance_km"] > 0
    ]
    if len(priced) < 3:
        return {"cost": [], "co2e": [], "threshold": OUTLIER_Z}

    def per_tonne_km(lane, key):
        return lane[key] / ((lane["total_weight_kg"] / 1000.0) * lane["distance_km"])

    cost_rates = [per_tonne_km(lane, "cost") for lane in priced]
    co2e_rates = [per_tonne_km(lane, "co2e") for lane in priced]
    cost_z = robust_z(cost_rates)
    co2e_z = robust_z(co2e_rates)

    def collect(rates, zs):
        found = [
            {
                "name": f"{lane['origin_name']} to {lane['dest_name']}",
                "mode": lane["mode"],
                "rate": rate,
                "z": z,
                "distance_km": lane["distance_km"],
                "orders": lane["order_count"],
            }
            for lane, rate, z in zip(priced, rates, zs)
            if z >= OUTLIER_Z
        ]
        return sorted(found, key=lambda item: -item["z"])[:5]

    return {
        "cost": collect(cost_rates, cost_z),
        "co2e": collect(co2e_rates, co2e_z),
        "cost_median": median(cost_rates),
        "co2e_median": median(co2e_rates),
        "threshold": OUTLIER_Z,
    }


def order_sizes(orders):
    """The shape of the order book, and what the small end of it costs.

    Packaging and handling are charged per order rather than per kilo, so a
    long tail of tiny orders is expensive in a way that never shows up in a
    cost-per-tonne report. This puts a number on it.
    """
    weights = [order["weight_kg"] for order in orders if order["weight_kg"]]
    if not weights:
        return None

    p25 = percentile(weights, 0.25)
    p50 = percentile(weights, 0.50)
    p75 = percentile(weights, 0.75)
    p90 = percentile(weights, 0.90)
    small = [weight for weight in weights if weight <= p25]

    return {
        "count": len(weights),
        "p10": percentile(weights, 0.10),
        "p25": p25,
        "median": p50,
        "p75": p75,
        "p90": p90,
        "max": max(weights),
        "iqr": p75 - p25,
        # Mean over median. Above about 1.3 the book has a heavy top end,
        # which is the usual signature of a few bulk orders sitting on top of
        # a lot of small ones.
        "tail_ratio": (sum(weights) / len(weights)) / p50 if p50 else 0.0,
        "small_share_of_orders": len(small) / len(weights),
        "small_share_of_weight": sum(small) / (sum(weights) or 1.0),
        "small_packaging_cost": len(small) * factors.PACKAGING_COST_PER_ORDER,
        "histogram": _weight_histogram(weights),
    }


def seasonality(orders):
    """Whether the year is flat or peaked, which decides whether the annual
    figures in the rest of the tool describe any actual month."""
    names = (
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    )
    months = defaultdict(lambda: {"orders": 0, "weight": 0.0})
    for order in orders:
        date = order["order_date"]
        if not date or len(str(date)) < 7:
            continue
        months[str(date)[:7]]["orders"] += 1
        months[str(date)[:7]]["weight"] += order["weight_kg"] or 0.0

    if len(months) < 3:
        return None

    series = sorted(months.items())
    counts = [item[1]["orders"] for item in series]
    mean = sum(counts) / len(counts)
    variance = sum((count - mean) ** 2 for count in counts) / len(counts)
    cv = math.sqrt(variance) / mean if mean else 0.0
    peak = max(series, key=lambda item: item[1]["orders"])
    trough = min(series, key=lambda item: item[1]["orders"])
    top = max(counts) or 1

    return {
        "months": [
            {
                "label": _month_label(label, names),
                "orders": data["orders"],
                "weight": data["weight"],
                "height": data["orders"] / top,
            }
            for label, data in series
        ],
        "cv": cv,
        "peak": {"label": _month_label(peak[0], names), "orders": peak[1]["orders"]},
        "trough": {
            "label": _month_label(trough[0], names),
            "orders": trough[1]["orders"],
        },
        "peak_to_trough": (
            peak[1]["orders"] / trough[1]["orders"]
            if trough[1]["orders"]
            else None
        ),
        "flat": cv < 0.15,
    }


def _month_label(key, names):
    """2025-09 reads as a product code. Sep 2025 reads as a month."""
    try:
        year, month = key.split("-")
        return f"{names[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return key


def returns_by_lane(lanes):
    """Return rates with an interval, so a lane is only called bad when the
    data is strong enough to say so.

    One return out of four is a 25 percent rate and means nothing. Reporting
    it next to a genuine 12 percent measured over four hundred orders would
    put the noisy lane at the top of the list, which is how return rates
    usually get read wrong.
    """
    total_orders = sum(lane["order_count"] for lane in lanes)
    total_returns = sum(lane["return_count"] for lane in lanes)
    if not total_orders:
        return None
    network_rate = total_returns / total_orders

    rows = []
    for lane in lanes:
        if lane["order_count"] < MIN_RETURNS_SAMPLE:
            continue
        rate, low, high = wilson(lane["return_count"], lane["order_count"])
        rows.append(
            {
                "name": f"{lane['origin_name']} to {lane['dest_name']}",
                "orders": lane["order_count"],
                "returns": lane["return_count"],
                "rate": rate,
                "low": low,
                "high": high,
                # Only a lane whose whole interval sits above the network rate
                # is genuinely worse than the network, rather than luckier or
                # unluckier than average.
                "worse": low > network_rate,
            }
        )
    rows.sort(key=lambda row: -row["rate"])

    return {
        "network_rate": network_rate,
        "network_orders": total_orders,
        "rows": rows[:10],
        "worse_count": sum(1 for row in rows if row["worse"]),
        "min_sample": MIN_RETURNS_SAMPLE,
        "tested": len(rows),
        "skipped": sum(
            1 for lane in lanes if lane["order_count"] < MIN_RETURNS_SAMPLE
        ),
    }


def by_mode(lanes):
    """Cost and carbon per tonne-km actually achieved by mode, next to the
    published factor. They differ because returns and packaging ride along."""
    groups = defaultdict(
        lambda: {"lanes": 0, "tonne_km": 0.0, "cost": 0.0, "co2e": 0.0}
    )
    for lane in lanes:
        tonne_km = (lane["total_weight_kg"] / 1000.0) * lane["distance_km"]
        row = groups[lane["mode"]]
        row["lanes"] += 1
        row["tonne_km"] += tonne_km
        row["cost"] += lane["cost"]
        row["co2e"] += lane["co2e"]

    total_tonne_km = sum(row["tonne_km"] for row in groups.values()) or 1.0
    total_cost = sum(row["cost"] for row in groups.values()) or 1.0
    total_co2e = sum(row["co2e"] for row in groups.values()) or 1.0

    return sorted(
        (
            {
                "mode": mode,
                "lanes": row["lanes"],
                "tonne_km": row["tonne_km"],
                "tonne_km_share": row["tonne_km"] / total_tonne_km,
                "cost_share": row["cost"] / total_cost,
                "co2e_share": row["co2e"] / total_co2e,
                "cost_rate": row["cost"] / (row["tonne_km"] or 1.0),
                "co2e_rate": row["co2e"] / (row["tonne_km"] or 1.0),
                "published_cost": factors.COST_FACTORS[mode],
                "published_co2e": factors.EMISSION_FACTORS[mode],
            }
            for mode, row in groups.items()
        ),
        key=lambda row: -row["co2e_share"],
    )


# ------------------------------------------------------------------- drawing
# Geometry is worked out here and handed to the template as plain strings, so
# the page draws its own SVG and pulls in nothing from a CDN to do it. The
# viewbox is 100 by 100 in every case and the stylesheet does the scaling.


def _svg_curve(points):
    return " ".join(f"{x * 100:.2f},{100 - y * 100:.2f}" for x, y in points)


def _svg_scatter(lanes):
    """Cost against carbon per lane, both on a log scale.

    Log because lane sizes span three or four orders of magnitude and a linear
    plot would be one dot in the corner and everything else against the axis.
    """
    points = [
        (lane["cost"], lane["co2e"], lane)
        for lane in lanes
        if lane["cost"] > 0 and lane["co2e"] > 0
    ]
    if len(points) < 2:
        return []

    xs = [math.log10(cost) for cost, _, _ in points]
    ys = [math.log10(co2e) for _, co2e, _ in points]
    x_low, x_high = min(xs), max(xs)
    y_low, y_high = min(ys), max(ys)
    x_span = (x_high - x_low) or 1.0
    y_span = (y_high - y_low) or 1.0

    return [
        {
            "x": 6 + 88 * (x - x_low) / x_span,
            "y": 94 - 88 * (y - y_low) / y_span,
            "flagged": lane["flagged"],
            "mode": lane["mode"],
            "name": f"{lane['origin_name']} to {lane['dest_name']}",
            "cost": lane["cost"],
            "co2e": lane["co2e"],
        }
        for x, y, (_, _, lane) in zip(xs, ys, points)
    ]


def _histogram(values, marker, bins=28):
    low, high = min(values), max(values)
    if high <= low:
        return {"bars": [], "low": low, "high": high, "marker": 0.0}
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1
    tallest = max(counts) or 1
    return {
        "bars": [
            {
                "x": index * (100 / bins),
                "width": 100 / bins,
                "height": count / tallest,
                "from": low + index * width,
                "to": low + (index + 1) * width,
            }
            for index, count in enumerate(counts)
        ],
        "low": low,
        "high": high,
        "marker": max(0.0, min(100.0, 100 * (marker - low) / (high - low))),
    }


def _weight_histogram(weights, bins=24):
    """Order weights on a log scale, because order books are lognormal enough
    that a linear histogram is one tall bar and a lot of empty paper."""
    positive = [weight for weight in weights if weight > 0]
    if len(positive) < 2:
        return {"bars": []}
    logs = [math.log10(weight) for weight in positive]
    low, high = min(logs), max(logs)
    if high <= low:
        return {"bars": []}
    width = (high - low) / bins
    counts = [0] * bins
    for value in logs:
        counts[min(bins - 1, int((value - low) / width))] += 1
    tallest = max(counts) or 1
    return {
        "bars": [
            {
                "x": index * (100 / bins),
                "width": 100 / bins,
                "height": count / tallest,
                "from": 10 ** (low + index * width),
                "to": 10 ** (low + (index + 1) * width),
                "count": count,
            }
            for index, count in enumerate(counts)
        ],
        "low": 10**low,
        "high": 10**high,
    }
