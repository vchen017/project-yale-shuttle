// The trip page's map (#131): the rider's start and end, the walk to the stop,
// the stretch of the route they ride, the stop they get off at, and the walk
// from there. The page gives the data as JSON ([lat, lon] pairs); nothing here
// calls the shuttle feed. The base map is the same OpenFreeMap style as the
// home page's map (map.js).
(() => {
  const holder = document.querySelector("[data-trip-map]");
  const raw = document.getElementById("trip-map-data");
  if (!holder || !raw || !window.maplibregl) return;
  const data = JSON.parse(raw.textContent);
  const lngLat = ([lat, lon]) => [lon, lat];
  const WALK = "#6c757d";

  const points = [data.origin, data.destination];
  if (data.board) points.push(data.board.at, data.get_off.at);
  if (data.route) points.push(...data.route.path);
  const bounds = new maplibregl.LngLatBounds();
  for (const point of points) bounds.extend(lngLat(point));

  const map = new maplibregl.Map({
    container: holder,
    style: "https://tiles.openfreemap.org/styles/bright",
    bounds,
    fitBoundsOptions: { padding: 48, maxZoom: 17 },
    attributionControl: { compact: true },
    cooperativeGestures: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");

  function line(id, coordinates, paint, layout) {
    map.addSource(id, {
      type: "geojson",
      data: { type: "Feature", geometry: { type: "LineString", coordinates: coordinates.map(lngLat) } },
    });
    map.addLayer({ id, type: "line", source: id, paint, layout: { "line-cap": "round", "line-join": "round", ...layout } });
  }

  // Built from DOM nodes, never HTML strings: the names come from the feed.
  function dot(className, label) {
    const element = document.createElement("div");
    element.className = className;
    element.title = label;
    return element;
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
  });
})();
