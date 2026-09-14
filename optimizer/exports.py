"""The spreadsheet exports.

Two files take the findings out of the tool. The CSV is plain data, one row
per opportunity, for whatever system reads it next. The workbook is the one
meant to be opened by a person: a summary sheet first, then every opportunity
and every route laid out with the unit in each heading, number formats, frozen
headings, filters and totals, then the data check and the assumptions the
figures rest on. Both are built from the same rows, so they cannot disagree.

No cost or carbon figure is worked out here. Every one comes from the engine.
What this adds is layout, one carbon unit for the file, and the few ratios a
reader would otherwise type in themselves (shares, per tonne, return rates),
which are written as formulas beside the figures they come from.
"""

import csv
import io
import math
from collections import namedtuple

from . import analysis, diagnosis, distance, factors, scoring, stats, xlsx

Column = namedtuple("Column", "key heading kind width group")

EFFORT = {"low": "Easy", "med": "Medium", "high": "Hard"}

KIND = {
    "mode_switch": "Route mode change",
    "supplier_on_time": "Supplier terms",
    "warehouse_grid": "Warehouse electricity",
    "cost_to_serve": "Serving site",
    "returns": "Returns",
}

NUMERIC = {"int", "usd", "usd2", "carbon", "pct", "days", "km", "tonnes", "rate", "dec2", "dec3"}

# Decimal places when a figure is written to the CSV. A CSV has no number
# formats, so the rounding is the formatting.
CSV_PLACES = {
    "int": 0,
    "usd": 0,
    "usd2": 2,
    "pct": 1,
    "days": 0,
    "km": 0,
    "tonnes": 2,
    "rate": 3,
    "dec2": 2,
    "dec3": 3,
}

# The product palette, darkened where it has to carry text on white. Orange
# is cost and green is carbon here as everywhere else.
INK = "16150F"
INK_2 = "3D3A33"
INK_3 = "5E594F"
LINE_SOFT = "E4E0D8"
DARK = "121110"
IVORY = "F3F0EA"
COST = "F2913D"
CARBON = "3FCF8E"
COST_INK = "9C5518"
CARBON_INK = "0F6B47"
DANGER = "8F2F23"
FLAG_FILL = "FBEFE3"

GROUP_TONE = {"Cost": "band_cost", "CO2e": "band_carbon"}


# ------------------------------------------------------------------ units


def carbon_unit(total_kg):
    """One carbon unit for the whole file, chosen by the size of the network.

    A spreadsheet column holds one unit. Tonnes suit a network whose
    kilograms run to seven digits; kilograms suit a small one, where tonnes
    would round a route's saving down to nothing. The choice is made once and
    named in every heading."""
    if total_kg >= 100_000:
        return {"label": "t", "divisor": 1000.0, "places": 1, "format": "#,##0.0"}
    return {"label": "kg", "divisor": 1.0, "places": 0, "format": "#,##0"}


def _carbon(kg, unit):
    return None if kg is None else kg / unit["divisor"]


def co2e_text(kg):
    """Carbon in prose, the way the report prints it."""
    kg = kg or 0.0
    if kg < 1000:
        return f"{kg:,.0f} kg"
    if kg < 1_000_000:
        return f"{kg / 1000:,.1f} t"
    return f"{kg / 1000:,.0f} t"


def _plural(count, one, many=None):
    return one if count == 1 else (many or one + "s")


def _share(part, whole):
    return part / whole if whole else None


# ---------------------------------------------------------- opportunities


def opportunity_columns(unit):
    c = unit["label"]
    return [
        Column("rank", "Rank", "int", 6, "Opportunity"),
        Column("title", "Opportunity", "text", 34, "Opportunity"),
        Column("action", "What to do", "long", 38, "Opportunity"),
        Column("type", "Type", "text", 20, "Opportunity"),
        Column("stage", "Stage", "text", 16, "Opportunity"),
        Column("origin", "From", "text", 24, "Opportunity"),
        Column("dest", "To", "text", 22, "Opportunity"),
        Column("cost_saving", "Cost saving (USD a year)", "usd", 13, "Cost"),
        Column("cost_share", "Share of chain cost", "pct", 11, "Cost"),
        Column("cost_cut", "Cost cut on this route", "pct", 11, "Cost"),
        Column("co2e_saving", f"CO2e avoided ({c} a year)", "carbon", 13, "CO2e"),
        Column("co2e_share", "Share of chain CO2e", "pct", 11, "CO2e"),
        Column("co2e_cut", "CO2e cut on this route", "pct", 11, "CO2e"),
        Column("confidence", "Confidence", "text", 13, "How sure"),
        Column("held", "Simulations where it still paid", "pct", 13, "How sure"),
        Column("mode_now", "Current mode", "text", 10, "Current and proposed"),
        Column("mode_new", "Proposed mode", "text", 10, "Current and proposed"),
        Column("days_now", "Transit now (days)", "days", 10, "Current and proposed"),
        Column("days_new", "Transit after (days)", "days", 10, "Current and proposed"),
        Column("days_added", "Extra days in transit", "days", 10, "Current and proposed"),
        Column("cost_now", "Current cost (USD a year)", "usd", 13, "Current and proposed"),
        Column("cost_new", "Proposed cost (USD a year)", "usd", 13, "Current and proposed"),
        Column("co2e_now", f"Current CO2e ({c} a year)", "carbon", 13, "Current and proposed"),
        Column("co2e_new", f"Proposed CO2e ({c} a year)", "carbon", 13, "Current and proposed"),
        Column("weight", "Weight (t a year)", "tonnes", 11, "Route"),
        Column("orders", "Orders a year", "int", 9, "Route"),
        Column("km_now", "Current distance (km)", "km", 11, "Route"),
        Column("km_new", "Proposed distance (km)", "km", 11, "Route"),
        Column("saving_per_t", "Saving per tonne shipped (USD)", "usd2", 12, "Route"),
        Column("checks", "Check before acting", "long", 50, "Before acting"),
        Column("alternatives", "If that is not possible", "long", 36, "Before acting"),
        Column("effort", "Effort", "text", 9, "Before acting"),
    ]


