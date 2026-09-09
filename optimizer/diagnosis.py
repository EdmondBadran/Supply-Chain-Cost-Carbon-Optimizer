"""The report a consultant would hand over: the truth, the diagnosis, the plan.

The value chain page shows what is wrong. The map shows where. Neither one
tells someone what to do first, what it is worth, and what the catch is. This
assembles that from whatever data is loaded, in plain sentences, with every
figure traceable back to a factor on the method page.

Nothing here is written for a particular company. Every sentence is composed
from the loaded data, so an empty chain says so and a clean chain says that
instead of inventing something to worry about.
"""

from . import analysis, chain, factors, scoring

# How many problems make it into the report. Below five it stops looking like
# a diagnosis, above ten nobody reads to the end.
REPORT_LIMIT = 8

# A single problem holding this much of everything recoverable is worth
# calling out on its own, because it changes what the reader should do first.
CONCENTRATION_HIGH = 0.45
CONCENTRATION_SOME = 0.25


def money(value):
    if 0 < value < 10:
        return f"${value:,.2f}"
    return f"${value:,.0f}"


def tonnes(kg):
    if kg < 100:
        return f"{kg / 1000:,.2f} tonnes"
    return f"{kg / 1000:,.0f} tonnes"


def days(value):
    if value < 1:
        return "under a day"
    if value < 2:
        return "about a day"
    return f"about {value:,.0f} days"


def build(conn, limit=REPORT_LIMIT):
    """The whole report, or None when there is nothing loaded to report on."""
    lanes = scoring.rank(conn)
    if not lanes:
        return None

    stages = chain.build(conn)
    totals = analysis.totals(conn)
    everything = _problems(lanes, stages, totals)
    problems = everything[:limit]

    return {
        "overview": _overview(lanes, totals, problems, len(everything)),
        "flow": _flow(stages),
        "problems": problems,
        "method": _method(),
        "plan": [_step(n, p) for n, p in enumerate(problems, start=1)],
        "top_three": problems[:3],
        "closing": (
            "Follow this plan and your supply chain will be cheaper, cleaner, "
            "and finally visible."
        ),
    }


def _overview(lanes, totals, problems, total_found):
    """One paragraph that tells someone the truth about their own chain."""
    cost = totals["cost"]
    co2e = totals["co2e"]
    recoverable_cost = sum(p["cost_at_stake"] for p in problems)
    recoverable_co2e = sum(p["co2e_at_stake"] for p in problems)
    flagged = [l for l in lanes if l["flagged"]]

    if not problems:
        truth = (
            f"Moving your goods costs {money(cost)} a year and puts "
            f"{tonnes(co2e)} of greenhouse gas into the air, counted as the "
            f"weight of carbon dioxide that would do the same damage. Nothing "
            f"you ship is going by a method that a cheaper and cleaner one "
            f"could replace, so the savings you are after are not in how you "
            f"ship. They are in what you pay, how much you move, or where "
            f"your buildings are."
        )
        concentration = ""
    else:
        more = ""
        if total_found > len(problems):
            more = (
                f" There are {total_found} problems in total. These are the "
                f"{len(problems)} worth doing first."
            )
        truth = (
            f"Moving your goods costs {money(cost)} a year and puts "
            f"{tonnes(co2e)} of greenhouse gas into the air, counted as the "
            f"weight of carbon dioxide that would do the same damage. "
            f"{money(recoverable_cost)} of that cost and "
            f"{tonnes(recoverable_co2e)} of that gas sit on "
            f"{len(problems)} problems, and in most cases one change fixes "
            f"both at once.{more}"
        )
        top = problems[0]
        share = top["cost_at_stake"] / recoverable_cost if recoverable_cost else 0
        if share >= CONCENTRATION_HIGH:
            concentration = (
                f"Nearly half of the money on the table is on one line: "
                f"{top['title']}. Start there and the rest is detail."
            )
        elif share >= CONCENTRATION_SOME:
            concentration = (
                f"A quarter of the money on the table is on one line: "
                f"{top['title']}."
            )
            concentration += " It is the obvious place to start."
        else:
            concentration = (
                "No single line dominates. The value is spread across several "
                "changes, so this is a programme of work rather than one "
                "decision."
            )

    return {
        "truth": truth,
        "concentration": concentration,
        "cost": cost,
        "co2e": co2e,
        "recoverable_cost": recoverable_cost,
        "recoverable_co2e": recoverable_co2e,
        "cost_share": recoverable_cost / cost if cost else 0.0,
        "co2e_share": recoverable_co2e / co2e if co2e else 0.0,
        "routes": len(lanes),
        "flagged_routes": len(flagged),
        "tonne_km": totals["tonne_km"],
    }


