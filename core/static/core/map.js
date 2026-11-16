// The home page's map of shuttle stops (#71): the routes running now in their
// colours, a pin for each stop, and the stops nearest the rider. The data comes
// from /network.json after the page loads, so the page never waits on the feed.
(() => {
  const section = document.getElementById("stops");
  if (!section || !window.L) return;
  const status = section.querySelector("[data-map-status]");
  const locateButton = section.querySelector("[data-locate]");
  const locateStatus = section.querySelector("[data-locate-status]");
  const nearbyList = section.querySelector("[data-nearby]");
  const CENTRAL_CAMPUS = [41.3111, -72.9267];
  const WALK_METERS_PER_MINUTE = 80;
  let youAreHere = null;

  const map = L.map(section.querySelector("[data-map]"), { scrollWheelZoom: false }).setView(CENTRAL_CAMPUS, 15);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);

  // Built from DOM nodes, never HTML strings: the names come from the feed.
  function chip(route) {
    const span = document.createElement("span");
    span.className = "route-chip";
    span.style.backgroundColor = `#${route.color}`;
    span.style.color = isLight(route.color) ? "#212529" : "#fff";
    span.textContent = route.label;
    span.title = route.name;
    return span;
  }

  function isLight(hex) {
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6;
  }

  function stopDetails(stop, routesById, note) {
    const box = document.createElement("div");
    const name = document.createElement("div");
    name.className = "fw-semibold";
    name.textContent = stop.name;
    box.append(name);
    if (note) {
      const line = document.createElement("div");
      line.className = "small text-body-secondary";
      line.textContent = note;
      box.append(line);
    }
    const chips = document.createElement("div");
    for (const id of stop.routes) {
      if (routesById.has(id)) chips.append(chip(routesById.get(id)));
    }
    box.append(chips);
    if (stop.url) {
      const link = document.createElement("a");
      link.href = stop.url;
      link.className = "d-inline-block mt-1";
      link.textContent = "Next buses →";
      box.append(link);
    }
    return box;
  }

  function metersBetween([lat1, lon1], [lat2, lon2]) {
    const rad = Math.PI / 180;
    const a =
      Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.sqrt(a));
  }

  function walk(meters) {
    return `about ${Math.max(1, Math.round(meters / WALK_METERS_PER_MINUTE))} min walk`;
  }

  function sayNearby(text) {
    locateStatus.textContent = text;
    locateStatus.hidden = false;
  }

  function draw(network) {
    const routesById = new Map(network.routes.map((route) => [route.id, route]));
    for (const route of network.routes) {
      const points = [];
      for (let i = 0; i + 1 < route.path.length; i += 2) points.push([route.path[i], route.path[i + 1]]);
      if (points.length > 1) {
        L.polyline(points, { color: `#${route.color}`, weight: 4, opacity: network.running ? 0.75 : 0.35 }).addTo(map);
      }
    }
    for (const stop of network.stops) {
      L.circleMarker([stop.lat, stop.lon], { radius: 6, color: "#00356b", weight: 2, fillColor: "#fff", fillOpacity: 1 })
        .bindPopup(() => stopDetails(stop, routesById))
        .addTo(map);
    }
    const count = network.routes.length;
    const tapFor = network.stops.some((stop) => stop.url) ? "its routes and next buses" : "the routes that stop there";
    status.textContent = network.running
      ? `The ${count} route${count === 1 ? "" : "s"} running now. Tap a stop to see ${tapFor}.`
      : "No shuttles are running right now. This is where they go.";
    if ("geolocation" in navigator && network.stops.length) {
      locateButton.hidden = false;
      locateButton.onclick = () => locate(network, routesById);
    }
  }

  function locate(network, routesById) {
    locateButton.disabled = true;
    sayNearby("Finding you…");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locateButton.disabled = false;
        const here = [position.coords.latitude, position.coords.longitude];
        if (youAreHere) youAreHere.setLatLng(here);
        else {
          youAreHere = L.circleMarker(here, { radius: 8, color: "#fff", weight: 3, fillColor: "#286dc0", fillOpacity: 1 })
            .bindTooltip("You are here")
            .addTo(map);
        }
        const nearest = network.stops
          .map((stop) => ({ stop, meters: metersBetween(here, [stop.lat, stop.lon]) }))
          .sort((a, b) => a.meters - b.meters)
          .slice(0, 5);
        map.fitBounds(L.latLngBounds([here, ...nearest.map(({ stop }) => [stop.lat, stop.lon])]).pad(0.2), { maxZoom: 17 });
        nearbyList.replaceChildren(
          ...nearest.map(({ stop, meters }) => {
            const item = document.createElement("li");
            item.className = "list-group-item d-flex gap-2";
            item.append(stopDetails(stop, routesById, walk(meters)));
            return item;
          }),
        );
        nearbyList.hidden = false;
        sayNearby(
          nearest[0].meters > 3000
            ? "You're far from the shuttle routes. These are the closest stops."
            : "The stops closest to you, nearest first.",
        );
      },
      (error) => {
        locateButton.disabled = false;
        sayNearby(
          error.code === error.PERMISSION_DENIED
            ? "Location is blocked for this site. Allow it in your browser's settings to see the stops near you."
            : "Couldn't find your location. Try again in a moment.",
        );
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  }

  fetch(section.dataset.networkUrl, { headers: { Accept: "application/json" } })
    .then((response) => {
      if (!response.ok) throw new Error(`the server answered ${response.status}`);
      return response.json();
    })
    .then(draw)
    .catch(() => {
      status.textContent = "Live shuttle data is unavailable right now. Try again in a minute.";
    });
})();