def opportunity_rows(report, unit):
    """One record per opportunity, in report order. A figure the engine does
    not estimate is left empty rather than written as zero."""
    rows = []
    for rank, problem in enumerate(report["problems"] if report else [], start=1):
        route = problem["route"] or {}
        now = route.get("now") or {}
        new = route.get("proposed") or {}
        # A serving-site change carries a sentence saying its carbon is not
        # counted. An empty cell says the same; a zero would claim it is none.
        counted = problem["co2e_line"] is None
        weight_t = route.get("weight_t")
        days_now = round(now["days"]) if route else None
        days_new = round(new["days"]) if route else None
        rows.append(
            {
                "rank": rank,
                "title": problem["title"],
                "action": problem["action"],
                "type": KIND.get(problem["kind"], problem["stage"]),
                "stage": problem["stage"],
                "origin": route.get("origin"),
                "dest": route.get("dest"),
                "cost_saving": problem["cost_at_stake"] or 0.0,
                "cost_share": problem["cost_share"],
                "cost_cut": route.get("cost_pct"),
                "co2e_saving": _carbon(problem["co2e_at_stake"] or 0.0, unit) if counted else None,
                "co2e_share": problem["co2e_share"] if counted else None,
                "co2e_cut": route.get("co2e_pct"),
                "confidence": (problem["confidence_label"] or "not simulated").capitalize(),
                "held": problem["confidence"],
                "mode_now": now["mode"].title() if route else None,
                "mode_new": new["mode"].title() if route else None,
                "days_now": days_now,
                "days_new": days_new,
                "days_added": days_new - days_now if route else None,
                "cost_now": now.get("cost"),
                "cost_new": new.get("cost"),
                "co2e_now": _carbon(now.get("co2e"), unit),
                "co2e_new": _carbon(new.get("co2e"), unit),
                "weight": weight_t,
                "orders": route.get("orders"),
                "km_now": now.get("route_km"),
                "km_new": new.get("route_km"),
                "saving_per_t": (
                    (problem["cost_at_stake"] or 0.0) / weight_t if weight_t else None
                ),
                "checks": list(problem["checks"]),
                "alternatives": list(problem["alternatives"]),
                "effort": EFFORT.get(problem["effort"]),
            }
        )
    return rows


# -------------------------------------------------------------------- CSV


def _csv_text(value):
    """Text a spreadsheet will not run as a formula. A cell starting with one
    of these is read as a formula when the CSV is opened, and uploaded names
    end up in these cells."""
    text = str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _csv_value(value, kind, unit):
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        return "; ".join(_csv_text(item) for item in value)
    if kind in NUMERIC and isinstance(value, (int, float)):
        places = unit["places"] if kind == "carbon" else CSV_PLACES[kind]
        if kind == "pct":
            value = value * 100
        number = round(value, places)
        return int(number) if places == 0 else number
    return _csv_text(value)


def _csv_heading(column):
    return column.heading + " (%)" if column.kind == "pct" else column.heading


def opportunities_csv(context):
    """Every opportunity, one row each, one heading row. The unit is in every
    heading and every figure is a bare number, so the columns sort and sum.
    The byte order mark is what makes Excel read the file as UTF-8, so a city
    like Malmo with its accent arrives intact."""
    unit = carbon_unit(context["totals"]["co2e"])
    columns = opportunity_columns(unit)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([_csv_heading(column) for column in columns])
    for row in opportunity_rows(context["report"], unit):
        writer.writerow(
            [_csv_value(row[column.key], column.kind, unit) for column in columns]
        )
    return "﻿" + buffer.getvalue()


# --------------------------------------------------------------- workbook


def workbook(context):
    """The findings as a workbook, bytes ready to send."""
    report = context["report"]
    unit = carbon_unit(context["totals"]["co2e"])
    book = xlsx.Workbook(
        title=f"Overlap, {context['subject']}, {context['generated']:%d %B %Y}",
        author="Overlap",
    )
    _styles(book, unit)

    has_opportunities = bool(report and report["problems"])
    contents = [("Summary", "The short answer, where cost and carbon come from, and the top opportunities.")]
    if has_opportunities:
        contents.append(("Opportunities", "Every opportunity, ranked, with what to do, what it is worth and what to check."))
    contents.append(("Routes", "Every route in the network with its cost and CO2e by driver, and whether it is worth changing."))
    contents.append(("Data check", "What the file turned into, and any row that did not make it into the figures."))
    contents.append(("Assumptions", "The factors, rules and method every figure rests on."))

    _summary_sheet(book, context, unit, contents)
    if has_opportunities:
        _opportunities_sheet(book, context, unit)
    _routes_sheet(book, context, unit)
    _data_sheet(book, context)
    _assumptions_sheet(book, context)
    return book.to_bytes()


def _styles(book, unit):
    add = book.add_style
    soft = ("thin", LINE_SOFT)
    total_top = ("thin", DARK)

    add("title", bold=True, size=16, vertical="center")
    add("subtitle", size=10, color=INK_3)
    add("section", bold=True, size=11, bottom=("medium", DARK), vertical="bottom")
    add("section_rule", bottom=("medium", DARK))
    add("headline", bold=True, size=13, wrap=True)
    add("prose", size=10, color=INK_2, wrap=True)
    add("note", size=9, italic=True, color=INK_3, wrap=True)
    add("flag", bold=True, size=10, color=DANGER, fill=FLAG_FILL, wrap=True, vertical="center", indent=1)

    add("band", bold=True, size=9, color=IVORY, fill=DARK, vertical="center", indent=1)
    add("band_cost", bold=True, size=9, color=DARK, fill=COST, vertical="center", indent=1)
    add("band_carbon", bold=True, size=9, color=DARK, fill=CARBON, vertical="center", indent=1)
    add("head", bold=True, size=9, fill=IVORY, wrap=True, vertical="bottom", bottom=("medium", DARK))
    add("head_num", bold=True, size=9, fill=IVORY, wrap=True, vertical="bottom", horizontal="right", bottom=("medium", DARK))

    add("cell", bottom=soft)
    add("cell_bold", bold=True, bottom=soft)
    add("cell_wrap", wrap=True, bottom=soft)
    add("cell_right", horizontal="right", bottom=soft)
    add("label", color=INK_2, wrap=True, bottom=soft)
    add("bad", bold=True, color=DANGER, bottom=soft)
    add("t_label", bold=True, fill=IVORY, top=total_top)
    add("t_blank", fill=IVORY, top=total_top)

    formats = {
        "int": "#,##0",
        "usd": "$#,##0",
        "usd2": "$#,##0.00",
        "carbon": unit["format"],
        "pct": "0.0%",
        "days": "0",
        "km": "#,##0",
        "tonnes": "#,##0.00",
        "rate": "0.000",
        "dec2": "0.00",
        "dec3": "0.000",
    }
    for kind, code in formats.items():
        add(f"n_{kind}", number=code, horizontal="right", bottom=soft)
        add(f"n_{kind}_bold", number=code, bold=True, horizontal="right", bottom=soft)
        add(f"t_{kind}", number=code, bold=True, horizontal="right", fill=IVORY, top=total_top)
    add("n_cost", number=formats["usd"], bold=True, color=COST_INK, horizontal="right", bottom=soft)
    add("n_carbon_lit", number=formats["carbon"], bold=True, color=CARBON_INK, horizontal="right", bottom=soft)
    add("t_cost", number=formats["usd"], bold=True, color=COST_INK, horizontal="right", fill=IVORY, top=total_top)
    add("t_carbon_lit", number=formats["carbon"], bold=True, color=CARBON_INK, horizontal="right", fill=IVORY, top=total_top)
    add("big_cost", number=formats["usd"], bold=True, size=13, color=COST_INK, horizontal="right", bottom=soft)
    add("big_carbon", number=formats["carbon"], bold=True, size=13, color=CARBON_INK, horizontal="right", bottom=soft)