def _flow(stages):
    """The six stages described in words, with each one's real figures."""
    lines = []

    for stage in stages:
        count = stage["headline"]
        told = f"{count:,} {stage['unit']}" if count is not None else ""
        problems = stage["problem_count"]
        spend = (
            f"This costs {money(stage['cost'])} a year and emits "
            f"{tonnes(stage['co2e'])}."
        )
        found = (
            f"{problems} thing{'s' if problems != 1 else ''} here "
            f"need{'' if problems != 1 else 's'} attention."
            if problems
            else "Nothing here needs attention."
        )

        if stage["key"] == "suppliers":
            what = (
                f"{told} send you goods. What it costs to bring those goods in "
                f"belongs to the next stage, so this one carries no money of "
                f"its own."
            )
            # Two different kinds of problem live in this stage. Freight is
            # the same lane the inbound stage already lists. Terms are not
            # freight at all, and saying so is the whole point of the stage.
            freight = sum(1 for p in stage["problems"] if p["kind"] is None)
            terms = problems - freight
            parts = []
            if freight:
                parts.append(
                    f"{freight} ship on a mode that could be cheaper and "
                    f"cleaner, which is the same route inbound freight lists, "
                    f"seen from the supplier end"
                )
            if terms:
                parts.append(
                    f"{terms} carry terms that cost you something no freight "
                    f"decision can fix: lateness, lead time, or a minimum "
                    f"order bigger than you need"
                )
            found = (
                "Of those, " + " and ".join(parts) + "."
                if parts
                else "Every supplier already ships on a sensible mode, on "
                "terms that are not costing you anything."
            )
        elif stage["key"] == "inbound":
            what = f"{told} bring goods from suppliers into your warehouses. {spend}"
        elif stage["key"] == "warehousing":
            what = (
                f"{told} hold, handle and power your goods in between. {spend} "
                f"Most of that carbon is electricity."
            )
        elif stage["key"] == "outbound":
            what = f"{told} take goods from your warehouses out to customers. {spend}"
        elif stage["key"] == "customers":
            what = (
                f"You deliver to {told}. The money is already counted in "
                f"outbound freight, so this stage asks a different question: "
                f"which of them costs the most to reach."
            )
            found = (
                f"{problems} of them cost noticeably more per tonne than the "
                f"rest."
                if problems
                else "No destination stands out as expensive to reach."
            )
        elif stage["key"] not in chain.BUILTIN_KEYS:
            # A stage somebody added themselves. Nothing feeds it, so the
            # report says so rather than describing figures it does not have.
            what = (
                "You added this stage yourself. Nothing in an orders file "
                "measures it, so it carries no figures here and no checks run "
                "against it. It is in the picture because it is part of your "
                "chain, not because this tool can see it."
            )
            found = "Nothing to check here."
        else:
            what = (
                f"{count:,} order{'s' if count != 1 else ''} came back. That "
                f"second trip costs {money(stage['cost'])} and emits "
                f"{tonnes(stage['co2e'])}, and it earns you nothing."
            )
            found = (
                f"{problems} route{'s' if problems != 1 else ''} send back "
                f"more than the rest."
                if problems
                else "No route returns more than you would expect."
            )

        lines.append(
            {
                "key": stage["key"],
                "name": stage["name"],
                "blurb": stage["blurb"],
                "what": what,
                "found": found,
                "problem_count": problems,
                "cost": stage["cost"],
                "co2e": stage["co2e"],
            }
        )

    note = (
        "Almost nobody sees these six stages at once. Freight is bought by one "
        "team, warehouses are run by another, and carbon is reported once a "
        "year by a third. The waste collects in the gaps between them, which "
        "is exactly why it survives."
    )
    return {"stages": lines, "note": note}


