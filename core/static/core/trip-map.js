// A trip's map: the rider's start and end, the walk to the stop, the stretch of
// the route they ride, the stop they get off at, and the walk from there. The
// trip page has one (#131); the when-to-leave page has one per trip, each
// drawn once it scrolls near the screen, so a full day doesn't load every map
// at once. Each [data-trip-map] holder names its JSON ([lat, lon] pairs) in
// data-map-data, or the trip page's trip-map-data; nothing here calls the
// shuttle feed. The base map is the same OpenFreeMap style as the home page's
// map (map.js). Once a map has loaded, its holder gets a "trip-map:ready"
// event carrying it, so the next trip card (next-trip.js, #157) can put the
// live bus on it.
(() => {
  if (!window.maplibregl) return;
  const lngLat = ([lat, lon]) => [lon, lat];
  const WALK = "#6c757d";

  // Built from DOM nodes, never HTML strings: the names come from the feed.
  function dot(className, label) {
    const element = document.createElement("div");
    element.className = className;
    element.title = label;
    return element;
  }

  function draw(holder) {
    const raw = document.getElementById(holder.dataset.mapData || "trip-map-data");
    if (!raw) return;
    const data = JSON.parse(raw.textContent);

    const points = [data.origin, data.destination];
    if (data.board) points.push(data.board.at, data.get_off.at);
    if (data.route) points.push(...data.route.path);
    const bounds = new maplibregl.LngLatBounds();
    for (const point of points) bounds.extend(lngLat(point));

    const small = "mapCompact" in holder.dataset; // the when-to-leave page's maps
    const map = new maplibregl.Map({
      container: holder,
      style: "https://tiles.openfreemap.org/styles/bright",
      bounds,
      fitBoundsOptions: { padding: small ? 28 : 48, maxZoom: 17 },
      attributionControl: { compact: true },
      cooperativeGestures: true,
    });
    // A small map keeps its room for the trip: no zoom buttons (pinch or ctrl-scroll still zoom), and the
    // credits folded behind their (i) button from the start, as MapLibre folds them once a map is moved.
    if (small) {
      map.once("load", () => holder.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show"));
    } else {
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    }

    function line(id, coordinates, paint, layout) {
      map.addSource(id, {
        type: "geojson",
        data: { type: "Feature", geometry: { type: "LineString", coordinates: coordinates.map(lngLat) } },
      });
      map.addLayer({ id, type: "line", source: id, paint, layout: { "line-cap": "round", "line-join": "round", ...layout } });
    }

    map.on("load", () => {
      if (data.route && data.route.path.length > 1) {
        const color = /^[0-9A-Fa-f]{6}$/.test(data.route.color) ? `#${data.route.color}` : "#00356b";
        line("ride", data.route.path, { "line-color": color, "line-width": 6 });
      }
      const walks = data.board
        ? [
            [data.origin, data.board.at],
            [data.get_off.at, data.destination],
          ]
        : [[data.origin, data.destination]];
      walks.forEach((walk, i) => line(`walk-${i}`, walk, { "line-color": WALK, "line-width": 3, "line-dasharray": [1, 2] }));

      new maplibregl.Marker({ element: dot("you-are-here", "Start") }).setLngLat(lngLat(data.origin)).addTo(map);
      new maplibregl.Marker({ color: "#00356b" }).setLngLat(lngLat(data.destination)).addTo(map);
      if (data.board) {
        for (const [stop, label] of [
          [data.board, "Get on"],
          [data.get_off, "Get off"],
        ]) {
          const popup = new maplibregl.Popup({ offset: 12, closeButton: false }).setText(`${label}: ${stop.name}`);
          new maplibregl.Marker({ element: dot("trip-stop", `${label}: ${stop.name}`) })
            .setLngLat(lngLat(stop.at))
            .setPopup(popup)
            .addTo(map);
        }
      }
      holder.dispatchEvent(new CustomEvent("trip-map:ready", { detail: map }));
    });
  }

  const holders = [...document.querySelectorAll("[data-trip-map]")];
  if (holders.length > 1 && "IntersectionObserver" in window) {
    const watcher = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          watcher.unobserve(entry.target);
          draw(entry.target);
        }
      },
      { rootMargin: "300px 0px" },
    );
    holders.forEach((holder) => watcher.observe(holder));
  } else {
    holders.forEach(draw);
  }
})();