# Columns whose figure is the point of the row, printed in the colour of what
# they measure.
LIT = {"cost_saving": "n_cost", "saved_cost": "n_cost", "co2e_saving": "n_carbon_lit", "saved_co2e": "n_carbon_lit"}
BOLD = {"cost", "co2e", "title"}


def _body_style(column):
    if column.key in LIT:
        return LIT[column.key]
    if column.kind in NUMERIC:
        return f"n_{column.kind}_bold" if column.key in BOLD else f"n_{column.kind}"
    if column.kind == "long":
        return "cell_wrap"
    return "cell_bold" if column.key in BOLD else "cell"


def _lines(text, width):
    """Roughly how many lines a wrapped cell needs. Excel does not size a row
    to wrapped text in a file it did not write itself, so the row height is
    set from this."""
    per_line = max(int(width * 1.2) - 1, 8)
    return sum(
        max(1, math.ceil(len(part) / per_line)) for part in str(text).split("\n")
    )


def _row_height(lines):
    return round(max(1, lines) * 13 + 4, 1)


def _section(sheet, row, text, last_col):
    sheet.write(row, 1, text, "section")
    for col in range(2, last_col + 1):
        sheet.write(row, col, None, "section_rule")
    sheet.height(row, 22)


def _band(sheet, row, columns):
    start = 1
    for index in range(1, len(columns) + 1):
        column = columns[index - 1]
        ends = index == len(columns) or columns[index].group != column.group
        if not ends:
            continue
        style = GROUP_TONE.get(column.group, "band")
        sheet.write(row, start, column.group, style)
        for col in range(start + 1, index + 1):
            sheet.write(row, col, None, style)
        sheet.merge(row, start, row, index)
        start = index + 1
    sheet.height(row, 18)


def _table(sheet, top, columns, records, formulas=None, band=True):
    """A band of column groups, a heading row, and one row per record.

    `formulas` maps a column key to a function of (row, ref, record, value)
    that returns the cell to write, which is how a figure gets its formula.
    `ref(key, row)` gives a cell address in this table. Returns the heading
    row and the last row written."""
    formulas = formulas or {}
    at = {column.key: index for index, column in enumerate(columns, start=1)}

    def ref(key, row, absolute=False):
        return xlsx.cell_ref(row, at[key], absolute)

    head = top
    if band:
        _band(sheet, top, columns)
        head = top + 1

    heading_lines = 1
    for index, column in enumerate(columns, start=1):
        sheet.write(head, index, column.heading, "head_num" if column.kind in NUMERIC else "head")
        sheet.width(index, column.width)
        heading_lines = max(heading_lines, _lines(column.heading, column.width))
    sheet.height(head, _row_height(heading_lines))

    row = head
    for record in records:
        row += 1
        lines = 1
        for index, column in enumerate(columns, start=1):
            value = record.get(column.key)
            if isinstance(value, list):
                value = "\n".join("• " + item for item in value) if value else None
            if value is not None and column.key in formulas:
                value = formulas[column.key](row, ref, record, value)
            if column.kind == "long" and isinstance(value, str):
                lines = max(lines, _lines(value, column.width))
            sheet.write(row, index, value, _body_style(column))
        if lines > 1:
            sheet.height(row, _row_height(lines))
    return head, row, ref


def _merged_text(sheet, row, text, style, last_col, width):
    sheet.write(row, 1, text, style)
    sheet.merge(row, 1, row, last_col)
    lines = _lines(text, width)
    if lines > 1:
        sheet.height(row, _row_height(lines))


def _notes(sheet, row, notes, last_col, width):
    for note in notes:
        row += 1
        _merged_text(sheet, row, note, "note", last_col, width)
    return row


def _sample_line(context):
    return (
        f"Sample data. {context['subject']} is not a real company. "
        "Its data is generated to follow realistic patterns."
    )


def _date_window(summary):
    first, last = summary.get("first_order"), summary.get("last_order")
    if first and last:
        return (
            f"Orders dated {first} to {last}. Every figure treats the loaded "
            "file as one year of shipping."
        )
    return (
        "The file carries no order dates. Every figure treats the loaded file "
        "as one year of shipping."
    )


def _title_block(sheet, context, title, subtitle, last_col, width):
    sheet.write(1, 1, title, "title")
    sheet.height(1, 26)
    _merged_text(sheet, 2, subtitle, "subtitle", last_col, width)
    row = 2
    if context["sample"]:
        row += 1
        sheet.write(row, 1, _sample_line(context), "flag")
        for col in range(2, last_col + 1):
            sheet.write(row, col, None, "flag")
        sheet.merge(row, 1, row, last_col)
        sheet.height(row, 20)
    return row


# ---------------------------------------------------------------- summary