def _mode_switch_problem(lane, totals):
    """A route that would be cheaper and cleaner on a different mode."""
    switch = lane["switch"]
    from_mode, to_mode = lane["mode"], switch["mode"]

    cost_ratio = factors.cost_factor(from_mode) / factors.cost_factor(to_mode)
    co2e_ratio = factors.emission_factor(from_mode) / factors.emission_factor(to_mode)

    before_days = analysis.lane_transit_days(lane["distance_km"], from_mode)
    after_days = analysis.lane_transit_days(lane["distance_km"], to_mode)

    return _problem(
        stage="Inbound freight" if lane["leg"] == "inbound" else "Outbound freight",
        title=f"{lane['origin_name']} to {lane['dest_name']}",
        happening=(
            f"You send {tonnes(lane['total_weight_kg'])} along this route "
            f"every year, {lane['distance_km']:,.0f} km each time, by "
            f"{from_mode}."
        ),
        why=(
            f"For every tonne you move a kilometre, {from_mode} costs "
            f"about {cost_ratio:,.0f} times what {to_mode} costs and puts out "
            f"about {co2e_ratio:,.0f} times the gas. Over a trip this long "
            f"that difference adds up to real money and real damage."
        ),
        cost_at_stake=switch["saved_cost"],
        co2e_at_stake=switch["saved_co2e"],
        totals=totals,
        action=f"Move this route from {from_mode} to {to_mode}.",
        note=(
            f"It would take {days(after_days)} to arrive instead of "
            f"{days(before_days)}, so {days(after_days - before_days)} longer. "
            f"Check your customers can wait that long, and that you can keep "
            f"enough stock to cover the extra wait. If this is going by "
            f"{from_mode} because of a delivery promise you made somebody, "
            f"that promise is what this is really costing you."
        ),
        edge_id=lane["id"],
        effort=lane["effort"],
    )


def _problems(lanes, stages, totals):
    """Every problem across the chain, ranked so they can be compared."""
    found = []

    for lane in lanes:
        if lane["flagged"] and lane["switch"]:
            found.append(_mode_switch_problem(lane, totals))

    by_key = {stage["key"]: stage for stage in stages}

    # Only the on-time check reaches the report. Lead time and minimum order
    # are real problems but neither can be costed from an orders file, and a
    # plan step worth no money and no carbon is not a plan step.
    for item in by_key.get("suppliers", {}).get("problems", []):
        if item["kind"] != "on_time":
            continue
        if not item["cost_at_stake"] and not item["co2e_at_stake"]:
            continue
        found.append(
            _problem(
                stage="Suppliers",
                title=item["title"],
                happening=f"{item['detail']}.",
                why=(
                    "A supplier missing a date does not stay a supplier "
                    "problem. Somebody has to rush the next delivery to keep "
                    "your own promise to your customer, and rushing means "
                    "putting it on a plane. The supplier does not pay for "
                    "that. You do, twice: once in what the flight costs and "
                    "once in what it puts into the air."
                ),
                cost_at_stake=item["cost_at_stake"],
                co2e_at_stake=item["co2e_at_stake"],
                totals=totals,
                action=(
                    "Write the delivery date into the contract with "
                    "something attached to missing it, or keep enough stock "
                    "on hand that a late delivery does not have to be flown."
                ),
                note=(
                    "This is a conversation with the supplier, not with "
                    "the shipping company. The figure assumes half of what "
                    "arrives late ends up being flown, which is an "
                    "assumption rather than something measured. Your own "
                    "records of what you have rushed would sharpen it."
                ),
            )
        )

    for item in by_key.get("warehousing", {}).get("problems", []):
        found.append(
            _problem(
                stage="Warehousing",
                title=item["title"],
                happening=f"This site runs at {item['detail']}.",
                why=(
                    "You are paying a carbon penalty for where this building "
                    "draws its power, not for how well it is run. A clean site "
                    "and a dirty site doing identical work report very "
                    "different numbers."
                ),
                cost_at_stake=item["cost_at_stake"],
                co2e_at_stake=item["co2e_at_stake"],
                totals=totals,
                action=(
                    "Move this site onto cleaner electricity, or shift volume "
                    "to one of your cleaner sites."
                ),
                note=(
                    "This is usually a supply contract, not an operations "
                    "change. Ask your energy supplier what a renewable tariff "
                    "at this site costs before you consider moving any volume."
                ),
            )
        )

    for item in by_key.get("customers", {}).get("problems", []):
        found.append(
            _problem(
                stage="Customers",
                title=item["title"],
                happening=f"This destination costs {item['detail']}.",
                why=(
                    "The transport mode here is already sensible, so the cost "
                    "is coming from distance. You are shipping further than "
                    "you need to in order to reach this customer."
                ),
                cost_at_stake=item["cost_at_stake"],
                co2e_at_stake=item["co2e_at_stake"],
                totals=totals,
                action="Serve this destination from your nearest other warehouse.",
                note=(
                    "Check the nearer site has the space and the stock. Moving "
                    "volume between warehouses is a planning change, and it "
                    "only pays if the receiving site is not already full."
                ),
                co2e_line=(
                    "Not counted here. A shorter journey emits less, but how "
                    "much less depends on which site takes the volume, so this "
                    "report does not put a number on it."
                ),
                edge_id=item["edge_id"],
            )
        )

    for item in by_key.get("returns", {}).get("problems", []):
        found.append(
            _problem(
                stage="Returns",
                title=item["title"],
                happening=f"On this route {item['detail']}.",
                why=(
                    "Every item sent back pays for the journey out a "
                    "second time and gives you nothing for it. At this rate "
                    "the route is carrying a cost that no amount of haggling "
                    "with shipping companies will remove."
                ),
                cost_at_stake=item["cost_at_stake"],
                co2e_at_stake=item["co2e_at_stake"],
                totals=totals,
                action=(
                    "Find out why things come back from here before "
                    "changing anything about how you ship to it."
                ),
                note=(
                    "This is a product or listing problem, not a logistics "
                    "one. Sizing, photographs, descriptions and damage in "
                    "transit are the usual four causes. Changing transport "
                    "mode here would save a little and fix nothing."
                ),
                edge_id=item["edge_id"],
            )
        )

    # Cost and carbon cannot be added together, so problems are compared by
    # how much of the whole network each one gives back. That is unit free,
    # which is the only way a dirty warehouse can be ranked against an air
    # route honestly.
    found.sort(key=lambda p: -p["weight"])
    return _dedupe(found)


