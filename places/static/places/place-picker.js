// Where a saved place is (#7): the rider types an address, a Yale building or
// a stop and picks from the suggestions, as in the trip search
// (place-search.js), or uses where they are now. A pick fills in the hidden
// location_at ("41.3112,-72.9290"). A place typed but not picked gets its top
// suggestion when the form is sent, since our server can't reach Photon.
(() => {
  const form = document.querySelector("[data-place-picker]");
  if (!form) return;
  const { find, item, orNothing } = window.placeSearch;
  const placesUrl = form.dataset.placesUrl;
  const input = form.elements.location;
  const at = form.elements.location_at;
  const list = document.getElementById(input.getAttribute("aria-controls"));
  const WAIT_MS = 200;
  let places = [];
  let active = -1;
  let timer = null;
  let request = null;

  input.addEventListener("input", () => {
    at.value = ""; // the text changed, so the old point no longer applies
    clearTimeout(timer);
    const query = input.value.trim();
    if (query.length < 2) return close();
    timer = setTimeout(() => suggest(query), WAIT_MS);
  });
  input.addEventListener("keydown", keyed);
  input.addEventListener("blur", close);
  document.addEventListener("pointerdown", (event) => {
    if (!input.parentElement.contains(event.target)) close();
  });

  const locateButton = form.querySelector("[data-use-my-location]");
  if (locateButton && "geolocation" in navigator) {
    locateButton.hidden = false;
    locateButton.addEventListener("click", useMyLocation);
  }

  form.addEventListener("submit", async (event) => {
    if (at.value || input.value.trim().length < 2) return;
    event.preventDefault();
    if (form.dataset.finding) return;
    form.dataset.finding = "yes";
    close();
    const [top] = await orNothing(find(input.value.trim(), { placesUrl }));
    if (top) pick(top);
    form.submit(); // still unfound: the server looks it up, or says so
  });
  window.addEventListener("pageshow", () => delete form.dataset.finding);

  async function suggest(query) {
    if (request) request.abort();
    request = new AbortController();
    try {
      const found = await find(query, { placesUrl, signal: request.signal });
      if (input.value.trim() !== query || document.activeElement !== input) return;
      show(found, found.length ? "" : "No places found near New Haven. Try a building, a street or a stop.");
    } catch (error) {
      if (error.name !== "AbortError") show([], "Suggestions aren't available right now. Type the place and save it.");
    }
  }

  function show(found, message = "") {
    places = found;
    active = -1;
    const items = found.map((place, index) => {
      const option = item(place, `${list.id}-${index}`);
      option.addEventListener("click", () => pick(place));
      return option;
    });
    if (message) {
      const note = document.createElement("li");
      note.className = "list-group-item small text-body-secondary";
      note.setAttribute("role", "presentation");
      note.textContent = message;
      items.push(note);
    }
    list.replaceChildren(...items);
    list.hidden = !items.length;
    input.setAttribute("aria-expanded", String(found.length > 0));
    input.removeAttribute("aria-activedescendant");
  }

  function close() {
    clearTimeout(timer);
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function keyed(event) {
    const open = !list.hidden && places.length;
    if (open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      active = (active + (event.key === "ArrowDown" ? 1 : -1) + places.length) % places.length;
      places.forEach((_, index) => {
        list.children[index].setAttribute("aria-selected", String(index === active));
        list.children[index].classList.toggle("active", index === active);
      });
      input.setAttribute("aria-activedescendant", `${list.id}-${active}`);
    } else if (open && event.key === "Enter") {
      event.preventDefault(); // Enter picks the highlighted place, or the first
      pick(places[Math.max(active, 0)]);
    } else if (event.key === "Escape") {
      close();
    }
  }

  // Written the way places/forms.py's label() writes a place looked up there.
  function pick(place) {
    input.value = place.detail === "Shuttle stop" ? `${place.name} (shuttle stop)` : [place.name, place.detail].filter(Boolean).join(", ");
    at.value = `${place.lat},${place.lon}`;
    input.classList.remove("is-invalid");
    close();
  }

  function useMyLocation() {
    close();
    const placeholder = input.placeholder;
    input.value = "";
    input.placeholder = "Finding you…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        input.placeholder = placeholder;
        pick({ name: "My location", detail: "", lat: position.coords.latitude, lon: position.coords.longitude });
      },
      (error) => {
        input.placeholder = placeholder;
        input.focus();
        show(
          [],
          error.code === error.PERMISSION_DENIED
            ? "Location is blocked for this site. Allow it in your browser's settings, or type the place."
            : "Couldn't find your location. Type the place instead.",
        );
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  }
})();
