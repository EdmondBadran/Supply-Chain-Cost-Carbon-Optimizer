const stages = document.querySelectorAll(".stage");

function open(key) {
    stages.forEach((button) => {
        const on = button.dataset.stage === key;
        button.classList.toggle("on", on);
        button.setAttribute("aria-expanded", String(on));
    });
    document.querySelectorAll(".stage-detail").forEach((panel) => {
        panel.hidden = panel.id !== "detail-" + key;
    });
}

stages.forEach((button) => {
    button.addEventListener("click", () => {
        const alreadyOpen = button.classList.contains("on");
        if (alreadyOpen) {
            button.classList.remove("on");
            button.setAttribute("aria-expanded", "false");
            document.getElementById("detail-" + button.dataset.stage).hidden = true;
            return;
        }
        open(button.dataset.stage);
    });
});

// Open the stage with the most at stake, so the page lands on the worst part
// of the chain rather than on whatever happens to come first.
const worst = [...stages]
    .filter((b) => b.classList.contains("has-problems"))
    .sort(
        (a, b) =>
            Number(b.dataset.stake || 0) - Number(a.dataset.stake || 0)
    )[0];
if (worst) open(worst.dataset.stage);
