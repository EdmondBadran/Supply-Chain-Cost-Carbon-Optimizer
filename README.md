# Overlap

A supply chain cost and carbon screener.

Most companies track logistics cost in one report and emissions in another,
and the two never get looked at together. That is a problem, because the
routes doing the most financial damage are very often the same routes doing
the most environmental damage, and nobody notices because the numbers live in
different spreadsheets.

Overlap answers one question: which shipping routes should I investigate if
I want to cut both cost and carbon? Load a year of orders and it ranks the
routes where changing transport mode would reduce both, lets you investigate
and test each one, and exports the result. It is a screen, not a network
optimiser. The name is the idea: it looks for the routes where wasted cost
and wasted carbon overlap.

## How it reads

**The front page** says what the tool does before it shows anything: find
shipping changes that cut cost and carbon, upload a year of orders or explore
an example, three steps, and one example result.

**The upload** asks for five columns and says what each one means, with an
example file to copy. It also recognises common alternative headings (Ship
From City, Weight (kg), Transport Mode, and so on), and if a column it needs
still isn't there under a name it knows, it reads the file's own heading row
in the browser and asks which of your columns holds it, before anything is
sent. Optional warehouse and supplier files sit behind a disclosure, and an
optional company name is printed on the results and exports. A file that
cannot be read gets a plain explanation of what to fix, with the loader's own
message underneath.

**The results** open on the decision: how many changes are worth reviewing,
what they could save a year in cost and CO2e, one button into the top change,
and three cards. A short strip says how much of the file was used, how
confident to be, and that these are planning estimates. A row of section
links follows the reader down the page.

**Recommendations** lists every change as a card, grouped by how ready it is
to act on. Recommended now means the change held up when every cost and
emission rate was redrawn; worth reviewing and needs more data say why not.
Each card states what would change, the estimated annual saving, why it is
recommended and how sure to be, and opens a decision view: today against
proposed, what to check before acting, Mark for review, Export this
recommendation as a one-page brief, and, folded away, the full calculation
and a what-if that recalculates on the server.

**Network** is the baseline and the map, which follows whichever change is
open and labels it before and after. The chain picture, effort ratings and a
what-if for closing or opening a warehouse are one click down.

**Data & assumptions** answers four questions in a sentence each: how much of
the data was usable, how reliable the changes are, which assumptions matter,
and what the analysis leaves out. Factors, formulas and the full statistics
are behind their own disclosures.

**Exports** are an executive summary for leadership, an Excel workbook for
analysts with every change, route, check and assumption, and a CSV for other
systems. The whole page also prints as a designed PDF.

## What it does

Load a CSV of order lines and it builds a graph of your chain: suppliers,
warehouses and destination cities as nodes, shipping routes as edges. Add a
supplier file and it runs end to end, supplier into warehouse and warehouse
out to customer, which matters because inbound freight is where a lot of the
hidden carbon turns out to be. On the sample data an inbound lane is the
second biggest thing worth fixing.

Then for every route it calculates:

- **Cost to serve** from transport by mode, warehouse storage and handling
  allocated by weight, and the cost of returns
- **Emissions** from tonne-km by mode, packaging per order, the lane's share
  of warehouse energy, and the return legs

Then it looks for lanes where a different transport mode would cut both, and
ranks those by how much of the network's total they recover. You can tag how
hard each one is to actually do, and the ranking reorders around that.

Every order ends up either loaded or listed with its line, field and reason.
After grouping, the loader checks the routes still add up to the orders: same
count, same weight, one route per origin, destination and mode. If they do
not, no report is produced.

Two kinds of row load but are flagged: one that repeats an earlier row or
order reference, and a road or rail order that runs further than any regular
service, which nearly always means a city matched the wrong place.

## Running it

