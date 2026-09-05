const stages = document.querySelectorAll(".river-stage");
const details = document.querySelectorAll(".stage-detail");

function close() {
    stages.forEach((button) => {
        button.classList.remove("on");
        button.setAttribute("aria-expanded", "false");
    });
    details.forEach((panel) => {
        panel.hidden = true;
    });
}

function open(key) {
    close();
    stages.forEach((button) => {
        if (button.dataset.stage !== key) return;
        button.classList.add("on");
        button.setAttribute("aria-expanded", "true");
    });
    const panel = document.getElementById("detail-" + key);
    if (!panel) return;
    panel.hidden = false;
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

stages.forEach((button) => {
    button.addEventListener("click", () => {
        if (button.classList.contains("on")) {
            close();
            return;
        }
        open(button.dataset.stage);
    });
});

// Nothing opens on load. The headline finding is already on the page, so the
// per-stage detail is the part worth keeping out of the way until asked for.
