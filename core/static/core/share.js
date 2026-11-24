// A shared trip's page (#10): the map of the bus and the stop where the rider
// gets off, the sharer's "Copy" button, and the live part kept fresh: every
// few seconds it fetches this same page and swaps in its times and the bus's
// position, so the map isn't rebuilt and a friend's pan and zoom stay put.
// Nothing here calls the shuttle feed. Markers are built from DOM nodes, never
// HTML strings: names come from the feed. (Without JavaScript the page reloads
// itself instead: see the <noscript> refresh.)
(() => {
  const copy = document.querySelector("[data-copy-link]");
  if (copy) {
    copy.addEventListener("click", async () => {
      const field = document.getElementById(copy.dataset.copyLink);
      try {
        await navigator.clipboard.writeText(field.value);
        copy.textContent = "Copied";
      } catch {
        field.select(); // no clipboard access: leave it selected to copy by hand
      }
    });
  }

  const lngLat = ([lat, lon]) => [lon, lat];
  const readData = (doc) => {
    const raw = doc.getElementById("share-map-data");
    return raw ? JSON.parse(raw.textContent) : null;
  };
  function dot(className, label) {
    const element = document.createElement("div");
    element.className = className;
    element.title = label;
    return element;
  }

  let map = null;
  let loaded = false; // markers wait for the map, so a late load can't put back an old position
  let busMarker = null;
  const data = readData(document);
  let bus = data ? data.bus : null; // the newest position, from the page or its latest refresh
  function showBus() {
    if (!loaded) return;
    if (bus && busMarker) busMarker.setLngLat(lngLat(bus.at));
    else if (bus) busMarker = new maplibregl.Marker({ element: dot("share-bus", "The bus") }).setLngLat(lngLat(bus.at)).addTo(map);
    else if (busMarker) {
      busMarker.remove();
      busMarker = null;
    }
  }

  const holder = document.querySelector("[data-share-map]");
  if (holder && data && window.maplibregl) {
    const points = [...data.route.path];
    if (data.get_off) points.push(data.get_off.at);
    if (data.bus) points.push(data.bus.at);
    if (points.length) {
      const bounds = new maplibregl.LngLatBounds();
      for (const point of points) bounds.extend(lngLat(point));
      map = new maplibregl.Map({
        container: holder,
        style: "https://tiles.openfreemap.org/styles/bright",
        bounds,
        fitBoundsOptions: { padding: 48, maxZoom: 16 },
        attributionControl: { compact: true },
        cooperativeGestures: true,
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
      map.on("load", () => {
        if (data.route.path.length > 1) {
          const color = /^[0-9A-Fa-f]{6}$/.test(data.route.color) ? `#${data.route.color}` : "#00356b";
          map.addSource("ride", {
            type: "geojson",
            data: { type: "Feature", geometry: { type: "LineString", coordinates: data.route.path.map(lngLat) } },
          });
          map.addLayer({
            id: "ride", type: "line", source: "ride",
            paint: { "line-color": color, "line-width": 6 },
            layout: { "line-cap": "round", "line-join": "round" },
          });
        }
        if (data.get_off) {
          new maplibregl.Marker({ element: dot("trip-stop", `Gets off at ${data.get_off.name}`) })
            .setLngLat(lngLat(data.get_off.at))
            .setPopup(new maplibregl.Popup({ offset: 12, closeButton: false }).setText(`Gets off at ${data.get_off.name}`))
            .addTo(map);
        }
        loaded = true;
        showBus();
      });
    } else {
      holder.remove();
    }
  }

  const live = document.querySelector("[data-share-live]");
  const seconds = Number(live && live.dataset.refreshSeconds);
  if (!live || !seconds) return;
  setInterval(async () => {
    let response;
    try {
      response = await fetch(location.href, { cache: "no-store", credentials: "same-origin" });
    } catch {
      return; // offline for a moment: try again next time
    }
    if (response.status === 404 || response.status === 410) {
      location.reload(); // the sharing ended: show the page that says so
      return;
    }
    if (!response.ok) return;
    const doc = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = doc.querySelector("[data-share-live]");
    if (fresh) live.innerHTML = fresh.innerHTML; // our own page's HTML, escaped by Django
    const freshData = readData(doc);
    if (freshData) {
      bus = freshData.bus;
      showBus();
    }
  }, seconds * 1000);
})();