def _summary_sheet(book, context, unit, contents):
    report = context["report"]
    totals = context["totals"]
    summary = context["summary"]
    ingest = context["ingest"] or {}
    problems = report["problems"] if report else []
    ov = report["overview"] if report else None
    c = unit["label"]

    sheet = book.add_sheet("Summary")
    sheet.gridlines = False
    widths = [40, 16, 16, 16, 16, 16, 16, 16]
    for col, width in enumerate(widths, start=1):
        sheet.width(col, width)
    last = len(widths)
    full = sum(widths)

    row = _title_block(
        sheet,
        context,
        "Overlap: cost and carbon screening",
        f"{context['subject']} · {summary['orders']:,} orders · "
        f"{summary['edges']:,} routes · generated {context['generated']:%d %B %Y}",
        last,
        full,
    )

    row += 2
    _section(sheet, row, "The short answer", last)
    row += 1
    if problems:
        headline = (
            f"{len(problems)} {_plural(len(problems), 'opportunity', 'opportunities')} "
            f"worth {diagnosis.money(ov['recoverable_cost'])} and "
            f"{co2e_text(ov['recoverable_co2e'])} CO2e a year"
        )
    else:
        headline = "No change found that cuts both cost and carbon"
    _merged_text(sheet, row, headline, "headline", last, full * 0.8)
    sheet.height(row, 22)
    lead = ov["concentration"] if problems else (ov["truth"] if ov else "")
    if lead:
        row += 1
        _merged_text(sheet, row, lead, "prose", last, full)
    row += 1
    _merged_text(sheet, row, _date_window(summary), "prose", last, full)

    # The key figures. Shares are formulas on the figures above them.
    row += 2
    sheet.write_row(row, 1, ["Measure"], "head")
    sheet.write_row(row, 2, ["Value", "Unit"], "head_num")
    sheet.write(row, 4, "Share of chain", "head_num")
    sheet.write(row, 5, "Notes", "head")
    for col in range(6, last + 1):
        sheet.write(row, col, None, "head")
    sheet.merge(row, 5, row, last)

    def key_row(label, value, value_style, unit_text="", share=None, note=""):
        nonlocal row
        row += 1
        sheet.write(row, 1, label, "label")
        sheet.write(row, 2, value, value_style)
        sheet.write(row, 3, unit_text, "cell_right")
        sheet.write(row, 4, share, "n_pct")
        sheet.write(row, 5, note or None, "cell_wrap")
        for col in range(6, last + 1):
            sheet.write(row, col, None, "cell")
        sheet.merge(row, 5, row, last)
        note_width = sum(widths[4:])
        if note and _lines(note, note_width) > 1:
            sheet.height(row, _row_height(_lines(note, note_width)))
        return row

    cost_row = key_row("Chain cost", totals["cost"], "n_usd_bold", "USD a year", note="Transport, warehouse and packaging, and returns.")
    co2e_row = key_row("Chain CO2e", _carbon(totals["co2e"], unit), "n_carbon_bold", f"{c} CO2e a year", note="Transport, packaging, warehouse energy and return legs.")
    if problems:
        saving = key_row(
            "Cost saving found",
            ov["recoverable_cost"],
            "big_cost",
            "USD a year",
        )
        sheet.write(saving, 4, xlsx.Formula(f"IF(B{cost_row}=0,\"\",B{saving}/B{cost_row})", ov["cost_share"]), "n_pct_bold")
        avoided = key_row(
            "CO2e avoided",
            _carbon(ov["recoverable_co2e"], unit),
            "big_carbon",
            f"{c} CO2e a year",
        )
        sheet.write(avoided, 4, xlsx.Formula(f"IF(B{co2e_row}=0,\"\",B{avoided}/B{co2e_row})", ov["co2e_share"]), "n_pct_bold")
        others = len(problems) - ov["route_changes"]
        key_row(
            "Opportunities",
            len(problems),
            "n_int_bold",
            note=(
                f"{ov['route_changes']} {_plural(ov['route_changes'], 'route mode change')}"
                + (f", {others} {_plural(others, 'other change')}" if others else "")
                + "."
            ),
        )
        sure = report["confidence"]
        if sure["tested"]:
            note = (
                f"The weakest route change still paid in {sure['lowest']:.0%} of "
                f"{stats.CONFIDENCE_CHECKS} simulations with every factor redrawn."
            )
            if sure["untested"]:
                note += f" {sure['untested']} other {_plural(sure['untested'], 'change rests', 'changes rest')} on stated assumptions and {_plural(sure['untested'], 'was', 'were')} not simulated."
            key_row("Confidence", (sure["level"] or "").capitalize(), "cell_right", note=note)
        else:
            key_row("Confidence", "Not simulated", "cell_right", note="Only route mode changes are simulated, and there are none here. Each change rests on the assumptions listed with it.")
        band = context.get("uncertainty")
        if band:
            key_row(
                "Route savings, likely range",
                f"${band['p10']:,.0f} to ${band['p90']:,.0f}",
                "cell_right",
                "USD a year",
                note=(
                    f"10th to 90th percentile of {band['trials']:,} runs with every cost and "
                    f"emission factor redrawn up to {band['spread']:.0%} either side."
                ),
            )
    key_row("Routes analysed", summary["edges"], "n_int")
    if ingest:
        excluded = ingest.get("rows_skipped") or 0
        key_row(
            "Orders used",
            ingest.get("orders_loaded"),
            "n_int",
            note=(
                f"Of {ingest.get('rows_in_file', 0):,} rows in the file"
                + (f"; {excluded:,} excluded, listed on the Data check sheet." if excluded else "; every row was used.")
            ),
        )

    # Where the money and the carbon come from.
    drivers = [
        ("Transport", totals["transport_cost"], totals["transport_co2e"]),
        ("Warehouse and packaging", totals["handling_cost"], totals["warehouse_co2e"] + totals["packaging_co2e"]),
    ]
    if totals["returns_cost"] or totals["returns_co2e"]:
        drivers.append(("Returns", totals["returns_cost"], totals["returns_co2e"]))

    row += 2
    _section(sheet, row, "Where the cost and carbon come from", last)
    row += 1
    sheet.write(row, 1, "Driver", "head")
    sheet.write_row(row, 2, ["Cost (USD a year)", "Share of cost", f"CO2e ({c} a year)", "Share of CO2e"], "head_num")
    first = row + 1
    total_row = first + len(drivers)
    for name, cost, co2e in drivers:
        row += 1
        sheet.write(row, 1, name, "label")
        sheet.write(row, 2, cost, "n_usd")
        sheet.write(row, 3, xlsx.Formula(f"IF($B${total_row}=0,\"\",B{row}/$B${total_row})", _share(cost, totals["cost"])), "n_pct")
        sheet.write(row, 4, _carbon(co2e, unit), "n_carbon")
        sheet.write(row, 5, xlsx.Formula(f"IF($D${total_row}=0,\"\",D{row}/$D${total_row})", _share(co2e, totals["co2e"])), "n_pct")
    row += 1
    sheet.write(row, 1, "Whole chain", "t_label")
    sheet.write(row, 2, xlsx.Formula(f"SUM(B{first}:B{row - 1})", sum(d[1] for d in drivers)), "t_usd")
    sheet.write(row, 3, xlsx.Formula(f"SUM(C{first}:C{row - 1})", 1.0), "t_pct")
    sheet.write(row, 4, xlsx.Formula(f"SUM(D{first}:D{row - 1})", _carbon(sum(d[2] for d in drivers), unit)), "t_carbon")
    sheet.write(row, 5, xlsx.Formula(f"SUM(E{first}:E{row - 1})", 1.0), "t_pct")

    # By transport mode, only when there is more than one to compare.
    modes = {}
    for lane in context["lanes"]:
        entry = modes.setdefault(lane["mode"], {"routes": 0, "tonne_km": 0.0, "cost": 0.0, "co2e": 0.0})
        entry["routes"] += 1
        entry["tonne_km"] += analysis.tonne_km(lane["total_weight_kg"], lane["distance_km"], lane["mode"])
        entry["cost"] += lane["cost"]
        entry["co2e"] += lane["co2e"]
    if len(modes) > 1:
        ordered = sorted(modes.items(), key=lambda item: -item[1]["co2e"])
        all_tkm = sum(m["tonne_km"] for _, m in ordered)
        all_cost = sum(m["cost"] for _, m in ordered)
        all_co2e = sum(m["co2e"] for _, m in ordered)
        row += 2
        _section(sheet, row, "By transport mode", last)
        row += 1
        sheet.write(row, 1, "Mode", "head")
        sheet.write_row(
            row,
            2,
            ["Routes", "Tonne-km", "Share of tonne-km", "Cost (USD a year)", "Share of cost", f"CO2e ({c} a year)", "Share of CO2e"],
            "head_num",
        )
        first = row + 1
        total_row = first + len(ordered)
        for mode, entry in ordered:
            row += 1
            sheet.write(row, 1, mode.title(), "label")
            sheet.write(row, 2, entry["routes"], "n_int")
            sheet.write(row, 3, entry["tonne_km"], "n_int")
            sheet.write(row, 4, xlsx.Formula(f"IF($C${total_row}=0,\"\",C{row}/$C${total_row})", _share(entry["tonne_km"], all_tkm)), "n_pct")
            sheet.write(row, 5, entry["cost"], "n_usd")
            sheet.write(row, 6, xlsx.Formula(f"IF($E${total_row}=0,\"\",E{row}/$E${total_row})", _share(entry["cost"], all_cost)), "n_pct")
            sheet.write(row, 7, _carbon(entry["co2e"], unit), "n_carbon")
            sheet.write(row, 8, xlsx.Formula(f"IF($G${total_row}=0,\"\",G{row}/$G${total_row})", _share(entry["co2e"], all_co2e)), "n_pct")
        row += 1
        sheet.write(row, 1, "All modes", "t_label")
        sheet.write(row, 2, xlsx.Formula(f"SUM(B{first}:B{row - 1})", sum(m["routes"] for _, m in ordered)), "t_int")
        sheet.write(row, 3, xlsx.Formula(f"SUM(C{first}:C{row - 1})", all_tkm), "t_int")
        sheet.write(row, 4, xlsx.Formula(f"SUM(D{first}:D{row - 1})", 1.0), "t_pct")
        sheet.write(row, 5, xlsx.Formula(f"SUM(E{first}:E{row - 1})", all_cost), "t_usd")
        sheet.write(row, 6, xlsx.Formula(f"SUM(F{first}:F{row - 1})", 1.0), "t_pct")
        sheet.write(row, 7, xlsx.Formula(f"SUM(G{first}:G{row - 1})", _carbon(all_co2e, unit)), "t_carbon")
        sheet.write(row, 8, xlsx.Formula(f"SUM(H{first}:H{row - 1})", 1.0), "t_pct")
        row = _notes(
            sheet,
            row,
            ["A mode carrying a small share of tonne-km and a large share of CO2e is where a mode change pays most."],
            last,
            full,
        )

    if problems:
        top = problems[:3]
        row += 2
        _section(sheet, row, "Top opportunities" if len(problems) > len(top) else "The opportunities", last)
        row += 1
        sheet.write(row, 1, "Opportunity", "head")
        sheet.write(row, 2, "What to do", "head")
        for col in (3, 4, 5):
            sheet.write(row, col, None, "head")
        sheet.merge(row, 2, row, 5)
        sheet.write_row(row, 6, ["Cost saving (USD a year)", f"CO2e avoided ({c} a year)"], "head_num")
        sheet.write(row, 8, "Confidence", "head_num")
        sheet.height(row, _row_height(2))
        for rank, problem in enumerate(top, start=1):
            row += 1
            title = f"{rank}. {problem['title']}"
            sheet.write(row, 1, title, "cell_bold")
            sheet.write(row, 2, problem["action"], "cell_wrap")
            for col in (3, 4, 5):
                sheet.write(row, col, None, "cell")
            sheet.merge(row, 2, row, 5)
            sheet.write(row, 6, problem["cost_at_stake"] or 0.0, "n_cost")
            counted = problem["co2e_line"] is None
            sheet.write(row, 7, _carbon(problem["co2e_at_stake"] or 0.0, unit) if counted else "Not estimated", "n_carbon_lit" if counted else "cell_right")
            sheet.write(row, 8, (problem["confidence_label"] or "not simulated").capitalize(), "cell_right")
            lines = max(_lines(title, 40), _lines(problem["action"], 62))
            if lines > 1:
                sheet.height(row, _row_height(lines))
        if len(problems) > len(top):
            row = _notes(sheet, row, [f"All {len(problems)} are on the Opportunities sheet, with the checks and the working."], last, full)

    row += 2
    _section(sheet, row, "In this workbook", last)
    for name, description in contents:
        row += 1
        sheet.write(row, 1, name, "cell_bold")
        sheet.write(row, 2, description, "cell_wrap")
        for col in range(3, last + 1):
            sheet.write(row, col, None, "cell")
        sheet.merge(row, 2, row, last)

    row += 2
    _merged_text(
        sheet,
        row,
        "A screening estimate built on published freight and emission factors, "
        "not a quote or a network optimisation. Questions go to Overlap's "
        f"creator, {context['contact_email']}.",
        "note",
        last,
        full,
    )


