// Finding places as a rider types (story #5, #7), shared by the trip search
// (trip-search.js) and saved places (places/place-picker.js):
//
//   placeSearch.find(query, { placesUrl, signal }) -> [{name, detail, lat, lon}, ...]
//   placeSearch.item(place, id) -> an <li> for a list of suggestions
//
// The places are ours first, from /places.json (the rider's saved places when
// placesUrl asks for them with mine=1, then our stops), then buildings and
// addresses from Photon. Photon (https://photon.komoot.io), a free search over
// OpenStreetMap data, refuses requests from our Render servers, so the browser
// asks it directly, the way the map loads its tiles. Its results are kept to
// New Haven and its neighbours, nearer campus first, as in transit/places.py.
window.placeSearch = (() => {
  const PHOTON_URL = "https://photon.komoot.io/api/";
  const PHOTON_AREA = { lat: "41.3111", lon: "-72.9267", zoom: "14", bbox: "-73.05,41.24,-72.83,41.40" };
  const MOST_PLACES = 5;

  // Ours, then Photon's. Either can fail without taking the other with it.
  async function find(query, { placesUrl, signal } = {}) {
    const [ours, addresses] = await Promise.all([
      orNothing(fromUs(placesUrl, query, signal)),
      orNothing(photon(query, signal)),
    ]);
    return [...ours, ...addresses];
  }

  async function orNothing(promise) {
    try {
      return await promise;
    } catch (error) {
      if (error.name === "AbortError") throw error;
      return [];
    }
  }

  async function fromUs(placesUrl, query, signal) {
    const url = new URL(placesUrl, window.location.href);
    url.searchParams.set("q", query);
    const response = await fetch(url, { signal, headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
    return (await response.json()).places;
  }

  async function photon(query, signal) {
    const params = new URLSearchParams({ q: query, limit: "10", lang: "en", ...PHOTON_AREA });
    const response = await fetch(`${PHOTON_URL}?${params}`, { signal });
    if (!response.ok) throw new Error(`Photon answered ${response.status}`);
    return distinct((await response.json()).features.map(photonPlace)).slice(0, MOST_PLACES);
  }

  // One of Photon's results as a place: "Sterling Library", "York Street, New Haven".
  function photonPlace(feature) {
    const about = feature.properties || {};
    const [lon, lat] = feature.geometry.coordinates;
    const street = [about.housenumber, about.street].filter(Boolean).join(" ");
    const name = about.name || street || about.city || "";
    const detail = [about.name ? street : "", about.city].filter(Boolean).join(", ");
    return { name, detail: detail === name ? "" : detail, lat, lon };
  }

  // Without nameless places or repeats: OpenStreetMap often has a station, its
  // building and its platforms under one name, and a street in pieces.
  function distinct(places) {
    const seen = new Set();
    return places.filter((place) => {
      const name = place.name.toLowerCase();
      const keys = [`${name}|${place.detail.toLowerCase()}`, `${name}|${place.lat.toFixed(2)}|${place.lon.toFixed(2)}`];
      if (!place.name || keys.some((key) => seen.has(key))) return false;
      keys.forEach((key) => seen.add(key));
      return true;
    });
  }

  // A suggestion, built from DOM nodes: the names come from outside. Saved
  // places, and "Use my location", get an icon.
  function item(place, id) {
    const item = document.createElement("li");
    item.className = "list-group-item list-group-item-action";
    item.id = id;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", "false");
    const name = document.createElement("div");
    name.className = place.mine ? "fw-semibold text-primary" : "fw-semibold";
    const iconName = place.mine ? "bi-crosshair" : place.home ? "bi-house-door-fill" : place.saved ? "bi-star-fill" : "";
    if (iconName) {
      const icon = document.createElement("i");
      icon.className = `bi ${iconName} text-primary me-2`;
      icon.setAttribute("aria-hidden", "true");
      name.append(icon);
    }
    name.append(place.name);
    item.append(name);
    if (place.detail) {
      const detail = document.createElement("div");
      detail.className = "small text-body-secondary";
      detail.textContent = place.detail;
      item.append(detail);
    }
    // Keep the box focused while the rider taps, so the list stays open.
    item.addEventListener("mousedown", (event) => event.preventDefault());
    return item;
  }

  return { find, item, orNothing };
})();
