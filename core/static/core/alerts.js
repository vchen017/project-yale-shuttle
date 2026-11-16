// The home page's service alerts (#13). The page arrives with the admins'
// alerts; this swaps in the full list, with the Downtowner's announcements,
// from the server. The server renders and escapes that HTML, so the page never
// waits on the shuttle feed and never builds markup from feed text itself.
(() => {
  const section = document.getElementById("service-alerts");
  if (!section) return;
  const list = section.querySelector("[data-alerts]");

  fetch(section.dataset.alertsUrl, { headers: { Accept: "text/html" } })
    .then((response) => {
      if (!response.ok) throw new Error(`the server answered ${response.status}`);
      return response.text();
    })
    .then((html) => {
      list.innerHTML = html;
      section.hidden = html.trim() === "";
    })
    .catch(() => {
      // Keep the admins' alerts the page arrived with.
    });
})();