# ---------------------------------------------------------- opportunities


def _opportunities_sheet(book, context, unit):
    report = context["report"]
    ov = report["overview"]
    columns = opportunity_columns(unit)
    records = opportunity_rows(report, unit)
    last = len(columns)

    sheet = book.add_sheet("Opportunities")
    sheet.landscape = True
    row = _title_block(
        sheet,
        context,
        "Opportunities, ranked",
        f"{len(records)} {_plural(len(records), 'opportunity', 'opportunities')} worth "
        f"{diagnosis.money(ov['recoverable_cost'])} and {co2e_text(ov['recoverable_co2e'])} "
        "CO2e a year, ranked by the share of total cost and carbon each one recovers.",
        12,
        160,
    )

    def minus(later, earlier):
        def make(row, ref, record, value):
            if record.get(later) is None or record.get(earlier) is None:
                return value
            return xlsx.Formula(f"{ref(earlier, row)}-{ref(later, row)}", value)
        return make

    def difference(row, ref, record, value):
        return xlsx.Formula(f"{ref('days_new', row)}-{ref('days_now', row)}", value)

    def per_tonne(row, ref, record, value):
        return xlsx.Formula(
            f"IF({ref('weight', row)}=0,\"\",{ref('cost_saving', row)}/{ref('weight', row)})",
            value,
        )

    top = row + 2
    head, last_row, ref = _table(
        sheet,
        top,
        columns,
        records,
        formulas={
            "cost_saving": minus("cost_new", "cost_now"),
            "co2e_saving": minus("co2e_new", "co2e_now"),
            "days_added": difference,
            "saving_per_t": per_tonne,
        },
    )

    total = last_row + 1
    for index, column in enumerate(columns, start=1):
        sheet.write(total, index, None, "t_blank")
    sheet.write(total, 2, "All opportunities", "t_label")
    first = head + 1
    for key, style, cached in (
        ("cost_saving", "t_cost", sum(r["cost_saving"] for r in records)),
        ("cost_share", "t_pct", sum(r["cost_share"] or 0 for r in records)),
        ("co2e_saving", "t_carbon_lit", sum(r["co2e_saving"] or 0 for r in records)),
        ("co2e_share", "t_pct", sum(r["co2e_share"] or 0 for r in records)),
    ):
        col = [c.key for c in columns].index(key) + 1
        letter = xlsx.column_letter(col)
        sheet.write(total, col, xlsx.Formula(f"SUM({letter}{first}:{letter}{last_row})", cached), style)

    sheet.freeze(head + 1, 3)
    sheet.autofilter(head, 1, last_row, last)
    sheet.repeat_rows(top, head)

    notes = [
        "Figures are annual estimates from published factors, not quotes. An empty CO2e cell means the change was not estimated in carbon, not that it saves none.",
        f"Confidence is how often a route mode change still cut both cost and CO2e on its route by at least {scoring.FLAG_THRESHOLD:.0%} when every factor was redrawn, over {stats.CONFIDENCE_CHECKS} simulations. Only route mode changes are simulated.",
        "Cost saving and CO2e avoided on a route change are current minus proposed; the formulas are in the cells.",
    ]
    _notes(sheet, total, notes, 12, 160)


