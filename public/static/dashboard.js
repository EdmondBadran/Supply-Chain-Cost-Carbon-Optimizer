// The map and the ranked routes in step 3.
//
// Orange and green mean cost and carbon everywhere on the site, so the map
// never uses them for a transport mode. Modes are told apart by line pattern:
// road solid, rail dotted, sea long dashes, air short dashes. Whether a route
// is worth changing is told by brightness and weight, not colour.

const MODE_DASH = {
    road: null,
    rail: [0.1, 2.4],
    sea: [4.2, 1.8],
    air: [2, 1.6],
};

const INK = {
    flagged: "#f3f0ea",
    other: "#8a8478",
    halo: "rgba(243, 240, 234, 0.2)",
};

const REDUCED_MOTION = matchMedia("(prefers-reduced-motion: reduce)").matches;

const WORLD_TOPOLOGY =
    "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";

let network = JSON.parse(document.getElementById("network-data").textContent);
let selectedId = null;

const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);

const money = (value) => "$" + Math.round(value).toLocaleString("en-US");
const tonnes = (kg) =>
    kg < 1000
        ? Math.round(kg).toLocaleString("en-US") + " kg"
        : (kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " t";
const percent = (value) => Math.round(value * 100) + "%";
const title = (text) => text.charAt(0).toUpperCase() + text.slice(1);

const modeTag = (mode) =>
    `<span class="mode-tag"><svg class="mode-glyph mode-${mode}" viewBox="0 0 28 8" aria-hidden="true" focusable="false"><line x1="3" y1="4" x2="25" y2="4"/></svg>${title(mode)}</span>`;

const confTag = (lane) =>
    lane.confidence_label
        ? `<span class="conf conf-${lane.confidence_label}"><span class="meter meter-${lane.confidence_label}" aria-hidden="true"><i></i><i></i><i></i></span><span>${title(lane.confidence_label)} confidence</span></span>`
        : "";

const laneById = (id) => network.lanes.find((lane) => lane.id === id);
const flaggedLanes = () => network.lanes.filter((lane) => lane.flagged);

/* Headline */

// A count, not a sum. The totals worth quoting are the report's own, and a
// second total added up here would drift from them.
function drawFinding() {
    const count = flaggedLanes().length;
    const el = document.getElementById("finding");
    el.innerHTML = count
        ? `<p class="finding-lead"><strong>${count} route${count === 1 ? "" : "s"}</strong>
             could switch transport mode and cut both cost and CO₂e. They are drawn bright.</p>`
        : '<p class="finding-lead">No route here can cut both cost and CO₂e by changing transport mode.</p>';
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
const landLayer = canvas.append("g").attr("class", "land");
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

function dashFor(mode, lineWidth, k) {
    const pattern = MODE_DASH[mode];
    if (!pattern) return null;
    // Dash lengths follow the line's width, so a thick route and a thin one
    // show the same pattern, and both hold their shape as the map zooms.
    const unit = Math.max(lineWidth, 1.6) * 2.2;
    return pattern.map((v) => (v * unit) / k).join(",");
}

// Everything drawn on the map is sized in screen pixels, so each element gets
// divided by the zoom level. Without this, zooming in turns the routes into
// thick ribbons and the labels into billboards.
function rescale() {
    const k = zoomScale;
    laneLayer.selectAll("path.lane-line").each(function () {
        const w = Number(this.dataset.width);
        this.setAttribute("stroke-width", w / k);
        const dash = dashFor(this.dataset.mode, w, k);
        if (dash) this.setAttribute("stroke-dasharray", dash);
    });
    laneLayer.selectAll("path.lane-halo").attr("stroke-width", function () {
        return this.dataset.width / k;
    });
    laneLayer.selectAll("path.lane-hit").attr("stroke-width", 14 / k);
    laneLayer.selectAll("circle.lane-pulse").attr("r", 4 / k);
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
        .attr("fill", "#1e1d1b")
        .attr("stroke", "#2f2d2a")
        .attr("stroke-width", 0.6);
}

function mapUnavailable() {
    mapEl.insertAdjacentHTML(
        "beforeend",
        '<p class="map-fallback">The world map could not load, so the routes ' +
            "are drawn without country outlines.</p>"
    );
}

let mapReady = false;
let pendingFocus = null;

// Two-argument then, not then().catch(): a rejection here means the topology
// genuinely failed to fetch. Anything thrown while drawing is a bug and
// should reach the console rather than being reported as a network problem.
d3.json(WORLD_TOPOLOGY).then(
    (world) => {
        drawLand(world);
        ready();
    },
    () => {
        mapUnavailable();
        ready();
    }
);

function ready() {
    mapReady = true;
    render();
    if (pendingFocus !== null) focusLane(pendingFocus);
}

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

function lanePoints(lane) {
    const nodeById = new Map(network.nodes.map((n) => [n.id, n]));
    const origin = nodeById.get(lane.origin_id);
    const dest = nodeById.get(lane.dest_id);
    if (!origin || !dest) return null;
    const a = projection([origin.lon, origin.lat]);
    const b = projection([dest.lon, dest.lat]);
    return a && b ? [a, b] : null;
}

function drawNetwork() {
    laneLayer.selectAll("*").remove();
    // Quiet routes first so the bright ones are drawn on top of them.
    const ordered = [...network.lanes].sort(
        (x, y) => Number(x.flagged) - Number(y.flagged) || Number(x.id === selectedId) - Number(y.id === selectedId)
    );

    ordered.forEach((lane) => {
        const points = lanePoints(lane);
        if (!points) return;
        const d = arc(points[0], points[1]);
        const selected = lane.id === selectedId;
        const label = `${lane.origin_name} to ${lane.dest_name} by ${lane.mode}`;
        const group = laneLayer.append("g").attr("class", "lane").style("cursor", "pointer");

        const tipHtml = `
      <strong>${esc(lane.origin_name)} to ${esc(lane.dest_name)}</strong>
      <span>${esc(lane.leg)} by ${esc(lane.mode)}, about ${Math.round(lane.route_km).toLocaleString()} km</span>
      <span class="cost-ink">${money(lane.cost)} a year</span>
      <span class="carbon-ink">${tonnes(lane.co2e)} CO₂e a year</span>
      ${lane.flagged ? '<span class="tip-flag">Could switch to ' + esc(lane.switch.mode) + "</span>" : ""}`;

        group
            .on("click", () => select(lane.id))
            .on("mousemove", (event) => showTip(event, tipHtml))
            .on("mouseleave", hideTip);

        // Routes worth changing can be reached from the keyboard. The rest are
        // all in the ranked list and the tables, so putting every one of them
        // in the tab order would cost a keyboard user dozens of stops.
        if (lane.flagged) {
            group
                .attr("tabindex", 0)
                .attr("role", "button")
                .attr("aria-label", `${label}, could switch to ${lane.switch.mode}. Press Enter to review the change.`)
                .on("keydown", (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        select(lane.id);
                        openPanel(lane.id, "investigate", event.currentTarget);
                    }
                })
                .on("focus", () => select(lane.id, { redraw: false }));
        }

        group
            .append("path")
            .attr("class", "lane-hit")
            .attr("d", d)
            .attr("stroke", "transparent")
            .attr("stroke-width", 14 / zoomScale)
            .attr("fill", "none");

        const w = laneWidth(lane) * (selected ? 1.8 : 1);

        if (selected) {
            const halo = group
                .append("path")
                .attr("class", "lane-halo")
                .attr("d", d)
                .attr("stroke", INK.halo)
                .attr("stroke-linecap", "round")
                .attr("fill", "none");
            halo.node().dataset.width = w * 3.2;
        }

        const line = group
            .append("path")
            .attr("class", "lane-line")
            .attr("d", d)
            .attr("stroke", lane.flagged || selected ? INK.flagged : INK.other)
            .attr("stroke-linecap", lane.mode === "rail" ? "round" : "butt")
            .attr("fill", "none")
            .attr("opacity", selected ? 1 : lane.flagged ? 0.92 : 0.5);
        line.node().dataset.width = lane.flagged || selected ? w : Math.max(1, w * 0.8);
        line.node().dataset.mode = lane.mode;

        if (selected) travel(group, d, w);
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

        const group = nodeLayer.append("g").style("cursor", "pointer");
        group
            .on("click", () => {
                const lane = lanes.find((l) => l.flagged) || lanes[0];
                if (lane) select(lane.id);
            })
            .on("mousemove", (event) =>
                showTip(
                    event,
                    `<strong>${esc(node.name)}</strong>
           <span>${NODE_LABEL[node.node_type]}, ${lanes.length} route${lanes.length === 1 ? "" : "s"}</span>
           <span class="cost-ink">${money(cost)} a year</span>
           <span class="carbon-ink">${tonnes(co2e)} CO₂e a year</span>`
                )
            )
            .on("mouseleave", hideTip);

        if (node.node_type === "supplier") {
            const rect = group
                .append("rect")
                .attr("fill", "#121110")
                .attr("stroke", "#c9c3b8");
            rect.node().dataset.x = point[0];
            rect.node().dataset.y = point[1];
        } else {
            const isWarehouse = node.node_type === "warehouse";
            const circle = group
                .append("circle")
                .attr("cx", point[0])
                .attr("cy", point[1])
                .attr("fill", isWarehouse ? "#e6e1d8" : "#121110")
                .attr("stroke", isWarehouse ? "#121110" : "#a9a398");
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

// The selected route walks itself from origin to destination, so direction
// reads without an arrowhead. Skipped entirely under reduced motion.
function travel(group, d, lineWidth) {
    if (REDUCED_MOTION) return;

    const shape = group.select("path.lane-line").node();
    const length = shape.getTotalLength();
    const pulse = group
        .append("circle")
        .attr("class", "lane-pulse")
        .attr("r", 4 / zoomScale)
        .attr("fill", INK.flagged)
        .attr("stroke", "none");

    function run() {
        if (!shape.isConnected) return;
        pulse
            .attr("opacity", 1)
            .transition()
            .duration(2200)
            .ease(d3.easeCubicInOut)
            .attrTween("transform", () => (t) => {
                const point = shape.getPointAtLength(t * length);
                return "translate(" + point.x + "," + point.y + ")";
            })
            .transition()
            .duration(600)
            .attr("opacity", 0)
            .on("end", run);
    }

    run();
}

function arc(a, b) {
    // A slight curve keeps overlapping lanes readable instead of stacking
    // them all on the same straight line.
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const bend = Math.sqrt(dx * dx + dy * dy) * 1.9;
    return `M${a[0]},${a[1]}A${bend},${bend} 0 0,1 ${b[0]},${b[1]}`;
}

// Bring one route into view: fit both ends in the frame with room around
// them. The page itself is not scrolled, so opening a route from anywhere in
// the report leaves the reader where they were.
function focusLane(id) {
    const lane = laneById(id);
    if (!lane) return;
    if (!mapReady) {
        pendingFocus = id;
        return;
    }
    pendingFocus = null;
    select(id);
    const points = lanePoints(lane);
    if (!points) return;
    const [[x0, y0], [x1, y1]] = [
        [Math.min(points[0][0], points[1][0]), Math.min(points[0][1], points[1][1])],
        [Math.max(points[0][0], points[1][0]), Math.max(points[0][1], points[1][1])],
    ];
    const span = Math.max((x1 - x0) / width, (y1 - y0) / height, 0.02);
    const k = Math.max(1, Math.min(8, 0.62 / span));
    const cx = (x0 + x1) / 2;
    const cy = (y0 + y1) / 2 - 6;
    svg.call(
        zoom.transform,
        d3.zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-cx, -cy)
    );
}

/* Ranked routes */

function drawWins() {
    const flagged = flaggedLanes().slice(0, 8);
    const wins = document.getElementById("wins");

    if (!flagged.length) {
        wins.innerHTML =
            '<p class="empty">There are no route changes to rate. Every route is ' +
            "already on the best transport mode available to it.</p>";
        return;
    }

    wins.innerHTML = flagged
        .map((lane, index) => {
            const s = lane.switch;
            return `
      <article class="win${lane.id === selectedId ? " on" : ""}" data-id="${lane.id}">
        <div class="win-rank">${index + 1}</div>
        <div class="win-body">
          <h4>${esc(lane.origin_name)} <span aria-hidden="true">&rarr;</span><span class="visually-hidden"> to </span> ${esc(lane.dest_name)}</h4>
          <p class="win-move">
            <span class="change">${modeTag(lane.mode)}<span class="arrow" aria-hidden="true">&rarr;</span><span class="visually-hidden"> to </span>${modeTag(s.mode)}</span>
            <span>${lane.leg === "inbound" ? "Inbound" : "Outbound"}</span>
            ${confTag(lane)}
          </p>
          <p class="win-gain">
            Save <span class="cost-ink">${money(s.saved_cost)}</span>
            and <span class="carbon-ink">${tonnes(s.saved_co2e)} CO₂e</span> a year
          </p>
        </div>
        <div class="win-side">
          <label class="win-effort">
            <span>How hard</span>
            <select data-effort="${lane.id}">
              <option value=""${!lane.effort ? " selected" : ""}>Not rated</option>
              <option value="low"${lane.effort === "low" ? " selected" : ""}>Easy</option>
              <option value="med"${lane.effort === "med" ? " selected" : ""}>Medium</option>
              <option value="high"${lane.effort === "high" ? " selected" : ""}>Hard</option>
            </select>
          </label>
          <button type="button" class="btn btn-ghost btn-small" data-investigate="${lane.id}">Review</button>
        </div>
      </article>`;
        })
        .join("");

    wins.querySelectorAll(".win").forEach((card) => {
        card.addEventListener("click", (event) => {
            if (event.target.closest("button, select, label")) return;
            focusLane(Number(card.dataset.id));
        });
    });

    wins.querySelectorAll("[data-effort]").forEach((field) => {
        field.addEventListener("change", (event) =>
            saveEffort(Number(event.target.dataset.effort), event.target.value)
        );
    });
}

async function saveEffort(edgeId, effort) {
    try {
        const response = await postJson("/api/effort", {
            edge_id: edgeId,
            effort: effort,
        });
        if (!response.ok) throw new Error(response.status);
        const fresh = (await response.json()).network;
        network = fresh;
        trouble(null);
    } catch {
        trouble("That rating was not saved. The server did not answer.");
    }
    // Either way the list is redrawn from what the server actually holds, so
    // a dropdown never sits showing a value that did not stick.
    render();
}

function trouble(message) {
    const line = document.getElementById("wins-trouble");
    line.textContent = message || "";
    line.hidden = !message;
}

/* The selected route, beside the map */

function select(id, { redraw = true } = {}) {
    if (selectedId === id && !redraw) return;
    selectedId = id;
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
            '<p class="detail-empty">Select a route on the map, or open a recommendation, to see it here.</p>';
        return;
    }

    const s = lane.switch;
    const arrow = '<span class="arrow" aria-hidden="true">&rarr;</span><span class="visually-hidden"> to </span>';
    panel.innerHTML = `
    <p class="detail-kicker">Selected route</p>
    <h4 class="detail-title">${esc(lane.origin_name)} ${arrow} ${esc(lane.dest_name)}</h4>
    <p class="detail-sub">${lane.leg === "inbound" ? "Inbound from a supplier" : "Outbound to customers"},
       about ${Math.round(lane.route_km).toLocaleString("en-US")} km by ${esc(lane.mode)},
       ${(lane.total_weight_kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} t a year</p>

    <div class="detail-state">
      <p class="detail-label">Before: today</p>
      <p class="detail-line">${modeTag(lane.mode)}</p>
      <p class="detail-figs"><span class="cost-ink">${money(lane.cost)}</span> and <span class="carbon-ink">${tonnes(lane.co2e)} CO₂e</span> a year</p>
    </div>

    ${
        lane.flagged
            ? `<div class="detail-state is-proposed">
                 <p class="detail-label">After: recommended change</p>
                 <p class="detail-line"><span class="change">${modeTag(lane.mode)}${arrow}${modeTag(s.mode)}</span></p>
                 <p class="detail-figs">Saves an estimated <span class="cost-ink">${money(s.saved_cost)}</span> and <span class="carbon-ink">${tonnes(s.saved_co2e)} CO₂e</span> a year</p>
               </div>`
            : `<p class="detail-none">No change recommended. No other realistic mode on this route cuts both cost and CO₂e by at least ${percent(network.threshold)}.</p>`
    }

    <button type="button" class="btn btn-secondary btn-small detail-open" data-investigate="${lane.id}">${lane.flagged ? "Review change" : "Open route details"}</button>`;
}

// The route panel lives in workspace.js. It asks the map to follow along
// through an event, so neither script needs to know how the other is built.
function openPanel(id, view, trigger) {
    document.dispatchEvent(
        new CustomEvent("route:open", { detail: { id, view, trigger } })
    );
}

document.addEventListener("route:focus", (event) => focusLane(event.detail.id));

/* Reshaping the network: closing sites and opening new ones */

function networkResult(result) {
    const cost = result.saved.cost;
    const co2e = result.saved.co2e;
    const good = cost > 0 && co2e > 0;
    const bad = cost < 0 && co2e < 0;
    const tone = good ? "good" : bad ? "bad" : "mixed";
    const verb = (value) => (value > 0 ? "saves " : "adds ");

    const rows = result.sites
        .map(
            (site) => `
      <tr class="site-${site.status}${site.full ? " site-full" : ""}">
        <td>${esc(site.name)}</td>
        <td>${site.status}${site.full ? " (over capacity)" : ""}</td>
        <td class="num">${site.tonnes_before.toLocaleString("en-US", { maximumFractionDigits: 1 })} t</td>
        <td class="num">${site.status === "closed" ? "closed" : site.tonnes_after.toLocaleString("en-US", { maximumFractionDigits: 1 }) + " t"}</td>
        <td class="num">${site.status === "closed" ? "0" : site.lanes}</td>
        <td class="num">${site.capacity_tonnes.toLocaleString("en-US", { maximumFractionDigits: 1 })} t
          <span class="muted-ink">${site.capacity_basis}</span></td>
      </tr>`
        )
        .join("");

    return `
    <div class="sim ${tone}">
      <p class="sim-badge">Scenario</p>
      <p class="sim-line">
        ${verb(cost)}<strong>${money(Math.abs(cost))}</strong>
        and ${verb(co2e)}<strong>${tonnes(Math.abs(co2e))} CO2e</strong> a year
      </p>
      <p class="sim-sub">
        ${result.moved_lanes} route${result.moved_lanes === 1 ? "" : "s"} would be
        served from somewhere else.
        ${good ? "Both fall." : bad ? "Both rise." : "One improves at the other's expense."}
      </p>
    </div>
    ${
        result.over_capacity
            ? `<p class="network-warn">At least one site is over the volume it could
                 credibly take. The numbers above assume it copes anyway, so treat
                 this shape as a question rather than an answer.${
                     result.stated_capacity
                         ? " The limits marked stated came from your warehouse file."
                         : " No capacity column was given, so every limit here is the" +
                           " headroom assumption rather than a measured one."
                 }</p>`
            : ""
    }
    <div class="table-scroll" tabindex="0">
    <table class="grid network-sites">
      <thead>
        <tr><th>Site</th><th>Status</th><th class="num">Handles now</th><th class="num">Would handle</th><th class="num">Routes</th><th class="num">Capacity</th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    </div>`;
}

async function runNetwork() {
    const panel = document.getElementById("network-result");
    const closed = [...document.querySelectorAll("#site-toggles input:not(:checked)")].map(
        (box) => Number(box.dataset.site)
    );

    panel.innerHTML = '<p class="whatif-hint">Working it out.</p>';
    let result;
    try {
        const response = await postJson("/api/network", {
            closed: closed,
            city: document.getElementById("new-site").value,
            country: document.getElementById("new-site-country").value,
        });
        result = await response.json();
        if (!response.ok) {
            panel.innerHTML = `<p class="network-warn">${esc(result.error || "That could not be worked out.")}</p>`;
            return;
        }
    } catch {
        panel.innerHTML =
            '<p class="network-warn">The server did not answer. Try again.</p>';
        return;
    }
    panel.innerHTML = networkResult(result);
}

document.getElementById("run-network").addEventListener("click", runNetwork);
document.querySelectorAll("#site-toggles input").forEach((box) => {
    box.addEventListener("change", runNetwork);
});
document.getElementById("new-site").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runNetwork();
});

// A link naming a route selects it. Otherwise the map opens on the biggest
// win, so it arrives showing something rather than asking to be explored.
const requested = Number(new URLSearchParams(location.search).get("lane"));
const opener = (requested && laneById(requested)) || flaggedLanes()[0];
if (opener) selectedId = opener.id;
drawFinding();
drawWins();
drawDetail();
