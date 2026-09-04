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

const money = (value) =>
    "$" + Math.round(value).toLocaleString("en-US");
const tonnes = (kg) =>
    (kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " t";
const percent = (value) => Math.round(value * 100) + "%";

const laneById = (id) => network.lanes.find((lane) => lane.id === id);

function laneWidth(lane) {
    const weights = network.lanes.map((l) => l.total_weight_kg);
    const max = Math.max(...weights);
    return 1 + 3.4 * Math.sqrt(lane.total_weight_kg / max);
}

/* Map */

const mapEl = document.getElementById("map");
const width = 900;
const height = 460;

const projection = d3
    .geoNaturalEarth1()
    .scale(168)
    .translate([width / 2, height / 2 + 12]);
const path = d3.geoPath(projection);

const svg = d3
    .select("#map")
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("width", "100%")
    .style("display", "block");

const landLayer = svg.append("g");
const laneLayer = svg.append("g");
const nodeLayer = svg.append("g");

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
        drawNetwork();
    },
    () => {
        mapUnavailable();
        drawNetwork();
    }
);

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

        const group = laneLayer
            .append("g")
            .attr("class", "lane")
            .style("cursor", "pointer")
            .on("click", () => select(lane.id));

        // A wide invisible line under each lane so thin routes stay clickable.
        group
            .append("path")
            .attr("d", arc(a, b))
            .attr("stroke", "transparent")
            .attr("stroke-width", 12)
            .attr("fill", "none");

        group
            .append("path")
            .attr("d", arc(a, b))
            .attr("stroke", MODE_COLOR[lane.mode])
            .attr("stroke-width", laneWidth(lane))
            .attr("stroke-linecap", "round")
            .attr("fill", "none")
            .attr("opacity", lane.id === selectedId ? 1 : lane.flagged ? 0.92 : 0.45)
            .attr("stroke-dasharray", lane.flagged ? "6,4" : null);
    });

    nodeLayer.selectAll("*").remove();
    network.nodes.forEach((node) => {
        const point = projection([node.lon, node.lat]);
        if (!point) return;
        const isWarehouse = node.node_type === "warehouse";
        const touched = network.lanes.some(
            (lane) =>
                lane.flagged &&
                (lane.dest_id === node.id || lane.origin_id === node.id)
        );

        const group = nodeLayer
            .append("g")
            .style("cursor", isWarehouse ? "default" : "pointer");

        if (!isWarehouse) {
            group.on("click", () => {
                const lane = network.lanes.find((l) => l.dest_id === node.id);
                if (lane) select(lane.id);
            });
        }

        group
            .append("circle")
            .attr("cx", point[0])
            .attr("cy", point[1])
            .attr("r", isWarehouse ? 6 : 3.6)
            .attr("fill", isWarehouse ? "#1a6b4f" : "#ffffff")
            .attr("stroke", touched && !isWarehouse ? "#9d3427" : "#1a6b4f")
            .attr("stroke-width", isWarehouse ? 0 : 1.6);

        if (isWarehouse) {
            group
                .append("text")
                .attr("x", point[0])
                .attr("y", point[1] - 11)
                .attr("text-anchor", "middle")
                .attr("class", "map-label")
                .text(node.name);
        }
    });
}

function arc(a, b) {
    // A slight curve keeps overlapping lanes readable instead of stacking
    // them all on the same straight line.
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const bend = Math.sqrt(dx * dx + dy * dy) * 1.9;
    return `M${a[0]},${a[1]}A${bend},${bend} 0 0,1 ${b[0]},${b[1]}`;
}

/* Quick wins list */