# ----------------------------------------------------------------- routes


def route_columns(unit):
    c = unit["label"]
    return [
        Column("rank", "Rank by cost", "int", 7, "Route"),
        Column("origin", "From", "text", 26, "Route"),
        Column("dest", "To", "text", 22, "Route"),
        Column("leg", "Leg", "text", 9, "Route"),
        Column("mode", "Mode", "text", 8, "Route"),
        Column("orders", "Orders", "int", 8, "Volume"),
        Column("returns", "Returns", "int", 8, "Volume"),
        Column("return_rate", "Return rate", "pct", 8, "Volume"),
        Column("weight", "Weight (t)", "tonnes", 10, "Volume"),
        Column("straight_km", "Straight-line distance (km)", "km", 11, "Volume"),
        Column("mode_km", "Distance by this mode (km)", "km", 11, "Volume"),
        Column("tonne_km", "Tonne-km", "int", 11, "Volume"),
        Column("days", "Transit (days)", "days", 8, "Volume"),
        Column("transport_cost", "Transport (USD)", "usd", 11, "Cost"),
        Column("handling_cost", "Warehouse and packaging (USD)", "usd", 12, "Cost"),
        Column("returns_cost", "Returns (USD)", "usd", 10, "Cost"),
        Column("cost", "Total cost (USD a year)", "usd", 12, "Cost"),
        Column("cost_share", "Share of chain cost", "pct", 9, "Cost"),
        Column("cost_per_t", "Cost per tonne (USD)", "usd2", 10, "Cost"),
        Column("transport_co2e", f"Transport ({c})", "carbon", 10, "CO2e"),
        Column("packaging_co2e", f"Packaging ({c})", "carbon", 10, "CO2e"),
        Column("warehouse_co2e", f"Warehouse energy ({c})", "carbon", 10, "CO2e"),
        Column("returns_co2e", f"Returns ({c})", "carbon", 10, "CO2e"),
        Column("co2e", f"Total CO2e ({c} a year)", "carbon", 12, "CO2e"),
        Column("co2e_share", "Share of chain CO2e", "pct", 9, "CO2e"),
        Column("co2e_per_tkm", "kg CO2e per tonne-km", "rate", 10, "CO2e"),
        Column("flagged", "Worth changing", "text", 9, "Mode change"),
        Column("switch", "Change to", "text", 9, "Mode change"),
        Column("saved_cost", "Cost saving (USD a year)", "usd", 12, "Mode change"),
        Column("saved_co2e", f"CO2e avoided ({c} a year)", "carbon", 12, "Mode change"),
        Column("confidence", "Confidence", "text", 11, "Mode change"),
        Column("in_report", "Opportunity rank", "int", 10, "Mode change"),
    ]


def _routes_sheet(book, context, unit):
    lanes = sorted(context["lanes"], key=lambda lane: -lane["cost"])
    report = context["report"]
    ranked = {
        problem["edge_id"]: rank
        for rank, problem in enumerate(report["problems"] if report else [], start=1)
        if problem["edge_id"]
    }
    held = {row["edge_id"]: row["confidence"] for row in stats.confidence(context["lanes"])}
    total_cost = sum(lane["cost"] for lane in lanes)
    total_co2e = sum(lane["co2e"] for lane in lanes)
    divisor = unit["divisor"]

    records = []
    for rank, lane in enumerate(lanes, start=1):
        weight_t = lane["total_weight_kg"] / 1000.0
        tonne_km = analysis.tonne_km(lane["total_weight_kg"], lane["distance_km"], lane["mode"])
        switch = lane["switch"] if lane["flagged"] else None
        share = held.get(lane["id"])
        records.append(
            {
                "rank": rank,
                "origin": lane["origin_name"],
                "dest": lane["dest_name"],
                "leg": lane["leg"].title(),
                "mode": lane["mode"].title(),
                "orders": lane["order_count"],
                "returns": lane["return_count"],
                "return_rate": _share(lane["return_count"], lane["order_count"]),
                "weight": weight_t,
                "straight_km": lane["distance_km"],
                "mode_km": distance.by_mode(lane["distance_km"], lane["mode"]),
                "tonne_km": tonne_km,
                "days": round(analysis.lane_transit_days(lane["distance_km"], lane["mode"])),
                "transport_cost": lane["transport_cost"] or 0.0,
                "handling_cost": lane["handling_cost"] or 0.0,
                "returns_cost": lane["returns_cost"] or 0.0,
                "cost": lane["cost"],
                "cost_share": _share(lane["cost"], total_cost),
                "cost_per_t": _share(lane["cost"], weight_t),
                "transport_co2e": _carbon(lane["transport_co2e"] or 0.0, unit),
                "packaging_co2e": _carbon(lane["packaging_co2e"] or 0.0, unit),
                "warehouse_co2e": _carbon(lane["warehouse_co2e"] or 0.0, unit),
                "returns_co2e": _carbon(lane["returns_co2e"] or 0.0, unit),
                "co2e": _carbon(lane["co2e"], unit),
                "co2e_share": _share(lane["co2e"], total_co2e),
                "co2e_per_tkm": _share(lane["co2e"], tonne_km),
                "flagged": "Yes" if switch else "No",
                "switch": switch["mode"].title() if switch else None,
                "saved_cost": switch["saved_cost"] if switch else None,
                "saved_co2e": _carbon(switch["saved_co2e"], unit) if switch else None,
                "confidence": (diagnosis.confidence_label(share) or "").capitalize() or None if switch else None,
                "in_report": ranked.get(lane["id"]),
            }
        )

    columns = route_columns(unit)
    sheet = book.add_sheet("Routes")
    sheet.landscape = True
    flagged = sum(1 for record in records if record["flagged"] == "Yes")
    row = _title_block(
        sheet,
        context,
        "Every route",
        f"{len(records)} {_plural(len(records), 'route')} costing {diagnosis.money(total_cost)} and "
        f"emitting {co2e_text(total_co2e)} CO2e a year, biggest cost first. "
        f"{flagged} {_plural(flagged, 'is', 'are')} worth a mode change.",
        12,
        160,
    )

    top = row + 2
    total = top + 1 + len(records) + 1

    def rate(numerator, denominator, scale=""):
        def make(row, ref, record, value):
            return xlsx.Formula(
                f"IF({ref(denominator, row)}=0,\"\",{ref(numerator, row)}{scale}/{ref(denominator, row)})",
                value,
            )
        return make

    def span(first_key, last_key):
        def make(row, ref, record, value):
            return xlsx.Formula(f"SUM({ref(first_key, row)}:{ref(last_key, row)})", value)
        return make

    def share_of_total(key):
        def make(row, ref, record, value):
            whole = ref(key, total, absolute=True)
            return xlsx.Formula(f"IF({whole}=0,\"\",{ref(key, row)}/{whole})", value)
        return make

    per_tkm_scale = "*1000" if divisor == 1000.0 else ""
    head, last_row, ref = _table(
        sheet,
        top,
        columns,
        records,
        formulas={
            "return_rate": rate("returns", "orders"),
            "cost": span("transport_cost", "returns_cost"),
            "cost_share": share_of_total("cost"),
            "cost_per_t": rate("cost", "weight"),
            "co2e": span("transport_co2e", "returns_co2e"),
            "co2e_share": share_of_total("co2e"),
            "co2e_per_tkm": rate("co2e", "tonne_km", per_tkm_scale),
        },
    )

    at = {column.key: index for index, column in enumerate(columns, start=1)}
    first = head + 1
    for index in range(1, len(columns) + 1):
        sheet.write(total, index, None, "t_blank")
    sheet.write(total, at["origin"], "Whole network", "t_label")

    def total_sum(key, style, cached):
        letter = xlsx.column_letter(at[key])
        sheet.write(total, at[key], xlsx.Formula(f"SUM({letter}{first}:{letter}{last_row})", cached), style)

    def total_of(key):
        return sum(record[key] or 0 for record in records)

    for key in ("orders", "returns", "tonne_km"):
        total_sum(key, "t_int", total_of(key))
    total_sum("weight", "t_tonnes", total_of("weight"))
    for key in ("transport_cost", "handling_cost", "returns_cost", "cost"):
        total_sum(key, "t_usd", total_of(key))
    for key in ("transport_co2e", "packaging_co2e", "warehouse_co2e", "returns_co2e", "co2e"):
        total_sum(key, "t_carbon", total_of(key))
    total_sum("saved_cost", "t_cost", total_of("saved_cost"))
    total_sum("saved_co2e", "t_carbon_lit", total_of("saved_co2e"))

    cell = lambda key: xlsx.cell_ref(total, at[key])
    orders, returns = total_of("orders"), total_of("returns")
    weight, tonne_km = total_of("weight"), total_of("tonne_km")
    sheet.write(total, at["return_rate"], xlsx.Formula(f"IF({cell('orders')}=0,\"\",{cell('returns')}/{cell('orders')})", _share(returns, orders)), "t_pct")
    sheet.write(total, at["cost_share"], xlsx.Formula(f"SUM({xlsx.column_letter(at['cost_share'])}{first}:{xlsx.column_letter(at['cost_share'])}{last_row})", 1.0 if total_cost else None), "t_pct")
    sheet.write(total, at["co2e_share"], xlsx.Formula(f"SUM({xlsx.column_letter(at['co2e_share'])}{first}:{xlsx.column_letter(at['co2e_share'])}{last_row})", 1.0 if total_co2e else None), "t_pct")
    sheet.write(total, at["cost_per_t"], xlsx.Formula(f"IF({cell('weight')}=0,\"\",{cell('cost')}/{cell('weight')})", _share(total_cost, weight)), "t_usd2")
    sheet.write(total, at["co2e_per_tkm"], xlsx.Formula(f"IF({cell('tonne_km')}=0,\"\",{cell('co2e')}{per_tkm_scale}/{cell('tonne_km')})", _share(total_co2e, tonne_km)), "t_rate")

    sheet.freeze(head + 1, 4)
    sheet.autofilter(head, 1, last_row, len(columns))
    sheet.repeat_rows(top, head)

    notes = [
        "Orders travelling the same way by the same mode are grouped into one route carrying the file's weight for the year.",
        "Distance by mode is the straight line times a screening multiplier for that mode, not a routed distance. Transit days are planning estimates.",
        f"A route is worth changing when a different mode cuts both its cost and its CO2e by at least {scoring.FLAG_THRESHOLD:.0%}. The opportunity rank shows where it sits in the report.",
    ]
    _notes(sheet, total, notes, 12, 160)


