const MODE_COLOR = {
    road: "#8a8578",
    rail: "#6b5bc4",
    sea: "#2d7fb8",
    air: "#c2703a",
};

const WORLD_TOPOLOGY =
    "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";

let network = JSON.parse(document.getElementById("network-data").textContent);
let selectedId = null;
let pending = null;

const money = (value) => "$" + Math.round(value).toLocaleString("en-US");
const tonnes = (kg) =>
    (kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " t";
const percent = (value) => Math.round(value * 100) + "%";

const laneById = (id) => network.lanes.find((lane) => lane.id === id);
const flaggedLanes = () => network.lanes.filter((lane) => lane.flagged);

/* Headline: say what is wrong before showing anything to explore */

function drawFinding() {
    const flagged = flaggedLanes();
    const el = document.getElementById("finding");
    if (!flagged.length) {
        el.innerHTML =
            "<p>Nothing in this network can be improved by changing transport " +
            "mode. Every route is already on the best option available to it.</p>";
        return;
    }

    const cost = flagged.reduce((sum, l) => sum + l.switch.saved_cost, 0);
    const co2e = flagged.reduce((sum, l) => sum + l.switch.saved_co2e, 0);
    const costPct = flagged.reduce((sum, l) => sum + l.network_cost_pct, 0);
    const co2ePct = flagged.reduce((sum, l) => sum + l.network_co2e_pct, 0);
    const inbound = flagged.filter((l) => l.leg === "inbound").length;

    el.innerHTML = `
    <p class="finding-lead">
      <strong>${flagged.length} route${flagged.length === 1 ? "" : "s"}</strong>
      cost more and emit more than they need to. Changing how they ship
      would save <strong class="cost-ink">${money(cost)}</strong> a year and
      <strong class="carbon-ink">${tonnes(co2e)} of CO2e</strong>.
    </p>
    <p class="finding-sub">
      That is ${percent(costPct)} of what this network costs to run and
      ${percent(co2ePct)} of what it emits${
          inbound
              ? `, and ${inbound} of the ${flagged.length} ${inbound === 1 ? "is" : "are"} on the way in from suppliers rather than out to customers`
              : ""
      }.
    </p>`;
}

/* Map */

const mapEl = document.getElementById("map");
const width = 900;
const height = 470;

const projection = d3
    .geoNaturalEarth1()
    .scale(172)
    .translate([width / 2, height / 2 + 14]);
const path = d3.geoPath(projection);

const svg = d3
    .select("#map")
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .style("display", "block");

const canvas = svg.append("g");
const landLayer = canvas.append("g");
const laneLayer = canvas.append("g");
const nodeLayer = canvas.append("g");

let zoomScale = 1;

const zoom = d3
    .zoom()
    .scaleExtent([1, 12])
    // Both extents have to be in viewBox units. Left to itself d3 measures the
    // element in CSS pixels, and the mismatch against translateExtent cancels
    // every zoom out to nothing.
    .extent([
        [0, 0],
        [width, height],
    ])
    .translateExtent([
        [0, 0],
        [width, height],
    ])
    .on("zoom", (event) => {
        zoomScale = event.transform.k;
        canvas.attr("transform", event.transform);
        rescale();
    });

svg.call(zoom);

// Everything drawn on the map is sized in screen pixels, so each element gets
// divided by the zoom level. Without this, zooming in turns the routes into
// thick ribbons and the labels into billboards.
function rescale() {
    const k = zoomScale;
    laneLayer.selectAll("path.lane-line").attr("stroke-width", function () {
        return this.dataset.width / k;
    });
    laneLayer.selectAll("path.lane-hit").attr("stroke-width", 14 / k);
    nodeLayer.selectAll("circle").attr("r", function () {
        return this.dataset.r / k;
    });
    nodeLayer.selectAll("rect").attr("width", 9 / k).attr("height", 9 / k)
        .attr("x", function () { return this.dataset.x - 4.5 / k; })
        .attr("y", function () { return this.dataset.y - 4.5 / k; });
    nodeLayer.selectAll("circle, rect").attr("stroke-width", 1.6 / k);
    nodeLayer.selectAll("text")
        .attr("font-size", 8.5 / k)
        .attr("y", function () { return Number(this.dataset.y) - 11 / k; })
        // Supplier names sit on top of each other around the manufacturing
        // clusters, so they stay hidden until there is room for them.
        .attr("display", function () {
            return this.dataset.kind === "supplier" && k < 2.4 ? "none" : null;
        });
}

// Called on the selection, not on a transition. Wrapping these in
// transition().call(zoom.scaleBy, k) silently does nothing, so the buttons
// step the zoom directly and the CSS transition on the group smooths it.
document.getElementById("zoom-in").addEventListener("click", () => {
    svg.call(zoom.scaleBy, 1.6);
});
document.getElementById("zoom-out").addEventListener("click", () => {
    svg.call(zoom.scaleBy, 1 / 1.6);
});
document.getElementById("zoom-reset").addEventListener("click", () => {
    svg.call(zoom.transform, d3.zoomIdentity);
});

function drawLand(world) {
    const countries = topojson.feature(world, world.objects.countries);
    landLayer
        .selectAll("path")
        .data(countries.features)
        .join("path")
        .attr("d", path)
        .attr("fill", "#ded7c9")
        .attr("stroke", "#f4f1ea")
        .attr("stroke-width", 0.6);
}

function mapUnavailable() {
    mapEl.insertAdjacentHTML(
        "beforeend",
        '<p class="map-fallback">The world map could not load, so the lanes ' +
            "are drawn without country outlines.</p>"
    );
}

// Two-argument then, not then().catch(): a rejection here means the topology
// genuinely failed to fetch. Anything thrown while drawing is a bug and
// should reach the console rather than being reported as a network problem.
d3.json(WORLD_TOPOLOGY).then(
    (world) => {
        drawLand(world);
        render();
    },
    () => {
        mapUnavailable();
        render();
    }
);

const tip = document.getElementById("tip");

function showTip(event, html) {
    tip.innerHTML = html;
    tip.hidden = false;
    const box = mapEl.getBoundingClientRect();
    tip.style.left = Math.min(event.clientX - box.left + 14, box.width - 210) + "px";
    tip.style.top = event.clientY - box.top + 14 + "px";
}

const hideTip = () => {
    tip.hidden = true;
};

function laneWidth(lane) {
    const max = Math.max(...network.lanes.map((l) => l.total_weight_kg));
    return 1 + 3.4 * Math.sqrt(lane.total_weight_kg / max);
}

function drawNetwork() {
    const nodeById = new Map(network.nodes.map((n) => [n.id, n]));

    laneLayer.selectAll("*").remove();
    network.lanes.forEach((lane) => {
        const origin = nodeById.get(lane.origin_id);
        const dest = nodeById.get(lane.dest_id);
        if (!origin || !dest) return;
        const a = projection([origin.lon, origin.lat]);
        const b = projection([dest.lon, dest.lat]);
        if (!a || !b) return;

        const d = arc(a, b);
        const group = laneLayer.append("g").style("cursor", "pointer");
        const tipHtml = `
      <strong>${lane.origin_name} to ${lane.dest_name}</strong>
      <span>${lane.leg} by ${lane.mode}, ${Math.round(lane.distance_km).toLocaleString()} km</span>
      <span class="cost-ink">${money(lane.cost)}</span>
      <span class="carbon-ink">${tonnes(lane.co2e)} CO2e</span>
      ${lane.flagged ? '<span class="tip-flag">Flagged: switching to ' + lane.switch.mode + " would cut both</span>" : ""}`;

        group
            .on("click", () => select(lane.id))
            .on("mousemove", (event) => showTip(event, tipHtml))
            .on("mouseleave", hideTip);

        group
            .append("path")
            .attr("class", "lane-hit")
            .attr("d", d)
            .attr("stroke", "transparent")
            .attr("stroke-width", 14 / zoomScale)
            .attr("fill", "none");

        const line = group
            .append("path")
            .attr("class", "lane-line")
            .attr("d", d)
            .attr("stroke", MODE_COLOR[lane.mode])
            .attr("stroke-linecap", "round")
            .attr("fill", "none")
            .attr("opacity", lane.id === selectedId ? 1 : lane.flagged ? 0.95 : 0.4)
            .attr("stroke-dasharray", lane.flagged ? "6,4" : null);

        const w = laneWidth(lane) * (lane.id === selectedId ? 2 : 1);
        line.node().dataset.width = w;
        line.attr("stroke-width", w / zoomScale);
    });

    nodeLayer.selectAll("*").remove();
    network.nodes.forEach((node) => {
        const point = projection([node.lon, node.lat]);
        if (!point) return;

        const lanes = network.lanes.filter(
            (l) => l.dest_id === node.id || l.origin_id === node.id
        );
        const cost = lanes.reduce((s, l) => s + l.cost, 0);
        const co2e = lanes.reduce((s, l) => s + l.co2e, 0);
        const onFlagged = lanes.some((l) => l.flagged);

        const group = nodeLayer.append("g").style("cursor", "pointer");
        group
            .on("click", () => {
                const lane =
                    lanes.find((l) => l.flagged) || lanes[0];
                if (lane) select(lane.id);
            })
            .on("mousemove", (event) =>
                showTip(
                    event,
                    `<strong>${node.name}</strong>
           <span>${NODE_LABEL[node.node_type]}, ${lanes.length} route${lanes.length === 1 ? "" : "s"}</span>
           <span class="cost-ink">${money(cost)}</span>
           <span class="carbon-ink">${tonnes(co2e)} CO2e</span>`
                )
            )
            .on("mouseleave", hideTip);

        if (node.node_type === "supplier") {
            const rect = group
                .append("rect")
                .attr("fill", "#faf8f4")
                .attr("stroke", onFlagged ? "#9d3427" : "#6b5bc4");
            rect.node().dataset.x = point[0];
            rect.node().dataset.y = point[1];
        } else {
            const isWarehouse = node.node_type === "warehouse";
            const circle = group
                .append("circle")
                .attr("cx", point[0])
                .attr("cy", point[1])
                .attr("fill", isWarehouse ? "#1a6b4f" : "#ffffff")
                .attr("stroke", onFlagged && !isWarehouse ? "#9d3427" : "#1a6b4f");
            circle.node().dataset.r = isWarehouse ? 6.5 : 3.6;
        }

        if (node.node_type !== "customer") {
            const text = group
                .append("text")
                .attr("x", point[0])
                .attr("text-anchor", "middle")
                .attr("class", "map-label")
                .text(node.name);
            text.node().dataset.y = point[1];
            text.node().dataset.kind = node.node_type;
        }
    });

    rescale();
}

const NODE_LABEL = {
    supplier: "Supplier",
    warehouse: "Warehouse",
    customer: "Customer city",
};

function arc(a, b) {
    // A slight curve keeps overlapping lanes readable instead of stacking
    // them all on the same straight line.
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const bend = Math.sqrt(dx * dx + dy * dy) * 1.9;
    return `M${a[0]},${a[1]}A${bend},${bend} 0 0,1 ${b[0]},${b[1]}`;
}

/* Quick wins */

function drawWins() {
    const flagged = flaggedLanes().slice(0, 6);
    const wins = document.getElementById("wins");

    if (!flagged.length) {
        wins.innerHTML =
            '<p class="empty">No lane can be improved by changing transport ' +
            "mode. Every route is already on the best option available to it.</p>";
        return;
    }

    wins.innerHTML = flagged
        .map((lane, index) => {
            const s = lane.switch;
            return `
      <article class="win${lane.id === selectedId ? " on" : ""}" data-id="${lane.id}">
        <div class="win-rank">${index + 1}</div>
        <div class="win-body">
          <h3>${lane.origin_name} to ${lane.dest_name}
            <span class="leg-tag ${lane.leg}">${lane.leg}</span>
          </h3>
          <p class="win-move">
            Ship it by <strong>${s.mode}</strong> instead of
            <strong>${lane.mode}</strong>, over
            ${Math.round(lane.distance_km).toLocaleString()} km
          </p>
          <div class="win-gain">
            <span class="cost-ink">${money(s.saved_cost)} a year</span>
            <span class="carbon-ink">${tonnes(s.saved_co2e)} CO2e</span>
            <span class="muted-ink">${percent(lane.network_cost_pct)} of network cost</span>
          </div>
        </div>
        <label class="win-effort">
          <span>How hard</span>
          <select data-effort="${lane.id}">
            <option value=""${!lane.effort ? " selected" : ""}>Not judged</option>
            <option value="low"${lane.effort === "low" ? " selected" : ""}>Easy</option>
            <option value="med"${lane.effort === "med" ? " selected" : ""}>Medium</option>
            <option value="high"${lane.effort === "high" ? " selected" : ""}>Hard</option>
          </select>
        </label>
      </article>`;
        })
        .join("");

    wins.querySelectorAll(".win").forEach((card) => {
        card.addEventListener("click", (event) => {
            if (event.target.closest(".win-effort")) return;
            select(Number(card.dataset.id));
        });
    });

    wins.querySelectorAll("[data-effort]").forEach((field) => {
        field.addEventListener("change", (event) =>
            saveEffort(Number(event.target.dataset.effort), event.target.value)
        );
    });
}

async function saveEffort(edgeId, effort) {
    const response = await fetch("/api/effort", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edge_id: edgeId, effort: effort }),
    });
    if (!response.ok) return;
    network = (await response.json()).network;
    render();
}

