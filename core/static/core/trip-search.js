// The trip search (story #5): two boxes, where the rider is starting and where
// they're going. They type an address, a building or a stop, and pick from the
// places suggested as they type: our stops and Yale buildings (from
// /places.json), then buildings and addresses from Photon. Picking fills in the place's coordinates; once
// both boxes have a place, the search runs. A place typed but not picked gets
// its top suggestion when the rider presses Find my bus.
//
// Photon (https://photon.komoot.io), a free search over OpenStreetMap data,
// refuses requests from our Render servers, so the browser asks it directly,
// the way the map loads its tiles. Its results are kept to New Haven and its
// neighbours, nearer campus first, as in transit/places.py.
//
// "Use my location" fills the first box, and the home page's map fills either
// one with a "trip:pick" event: {end: "from" or "to", place: {name, lat, lon}}.
(() => {
  const form = document.querySelector("[data-trip-search]");
  if (!form) return;
  const placesUrl = form.dataset.placesUrl;
  const canLocate = "geolocation" in navigator;
  const MY_LOCATION = { name: "Use my location", detail: "Start where you are now", mine: true };
  const WAIT_MS = 200; // typing pause before asking for suggestions
  const PHOTON_URL = "https://photon.komoot.io/api/";
  const PHOTON_AREA = { lat: "41.3111", lon: "-72.9267", zoom: "14", bbox: "-73.05,41.24,-72.83,41.40" };
  const MOST_PLACES = 5;
  const boxes = {};

  for (const end of ["from", "to"]) {
    const input = form.elements[end];
    const box = {
      end,
      input,
      at: form.elements[`${end}_at`],
      list: document.getElementById(input.getAttribute("aria-controls")),
      places: [],
      active: -1,
      timer: null,
      request: null,
    };
    box.at.disabled = false; // without this script, the server looks up the typed name instead
    boxes[end] = box;
    input.addEventListener("input", () => typed(box));
    input.addEventListener("keydown", (event) => keyed(box, event));
    input.addEventListener("focus", () => {
      if (!input.value.trim()) offerMyLocation(box);
    });
    input.addEventListener("blur", () => close(box));
  }

  // A tap anywhere else closes the suggestions, even where the box keeps focus.
  document.addEventListener("pointerdown", (event) => {
    for (const box of Object.values(boxes)) {
      if (!box.input.parentElement.contains(event.target)) close(box);
    }
  });

  const locateButton = form.querySelector("[data-use-my-location]");
  if (locateButton && canLocate) {
    locateButton.hidden = false;
    locateButton.addEventListener("click", () => useMyLocation(boxes.from));
  }

  // A place typed but not picked: take its top suggestion, as Enter would, so
  // the server doesn't have to look it up (it can't reach Photon).
  form.addEventListener("submit", async (event) => {
    const unpicked = Object.values(boxes).filter((box) => box.input.value.trim().length >= 2 && !box.at.value);
    if (!unpicked.length) return;
    event.preventDefault();
    if (form.dataset.finding) return;
    form.dataset.finding = "yes";
    for (const box of unpicked) {
      close(box);
      const [top] = await orNothing(findPlaces(box.input.value.trim()));
      if (top) {
        box.input.value = top.name;
        box.at.value = `${top.lat},${top.lon}`;
      }
    }
    form.submit(); // anything still unfound, the server says so
  });
  // Back on this page from the trip, the form can search again.
  window.addEventListener("pageshow", () => delete form.dataset.finding);

  document.addEventListener("trip:pick", (event) => {
    const { end, place } = event.detail;
    if (boxes[end]) choose(boxes[end], place, { fromMap: true });
  });

  function typed(box) {
    box.at.value = ""; // the text changed, so the old place no longer applies
    box.input.classList.remove("is-invalid");
    clearTimeout(box.timer);
    const query = box.input.value.trim();
    if (query.length < 2) {
      offerMyLocation(box);
      return;
    }
    box.timer = setTimeout(() => suggest(box, query), WAIT_MS);
  }

  async function suggest(box, query) {
    if (box.request) box.request.abort();
    box.request = new AbortController();
    try {
      const places = await findPlaces(query, box.request.signal);
      if (box.input.value.trim() !== query || document.activeElement !== box.input) return;
      show(box, places, places.length ? "" : "No places found near New Haven. Try a building, a street or a stop.");
    } catch (error) {
      if (error.name !== "AbortError") show(box, [], "Suggestions aren't available right now. Type the place and press Find my bus.");
    }
  }

  // Our stops and buildings matching the query, then Photon's buildings and
  // addresses, without the ones we already named. Either can fail without
  // taking the other with it.
  async function findPlaces(query, signal) {
    const [ours, addresses] = await Promise.all([
      orNothing(ourStops(query, signal)),
      orNothing(photon(query, signal)),
    ]);
    const named = new Set(ours.map((place) => place.name.toLowerCase()));
    return [...ours, ...addresses.filter((place) => !named.has(place.name.toLowerCase()))];
  }

  async function orNothing(promise) {
    try {
      return await promise;
    } catch (error) {
      if (error.name === "AbortError") throw error;
      return [];
    }
  }

  async function ourStops(query, signal) {
    const url = `${placesUrl}?${new URLSearchParams({ q: query })}`;
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

  function offerMyLocation(box) {
    show(box, box.end === "from" && canLocate ? [MY_LOCATION] : []);
  }

  // The suggestions under a box, built from DOM nodes: the names come from outside.
  function show(box, places, message = "") {
    box.places = places;
    box.active = -1;
    const items = places.map((place, index) => {
      const item = document.createElement("li");
      item.className = "list-group-item list-group-item-action";
      item.id = `${box.list.id}-${index}`;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", "false");
      const name = document.createElement("div");
      name.className = place.mine ? "fw-semibold text-primary" : "fw-semibold";
      if (place.mine) {
        const icon = document.createElement("i");
        icon.className = "bi bi-crosshair me-2";
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
      item.addEventListener("click", () => choose(box, place));
      return item;
    });
    if (message) {
      const note = document.createElement("li");
      note.className = "list-group-item small text-body-secondary";
      note.setAttribute("role", "presentation");
      note.textContent = message;
      items.push(note);
    }
    box.list.replaceChildren(...items);
    box.list.hidden = !items.length;
    box.input.setAttribute("aria-expanded", String(places.length > 0));
    box.input.removeAttribute("aria-activedescendant");
  }

  function close(box) {
    clearTimeout(box.timer);
    box.list.hidden = true;
    box.input.setAttribute("aria-expanded", "false");
    box.input.removeAttribute("aria-activedescendant");
  }

  function keyed(box, event) {
    const open = !box.list.hidden && box.places.length;
    if (open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      box.active = (box.active + step + box.places.length) % box.places.length;
      box.places.forEach((_, index) => {
        box.list.children[index].setAttribute("aria-selected", String(index === box.active));
        box.list.children[index].classList.toggle("active", index === box.active);
      });
      box.input.setAttribute("aria-activedescendant", `${box.list.id}-${box.active}`);
    } else if (open && event.key === "Enter") {
      // Enter takes the highlighted suggestion, or the first one.
      event.preventDefault();
      choose(box, box.places[Math.max(box.active, 0)]);
    } else if (event.key === "Escape") {
      close(box);
    }
  }

  function choose(box, place, { fromMap = false } = {}) {
    if (place.mine) {
      useMyLocation(box);
      return;
    }
    box.input.value = place.name;
    box.at.value = `${place.lat},${place.lon}`;
    box.input.classList.remove("is-invalid");
    close(box);
    const other = boxes[box.end === "from" ? "to" : "from"];
    if (other.at.value) {
      form.requestSubmit ? form.requestSubmit() : form.submit();
    } else if (!fromMap) {
      other.input.focus();
      if (other.input.value.trim()) typed(other);
    }
  }

  function useMyLocation(box) {
    close(box);
    box.input.value = "";
    box.input.placeholder = "Finding you…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        box.input.placeholder = "Where are you starting?";
        choose(box, { name: "My location", lat: position.coords.latitude, lon: position.coords.longitude });
      },
      (error) => {
        box.input.placeholder = "Where are you starting?";
        box.input.focus();
        show(
          box,
          [],
          error.code === error.PERMISSION_DENIED
            ? "Location is blocked for this site. Allow it in your browser's settings, or type where you are."
            : "Couldn't find your location. Type where you are instead.",
        );
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  }
})();
