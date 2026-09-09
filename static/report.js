// The rail tracks which step you are reading.
//
// This began as an IntersectionObserver with a thin rootMargin band across
// the middle of the screen, which is the tidier way to ask the question. It
// did not fire reliably, and a rail that silently stops updating is worse
// than a slightly less elegant one that does not. So it measures instead:
// which step contains the middle of the viewport, recalculated on scroll and
// throttled to one frame, which is a few rectangle reads and costs nothing.

const rail = document.querySelectorAll("[data-rail]");
const steps = [...document.querySelectorAll("[data-step]")];

if (rail.length && steps.length) {
    let current = null;

    const mark = (key) => {
        if (key === current) return;
        current = key;
        rail.forEach((link) => {
            const on = link.dataset.rail === key;
            link.classList.toggle("on", on);
            if (on) {
                link.setAttribute("aria-current", "true");
            } else {
                link.removeAttribute("aria-current");
            }
        });
    };

    const update = () => {
        const middle = window.innerHeight / 2;
        // Last step whose top has passed the middle of the screen. Walking
        // backwards means the deepest one wins, which is the one being read.
        let found = steps[0];
        for (const step of steps) {
            if (step.getBoundingClientRect().top <= middle) found = step;
        }
        mark(found.dataset.step);
    };

    // Cancel and re-request rather than guarding with a boolean. A background
    // tab does not run animation frames at all, and a boolean guard set on the
    // way in never gets cleared, so the rail would stop updating for good the
    // moment the page spent a second in the background. This way the pending
    // frame is simply replaced, and whatever is outstanding runs when the tab
    // comes back.
    let frame = 0;
    const schedule = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(update);
    };

    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule, { passive: true });
    window.addEventListener("load", schedule);
    document.addEventListener("visibilitychange", update);
    update();
}

/* Landing on the right step, and staying there.
 *
 * Three separate things used to move the page out from under a link to a
 * step. The browser restores the scroll position it remembers for the URL,
 * and it does that after the jump. The map is most of a screen tall and
 * arrives late, because it fetches world geometry before it can draw. And the
 * ranked wins fill in at about the same time. Reserving space in the
 * stylesheet handles most of the last two, but the map is not
 * pixel-predictable, so the target is held for a moment rather than jumped to
 * once and hoped for.
 */

const target = location.hash ? document.querySelector(location.hash) : null;

if (target) {
    if ("scrollRestoration" in history) {
        // Only turned off when the address names a step. Restoring the
        // position is the right behaviour every other time.
        history.scrollRestoration = "manual";
        window.addEventListener("pagehide", () => {
            history.scrollRestoration = "auto";
        });
    }

    // The moment the reader takes over, this stops. Nothing is more annoying
    // than a page that scrolls itself while you are scrolling it.
    let held = true;
    const release = () => {
        held = false;
    };
    for (const event of ["wheel", "touchstart", "keydown", "pointerdown"]) {
        window.addEventListener(event, release, { passive: true, once: true });
    }

    const pin = () => {
        if (!held) return;
        const previous = document.documentElement.style.scrollBehavior;
        document.documentElement.style.scrollBehavior = "auto";
        target.scrollIntoView();
        document.documentElement.style.scrollBehavior = previous;
    };

    pin();

    // Re-pin while the page is still changing height, then stop. The map
    // fetches world geometry from a CDN before it can draw, so the last shift
    // can arrive several seconds in on a cold cache. Five seconds covers it,
    // and costs nothing, because any touch of the wheel or the keyboard ends
    // it immediately.
    const watchHeight = new ResizeObserver(pin);
    watchHeight.observe(document.body);
    window.addEventListener("load", () => requestAnimationFrame(pin));
    setTimeout(() => {
        watchHeight.disconnect();
        held = false;
    }, 5000);
}
