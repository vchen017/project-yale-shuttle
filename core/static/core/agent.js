// "Ask the trip agent" on the when-to-leave page (#148). The form works without
// JavaScript: the page shows the agent's answer once it's reloaded. With it,
// this sends the question in the background and polls until the agent has
// answered. The server renders and escapes each answer's HTML, so this never
// builds markup from the model's text itself.
(() => {
  const POLL_EVERY_MS = 3000;
  const MAX_POLLS = 80; // 4 minutes: by then the server has given up on the agent too

  const getJSON = (url, options = {}) =>
    fetch(url, { ...options, credentials: "same-origin", headers: { Accept: "application/json" } }).then(
      (response) => {
        if (!response.ok) throw new Error(`the server answered ${response.status}`);
        return response.json();
      },
    );

  function swap(box, html, polls) {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    const fresh = template.content.firstElementChild;
    box.replaceWith(fresh);
    watch(fresh, polls);
  }

  function poll(box, polls) {
    if (polls >= MAX_POLLS) return;
    setTimeout(() => {
      getJSON(box.dataset.agentPoll)
        .then((answer) => swap(box, answer.html, polls + 1))
        .catch(() => poll(box, polls + 1)); // the server may be waking up: try again
    }, POLL_EVERY_MS);
  }

  function watch(box, polls = 0) {
    const form = box.querySelector("form[data-agent-ask]");
    if (form) {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        form.querySelector("button").disabled = true;
        getJSON(form.action, { method: "POST", body: new FormData(form) })
          .then((answer) => swap(box, answer.html, 0))
          .catch(() => form.submit()); // fall back to the plain form
      });
    }
    if (box.dataset.agentPoll) poll(box, polls);
  }

  document.querySelectorAll("[data-agent]").forEach((box) => watch(box));
})();