function drawWins() {
    const flagged = network.lanes.filter((lane) => lane.flagged).slice(0, 6);
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
          <h3>${lane.origin_name} to ${lane.dest_name}</h3>
          <p class="win-move">
            Move <strong>${lane.mode}</strong> to <strong>${s.mode}</strong>
            over ${Math.round(lane.distance_km).toLocaleString()} km
          </p>
          <div class="win-gain">
            <span class="cost-ink">${money(s.saved_cost)}</span>
            <span class="carbon-ink">${tonnes(s.saved_co2e)} CO2e</span>
            <span class="muted-ink">${percent(lane.network_cost_pct)} of network cost</span>
          </div>
        </div>
        <label class="win-effort">
          <span>Effort</span>
          <select data-effort="${lane.id}">
            <option value=""${!lane.effort ? " selected" : ""}>Untagged</option>
            <option value="low"${lane.effort === "low" ? " selected" : ""}>Low</option>
            <option value="med"${lane.effort === "med" ? " selected" : ""}>Medium</option>
            <option value="high"${lane.effort === "high" ? " selected" : ""}>High</option>
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

    wins.querySelectorAll("[data-effort]").forEach((select) => {
        select.addEventListener("change", (event) =>
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
    const data = await response.json();
    network = data.network;
    render();
}

/* Detail panel and what-if */

function select(id) {
    selectedId = id;
    pending = null;
    render();
}

function render() {
    drawNetwork();
    drawWins();
    drawDetail();
}

function drawDetail() {
    const panel = document.getElementById("detail");
    const lane = laneById(selectedId);

    if (!lane) {
        panel.innerHTML =
            '<p class="empty">Pick a lane on the map or a quick win below to ' +
            "see what it costs, what it emits, and what changing it would do.</p>";
        return;
    }

    const modes = ["road", "rail", "sea", "air"];
    const warehouses = network.warehouses;
    const result = pending && pending.edge_id === lane.id ? pending : null;

    panel.innerHTML = `
    <header class="detail-head">
      <h3>${lane.origin_name} to ${lane.dest_name}</h3>
      <p>${Math.round(lane.distance_km).toLocaleString()} km by ${lane.mode},
         ${lane.order_count.toLocaleString()} orders,
         ${Math.round(lane.total_weight_kg / 1000).toLocaleString()} t shipped</p>
    </header>

    <dl class="detail-figures">
      <div><dt>Cost to serve</dt><dd class="cost-ink">${money(lane.cost)}</dd></div>
      <div><dt>Emissions</dt><dd class="carbon-ink">${tonnes(lane.co2e)} CO2e</dd></div>
    </dl>

    <div class="whatif">
      <p class="whatif-title">Try a change</p>
      <label>Transport mode
        <select id="sim-mode">
          ${modes
              .map(
                  (m) =>
                      `<option value="${m}"${m === (result ? result.mode : lane.mode) ? " selected" : ""}>${m}</option>`
              )
              .join("")}
        </select>
      </label>
      <label>Served from
        <select id="sim-origin">
          ${warehouses
              .map(
                  (w) =>
                      `<option value="${w.id}"${w.id === lane.origin_id ? " selected" : ""}>${w.name}</option>`
              )
              .join("")}
        </select>
      </label>
      ${result ? resultBlock(result) : '<p class="whatif-hint">Change either one to see the effect.</p>'}
    </div>`;

    document
        .getElementById("sim-mode")
        .addEventListener("change", () => runSimulation(lane.id));
    document
        .getElementById("sim-origin")
        .addEventListener("change", () => runSimulation(lane.id));
}

function resultBlock(result) {
    const cost = result.saved.cost;
    const co2e = result.saved.co2e;
    const good = cost > 0 && co2e > 0;
    const bad = cost < 0 && co2e < 0;
    const tone = good ? "good" : bad ? "bad" : "mixed";
    const sign = (value) => (value > 0 ? "saves " : "adds ");

    return `
    <div class="sim ${tone}">
      <p class="sim-line">
        ${sign(cost)}<strong>${money(Math.abs(cost))}</strong>
        and ${sign(co2e)}<strong>${tonnes(Math.abs(co2e))} CO2e</strong>
      </p>
      <p class="sim-sub">
        ${Math.round(result.distance_km).toLocaleString()} km by ${result.mode}.
        ${good ? "Cost and carbon both fall." : bad ? "Both rise." : "One improves at the other's expense."}
      </p>
    </div>`;
}

async function runSimulation(edgeId) {
    const mode = document.getElementById("sim-mode").value;
    const originId = document.getElementById("sim-origin").value;
    const response = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            edge_id: edgeId,
            mode: mode,
            origin_id: originId,
        }),
    });
    if (!response.ok) return;
    pending = await response.json();
    drawDetail();
}

render();
