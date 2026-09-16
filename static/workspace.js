// The decision view: one change in a side panel, opened from its card, from
// the map or from a link. What it shows is written by the server into a
// <template> per change, so every figure in it is the engine's own. The
// what-if inside it asks /api/simulate and only ever redraws itself.
//
// Also here: marking changes for review, which is remembered in this browser
// only and never reaches the server or the exports.

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

    const laneById = (id) => NETWORK && NETWORK.lanes.find((lane) => lane.id === id);

    const templates = [...document.querySelectorAll("template[data-rank]")];
    const rankForEdge = new Map(
        templates
            .filter((tpl) => tpl.dataset.edge)
            .map((tpl) => [Number(tpl.dataset.edge), Number(tpl.dataset.rank)])
    );

    /* Marked for review */

    const MARKS = "overlap:marked-for-review";

    const readMarks = () => {
        try {
            return new Set(JSON.parse(localStorage.getItem(MARKS) || "[]"));
        } catch {
            return new Set();
        }
    };

    const writeMarks = (marks) => {
        try {
            localStorage.setItem(MARKS, JSON.stringify([...marks]));
        } catch {
            // Storage can be blocked. The mark then lasts until the page is
            // left, which is still better than a button that does nothing.
        }
    };

    let marks = readMarks();

    function toggleMark(key) {
        if (marks.has(key)) marks.delete(key);
        else marks.add(key);
        writeMarks(marks);
        syncMarks();
    }

    function syncMarks() {
        document.querySelectorAll("[data-mark]").forEach((button) => {
            const on = marks.has(button.dataset.mark);
            button.setAttribute("aria-pressed", String(on));
            const label = button.querySelector("[data-mark-label]");
            if (label) label.textContent = on ? "Marked for review" : "Mark for review";
        });

        const cards = [...document.querySelectorAll(".rec-card[data-mark-key]")];
        let marked = 0;
        cards.forEach((card) => {
            const on = marks.has(card.dataset.markKey);
            if (on) marked += 1;
            card.classList.toggle("is-marked", on);
            const flag = card.querySelector("[data-marked-flag]");
            if (flag) flag.hidden = !on;
        });

        const count = document.querySelector("[data-marked-count]");
        const wrap = document.querySelector("[data-marked-filter-wrap]");
        const filter = document.querySelector("[data-marked-filter]");
        if (count) {
            count.hidden = !marked;
            count.textContent = `${marked} change${marked === 1 ? "" : "s"} marked for review in this browser`;
        }
        if (wrap) wrap.hidden = !marked;
        if (filter && !marked) filter.checked = false;
        applyFilter();
    }

    function applyFilter() {
        const filter = document.querySelector("[data-marked-filter]");
        const only = Boolean(filter && filter.checked);
        document.querySelectorAll(".rec-card[data-mark-key]").forEach((card) => {
            card.hidden = only && !card.classList.contains("is-marked");
        });
        document.querySelectorAll(".rec-group").forEach((group) => {
            group.hidden = only && !group.querySelector(".rec-card:not([hidden])");
        });
    }

    const filter = document.querySelector("[data-marked-filter]");
    if (filter) filter.addEventListener("change", applyFilter);
    syncMarks();

    if (!drawer || !body) return;

    /* State */

    const state = {
        rank: null,
        edge: null,
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

    function resetScenario(edge) {
        state.edge = edge;
        state.pinned = [];
        state.result = null;
        state.failed = null;
        state.loading = false;
        const lane = laneById(edge);
        const rec = RECS[String(edge)];
        // A what-if opens on the change being recommended, so the first thing
        // it shows is the answer, and Reset takes it back to today.
        state.mode = rec ? rec.route.proposed.mode : lane ? lane.mode : null;
        state.origin = lane && lane.leg !== "inbound" ? lane.origin_id : null;
    }

    function show(trigger, edge) {
        if (trigger) state.trigger = trigger;
        drawer.hidden = false;
        document.body.classList.add("drawer-open");
        body.scrollTop = 0;
        const heading = document.getElementById("drawer-title");
        if (heading) heading.focus({ preventScroll: true });
        if (edge) document.dispatchEvent(new CustomEvent("route:focus", { detail: { id: edge } }));
    }

    function openScenario() {
        const panel = body.querySelector("[data-scenario-panel]");
        if (!panel) return;
        panel.open = true;
        startScenario();
        panel.scrollIntoView({ block: "start" });
    }

    function openChange(rank, trigger = null, { scenario = false } = {}) {
        const template = document.getElementById("change-detail-" + rank);
        if (!template) return;
        state.rank = rank;
        body.replaceChildren(template.content.cloneNode(true));
        const edge = template.dataset.edge ? Number(template.dataset.edge) : null;
        resetScenario(edge);
        syncMarks();
        show(trigger, edge);
        if (scenario) openScenario();
    }

    function openLane(edge, trigger = null, { scenario = false } = {}) {
        const rank = rankForEdge.get(edge);
        if (rank) return openChange(rank, trigger, { scenario });
        const lane = laneById(edge);
        if (!lane) return;
        state.rank = null;
        body.innerHTML = plainView(lane);
        resetScenario(edge);
        show(trigger, edge);
        if (scenario) openScenario();
    }

    function close({ restoreFocus = true } = {}) {
        if (drawer.hidden) return;
        drawer.hidden = true;
        document.body.classList.remove("drawer-open");
        const back = state.trigger;
        state.trigger = null;
        if (restoreFocus && back && back.isConnected) back.focus({ preventScroll: true });
    }

    // Back to the list, landing on the card this change came from.
    function backToList() {
        const rank = state.rank;
        close({ restoreFocus: false });
        const card = rank && document.getElementById("change-" + rank);
        if (card) {
            card.scrollIntoView({ block: "center", behavior: "smooth" });
            const button = card.querySelector("[data-open-change]");
            if (button) button.focus({ preventScroll: true });
        } else {
            const list = document.getElementById("recommendations");
            if (list) list.scrollIntoView({ behavior: "smooth" });
        }
    }

    /* A route that is not one of the listed changes */

    function plainView(lane) {
        const s = lane.switch;
        const listed = templates.length;
        let heading;
        let why;
        if (lane.flagged && s) {
            heading = "Qualifies for a change";
            why = `Switching to ${esc(s.mode)} would save an estimated ${money(s.saved_cost)} and
                   ${carbon(s.saved_co2e)} CO₂e a year. It qualifies, but falls outside the
                   ${listed} change${listed === 1 ? "" : "s"} listed in these results.`;
        } else if (s) {
            heading = "No change recommended";
            why = `The best alternative, ${esc(s.mode)}, would cut cost by ${pct(lane.saving_cost_pct)}
                   and CO₂e by ${pct(lane.saving_co2e_pct)}. A change is only recommended when both
                   fall by at least ${pct(NETWORK.threshold)}.`;
        } else {
            heading = "No change recommended";
            why = "No other realistic transport mode on this route cuts both cost and CO₂e.";
        }
        const weight = (lane.total_weight_kg / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 });
        return `
        <header class="dv-head">
          <p class="dv-meta"><span class="rec-priority">Route</span></p>
          <h2 id="drawer-title" tabindex="-1">${esc(lane.origin_name)} ${arrow} ${esc(lane.dest_name)}</h2>
          <p class="dv-sub">${lane.leg === "inbound" ? "Inbound from a supplier" : "Outbound to customers"}
            &middot; ${weight} t shipped a year &middot; ${lane.order_count.toLocaleString("en-US")} orders</p>
        </header>
        <section class="dv-compare dv-compare-single" aria-label="Today">
          <div class="dv-side">
            <p class="dv-label">Today</p>
            <p class="dv-mode">${modeTag(lane.mode)}</p>
            <dl>
              <div><dt>Cost</dt><dd>${money(lane.cost)} <span>a year</span></dd></div>
              <div><dt>CO₂e</dt><dd>${carbon(lane.co2e)} <span>a year</span></dd></div>
              <div><dt>Transit</dt><dd>${days(lane.days)}</dd></div>
            </dl>
          </div>
        </section>
        <section class="dv-block">
          <h3 class="dv-label">${heading}</h3>
          <p>${why}</p>
        </section>
        <details class="disclosure dv-more">
          <summary>Route details</summary>
          <div class="disclosure-body">
            <dl class="facts">
              <div><dt>Straight-line distance</dt><dd>${km(lane.distance_km)}</dd></div>
              <div><dt>Distance by ${esc(lane.mode)}, estimated</dt><dd>${km(lane.route_km)}</dd></div>
              <div><dt>Weight a year</dt><dd>${weight} t</dd></div>
              <div><dt>Orders and returns</dt><dd>${lane.order_count} and ${lane.return_count}</dd></div>
            </dl>
          </div>
        </details>
        <details class="disclosure dv-more" data-scenario-panel>
          <summary>Try a different transport mode or warehouse</summary>
          <div class="disclosure-body sc-host"></div>
        </details>
        <div class="dv-actions">
          <button type="button" class="btn btn-secondary" data-go="map">See this route on the map</button>
          <button type="button" class="btn btn-ghost" data-close>Close</button>
        </div>`;
    }

    /* The what-if */

    function startScenario() {
        const host = body.querySelector(".sc-host");
        const lane = laneById(state.edge);
        if (!host || !lane) return;
        if (!host.childElementCount) host.innerHTML = scenarioView(lane);
        if (!state.result && !state.loading) runScenario();
    }

    function scenarioView(lane) {
        const inbound = lane.leg === "inbound";
        const modeOptions = MODES.map(
            (mode) =>
                `<option value="${mode}"${mode === state.mode ? " selected" : ""}>${title(mode)}${mode === lane.mode ? " (today)" : ""}</option>`
        ).join("");
        const siteOptions = inbound
            ? `<option>${esc(lane.dest_name)} (today)</option>`
            : NETWORK.warehouses
                  .map(
                      (site) =>
                          `<option value="${site.id}"${site.id === state.origin ? " selected" : ""}>${esc(site.name)}${site.id === lane.origin_id ? " (today)" : ""}</option>`
                  )
                  .join("");

        return `
        <p class="sc-banner" role="note"><span class="sc-badge">What-if</span> A test only. It does not change your results or exports.</p>

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
        ${inbound ? '<p class="sc-hint">An inbound route keeps its warehouse here. To close or open sites, use the warehouse what-if under Network.</p>' : ""}

        <div class="sc-result" id="sc-result" aria-live="polite">${resultBlock()}</div>

        <div class="sc-actions">
          <button type="button" class="btn btn-ghost btn-small" data-reset>Reset to today</button>
          <button type="button" class="btn btn-secondary btn-small" data-pin disabled>Add to comparison</button>
        </div>

        <div id="sc-compare">${compareBlock()}</div>`;
    }

    function resultBlock() {
        if (state.failed) {
            return `<p class="network-warn is-error">${esc(state.failed)}</p>`;
        }
        const result = state.result;
        if (!result) {
            return '<p class="whatif-hint" aria-busy="true">Working it out…</p>';
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
                 <strong class="cost-ink">${delta(result.saved.cost, money, "less cost a year", "more cost a year")}</strong>
                 <strong class="carbon-ink">${delta(result.saved.co2e, carbon, "less CO₂e a year", "more CO₂e a year")}</strong>
               </p>`
            : '<p class="sc-delta muted-ink">This is the route as it runs today. Change the mode or warehouse to test something.</p>';

        return `
        <div class="table-scroll${state.loading ? " is-loading" : ""}" tabindex="0">
        <table class="grid sc-table">
          <thead><tr><th></th><th class="num">Today</th><th class="num sc-after">What-if</th></tr></thead>
          <tbody>
            ${row("Mode", title(b.mode), title(a.mode))}
            ${row("From", esc(b.origin_name), esc(a.origin_name))}
            ${row("Cost a year", `<span class="cost-ink">${money(b.cost)}</span>`, `<span class="cost-ink">${money(a.cost)}</span>`)}
            ${row("CO₂e a year", `<span class="carbon-ink">${carbon(b.co2e)}</span>`, `<span class="carbon-ink">${carbon(a.co2e)}</span>`)}
            ${row("Transit", days(b.days), days(a.days))}
            ${row("Distance", km(b.route_km), km(a.route_km))}
          </tbody>
        </table>
        </div>
        ${summary}
        ${result.note ? `<p class="network-warn">${esc(result.note)}</p>` : ""}`;
    }

    function compareBlock() {
        if (!state.pinned.length || !state.result) return "";
        const base = state.result.before;
        const columns = [
            { label: "Today", sub: title(base.mode), figures: base, removable: false },
            ...state.pinned.map((item, index) => ({
                label: "What-if " + String.fromCharCode(65 + index),
                sub: title(item.mode) + (item.origin_name !== base.origin_name ? ", from " + esc(item.origin_name) : ""),
                figures: item,
                removable: true,
                index,
            })),
        ];
        const cells = (fn) => columns.map((col) => `<td class="num">${fn(col.figures)}</td>`).join("");
        return `
        <section class="sc-compare" aria-labelledby="sc-compare-title">
          <p class="dv-label" id="sc-compare-title">Comparison</p>
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
              <tr><th scope="row">Cost a year</th>${cells((f) => `<span class="cost-ink">${moneyShort(f.cost)}</span>`)}</tr>
              <tr><th scope="row">CO₂e a year</th>${cells((f) => `<span class="carbon-ink">${carbon(f.co2e)}</span>`)}</tr>
              <tr><th scope="row">Transit</th>${cells((f) => days(f.days))}</tr>
            </tbody>
          </table>
          </div>
        </section>`;
    }

    async function runScenario() {
        if (!laneById(state.edge)) return;
        const ticket = ++state.request;
        state.loading = true;
        state.failed = null;
        refreshResult();

        try {
            const response = await postJson("/api/simulate", {
                edge_id: state.edge,
                mode: state.mode,
                origin_id: state.origin,
            });
            const data = await response.json();
            if (ticket !== state.request) return;
            // A workspace that has expired, or a server that has restarted,
            // no longer holds the route this page was drawn from.
            if (data.error === "no such lane") {
                throw new Error("These results are no longer loaded, usually because the session expired. Reload the page to try again.");
            }
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
        const result = document.getElementById("sc-result");
        const compare = document.getElementById("sc-compare");
        if (result) result.innerHTML = resultBlock();
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

    document.addEventListener("click", (event) => {
        if (drawer.contains(event.target)) return;
        const change = event.target.closest("[data-open-change]");
        if (change) {
            event.preventDefault();
            openChange(Number(change.dataset.openChange), change);
            return;
        }
        const investigate = event.target.closest("[data-investigate]");
        if (investigate) {
            openLane(Number(investigate.dataset.investigate), investigate);
            return;
        }
        const scenario = event.target.closest("[data-scenario]");
        if (scenario) openLane(Number(scenario.dataset.scenario), scenario, { scenario: true });
    });

    document.addEventListener("route:open", (event) => {
        const { id, view, trigger } = event.detail;
        openLane(id, trigger, { scenario: view === "scenario" });
    });

    drawer.addEventListener("click", (event) => {
        const target = event.target.closest("button, a");
        if (!target) return;
        if (target.matches("[data-close]")) return close();
        if (target.matches("[data-back]")) {
            // A link out of the panel closes it and then goes where it points.
            if (target.tagName === "A") return close({ restoreFocus: false });
            return backToList();
        }
        if (target.matches("[data-mark]")) return toggleMark(target.dataset.mark);
        if (target.dataset.unpin !== undefined) {
            state.pinned.splice(Number(target.dataset.unpin), 1);
            return refreshResult();
        }
        if (target.matches("[data-pin]")) return pinScenario();
        if (target.matches("[data-reset]")) {
            const lane = laneById(state.edge);
            state.mode = lane.mode;
            state.origin = lane.leg === "inbound" ? null : lane.origin_id;
            const mode = document.getElementById("sc-mode");
            const origin = document.getElementById("sc-origin");
            if (mode) mode.value = state.mode;
            if (origin && state.origin !== null) origin.value = String(state.origin);
            return runScenario();
        }
        if (target.dataset.go === "map") {
            close({ restoreFocus: false });
            const map = document.querySelector(".map-panel");
            if (map) map.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    });

    // The what-if runs the first time its disclosure is opened. Toggle does not
    // bubble, so it is caught on the way down.
    drawer.addEventListener(
        "toggle",
        (event) => {
            if (event.target.matches("[data-scenario-panel]") && event.target.open) startScenario();
        },
        true
    );

    drawer.addEventListener("change", (event) => {
        if (event.target.id === "sc-mode") state.mode = event.target.value;
        else if (event.target.id === "sc-origin") state.origin = Number(event.target.value);
        else return;
        runScenario();
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !drawer.hidden) close();
    });

    // The sticky bars sit under the site header, whose height depends on how
    // its links wrap.
    const topbar = document.querySelector(".topbar");
    const syncTopbar = () => {
        if (topbar) document.documentElement.style.setProperty("--topbar-h", topbar.offsetHeight + "px");
    };
    syncTopbar();
    addEventListener("resize", syncTopbar, { passive: true });

    // A link from the summary, the landing page or a shared address can open
    // a change or a route.
    const params = new URLSearchParams(location.search);
    const asked = Number(params.get("open"));
    const investigate = Number(params.get("investigate"));
    const tested = Number(params.get("scenario"));
    if (asked) openChange(asked);
    else if (tested && laneById(tested)) openLane(tested, null, { scenario: true });
    else if (investigate && laneById(investigate)) openLane(investigate);
})();
