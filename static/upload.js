// The upload area. It names the file that was chosen or dropped, catches a
// file that is not a CSV before it is sent, and says the analysis is running
// while the server works. The server still checks everything; this only
// saves a round trip for the mistakes a browser can see.

const form = document.querySelector("[data-upload]");

if (form) {
    const input = form.querySelector("#orders");
    const zone = form.querySelector("[data-dropzone]");
    const title = form.querySelector("[data-drop-title]");
    const hint = form.querySelector("[data-drop-hint]");
    const error = form.querySelector("[data-drop-error]");
    const submit = form.querySelector("[data-upload-submit]");

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

    const update = () => {
        const file = input.files && input.files[0];
        zone.classList.remove("has-file");
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