/* Detail panel and what-if */

function select(id) {
    selectedId = id;
    pending = null;
    render();
}

function render() {
    drawFinding();
    drawNetwork();
    drawWins();
    drawDetail();
}

function drawDetail() {
    const panel = document.getElementById("detail");
    const lane = laneById(selectedId);

    if (!lane) {
        panel.innerHTML =
            '<p class="empty">Pick a route on the map or a quick win below.</p>';
        return;
    }

    const modes = ["road", "rail", "sea", "air"];
    const result = pending && pending.edge_id === lane.id ? pending : null;

    panel.innerHTML = `
    <header class="detail-head">
      <h3>${lane.origin_name} to ${lane.dest_name}</h3>
      <p>${lane.leg === "inbound" ? "Supplier into warehouse" : "Warehouse out to customer"},
         ${Math.round(lane.distance_km).toLocaleString()} km by ${lane.mode},
         ${Math.round(lane.total_weight_kg / 1000).toLocaleString()} t a year</p>
    </header>

    <dl class="detail-figures">
      <div><dt>Costs</dt><dd class="cost-ink">${money(lane.cost)}</dd></div>
      <div><dt>Emits</dt><dd class="carbon-ink">${tonnes(lane.co2e)}</dd></div>
    </dl>

    <div class="whatif">
      <p class="whatif-title">What if it shipped differently</p>
      <label>Ship it by
        <select id="sim-mode">
          ${modes
              .map(
                  (m) =>
                      `<option value="${m}"${m === (result ? result.mode : lane.mode) ? " selected" : ""}>${m}</option>`
              )
              .join("")}
        </select>
      </label>
      <label>${lane.leg === "inbound" ? "Delivered into" : "Shipped from"}
        <select id="sim-origin"${lane.leg === "inbound" ? " disabled" : ""}>
          ${network.warehouses
              .map(
                  (w) =>
                      `<option value="${w.id}"${w.id === lane.origin_id ? " selected" : ""}>${w.name}</option>`
              )
              .join("")}
        </select>
      </label>
      ${result ? resultBlock(result) : '<p class="whatif-hint">Change either one and the numbers update.</p>'}
    </div>`;

    document
        .getElementById("sim-mode")
        .addEventListener("change", () => runSimulation(lane.id));
    const origin = document.getElementById("sim-origin");
    if (!origin.disabled) {
        origin.addEventListener("change", () => runSimulation(lane.id));
    }
}