```
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000. A sample dataset loads itself, so no page
opens empty. Both sample companies are invented: their data is generated to
follow realistic patterns, and every page showing their figures says so.

    /                 the landing page, with live results on the loaded data
    /report           the report: the short answer, four parts, route panel, exports
    /report/summary   the executive summary, ready to print
    /findings.xlsx    the findings as a formatted workbook
    /findings.csv     every opportunity as plain data
    /method           how it works, in two boxes
    /data             upload your own CSV, or try a sample
    /privacy          what happens to a file you upload

The old `/chain`, `/dashboard`, `/diagnosis` and `/stats` addresses redirect
to the matching part of the report.

## Your own data

The orders file needs these columns, or a heading that means the same
thing (`Shipper` for `origin_name`, `Weight (kg)` for `weight_kg`, and so
on). Anything still unrecognised gets matched by hand on the upload page
before the file is sent:

| Column | What it is |
| --- | --- |
| `origin_name` | The warehouse or DC the order shipped from |
| `origin_city` | City the warehouse is in |
| `dest_city` | Where it went |
| `weight_kg` | Shipment weight |
| `mode` | road, rail, sea or air |

These are optional and make the results better: `origin_country`,
`dest_country`, `order_ref` (or `order_id`), `order_date`, `customer_id`,
`units`, `product_category`, `order_value`, `returned`.

The file can be comma or semicolon separated, and a semicolon file's decimal
commas (`1.000,5`) are read as `1000.5`. It can be saved as UTF-8 or in the
Windows encoding Excel writes by default. Mode also reads truck, LTL, ocean
freight, air cargo and a few other everyday names, not just road, rail, sea
and air.

Cities are resolved against a bundled GeoNames table, 34,135 cities across
244 countries, so it works anywhere and does not call out to a geocoding API.
If somewhere is too small to be in there, add `origin_lat` / `origin_lon` /
`dest_lat` / `dest_lon` and those win over the lookup.

You can also upload two more files. Warehouse costs, with `name`,
`storage_cost_annual`, `energy_kwh_annual`, `grid_intensity` and
`capacity_kg`, without which the warehouse side falls back to defaults and the
cost-to-serve numbers get less useful. And suppliers, with `name`, `city`, `country`, `supplies`
(the warehouse it feeds), `mode`, `annual_weight_kg`, `shipments_per_year`
and `annual_cost`, which is what turns the outbound network into a full
chain.

The supplier file takes three more optional columns: `lead_time_days`,
`min_order_qty` and `on_time_rate`, written either as 0.92 or as 92. These are
commercial terms rather than freight, and only the on-time rate carries money.
A supplier that misses its date gets expedited, expediting means air, and that
is the point where a service problem quietly becomes a carbon one. Lead time
and minimum order are flagged without a figure attached, because what they
cost depends on your demand and your cost of capital, neither of which is in
an orders file.

Anything the loader cannot read gets reported by line number and the rest of
the file still loads. One bad row does not cost you the import.

## The numbers behind it

Emission factors are kg CO2e per tonne-km in the ranges published by DEFRA
and the GLEC framework: road 0.101, rail 0.022, sea 0.008, air 0.602. Air
against sea is a 75 times gap, which is why air freight dominates the results
on almost any dataset that uses it.

Cost factors are USD per tonne-km: road 0.12, rail 0.04, sea 0.008, air 0.19.
Air is derived from general long haul cargo at roughly 2 to 5 USD per kg.
These vary far more in the real world than the emission factors do, so treat
them as a starting point, not gospel. They live in `optimizer/factors.py` and
are meant to be edited.

Distances start as great-circle between city coordinates, and each mode then
gets a screening multiplier for how far freight really travels: air 1.05,
road 1.25, rail 1.42, sea 1.60. These are assumptions, not routed distances,
and sea in particular varies a lot from route to route. They are applied in
one function, `optimizer/distance.py`, which is where real road, rail or sea
routing would go. A candidate mode is always measured from the route's own
two ends with its own multiplier.

Confidence on each recommendation comes from redrawing every factor 200
times: high means it still cut both cost and carbon by a quarter in at least
90% of draws, moderate at least 60%.

## How it decides what is a quick win

A lane being expensive is a procurement problem. A lane being dirty is a
reporting problem. Neither is interesting on its own. What this ranks is
lanes where switching transport mode cuts cost and carbon at the same time,
by at least 25% of each.

Size alone does not qualify. A huge lane already running on the best mode
available to it has nothing to fix, so it does not appear, no matter how big
the number next to it is.

Mode switches are kept to ones that could actually happen. Air can drop to
sea on a long haul or to road and rail on a short one. Road can move to rail.
Sea is already the cheapest and cleanest per tonne-km, so nothing beats it
and those lanes are left alone.

## How much of it to believe

Every saving here is the gap between two estimates built on published freight
and emission factors, and those are ranges rather than constants. Quoting a
figure to the dollar off inputs like that implies a precision they do not
have, so part four of the report does five things about it.

**The uncertainty band.** The whole ranking is re-run two thousand times with
every cost and emission factor redrawn from a triangular distribution up to a
third either side of its published value. What comes back is a 10th to 90th
percentile band around the recoverable figure. The factors are drawn once per
run and applied to every route together, because if fuel is dearer than
assumed it is dearer everywhere on the same day; drawing them independently
would let the errors cancel and give a band far narrower than the truth. It
also reports which recommendations survive every single run, and those are the
ones to open with, because they do not depend on the factors being right, only
on the ordering being right.

**Whether the premise holds.** The Spearman rank correlation between cost and
carbon across routes, which is the assumption this entire tool is built on,
put back under test on your data instead of asserted. Ranks rather than raw
values, because route sizes are heavy-tailed and one enormous route would
otherwise decide the answer alone. If it comes back weak, the page says so:
that this network needs the cost work and the carbon work planned separately
is more useful than a chart implying otherwise.

**Concentration.** Gini coefficients and Lorenz curves for cost and carbon,
and the smallest number of routes covering eighty percent of each. That is the
practical answer to whether the fix list is four conversations or a programme.

**Outliers that are really outliers.** Routes priced unlike the rest, measured
in median absolute deviations rather than standard deviations, because with a
few dozen routes one genuine outlier inflates the spread it is measured
against until it stops looking unusual.

**Return rates with the doubt attached.** One return in four is a 25 percent
return rate and means nothing. Every rate carries a Wilson score interval,
which behaves at the small samples where the textbook one produces bounds
below zero, and a route is only called worse than the network when its whole
interval sits above the network rate.

All of it is standard library. The sample sizes are small, the methods are
textbook, and numpy would buy nothing but a wheel to install.

## Built with

Python, Flask and SQLite, with D3 for the map. Flask is the only dependency.
No build step, no frontend framework, no API keys. It runs offline.

## Layout

```
app.py              routes, JSON endpoints and the summary
optimizer/
  geo.py            city lookup and great-circle distance
  distance.py       distance by transport mode
  factors.py        cost and emission factors per mode
  db.py             schema
  ingest.py         CSV validation and loading
  analysis.py       cost to serve and emissions
  scoring.py        bottleneck ranking and what-if
  chain.py          the value chain stages and their problem checks
  diagnosis.py      the written report: diagnosis, method and plan
  stats.py          concentration, correlation and the uncertainty band
  exports.py        the workbook and the CSV
  xlsx.py           a small .xlsx writer on the standard library
  store.py          one in-memory workspace per visitor