# ------------------------------------------------------------- data check


def _data_sheet(book, context):
    ingest = context["ingest"] or {}
    summary = context["summary"]
    sheet = book.add_sheet("Data check")
    sheet.gridlines = False
    for col, width in enumerate([44, 16, 22, 62], start=1):
        sheet.width(col, width)
    last, full = 4, 144

    row = _title_block(
        sheet,
        context,
        "Data check",
        "What the loaded file turned into, and anything that did not make it into the figures.",
        last,
        full,
    )

    row += 2
    _section(sheet, row, "Counts", last)
    row += 1
    sheet.write(row, 1, "What", "head")
    sheet.write(row, 2, "Count", "head_num")
    sheet.write(row, 3, None, "head")
    sheet.write(row, 4, "Notes", "head")

    def count(label, value, style="n_int", note=None):
        nonlocal row
        row += 1
        sheet.write(row, 1, label, "label")
        sheet.write(row, 2, value, style)
        sheet.write(row, 3, None, "cell")
        sheet.write(row, 4, note, "cell_wrap")

    count("Rows in the orders file", ingest.get("rows_in_file"))
    count("Orders used", ingest.get("orders_loaded"), "n_int_bold")
    count(
        "Orders excluded",
        ingest.get("rows_skipped") or 0,
        "n_int",
        "Each one is listed below with its line and the reason." if ingest.get("rows_skipped") else None,
    )
    count("Weight readable in the file (kg)", ingest.get("weight_in_file_kg"), "n_int")
    count("Weight used (kg)", ingest.get("weight_loaded_kg"), "n_int")
    if (ingest.get("weight_excluded_kg") or 0) > 0.5:
        count("Weight excluded (kg)", ingest.get("weight_excluded_kg"), "n_int")
    count("Routes from orders", ingest.get("lanes"))
    if ingest.get("suppliers"):
        count("Routes from suppliers", ingest.get("suppliers"))
    count("Routes analysed", ingest.get("edges", summary["edges"]), "n_int_bold")
    if summary.get("first_order"):
        count("First order date", summary["first_order"], "cell_right", "Every figure treats the loaded file as one year of shipping.")
        count("Last order date", summary["last_order"], "cell_right")
    else:
        count("Order dates", "None given", "cell_right", "Every figure treats the loaded file as one year of shipping.")

    checks = ingest.get("checks") or {}
    if checks:
        row += 2
        _section(sheet, row, "Checks", last)
        row += 1
        sheet.write(row, 1, "Check", "head")
        sheet.write(row, 2, "Result", "head_num")
        sheet.write(row, 3, None, "head")
        sheet.write(row, 4, None, "head")
        rows = [("Required columns present", True)] + [
            (label, checks.get(key))
            for key, label in (
                ("weights_readable", "Weights readable"),
                ("modes_recognised", "Transport modes recognised"),
                ("origins_found", "Origin cities found"),
                ("destinations_found", "Destination cities found"),
            )
            if key in checks
        ]
        for label, good in rows:
            row += 1
            sheet.write(row, 1, label, "label")
            sheet.write(row, 2, "Passed" if good else "Some rows failed", "cell_right" if good else "bad")
            sheet.write(row, 3, None, "cell")
            sheet.write(row, 4, None, "cell")

    warnings = ingest.get("warnings") or []
    if warnings:
        row += 2
        _section(sheet, row, "Loaded, but worth checking", last)
        row += 1
        sheet.write(row, 1, "Warning", "head")
        sheet.write(row, 2, "Rows", "head_num")
        sheet.write(row, 3, None, "head")
        sheet.write(row, 4, "Lines", "head")
        for warning in warnings:
            row += 1
            lines = ", ".join(
                str(item["line"])
                + (f" (same as {item['same_as']})" if item.get("same_as") else "")
                + (f" ({item['km']:,} km)" if item.get("km") else "")
                for item in warning.get("lines") or []
            )
            if warning.get("count", 0) > len(warning.get("lines") or []):
                lines += " and more"
            sheet.write(row, 1, warning["message"], "cell_wrap")
            sheet.write(row, 2, warning.get("count"), "n_int")
            sheet.write(row, 3, None, "cell")
            sheet.write(row, 4, lines or None, "cell_wrap")
            height = max(_lines(warning["message"], 44), _lines(lines, 62))
            if height > 1:
                sheet.height(row, _row_height(height))

    errors = ingest.get("errors") or []
    if errors:
        row += 2
        _section(sheet, row, "Rows not used", last)
        row += 1
        sheet.write_row(row, 1, ["File"], "head")
        sheet.write(row, 2, "Line", "head_num")
        sheet.write(row, 3, "Field", "head")
        sheet.write(row, 4, "Why it was not used", "head")
        first = row + 1
        for item in errors:
            row += 1
            sheet.write(row, 1, item.get("file") or "orders", "cell")
            sheet.write(row, 2, item.get("line"), "n_int")
            sheet.write(row, 3, item.get("field"), "cell")
            sheet.write(row, 4, item.get("problem"), "cell_wrap")
            if _lines(item.get("problem") or "", 62) > 1:
                sheet.height(row, _row_height(_lines(item["problem"], 62)))
        sheet.autofilter(first - 1, 1, row, 4)
        if ingest.get("error_count", 0) > len(errors):
            row = _notes(sheet, row, [f"The first {len(errors)} of {ingest['error_count']:,} are listed."], last, full)

    if not errors and not warnings:
        row += 2
        _merged_text(sheet, row, "Every row in the file was used, and nothing needed a warning.", "prose", last, full)


