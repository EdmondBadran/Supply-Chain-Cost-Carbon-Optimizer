// The upload area. It names the file that was chosen or dropped, catches a
// file that is not a CSV before it is sent, asks which column is which when
// the file's headings are not ones the loader knows, and says the analysis is
// running while the server works. The server still checks everything; this
// only saves a round trip for the mistakes a browser can see.

const form = document.querySelector("[data-upload]");

if (form) {
    const input = form.querySelector("#orders");
    const zone = form.querySelector("[data-dropzone]");
    const title = form.querySelector("[data-drop-title]");
    const hint = form.querySelector("[data-drop-hint]");
    const error = form.querySelector("[data-drop-error]");
    const submit = form.querySelector("[data-upload-submit]");
    const matcher = form.querySelector("[data-matcher]");
    const matcherTitle = form.querySelector("[data-matcher-title]");
    const matcherFields = form.querySelector("[data-matcher-fields]");

    let guide = { required: [], aliases: {} };
    try {
        guide = JSON.parse(form.dataset.columns || "{}");
    } catch {
        // Without the guide the server still names any missing column.
    }

    const startTitle = title.innerHTML;
    const startHint = hint.textContent;
    const startLabel = submit.textContent;

    const isCsv = (file) => /\.csv$/i.test(file.name);

    const size = (bytes) =>
        bytes >= 1024 * 1024
            ? (bytes / (1024 * 1024)).toFixed(1) + " MB"
            : Math.max(1, Math.round(bytes / 1024)) + " KB";

    const showError = (message) => {
        error.textContent = message;
        error.hidden = !message;
        zone.classList.toggle("has-error", Boolean(message));
    };

    // The same reduction the loader applies, so Weight (kg) is weight_kg here too.
    const key = (name) =>
        String(name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");

    const splitLine = (line, delimiter) => {
        const cells = [];
        let cell = "";
        let quoted = false;
        for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (quoted) {
                if (ch === '"' && line[i + 1] === '"') {
                    cell += '"';
                    i++;
                } else if (ch === '"') {
                    quoted = false;
                } else {
                    cell += ch;
                }
            } else if (ch === '"') {
                quoted = true;
            } else if (ch === delimiter) {
                cells.push(cell);
                cell = "";
            } else {
                cell += ch;
            }
        }
        cells.push(cell);
        return cells.map((value) => value.trim());
    };

    const readHead = async (file) => {
        const text = (await file.slice(0, 65536).text()).replace(/^﻿/, "");
        const lines = text.split(/\r?\n/).filter((line) => line.trim());
        const first = lines[0] || "";
        const delimiter = [";", "\t", "|"].reduce(
            (best, d) => (first.split(d).length > first.split(best).length ? d : best),
            ","
        );
        return {
            headings: splitLine(first, delimiter),
            example: lines[1] ? splitLine(lines[1], delimiter) : [],
        };
    };

    const clearMatcher = () => {
        matcherFields.replaceChildren();
        matcher.hidden = true;
    };

    const showMatcher = ({ headings, example }) => {
        const keys = headings.map(key);
        const missing = guide.required.filter(
            ([name]) => !keys.includes(name) && !(guide.aliases[name] || []).some((alias) => keys.includes(alias))
        );
        matcherFields.replaceChildren();
        if (!missing.length || !headings.some(Boolean)) {
            matcher.hidden = true;
            return;
        }
        matcherTitle.textContent = `Match ${missing.length} column${missing.length === 1 ? "" : "s"}`;
        for (const [name, meaning] of missing) {
            const field = document.createElement("label");
            field.className = "field";

            const label = document.createElement("span");
            label.className = "field-label";
            label.textContent = meaning;

            const note = document.createElement("span");
            note.className = "field-hint";
            note.textContent = `We call this ${name}`;

            const select = document.createElement("select");
            select.name = `column_${name}`;
            select.required = true;
            select.add(new Option("Choose a column from your file", ""));
            headings.forEach((heading, i) => {
                if (!heading) return;
                const sample = example[i] ? ` (for example ${example[i].slice(0, 40)})` : "";
                select.add(new Option(heading + sample, heading));
            });

            field.append(label, note, select);
            matcherFields.append(field);
        }
        matcher.hidden = false;
    };

    // Only the latest file chosen gets to draw the matcher.
    let reading = 0;

    const update = async () => {
        const file = input.files && input.files[0];
        zone.classList.remove("has-file");
        clearMatcher();
        if (!file) {
            title.innerHTML = startTitle;
            hint.textContent = startHint;
            showError("");
            return;
        }
        title.textContent = file.name;
        if (!isCsv(file)) {
            hint.textContent = "Choose a different file";
            showError("That file is not a CSV. Save your spreadsheet as CSV UTF-8 and choose it again.");
            return;
        }
        showError("");
        zone.classList.add("has-file");
        hint.textContent = size(file.size) + ", ready to analyze. Choose again to replace it.";

        const mine = ++reading;
        try {
            const head = await readHead(file);
            if (mine === reading) showMatcher(head);
        } catch {
            // Unreadable here is not the last word: the server reads it too.
        }
    };

    input.addEventListener("change", update);

    for (const type of ["dragenter", "dragover"]) {
        zone.addEventListener(type, () => zone.classList.add("is-over"));
    }
    for (const type of ["dragleave", "dragend", "drop"]) {
        zone.addEventListener(type, () => zone.classList.remove("is-over"));
    }

    form.addEventListener("submit", (event) => {
        const file = input.files && input.files[0];
        if (!file) {
            event.preventDefault();
            showError("Choose your orders CSV first, or drop it onto the upload area.");
            input.focus();
            return;
        }
        if (!isCsv(file)) {
            event.preventDefault();
            input.focus();
            return;
        }
        submit.disabled = true;
        submit.setAttribute("aria-busy", "true");
        submit.textContent = "Analyzing your data…";
    });

    // Coming back to this page from the results restores it from the cache,
    // button and all, so the busy state is undone on the way in.
    addEventListener("pageshow", () => {
        submit.disabled = false;
        submit.removeAttribute("aria-busy");
        submit.textContent = startLabel;
    });
}
