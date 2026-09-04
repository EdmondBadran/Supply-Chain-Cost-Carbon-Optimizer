# Decisions log

## 2026-09-05 - stack: Flask + SQLite + pandas, server-rendered frontend

Chose Flask over FastAPI/Django and a plain server-rendered frontend over
React/Vue. Reason: this needs to look and run like a solo dev's tool, run
with one command, and demo from a prebuilt dataset with zero setup friction.
A build step or SPA framework would fight that. Chart.js off a CDN gives
real charts without any bundler.

Rejected: Django (too much scaffolding for the scope), a separate JS
frontend (extra moving parts, extra build step, no real benefit for a
dashboard this size).

## 2026-09-05 - quick wins as a ranking, not a raw report

Core ask from the founder: don't just show cost numbers and carbon numbers
side by side, rank the overlap - things that are both cheap to fix and high
in cost/carbon impact. This is the actual product differentiator, so the
ranking logic (Stage 5) is the part to get right, not just calculate-and-
display.

## 2026-09-05 - grilling round before Stage 1 (4 decisions)

Ran a plan-sharpening interview before touching the schema, since the ranking
logic depends on things the original input list did not actually cover.

**Scope: portfolio demo now, real-use door left open.** Not purely a toy demo.
Avoid decisions that would need a rewrite if this ever got used on an actual
company's export - so real CSV validation, not hardcoded assumptions.
Consequence: budget more time in Stage 2 for messy-input handling than a pure
demo would need.

**Effort/fix-cost signal: manual tagging, not a heuristic.** Nothing in the
input data (order volume, routes, warehouse cost, supplier terms, returns)
tells us how hard something is to fix - that is a judgment call, not a
number a CSV can carry. So: rank by cost+carbon overlap first, let the user
tag effort (low/med/high) per flagged item in the dashboard, then re-rank
with that tag folded in. Rejected a hardcoded effort-by-category heuristic
- would be guessing at operational reality we don't have. Rejected skipping
effort entirely - it's the "quick win" framing that makes this different
from a generic dashboard, worth the extra interaction.

Consequence: the dashboard is not read-only anymore. It needs a write path
(save an effort tag against a flagged item, id'd in SQLite) and a way to
re-sort once tags exist. This moves scope from Stage 6 into something closer
to a real feature, not just charts.

**Distance: calculated from zip/city, not a flat column or a zone matrix.**
Rejected requiring a precomputed distance_km column (unrealistic - real order
data doesn't have this) and rejected a fixed zone-to-zone distance matrix
(faster to build but reads as fake in a demo). Going with real geocoding
instead: bundle a static zip/city -> lat-long reference table in the repo
(no external API, no key, so it still runs offline with one command) and
compute distance with the haversine formula.

Consequence: need to source and bundle a reference dataset (e.g. a free
US zip code centroid table) as part of Stage 1, and the sample CSV needs
real zip/city values that exist in that table, not made-up ones.

**Warehouse cost allocation: per unit weight/volume, not per order or per
revenue.** Order count under-weights heavy/bulky orders, revenue-based
conflates cost-to-serve with margin. Weight/volume is the more honest proxy
for actual warehouse burden. Consequence: the CSV schema needs a weight or
volume column per order line, and the sample dataset has to vary that
realistically across orders (not a constant), or the allocation logic has
nothing to differentiate on.

## 2026-09-05 - global, not US-only

Founder's call, made during Stage 1: this has to work anywhere, not just US
cities. Dropped the planned US zip-code centroid table for GeoNames
cities15000 (34,135 cities over 15k population, 244 countries, CC BY 4.0,
1.3 MB trimmed to city/country/lat/lon/population). Still no external API,
still runs offline, still one command.

Consequences that follow from going global:
- Units are km and tonne-km, not miles. Emission factors are now kg CO2e per
  tonne-km from DEFRA/GLEC ranges, which is how freight emissions are
  actually accounted for, so this is more defensible than the per-mile
  guesswork the US version would have used.
- Sea freight becomes a real mode (it was not in a US-only model), and it is
  the cheapest and lowest-carbon option per tonne-km by a wide margin. That
  makes air-vs-sea the single biggest quick win the tool can surface.
- The map in Stage 6 needs a world projection, not the US-only one used in
  the early mockups.
- Cities below 15k population will not resolve. Mitigated by accepting
  optional lat/lon columns in the CSV, which override the lookup entirely.

## 2026-09-05 - dropped pandas

Stack decision above said Flask + SQLite + pandas. Dropped pandas during
Stage 1 without replacing it. The only things it was doing were reading a
CSV and one groupby. The stdlib csv module reads the file, and the groupby
is a SQL GROUP BY over data that was going into SQLite anyway, with haversine
registered as a custom SQLite function so distance is computed in the same
statement.

Net: one dependency instead of two, ~40 MB less to install, and the rollup
logic lives in the database where the rest of the querying will happen.
Flask is now the only runtime dependency.

## 2026-09-05 - pivot: interactive network map replaces the static ranked table as the primary view

Original plan was upload CSV -> ranked table of quick wins. After seeing a
mockup of that table, the founder wanted something more concrete: see the
actual supply chain (warehouses, routes, regions) as a network, edit it, and
watch cost/carbon respond - "how much reducing that cost will lead to
sustainability."

This is a real architecture change, not just a UI skin: the data model moves
from flat CSV rows to a graph (nodes = warehouses/suppliers/regions, edges =
routes between them), because a map needs nodes and edges, a table doesn't.
Also solves the "works for small and big businesses" requirement for free -
a graph has no fixed size, it renders whatever nodes/edges exist, so a 3-node
network and a 300-node network use the same code path.

The bottleneck-scoring logic (cost+carbon overlap, effort tagging) from the
earlier decisions did not change - only where it surfaces. Instead of a table
row, a bottleneck is a flag on a node or edge on the map. The ranked table
becomes a secondary/drill-down view, not the main screen.

**Edit scope for v1: toggle and reassign only, not full add/remove.** Can
change an edge's transport mode or reassign a region to a different existing
warehouse, and see cost/carbon recalculate live with a before/after delta.
Rejected full add/remove of nodes for v1 - deleting a warehouse means solving
where its regions go (nearest-warehouse logic, capacity limits), which is a
real optimization problem, not arithmetic. Doing that half-right would look
worse than not having it. Revisit once toggle/reassign is working and proven
useful - noted in PLAN.md Stage 7 as deferred, not dropped.
