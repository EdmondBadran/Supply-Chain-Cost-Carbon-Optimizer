// Every form on the page carries a token proving the page came from here.
// The three endpoints called as JSON rather than posted as a form send the
// same token as a header, so one helper covers all of them.

const CSRF_TOKEN =
    document.querySelector('meta[name="csrf-token"]')?.content || "";

function postJson(url, body) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": CSRF_TOKEN,
        },
        body: JSON.stringify(body),
    });
}
