// The route panel, the scenario and the comparison, plus the opportunity
// strip that follows the reader down the report.
//
// Everything here reads figures the server already worked out: the
// recommendations embedded in the page, and /api/simulate for a scenario.
// Nothing is recalculated in the browser, so a number in the panel is always
// the engine's number. A scenario only ever redraws the panel it lives in.

// Wrapped so its helpers do not collide with the ones dashboard.js declares
// at the top level of the same page.
(() => {
    const readJson = (id) => {
        const el = document.getElementById(id);
        return el ? JSON.parse(el.textContent) : null;
    };

    const NETWORK = readJson("network-data");
    const RECS = readJson("recs-data") || {};
    const drawer = document.getElementById("drawer");
    const body = document.getElementById("drawer-body");

    const MODES = ["road", "rail", "sea", "air"];

    const esc = (value) =>
        String(value ?? "").replace(/[&<>"']/g, (ch) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        })[ch]);
    const money = (v) => "$" + Math.round(v).toLocaleString("en-US");
    const moneyShort = (v) =>
        Math.abs(v) >= 1e6
            ? "$" + (v / 1e6).toFixed(2) + "m"
            : Math.abs(v) >= 1e4
              ? "$" + (v / 1e3).toFixed(1) + "k"
              : money(v);
    const carbon = (kg) =>
        Math.abs(kg) < 1000
            ? Math.round(kg).toLocaleString("en-US") + " kg"
            : (kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " t";
    const pct = (v) => Math.round(v * 100) + "%";
    const km = (v) => Math.round(v).toLocaleString("en-US") + " km";
    const days = (v) => {
        const n = Math.round(v);
        return n + (n === 1 ? " day" : " days");
    };
    const title = (text) => text.charAt(0).toUpperCase() + text.slice(1);
    const arrow = '<span class="arrow" aria-hidden="true">&rarr;</span><span class="visually-hidden"> to </span>';

    const modeTag = (mode) =>
        `<span class="mode-tag"><svg class="mode-glyph mode-${mode}" viewBox="0 0 28 8" aria-hidden="true" focusable="false"><line x1="3" y1="4" x2="25" y2="4"/></svg>${title(mode)}</span>`;

    const meter = (label) =>
        `<span class="meter meter-${label || "none"}" aria-hidden="true"><i></i><i></i><i></i></span>`;

    const confTag = (label, share) =>
        label
            ? `<span class="conf conf-${label}">${meter(label)}<span>${title(label)} confidence<span class="conf-share">, ${pct(share)} of simulations</span></span></span>`
            : `<span class="conf conf-none">${meter(null)}<span>Not simulated</span></span>`;

    const checksBlock = (items, heading = "Check before acting") =>
        items && items.length
            ? `<div class="checks"><p class="checks-title">${heading}</p><ul>${items
                  .map((item) => `<li><span class="checks-icon" aria-hidden="true">&#9888;</span>${esc(item)}</li>`)
                  .join("")}</ul></div>`
            : "";

    const laneById = (id) => NETWORK && NETWORK.lanes.find((lane) => lane.id === id);

    /* State */

    const state = {
        id: null,
        view: "investigate",
        mode: null,
        origin: null,
        result: null,
        loading: false,
        failed: null,
        pinned: [],
        trigger: null,
        request: 0,
    };

    /* Opening and closing */

    function openRoute(id, view = "investigate", trigger = null) {
        const lane = laneById(id);
        if (!lane || !drawer) return;

        if (state.id !== id) {
            state.id = id;
            state.pinned = [];
            state.result = null;
            state.failed = null;
            const rec = RECS[String(id)];
            // A scenario opens on the change being recommended, so the first
            // thing it shows is the answer, and Reset takes it back to today.
            state.mode = rec ? rec.route.proposed.mode : lane.mode;
            state.origin = lane.leg === "inbound" ? null : lane.origin_id;
        }
        state.view = view;
        if (trigger) state.trigger = trigger;

        drawer.hidden = false;
        document.body.classList.add("drawer-open");
        render();
        document.dispatchEvent(new CustomEvent("route:focus", { detail: { id } }));

        if (view === "scenario") runScenario();
        const heading = document.getElementById("drawer-title");
        if (heading) heading.focus({ preventScroll: true });
    }

    function closeRoute() {
        if (!drawer || drawer.hidden) return;
        drawer.hidden = true;
        document.body.classList.remove("drawer-open");
        const back = state.trigger;
        state.trigger = null;
        if (back && back.isConnected) back.focus({ preventScroll: true });
    }

    function setView(view) {
        state.view = view;
        render();
        if (view === "scenario" && !state.result) runScenario();
    }

    /* Rendering */

    function render() {
        const lane = laneById(state.id);
        if (!lane) return;
        const rec = RECS[String(state.id)];

        drawer.querySelectorAll("[data-view]").forEach((button) => {
            button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
        });

        body.innerHTML =
            head(lane, rec) +
            (state.view === "scenario" ? scenarioView(lane, rec) : investigateView(lane, rec));
        body.scrollTop = 0;
    }

    function head(lane, rec) {
        const kicker = state.view === "scenario"
            ? "What if?"
            : (lane.leg === "inbound" ? "Inbound route" : "Outbound route") +
              (rec ? " &middot; worth changing" : "");
        return `
        <header class="dr-head">
          <p class="dr-kicker">${kicker}</p>
          <h2 id="drawer-title" tabindex="-1">${esc(lane.origin_name)} ${arrow} ${esc(lane.dest_name)}</h2>
          <p class="dr-sub">Current: ${modeTag(lane.mode)} &middot;
            ${(lane.total_weight_kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} t a year &middot;
            ${lane.order_count.toLocaleString("en-US")} orders</p>
        </header>`;
    }

    function sideBlock(label, figures, proposed) {
        return `
        <section class="dr-side${proposed ? " is-proposed" : ""}">
          <p class="dr-label">${label}</p>
          <p class="dr-mode">${modeTag(figures.mode)}</p>
          <dl>
            <div><dt>Cost</dt><dd class="cost-ink">${money(figures.cost)}</dd></div>
            <div><dt>CO2e</dt><dd class="carbon-ink">${carbon(figures.co2e)}</dd></div>
            <div><dt>Transit</dt><dd>${days(figures.days)}</dd></div>
          </dl>
        </section>`;
    }

    function investigateView(lane, rec) {
        if (!rec) return investigatePlain(lane);
        const r = rec.route;
        const threshold = pct(r.threshold);

        return `
        <div class="dr-compare">
          ${sideBlock("Current", r.now, false)}
          ${sideBlock("Proposed", r.proposed, true)}
        </div>

        <div class="dr-impact">
          <p class="dr-label">Impact a year</p>
          <p><strong class="cost-ink">${money(r.saved_cost)} cheaper</strong> <span class="impact-pct">&minus;${pct(r.cost_pct)}</span></p>
          <p><strong class="carbon-ink">${carbon(r.saved_co2e)} CO2e lower</strong> <span class="impact-pct">&minus;${pct(r.co2e_pct)}</span></p>
          <p class="dr-conf">${confTag(r.confidence_label, r.confidence)}</p>
        </div>

        <div class="dr-block">
          <p class="dr-label">Why flagged</p>
          <ul class="ticks">
            <li><span aria-hidden="true">&#10003;</span> Cost falls ${pct(r.cost_pct)}, needs at least ${threshold}</li>
            <li><span aria-hidden="true">&#10003;</span> Carbon falls ${pct(r.co2e_pct)}, needs at least ${threshold}</li>
          </ul>
        </div>

        <div class="dr-block">
          <p class="dr-label">Why this route?</p>
          <dl class="why-grid">
            <div><dt>Cost</dt><dd>${esc(r.cost_level)}</dd></div>
            <div><dt>Carbon</dt><dd>${esc(r.carbon_level)}</dd></div>
            <div><dt>Alternative</dt><dd>${title(r.proposed.mode)}</dd></div>
            <div><dt>Confidence</dt><dd>${r.confidence === null ? "Not simulated" : pct(r.confidence)}</dd></div>
          </dl>
        </div>

        <div class="dr-block">${checksBlock(rec.checks, "Warnings")}</div>

        <details class="dr-more">
          <summary>Route details</summary>
          ${detailsTable(lane, r)}
        </details>

        <details class="dr-more" id="dr-audit">
          <summary>Show the calculation</summary>
          ${auditBlock(r)}
        </details>

        <div class="dr-actions">
          <button type="button" class="btn btn-strong" data-go="scenario">Test this scenario</button>
          <button type="button" class="btn btn-quiet" data-go="audit">Show calculation</button>
          <button type="button" class="btn btn-quiet" data-go="map">Show on map</button>
        </div>`;
    }

    function investigatePlain(lane) {
        const s = lane.switch;
        const why = s
            ? `The best alternative, ${esc(s.mode)}, cuts ${pct(lane.saving_cost_pct)} of cost and
               ${pct(lane.saving_co2e_pct)} of carbon. Both need to fall by at least
               ${pct(NETWORK.threshold)} for a route to be flagged.`
            : "No other transport mode on this route cuts both cost and carbon.";
        return `
        <div class="dr-compare dr-compare-single">
          ${sideBlock("Current", { mode: lane.mode, cost: lane.cost, co2e: lane.co2e, days: lane.days }, false)}
        </div>
        <div class="dr-block">
          <p class="dr-label">Not flagged for a mode change</p>
          <p class="dr-text">${why}</p>
        </div>
        <details class="dr-more">
          <summary>Route details</summary>
          <dl class="dr-facts">
            <div><dt>Straight-line distance</dt><dd>${km(lane.distance_km)}</dd></div>
            <div><dt>Distance by ${esc(lane.mode)}</dt><dd>${km(lane.route_km)}, screening estimate</dd></div>
            <div><dt>Weight a year</dt><dd>${(lane.total_weight_kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} t</dd></div>
            <div><dt>Orders and returns</dt><dd>${lane.order_count} and ${lane.return_count}</dd></div>
          </dl>
        </details>
        <div class="dr-actions">
          <button type="button" class="btn btn-strong" data-go="scenario">Test a scenario</button>
          <button type="button" class="btn btn-quiet" data-go="map">Show on map</button>
        </div>`;
    }

    function detailsTable(lane, r) {
        const row = (label, now, proposed) =>
            `<tr><th scope="row">${label}</th><td class="num">${now}</td><td class="num">${proposed}</td></tr>`;
        return `
        <div class="table-scroll" tabindex="0">
        <table class="grid dr-table">
          <thead><tr><th></th><th class="num">${title(r.now.mode)}</th><th class="num">${title(r.proposed.mode)}</th></tr></thead>
          <tbody>
            ${row("Straight-line distance", km(r.straight_km), km(r.straight_km))}
            ${row("Distance multiplier", r.now.circuity.toFixed(2), r.proposed.circuity.toFixed(2))}
            ${row("Distance by mode", km(r.now.route_km), km(r.proposed.route_km))}
            ${row("Cost factor, per t-km", "$" + r.now.cost_factor, "$" + r.proposed.cost_factor)}
            ${row("Emission factor, kg per t-km", r.now.emission_factor, r.proposed.emission_factor)}
            ${row("Transit", days(r.now.days), days(r.proposed.days))}
          </tbody>
        </table>
        </div>
        <dl class="dr-facts">
          <div><dt>Weight a year</dt><dd>${r.weight_t.toLocaleString("en-US", { maximumFractionDigits: 1 })} t</dd></div>
          <div><dt>Orders and returns</dt><dd>${r.orders} and ${r.returns}</dd></div>
        </dl>
        <p class="dr-note">
          Distances are screening estimates: the straight line times a per-mode
          multiplier, not a routed distance. Emission factors follow DEFRA and
          GLEC ranges. Cost factors are industry defaults, not your contract
          rates. Transit is a planning estimate. Inventory is not modelled.
        </p>`;
    }

    function auditBlock(r) {
        const side = (label, s) => `
        <div class="audit-side">
          <p class="audit-title">${label}: ${title(s.mode)}</p>
          <p class="audit-line">${km(r.straight_km)} straight line &times; ${s.circuity.toFixed(2)} = ${km(s.route_km)}</p>
          <table class="audit-sum"><tbody>
            <tr><td>${r.weight_t.toFixed(1)} t &times; ${km(s.route_km)} &times; $${s.cost_factor.toFixed(3)} per t-km</td><td class="num">${money(s.freight_cost)}</td></tr>
            <tr><td>Returns</td><td class="num">+ ${money(s.returns_cost)}</td></tr>
            <tr><td>Warehouse and packaging share, unchanged</td><td class="num">+ ${money(s.fixed_cost)}</td></tr>
            <tr class="audit-total"><td>Cost a year</td><td class="num cost-ink">${money(s.cost)}</td></tr>
            <tr><td>${r.weight_t.toFixed(1)} t &times; ${km(s.route_km)} &times; ${s.emission_factor.toFixed(3)} kg per t-km</td><td class="num">${carbon(s.freight_co2e)}</td></tr>
            <tr><td>Returns</td><td class="num">+ ${carbon(s.returns_co2e)}</td></tr>
            <tr><td>Warehouse energy and packaging, unchanged</td><td class="num">+ ${carbon(s.fixed_co2e)}</td></tr>
            <tr class="audit-total"><td>CO2e a year</td><td class="num carbon-ink">${carbon(s.co2e)}</td></tr>
          </tbody></table>
        </div>`;
        return `
        <div class="audit-body">
          ${side("Current", r.now)}
          ${side("Proposed", r.proposed)}
          <p class="audit-result">
            ${money(r.now.cost)} &minus; ${money(r.proposed.cost)} = <strong class="cost-ink">${money(r.saved_cost)}</strong> a year.<br>
            ${carbon(r.now.co2e)} &minus; ${carbon(r.proposed.co2e)} = <strong class="carbon-ink">${carbon(r.saved_co2e)}</strong> a year.
          </p>
          <p class="audit-note">Rounded for display.</p>
        </div>`;
    }

    function scenarioView(lane) {
        const inbound = lane.leg === "inbound";
        const modeOptions = MODES.map(
            (mode) =>
                `<option value="${mode}"${mode === state.mode ? " selected" : ""}>${title(mode)}${mode === lane.mode ? " (current)" : ""}</option>`
        ).join("");
        const siteOptions = inbound
            ? `<option>${esc(lane.dest_name)} (current)</option>`
            : NETWORK.warehouses
                  .map(
                      (site) =>
                          `<option value="${site.id}"${site.id === state.origin ? " selected" : ""}>${esc(site.name)}${site.id === lane.origin_id ? " (current)" : ""}</option>`
                  )
                  .join("");

        return `
        <p class="sc-banner" role="note"><span class="sc-badge">Scenario</span> A test only. Nothing here changes your data or the report.</p>

        <div class="sc-controls">
          <div class="sc-field">
            <label for="sc-mode">Transport mode</label>
            <select id="sc-mode">${modeOptions}</select>
          </div>
          <div class="sc-field">
            <label for="sc-origin">${inbound ? "Delivered into" : "Shipped from"}</label>
            <select id="sc-origin"${inbound ? " disabled" : ""}>${siteOptions}</select>
          </div>
        </div>
        ${inbound ? '<p class="sc-hint">An inbound route keeps its warehouse here. Close or open sites in the network scenario in step 3.</p>' : ""}

        <div class="sc-result" id="sc-result" aria-live="polite">${resultBlock(lane)}</div>

        <div class="dr-actions">
          <button type="button" class="btn btn-quiet" data-reset>Reset</button>
          <button type="button" class="btn" data-pin${state.result && state.result.changed ? "" : " disabled"}>Add to comparison</button>
          <button type="button" class="btn btn-quiet" data-go="investigate">Back to route</button>
        </div>

        <div id="sc-compare">${compareBlock()}</div>`;
    }

    function resultBlock() {
        if (state.failed) {
            return `<p class="network-warn">${esc(state.failed)}</p>`;
        }
        const result = state.result;
        if (!result) {
            return '<p class="whatif-hint">Working it out.</p>';
        }
        const b = result.before;
        const a = result.after;
        const row = (label, before, after) =>
            `<tr><th scope="row">${label}</th><td class="num">${before}</td><td class="num sc-after">${after}</td></tr>`;

        const delta = (value, unitFn, better, worse) => {
            if (Math.abs(value) < 0.5) return `<span class="muted-ink">No change</span>`;
            return value > 0 ? `${unitFn(value)} ${better}` : `${unitFn(-value)} ${worse}`;
        };

        const summary = result.changed
            ? `<p class="sc-delta">
                 <strong class="cost-ink">${delta(result.saved.cost, money, "cheaper", "more expensive")}</strong>
                 <strong class="carbon-ink">${delta(result.saved.co2e, carbon, "CO2e lower", "CO2e higher")}</strong>
               </p>`
            : '<p class="sc-delta muted-ink">This is the route as it runs today. Change the mode or warehouse to test something.</p>';

        return `
        <div class="table-scroll${state.loading ? " is-loading" : ""}" tabindex="0">
        <table class="grid sc-table">
          <thead><tr><th></th><th class="num">Baseline</th><th class="num sc-after">Scenario</th></tr></thead>
          <tbody>
            ${row("Mode", title(b.mode), title(a.mode))}
            ${row("From", esc(b.origin_name), esc(a.origin_name))}
            ${row("Cost a year", `<span class="cost-ink">${money(b.cost)}</span>`, `<span class="cost-ink">${money(a.cost)}</span>`)}
            ${row("CO2e a year", `<span class="carbon-ink">${carbon(b.co2e)}</span>`, `<span class="carbon-ink">${carbon(a.co2e)}</span>`)}
            ${row("Transit", days(b.days), days(a.days))}
            ${row("Distance", km(b.route_km), km(a.route_km))}
          </tbody>
        </table>
        </div>
        ${summary}
        ${result.note ? `<p class="network-warn"><span class="checks-icon" aria-hidden="true">&#9888;</span> ${esc(result.note)}</p>` : ""}`;
    }

    function compareBlock() {
        if (!state.pinned.length || !state.result) return "";
        const base = state.result.before;
        const columns = [
            { label: "Current", sub: title(base.mode), figures: base, removable: false },
            ...state.pinned.map((item, index) => ({
                label: "Scenario " + String.fromCharCode(65 + index),
                sub: title(item.mode) + (item.origin_name !== base.origin_name ? ", from " + esc(item.origin_name) : ""),
                figures: item,
                removable: true,
                index,
            })),
        ];
        const cells = (fn) => columns.map((col) => `<td class="num">${fn(col.figures)}</td>`).join("");
        return `
        <section class="sc-compare" aria-labelledby="sc-compare-title">
          <p class="dr-label" id="sc-compare-title">Compare</p>
          <div class="table-scroll" tabindex="0">
          <table class="grid">
            <thead>
              <tr><th></th>${columns
                  .map(
                      (col) => `<th class="num">${col.label}<br><span class="muted-ink">${col.sub}</span>${
                          col.removable
                              ? `<br><button type="button" class="link-button" data-unpin="${col.index}" aria-label="Remove ${col.label}">Remove</button>`
                              : ""
                      }</th>`
                  )
                  .join("")}</tr>
            </thead>
            <tbody>
              <tr><th scope="row">Cost</th>${cells((f) => `<span class="cost-ink">${moneyShort(f.cost)}</span>`)}</tr>
              <tr><th scope="row">CO2e</th>${cells((f) => `<span class="carbon-ink">${carbon(f.co2e)}</span>`)}</tr>
              <tr><th scope="row">Transit</th>${cells((f) => days(f.days))}</tr>
            </tbody>
          </table>
          </div>
        </section>`;
    }

    /* The scenario itself */

    async function runScenario() {
        const lane = laneById(state.id);
        if (!lane) return;
        const ticket = ++state.request;
        state.loading = true;
        state.failed = null;
        refreshResult();

        try {
            const response = await fetch("/api/simulate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    edge_id: state.id,
                    mode: state.mode,
                    origin_id: state.origin,
                }),
            });
            const data = await response.json();
            if (ticket !== state.request) return;
            if (!response.ok) throw new Error(data.error || "That could not be worked out.");
            state.result = data;
        } catch (error) {
            if (ticket !== state.request) return;
            state.failed =
                error instanceof TypeError
                    ? "The server did not answer. Change an option to try again."
                    : error.message;
        }
        state.loading = false;
        refreshResult();
    }

    // Only the result and the comparison are redrawn, so the select being used
    // keeps its focus while the numbers change beside it.
    function refreshResult() {
        if (state.view !== "scenario") return;
        const lane = laneById(state.id);
        const result = document.getElementById("sc-result");
        const compare = document.getElementById("sc-compare");
        if (result) result.innerHTML = resultBlock(lane);
        if (compare) compare.innerHTML = compareBlock();
        const pin = body.querySelector("[data-pin]");
        if (pin) pin.disabled = !(state.result && state.result.changed) || state.pinned.length >= 3;
    }

    function pinScenario() {
        const result = state.result;
        if (!result || !result.changed || state.pinned.length >= 3) return;
        const same = state.pinned.some(
            (item) => item.mode === result.after.mode && item.origin_name === result.after.origin_name
        );
        if (!same) state.pinned.push({ ...result.after });
        refreshResult();
    }

    /* Wiring */

    if (drawer) {
        document.addEventListener("click", (event) => {
            const investigate = event.target.closest("[data-investigate]");
            if (investigate) {
                openRoute(Number(investigate.dataset.investigate), "investigate", investigate);
                return;
            }
            const scenario = event.target.closest("[data-scenario]");
            if (scenario) {
                openRoute(Number(scenario.dataset.scenario), "scenario", scenario);
            }
        });

        document.addEventListener("route:open", (event) => {
            const { id, view, trigger } = event.detail;
            openRoute(id, view, trigger);
        });

        drawer.addEventListener("click", (event) => {
            const target = event.target.closest("button");
            if (!target) return;
            if (target.matches("[data-close]")) return closeRoute();
            if (target.dataset.view) return setView(target.dataset.view);
            if (target.dataset.unpin !== undefined) {
                state.pinned.splice(Number(target.dataset.unpin), 1);
                return refreshResult();
            }
            if (target.matches("[data-pin]")) return pinScenario();
            if (target.matches("[data-reset]")) {
                const lane = laneById(state.id);
                state.mode = lane.mode;
                state.origin = lane.leg === "inbound" ? null : lane.origin_id;
                render();
                return runScenario();
            }
            const go = target.dataset.go;
            if (go === "scenario" || go === "investigate") return setView(go);
            if (go === "audit") {
                const audit = document.getElementById("dr-audit");
                if (audit) {
                    audit.open = true;
                    audit.scrollIntoView({ block: "start", behavior: "smooth" });
                    audit.querySelector("summary").focus({ preventScroll: true });
                }
                return;
            }
            if (go === "map") {
                const map = document.querySelector(".map-panel");
                if (window.matchMedia("(max-width: 900px)").matches) closeRoute();
                if (map) map.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });

        drawer.addEventListener("change", (event) => {
            if (event.target.id === "sc-mode") state.mode = event.target.value;
            else if (event.target.id === "sc-origin") state.origin = Number(event.target.value);
            else return;
            runScenario();
        });

        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !drawer.hidden) closeRoute();
        });

        // A link from the landing page or a shared address can open a route.
        const asked = Number(new URLSearchParams(location.search).get("investigate"));
        if (asked && laneById(asked)) openRoute(asked, "investigate");
        const tested = Number(new URLSearchParams(location.search).get("scenario"));
        if (tested && laneById(tested)) openRoute(tested, "scenario");
    }

    /* The opportunity strip: shown once the overview has scrolled away, and a
       way back to it. It sits over the page rather than in it, so showing and
       hiding it never moves anything the reader is looking at. */

    const bar = document.querySelector("[data-oppbar]");
    const overview = document.getElementById("overview");
    const topbar = document.querySelector(".topbar");

    function syncTopbar() {
        if (topbar) {
            document.documentElement.style.setProperty("--topbar-h", topbar.offsetHeight + "px");
        }
    }
    syncTopbar();
    addEventListener("resize", syncTopbar, { passive: true });

    if (bar && overview && "IntersectionObserver" in window) {
        new IntersectionObserver(
            ([entry]) => {
                const gone = !entry.isIntersecting && entry.boundingClientRect.top < 0;
                bar.hidden = !gone;
            },
            { threshold: 0, rootMargin: "-120px 0px 0px 0px" }
        ).observe(overview);
    }
})();