def _dedupe(problems):
    """One line per route, so the same money is never counted twice.

    A route can fail more than one check at once. An air route to a far city
    is flagged for its mode and again for costing the most per tonne to
    reach, but that is one pot of money with two possible fixes, not two
    pots. Adding both would overstate what is actually recoverable, which is
    the fastest way to lose a reader who checks. The bigger problem keeps the
    money and the smaller one survives as an alternative fix on the same line.
    """
    kept = []
    seen = {}
    for problem in problems:
        edge_id = problem["edge_id"]
        if edge_id is None:
            kept.append(problem)
            continue
        if edge_id not in seen:
            seen[edge_id] = problem
            kept.append(problem)
            continue
        winner = seen[edge_id]
        if problem["action"] not in winner["alternatives"]:
            winner["alternatives"].append(problem["action"])
    return kept


def _problem(
    stage,
    title,
    happening,
    why,
    cost_at_stake,
    co2e_at_stake,
    totals,
    action,
    note,
    edge_id=None,
    effort=None,
    co2e_line=None,
):
    cost_share = cost_at_stake / totals["cost"] if totals["cost"] else 0.0
    co2e_share = co2e_at_stake / totals["co2e"] if totals["co2e"] else 0.0
    return {
        "stage": stage,
        "title": title,
        "happening": happening,
        "why": why,
        "cost_at_stake": cost_at_stake,
        "co2e_at_stake": co2e_at_stake,
        "cost_share": cost_share,
        "co2e_share": co2e_share,
        "weight": cost_share + co2e_share,
        "action": action,
        "note": note,
        "edge_id": edge_id,
        "effort": effort,
        "co2e_line": co2e_line,
        "alternatives": [],
    }


def _step(number, problem):
    """One problem turned into the four steps someone can actually follow."""
    if problem["cost_at_stake"]:
        cost_line = (
            f"About {money(problem['cost_at_stake'])} a year, which is "
            f"{problem['cost_share']:.1%} of what your whole chain costs."
        )
    else:
        cost_line = (
            "No direct saving. This one is worth doing for the carbon and for "
            "what it tells you about the rest of the chain."
        )

    if problem["co2e_line"]:
        co2e_line = problem["co2e_line"]
    elif problem["co2e_at_stake"]:
        co2e_line = (
            f"About {tonnes(problem['co2e_at_stake'])} of greenhouse gas a "
            f"year, which is {problem['co2e_share']:.1%} of your total."
        )
    else:
        co2e_line = "No measurable carbon change. This one is about the money."

    return {
        "number": number,
        "stage": problem["stage"],
        "title": problem["title"],
        "action": problem["action"],
        "cost_line": cost_line,
        "co2e_line": co2e_line,
        "note": problem["note"],
        "alternatives": problem["alternatives"],
        "edge_id": problem["edge_id"],
    }


