// The next trip card on the when-to-leave page (#157), kept live without
// reloading the page: while there's a bus to watch for (data-watching), every
// data-refresh-seconds it asks the server for the card's live part
// (calendars.views.next_trip_card) and swaps it in, and moves the bus on the
// trip's map without redrawing the map. Each ask reads the database, which
// stays awake 5 minutes after every use on our free plan, so it doesn't ask
// otherwise: a card for a later trip asks once, when its live window opens
// (data-live-from), and a tab in the background waits until it's shown again.
// When the next trip is a different event, the page reloads once, so the new
// trip gets its map. Nothing here calls the shuttle feed, and the bus's dot is
// built from DOM nodes, never HTML strings. (Without JavaScript the card is as
// of the page's load.)
(() => {
  const card = document.querySelector("[data-next-trip]");
  if (!card) return;
  const live = card.querySelector("[data-next-trip-live]");
  const holder = card.querySelector("[data-live-bus]");
  const seconds = Math.max(5, Number(card.dataset.refreshSeconds) || 30);
  const LATEST_WAKE = 6 * 3600 * 1000; // a live window further off than this waits for a reload
  const raw = document.getElementById("next-trip-bus");
  let bus = raw ? JSON.parse(raw.textContent) : null; // {lat, lon, age_minutes} or null
  let watching = card.dataset.watching === "true";
  let map = null;
  let marker = null;

  function showBus() {
    if (!map || !window.maplibregl) return;
    if (!bus) {
      if (marker) marker.remove();
      marker = null;
      return;
    }
    const at = [bus.lon, bus.lat];
    if (marker) {
      marker.setLngLat(at);
      return;
    }
    const element = document.createElement("div");
    element.className = "next-trip-bus-dot";
    element.title = "Your bus";
    marker = new maplibregl.Marker({ element }).setLngLat(at).addTo(map);
  }
  if (holder) {
    holder.addEventListener("trip-map:ready", (event) => {
      map = event.detail;
      showBus();
    });
  }

  let timer = null;
  function wait(ms) {
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(refresh, ms);
  }
  function next() {
    // While watching, every few seconds; otherwise once, when the live window opens.
    if (watching) return wait(seconds * 1000);
    const opens = Date.parse(card.dataset.liveFrom) - Date.now();
    if (opens > 0 && opens < LATEST_WAKE) wait(opens + 1000);
  }

  async function refresh() {
    timer = null;
    if (document.visibilityState === "hidden") return; // picked up again on "visibilitychange"
    try {
      const response = await fetch(card.dataset.url, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.ok) {
        const data = await response.json();
        if (data.feed_down) {
          next(); // keep the card as it is; the feed may be back next turn
          return;
        }
        if (data.key !== card.dataset.key) {
          window.location.reload(); // another event is next now (or none): start over with its map
          return;
        }
        live.innerHTML = data.html; // rendered by our own template, autoescaped
        bus = data.bus;
        watching = data.watching;
        showBus();
      }
    } catch {
      // The server may be waking up, or the phone offline: the next turn tries again.
    }
    next();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (watching) {
      refresh(); // a phone brought back to the page sees the trip as it is now
    } else {
      next();
    }
  });
  next();
})();
