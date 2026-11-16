// The home page's map of shuttle stops (#71, #73): the routes running now in
// their colours, a pin for each stop, and the stops nearest the rider. A stop's
// popup can start or end the rider's trip in the trip search above the map
// (story #5): its buttons send a "trip:pick" event to trip-search.js.
//
// The map is MapLibre over OpenFreeMap's "Bright" style: vector tiles, so it
// stays sharp on any screen, with parks, main roads and labels in colour,
// lightly faded so the route colours still stand out. Neither needs an API
// key. The shuttle data comes from /network.json after the page loads, so the
// page never waits on the shuttle feed.
(() => {
  const section = document.getElementById("stops");
  if (!section || !window.maplibregl) return;
  const status = section.querySelector("[data-map-status]");
  const locateButton = section.querySelector("[data-locate]");
  const locateStatus = section.querySelector("[data-locate-status]");
  const nearbyList = section.querySelector("[data-nearby]");
  const CENTRAL_CAMPUS = [-72.9267, 41.3111]; // longitude, latitude
  const WALK_METERS_PER_MINUTE = 80;
  // The base map, and how much its layers are faded so the routes stand out.
  const BASE_STYLE = "https://tiles.openfreemap.org/styles/bright";
  const FADE = { symbol: 0.8, line: 0.7, fill: 0.75 };
  let youAreHere = null;
  let popup = null;

  const map = new maplibregl.Map({
    container: section.querySelector("[data-map]"),
    style: BASE_STYLE,
    center: CENTRAL_CAMPUS,
    zoom: 14.6,
    attributionControl: { compact: true },
    // Scrolling the page shouldn't zoom the map: zoom with ctrl or two fingers.
    cooperativeGestures: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
  const mapReady = new Promise((resolve) =>
    map.on("load", () => {
      fadeBaseMap();
      collapseCreditsOnPhones();
      resolve();
    }),
  );

  // On a phone the map's credits start open and cover the bottom of the map;
  // start them closed, one tap away on the (i) button.
  function collapseCreditsOnPhones() {
    const credits = map.getContainer().querySelector(".maplibregl-ctrl-attrib");
    if (credits && map.getContainer().offsetWidth <= 640) {
      credits.removeAttribute("open");
      credits.classList.remove("maplibregl-compact-show");
    }
  }

  // Fade the base map's roads, labels and areas a little, so the routes carry the map.
  function fadeBaseMap() {
    for (const layer of map.getStyle().layers) {
      if (layer.type === "symbol") {
        map.setPaintProperty(layer.id, "text-opacity", FADE.symbol);
        map.setPaintProperty(layer.id, "icon-opacity", FADE.symbol);
      } else if (layer.type === "line") {
        map.setPaintProperty(layer.id, "line-opacity", FADE.line);
      } else if (layer.type === "fill") {
        map.setPaintProperty(layer.id, "fill-opacity", FADE.fill);
      }
    }
  }

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

  function stopDetails(stop, routesById, note, buttons) {
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
    if (buttons) box.append(buttons);
    if (stop.url) {
      const link = document.createElement("a");
      link.href = stop.url;
      link.className = "d-inline-block mt-1";
      link.textContent = "Next buses →";
      box.append(link);
    }
    return box;
  }

  // In a stop's popup, when the page has the trip search: start or end the trip here.
  function tripButtons(stop) {
    if (!document.querySelector("[data-trip-search]")) return null;
    const row = document.createElement("div");
    row.className = "d-flex flex-wrap gap-2 mt-2";
    for (const [end, label, icon] of [
      ["from", "Start here", "bi-record-circle"],
      ["to", "Go here", "bi-geo-alt-fill"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-primary";
      const mark = document.createElement("i");
      mark.className = `bi ${icon} me-1`;
      mark.setAttribute("aria-hidden", "true");
      button.append(mark, label);
      button.addEventListener("click", () => {
        if (popup) popup.remove();
        status.textContent =
          end === "from"
            ? `Starting at ${stop.name}. Now tap the stop you're going to, or type it above.`
            : `Going to ${stop.name}. Now tap the stop you're starting from, or type it above.`;
        const place = { name: stop.name, lat: stop.lat, lon: stop.lon };
        document.dispatchEvent(new CustomEvent("trip:pick", { detail: { end, place } }));
      });
      row.append(button);
    }
    return row;
  }

  function metersBetween([lon1, lat1], [lon2, lat2]) {
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
    const stopsById = new Map(network.stops.map((stop) => [stop.id, stop]));

    map.addSource("routes", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: network.routes.map((route) => {
          const coordinates = [];
          for (let i = 0; i + 1 < route.path.length; i += 2) coordinates.push([route.path[i + 1], route.path[i]]);
          return { type: "Feature", properties: { color: `#${route.color}` }, geometry: { type: "LineString", coordinates } };
        }),
      },
    });
    // A white outline under each route, so it stands out from the streets.
    map.addLayer({
      id: "route-outlines",
      type: "line",
      source: "routes",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": "#ffffff",
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 5, 14, 8, 16, 12],
        "line-opacity": network.running ? 0.9 : 0.4,
      },
    });
    map.addLayer({
      id: "routes",
      type: "line",
      source: "routes",
      layout: { "line-join": "round", "line-cap": "round" },
      paint: {
        "line-color": ["get", "color"],
        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 3, 14, 5, 16, 8],
        "line-opacity": network.running ? 1 : 0.35,
      },
    });

    map.addSource("stops", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: network.stops.map((stop) => ({
          type: "Feature",
          properties: { id: stop.id },
          geometry: { type: "Point", coordinates: [stop.lon, stop.lat] },
        })),
      },
    });
    map.addLayer({
      id: "stops",
      type: "circle",
      source: "stops",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 12, 2.5, 16, 7],
        "circle-color": "#ffffff",
        "circle-stroke-color": "#00356b",
        "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 12, 1, 16, 2.5],
      },
    });
    // The stops are small to hit with a finger, so a wider, invisible circle
    // around each one takes the tap.
    map.addLayer({
      id: "stop-targets",
      type: "circle",
      source: "stops",
      paint: { "circle-radius": 16, "circle-opacity": 0 },
    });

    map.on("click", "stop-targets", (event) => {
      // Of the stops around the tap, the one nearest to it.
      const away = (stop) => map.project([stop.lon, stop.lat]).dist(event.point);
      const stop = event.features
        .map((feature) => stopsById.get(feature.properties.id))
        .filter(Boolean)
        .reduce((nearest, stop) => (!nearest || away(stop) < away(nearest) ? stop : nearest), null);
      if (!stop) return;
      popup = new maplibregl.Popup({ offset: 10, maxWidth: "260px" })
        .setLngLat([stop.lon, stop.lat])
        .setDOMContent(stopDetails(stop, routesById, null, tripButtons(stop)))
        .addTo(map);
    });
    map.on("mouseenter", "stop-targets", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "stop-targets", () => {
      map.getCanvas().style.cursor = "";
    });

    const count = network.routes.length;
    const tapFor = network.stops.some((stop) => stop.url)
      ? "its next buses, or to start or end your trip there"
      : "the routes that stop there";
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
        const here = [position.coords.longitude, position.coords.latitude];
        if (!youAreHere) {
          const dot = document.createElement("div");
          dot.className = "you-are-here";
          dot.title = "You are here";
          youAreHere = new maplibregl.Marker({ element: dot });
        }
        youAreHere.setLngLat(here).addTo(map);
        const nearest = network.stops
          .map((stop) => ({ stop, meters: metersBetween(here, [stop.lon, stop.lat]) }))
          .sort((a, b) => a.meters - b.meters)
          .slice(0, 5);
        const bounds = new maplibregl.LngLatBounds(here, here);
        for (const { stop } of nearest) bounds.extend([stop.lon, stop.lat]);
        map.fitBounds(bounds, { padding: 60, maxZoom: 17 });
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

  const shuttles = fetch(section.dataset.networkUrl, { headers: { Accept: "application/json" } }).then((response) => {
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
    return response.json();
  });
  Promise.all([shuttles, mapReady])
    .then(([network]) => draw(network))
    .catch(() => {
      status.textContent = "Live shuttle data is unavailable right now. Try again in a minute.";
    });
})();