def _method():
    """How every number above was worked out, in plain words."""
    air_cost = factors.cost_factor("air") / factors.cost_factor("sea")
    air_co2e = factors.emission_factor("air") / factors.emission_factor("sea")

    return {
        "formulas": [
            (
                "Freight cost",
                "Tonnes carried, times distance in kilometres, times the cost "
                "factor for that transport mode.",
            ),
            (
                "Freight carbon",
                "Tonnes carried, times distance in kilometres, times the "
                "carbon factor for that transport mode.",
            ),
            (
                "Warehouse carbon",
                "How much electricity the building uses in a year, times "
                "how dirty the local power is, split across the routes it "
                "serves according to how much each one weighs.",
            ),
            (
                "Returns",
                f"Something sent back pays for its journey out a second "
                f"time, at {factors.RETURN_LEG_MULTIPLIER} times the price, "
                f"because a vehicle coming back is emptier than one going "
                f"out. Plus {money(factors.RETURN_HANDLING_COST)} for "
                f"somebody to unpack and check it.",
            ),
        ],
        "costs": factors.COST_FACTORS,
        "emissions": factors.EMISSION_FACTORS,
        "transit": factors.TRANSIT_KM_PER_DAY,
        "air_cost_ratio": air_cost,
        "air_co2e_ratio": air_co2e,
        "flag_threshold": scoring.FLAG_THRESHOLD,
        "sea_minimum": scoring.SEA_MINIMUM_KM,
        "surface_range": scoring.SURFACE_RANGE_KM,
        "assumptions": [
            (
                "Where the factors come from",
                "Carbon factors follow the published ranges from DEFRA and the "
                "GLEC framework, which is the method most freight carbon "
                "reporting is built on. Cost factors are industry defaults, "
                "not published standards, and your own contracted rates will "
                "differ. Replace them and the cost side gets sharper. The "
                "carbon side does not depend on them at all.",
            ),
            (
                "Routes are grouped, not listed one by one",
                "Every order travelling the same way by the same mode is "
                "collapsed into one route carrying a year of weight. Thousands "
                "of orders become a handful of decisions.",
            ),
            (
                "Distances are straight lines",
                "Distance is measured point to point across the earth. Real "
                "freight does not travel that way. Road runs roughly 15 to 25 "
                "percent longer, and sea can be far longer where ships route "
                "around land or through a canal. This means the savings shown "
                "here are optimistic in absolute terms. The comparison between "
                "two options on the same route is still fair, because both are "
                "measured the same way, and that comparison is what the "
                "ranking rests on.",
            ),
            (
                "Only sensible mode changes are offered",
                f"Sea is only suggested beyond "
                f"{scoring.SEA_MINIMUM_KM:,} km, because below that it is not "
                f"a real option. Air only drops to road or rail under "
                f"{scoring.SURFACE_RANGE_KM:,} km. Road can only become rail, "
                f"because road is already a land route. Nothing is suggested "
                f"as an alternative to sea, because nothing beats it per tonne "
                f"carried.",
            ),
            (
                "A problem has to be both",
                f"A route is only flagged when the change cuts at least "
                f"{scoring.FLAG_THRESHOLD:.0%} of its cost and at least "
                f"{scoring.FLAG_THRESHOLD:.0%} of its carbon. Expensive alone "
                f"is a buying question. Dirty alone is a reporting question. "
                f"Neither is what this report is for.",
            ),
            (
                "Lead times are estimates",
                "Transit days are worked out from typical door to door speeds "
                "per mode plus fixed time at each end for handling and "
                "customs. They are the right order of magnitude for deciding "
                "whether a change is worth investigating. They are not a "
                "schedule, and no carrier has quoted them.",
            ),
        ],
        "limits": [
            "Stock is not counted. Shipping something more slowly means "
            "more of your goods sitting on a ship instead of on a shelf, "
            "which is money you cannot spend on anything else, and none of "
            "that is in these figures.",
            "Unless your warehouse file says how much each building holds, "
            "moving work to another one assumes it has the space.",
            "This covers shipping, storage, packaging and things sent back. "
            "It says nothing about making the product in the first place, or "
            "the raw materials that went into it.",
            "The tool cannot know why something is shipped the way it is. "
            "Air freight is often there for a good reason, and that reason "
            "will not be anywhere in your spreadsheet.",
        ],
    }
