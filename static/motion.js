// Figures count up as they arrive, so the size of the number is what lands
// rather than the label beside it. The server already renders the final value,
// so if this never runs the page is still correct.

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

function countTo(el) {
    const target = Number(el.dataset.count);
    const prefix = el.dataset.prefix || "";
    const suffix = el.dataset.suffix || "";
    const start = performance.now();

    function step(now) {
        const t = Math.min((now - start) / 900, 1);
        const eased = 1 - Math.pow(1 - t, 3);
        const value = Math.round(target * eased).toLocaleString("en-US");
        el.textContent = prefix + value + suffix;
        if (t < 1) requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
}

if (!REDUCED) {
    const arriving = new IntersectionObserver(
        (entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                arriving.unobserve(entry.target);
                countTo(entry.target);
            });
        },
        { threshold: 0.6 }
    );

    document
        .querySelectorAll("[data-count]")
        .forEach((el) => arriving.observe(el));
}