# ------------------------------------------------------------ assumptions


def _assumptions_sheet(book, context):
    method = context["report"]["method"]
    sheet = book.add_sheet("Assumptions")
    sheet.gridlines = False
    widths = [46, 16, 16, 16, 16, 16]
    for col, width in enumerate(widths, start=1):
        sheet.width(col, width)
    last, full = len(widths), sum(widths)

    row = _title_block(
        sheet,
        context,
        "Assumptions and method",
        "The factors and rules every figure in this workbook rests on. Change a factor in the tool and every figure moves with it.",
        last,
        full,
    )

    row += 2
    _section(sheet, row, "Factors by transport mode", last)
    row += 1
    sheet.write(row, 1, "Mode", "head")
    sheet.write_row(
        row,
        2,
        ["Cost (USD per tonne-km)", "CO2e (kg per tonne-km)", "Distance multiplier", "Speed (km a day)", "Fixed days in transit"],
        "head_num",
    )
    sheet.height(row, _row_height(2))
    for mode in factors.MODES:
        row += 1
        sheet.write(row, 1, mode.title(), "label")
        sheet.write(row, 2, factors.COST_FACTORS[mode], "n_rate")
        sheet.write(row, 3, factors.EMISSION_FACTORS[mode], "n_rate")
        sheet.write(row, 4, factors.CIRCUITY[mode], "n_dec2")
        sheet.write(row, 5, factors.TRANSIT_KM_PER_DAY[mode], "n_int")
        sheet.write(row, 6, factors.TRANSIT_FIXED_DAYS[mode], "n_dec2")

    rules = [
        ("A change is only recommended when it cuts both cost and CO2e on the route by at least", scoring.FLAG_THRESHOLD, "n_pct"),
        ("Sea is only suggested on routes longer than (km, straight line)", scoring.SEA_MINIMUM_KM, "n_int"),
        ("Air only drops to road or rail on routes shorter than (km, straight line)", scoring.SURFACE_RANGE_KM, "n_int"),
        ("High confidence: share of simulations where the change still pays", diagnosis.CONFIDENCE_HIGH, "n_pct"),
        ("Moderate confidence", diagnosis.CONFIDENCE_MODERATE, "n_pct"),
        ("Simulations per route change", stats.CONFIDENCE_CHECKS, "n_int"),
        ("Runs behind the likely range", stats.TRIALS, "n_int"),
        ("How far each factor is redrawn, either side", stats.FACTOR_SPREAD, "n_pct"),
        ("Packaging cost per order (USD)", factors.PACKAGING_COST_PER_ORDER, "n_usd2"),
        ("Packaging CO2e per order (kg)", factors.PACKAGING_KG_CO2E_PER_ORDER, "n_dec2"),
        ("A return leg costs and emits this many times the outbound trip", factors.RETURN_LEG_MULTIPLIER, "n_dec2"),
        ("Handling cost per return (USD)", factors.RETURN_HANDLING_COST, "n_usd2"),
        ("Grid intensity where a warehouse gives none (kg CO2e per kWh)", factors.DEFAULT_GRID_INTENSITY, "n_dec2"),
        ("Share of late supplier deliveries assumed flown in", factors.EXPEDITE_SHARE_OF_LATE, "n_pct"),
    ]
    row += 2
    _section(sheet, row, "Rules and constants", last)
    row += 1
    sheet.write(row, 1, "Rule", "head")
    sheet.write(row, 2, "Value", "head_num")
    for label, value, style in rules:
        row += 1
        sheet.write(row, 1, label, "label")
        sheet.write(row, 2, value, style)
        if _lines(label, 46) > 1:
            sheet.height(row, _row_height(_lines(label, 46)))

    def described(title, pairs):
        nonlocal row
        row += 2
        _section(sheet, row, title, last)
        for name, words in pairs:
            row += 1
            sheet.write(row, 1, name, "cell_bold")
            sheet.write(row, 2, words, "cell_wrap")
            for col in range(3, last + 1):
                sheet.write(row, col, None, "cell")
            sheet.merge(row, 2, row, last)
            lines = max(_lines(name, 46), _lines(words, full - 46))
            if lines > 1:
                sheet.height(row, _row_height(lines))

    described("How each figure is worked out", method["formulas"])
    described("Assumptions", method["assumptions"])

    row += 2
    _section(sheet, row, "What this does not account for", last)
    for limit in method["limits"]:
        row += 1
        _merged_text(sheet, row, "• " + limit, "prose", last, full)

    row += 2
    _section(sheet, row, "Sources", last)
    row += 1
    _merged_text(
        sheet,
        row,
        "Emission factors follow the DEFRA and GLEC Framework published ranges. "
        "Cost factors are industry defaults, not contract rates. City coordinates "
        "come from GeoNames. The full method, with every source, is available "
        f"from Overlap's creator, {context['contact_email']}.",
        "prose",
        last,
        full,
    )