function resultBlock(result) {
    const cost = result.saved.cost;
    const co2e = result.saved.co2e;
    const good = cost > 0 && co2e > 0;
    const bad = cost < 0 && co2e < 0;
    const tone = good ? "good" : bad ? "bad" : "mixed";
    const verb = (value) => (value > 0 ? "saves " : "adds ");

    return `
    <div class="sim ${tone}">
      <p class="sim-line">
        ${verb(cost)}<strong>${money(Math.abs(cost))}</strong>
        and ${verb(co2e)}<strong>${tonnes(Math.abs(co2e))} CO2e</strong>
      </p>
      <p class="sim-sub">
        ${Math.round(result.distance_km).toLocaleString()} km by ${result.mode}.
        ${good ? "Both fall." : bad ? "Both rise." : "One improves at the other's expense."}
      </p>
    </div>`;
}

async function runSimulation(edgeId) {
    const response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            edge_id: edgeId,
            mode: document.getElementById("sim-mode").value,
            origin_id: document.getElementById("sim-origin").value,
        }),
    });
    if (!response.ok) return;
    pending = await response.json();
    drawDetail();
}

// Arriving from a problem in the value chain opens that exact lane. Otherwise
// open on the biggest win, so the page arrives showing something rather than
// asking to be explored first.
const requested = Number(new URLSearchParams(location.search).get("lane"));
const opener = (requested && laneById(requested)) || flaggedLanes()[0];
if (opener) selectedId = opener.id;
render();

if (requested && laneById(requested)) {
    document.querySelector(".map-panel").scrollIntoView({ behavior: "smooth" });
}
