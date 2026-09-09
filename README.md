# Supply chain cost and carbon optimizer

Most companies track logistics cost in one report and emissions in another,
and the two never get looked at together. That is a problem, because the
routes doing the most financial damage are very often the same routes doing
the most environmental damage, and nobody notices because the numbers live in
different spreadsheets.

This tool loads your order data, works out what every route costs to serve and
what it emits, and ranks the places where one change fixes both.

## The six views

**The landing page** is where it opens, and it explains the problem before
showing a single number. The demo is already loaded and one click away, so
nothing is hidden behind a form.

**The value chain** is the diagnostic itself, drawn as one river of money.
The band thickens at every stage that adds cost, so you can see where the
spend accumulates without reading a legend, and returns runs backwards
underneath it. Three steps down the page: this is your chain, this is what is
wrong, this is what to do. Six stages, suppliers through to
returns, each showing what it costs, what it emits, and what is wrong inside
it. Warehousing gets checked for carbon per tonne handled, customers for cost
per tonne to serve, returns for return rate, freight for whether a different
transport mode would be better. Click a stage to see its problems, click a
problem to land on that exact route on the map.

**The optimizer** is the world map. Every route drawn and scored, the ones
worth changing flagged, and a panel where you can try a different transport
mode or a different warehouse and watch cost and carbon move before you commit
to anything.

**The report** writes the whole thing up the way a consultant would hand it
over. The truth about your chain in one paragraph, every stage walked through
in plain language, the problems ranked across stages, how each figure was
reached, and a numbered plan with the money, the carbon and the operational
catch on every step. It ends with the three things to do if you only do three.
Every sentence is built from your own data.

**The statistics page** turns the tool's own output back on itself. How
concentrated cost and carbon are across routes, whether the two actually land
on the same routes in your network or only in the pitch, how far the
recoverable figure moves when the factors are redrawn inside their published
ranges, and which recommendations survive that unchanged. It is the page that
tells you how hard to lean on the rest of them.

**The method page** shows the working. Every formula, every factor with its
source, every assumption, and a section on what the tool does not account for
at all. You should not have to take any number here on faith.

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

The page opens by telling you what it found, in a sentence, before any chart.
The map is zoomable and every route and location explains itself on hover.
Pick any route and you can try a different transport mode, or serve it from a
different warehouse, and it tells you what that does to cost and carbon
before you commit to anything.

## Running it

```
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000. The sample dataset loads itself, so you land
on a working value chain rather than an upload form.

    /           the landing page
    /chain      the value chain and the diagnosis
    /dashboard  the optimizer, map and what-if
    /diagnosis  the written report and the plan
    /stats      how much of it to believe
    /method     how every number is worked out
    /data       load your own CSV
    /privacy    what happens to a file you upload

## Your own data

The orders file needs these columns:

| Column | What it is |
| --- | --- |
| `origin_name` | The warehouse or DC the order shipped from |
| `origin_city` | City the warehouse is in |
| `dest_city` | Where it went |
| `weight_kg` | Shipment weight |
| `mode` | road, rail, sea or air |

These are optional and make the results better: `origin_country`,
`dest_country`, `order_ref`, `order_date`, `customer_id`, `units`,
`product_category`, `order_value`, `returned`.

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
and the GLEC framework: road 0.062, rail 0.022, sea 0.008, air 0.602. Air
against sea is a 75 times gap, which is why air freight dominates the results
on almost any dataset that uses it.

Cost factors are USD per tonne-km: road 0.12, rail 0.04, sea 0.008, air 0.19.
Air is derived from general long haul cargo at roughly 2 to 5 USD per kg.
These vary far more in the real world than the emission factors do, so treat
them as a starting point, not gospel. They live in `optimizer/factors.py` and
are meant to be edited.

Distances are great-circle between city coordinates. Real routed distance is
longer, so the absolute numbers run slightly low, but the comparison between
two options on the same lane holds up, which is what the ranking depends on.

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
have, so the statistics page does five things about it.

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
app.py              routes and the JSON endpoints the map calls
optimizer/
  geo.py            city lookup and great-circle distance
  factors.py        cost and emission factors per mode
  db.py             schema
  ingest.py         CSV validation and loading
  analysis.py       cost to serve and emissions
  scoring.py        bottleneck ranking and what-if
  chain.py          the value chain stages and their problem checks
  diagnosis.py      the written report: diagnosis, method and plan
  stats.py          concentration, correlation and the uncertainty band
  store.py          one in-memory workspace per visitor
static/
  dashboard.js      the map, the ranking and the what-if panel
  chain.js          the value chain stages
data/               city reference table and the sample datasets
tests/              81 tests: the scoring thresholds and the statistics
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

81 of them, stdlib unittest, no test dependency.

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
schedules, and the method page says so rather than hiding it.
