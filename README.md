# Supply chain cost and carbon optimizer

Most companies track logistics cost in one report and emissions in another,
and the two never get looked at together. That is a problem, because the
routes doing the most financial damage are very often the same routes doing
the most environmental damage, and nobody notices because the numbers live in
different spreadsheets.

This tool loads your order data, works out what every lane costs to serve and
what it emits, and ranks the places where one change fixes both.

## What it does

Load a CSV of order lines and it builds a graph of your network: warehouses
and destination cities as nodes, shipping lanes as edges. Then for every lane
it calculates:

- **Cost to serve** from transport by mode, warehouse storage and handling
  allocated by weight, and the cost of returns
- **Emissions** from tonne-km by mode, packaging per order, the lane's share
  of warehouse energy, and the return legs

Then it looks for lanes where a different transport mode would cut both, and
ranks those by how much of the network's total they recover. You can tag how
hard each one is to actually do, and the ranking reorders around that.

The map is clickable. Pick any lane and you can try a different mode or serve
it from a different warehouse, and it tells you what that does to cost and
carbon before you commit to anything.

## Running it

```
pip install -r requirements.txt
python app.py
```

Then open http://localhost:5000. There is a sample dataset built in, so you
can click straight through to the dashboard without finding a CSV first.

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

You can also upload a second CSV of warehouse costs with `name`,
`storage_cost_annual`, `energy_kwh_annual` and `grid_intensity`. Without it
the warehouse side of the calculation falls back to defaults, which makes the
cost-to-serve numbers less useful, so it is worth providing.

Anything the loader cannot read gets reported by line number and the rest of
the file still loads. One bad row does not cost you the import.

## The numbers behind it

Emission factors are kg CO2e per tonne-km in the ranges published by DEFRA
and the GLEC framework: road 0.062, rail 0.022, sea 0.008, air 0.602. The
gap between air and sea is the reason air freight dominates the results on
almost any dataset that uses it.

Cost factors are USD per tonne-km and are much more variable in the real
world, so treat them as a starting point rather than gospel. They live in
`optimizer/factors.py` and are meant to be edited.

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

## Built with

Python, Flask and SQLite, with D3 for the map. Flask is the only dependency.
No build step, no frontend framework, no API keys. It runs offline.

## Layout

```
app.py              routes
optimizer/
  geo.py            city lookup and great-circle distance
  factors.py        cost and emission factors per mode
  db.py             schema
  ingest.py         CSV validation and loading
  analysis.py       cost to serve and emissions
  scoring.py        bottleneck ranking and what-if
data/               city reference table and the sample dataset
tools/make_sample.py  regenerates the sample data
```