static/
  dashboard.js      the map and the ranked routes
  workspace.js      the route panel, scenarios and comparison
  chain.js          the value chain stages
data/               city reference table and the sample datasets
tests/              180 tests: loading, thresholds, statistics, exports and the routes
tools/make_sample.py  regenerates the sample data
```

## Reshaping the network

The map's what-if panel goes past changing one route. Close a warehouse and
its work moves to whichever remaining site is nearest and has room, heaviest
routes first, while the building stops costing you rent and electricity. Name
a city and it opens a hypothetical site there, priced at the median cost and
energy per tonne of the sites you already run, because nothing in your data
describes a building that does not exist yet.

Only routes actually affected move. A route whose warehouse is still open and
still the nearest stays exactly where it is, so changing nothing saves exactly
nothing. That sounds obvious and is the whole reason the rest of the answer
can be trusted: a panel that quietly re-plans the entire network on every run
credits unrelated savings to whatever you just clicked.

Capacity comes from the warehouse file where you give it. Put a `capacity_kg`
against a site and closing its neighbour is checked against what that building
can really hold. Leave it out and a headroom figure stands in, half as much
again as the site handles today, and every site says which of the two it used
so a measured limit is never mistaken for a rule of thumb. Either way the
overflow goes to the next nearest, and if nothing has room the answer says the
shape is over capacity rather than pretending a warehouse is infinitely
elastic.

## Editing the chain

The six stages are what the tool can measure, not a claim about how your
business is arranged. Rename any of them to what you actually call it, move
them, hide one that does not apply, or add a step of your own. A stage you add
carries no figures and runs no checks, and says so, because inventing a number
to fill the space would be worse than the gap. Hiding a stage only takes it
out of the picture, and the figures behind it are still in the totals.

How you arrange the chain survives a new upload. It describes your business,
not the file you happened to load last.

## Tests

```
python -m unittest discover tests
```

180 of them, stdlib unittest, no test dependency.

`tests/test_exports.py` covers the workbook writer for the things that make
Excel refuse a file without saying why: a control character in an uploaded
name, a column past Z, a formula written without its value. It also checks
that uploaded text cannot run as a formula when the CSV is opened.

`tests/test_ingest.py` and `tests/test_recommendations.py` exist because
routes from different origins into one city were once merged into one. They
check every row is loaded or explained, routes stay separate by origin, each
route and each candidate mode is measured over its own distance, nothing is
recommended unless cost and carbon both fall by a quarter, and every saving
on a card equals current minus proposed.

The scoring thresholds decide which routes get flagged and in what order, and
they were tuned by hand. The risk was never that they are wrong, it is that
someone changes one and nothing complains until the output looks strange weeks
later. The tests pin the behaviour the thresholds exist to produce rather than
the numbers themselves, so moving one on purpose stays easy and moving one by
accident is loud.

The statistics get the opposite treatment. A Gini of a flat list is zero and a
Spearman of a reversed list is minus one, so those are pinned to the values
themselves. What sits on top of them is pinned to behaviour: the uncertainty
band has to bracket the figure quoted everywhere else, has to have width, and
has to give the same answer on every page load.

## Your data

Nothing you upload is written to disk. The file is spooled to a temporary
path for as long as the CSV parser needs to read it and deleted before the
request finishes. What survives is a graph in an in-memory SQLite database
belonging to your browser session alone, dropped after two hours idle, when
you press clear, or when the server restarts. Your browser holds one signed
cookie carrying a random identifier and nothing else.

That is deliberately not the same as claiming the server cannot read your
data. It can, because it is the thing doing the arithmetic. Making that untrue
would mean encrypting in the browser and calculating there, which is a
different piece of software, and claiming it without having built it would
fall apart the first time somebody asked how. `optimizer/store.py` and the
upload handler are about a hundred lines between them and are worth reading
rather than believing.

## Not done

**Screenshots in this file.** A portfolio repo wants a picture of the value
chain and one of the map, and there is not one here yet.

**Inventory.** Slower shipping ties up more working capital in stock and that
cost is not counted anywhere. Transit time itself is now estimated, from
typical door-to-door speeds per mode, so the report can tell you a mode switch
adds weeks rather than days. Those are planning figures, not carrier
schedules, and the report says so rather than hiding it.
