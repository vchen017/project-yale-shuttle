"""The places riders type in the trip search (story #5): a stop's name, a
building, or an address.

As the rider types, the suggestions are our stops, from stops_named() through
/places.json, then buildings and addresses that the rider's browser gets from
Photon, a free search over OpenStreetMap data (https://photon.komoot.io).
Photon refuses requests from our Render servers, so trip-search.js asks it
directly, the way the map loads its tiles.

search() is for a trip that arrives with a typed name and no coordinates,
which happens without JavaScript: our stops, then Photon from the server,
which works locally but not on Render.

    search("sterling", stops) -> [{"name": "Sterling Library",
                                   "detail": "York Street, New Haven",
                                   "lat": 41.3112, "lon": -72.9290}, ...]

Photon's answers are cached for a day, kept to New Haven and the towns around
it, and nearer campus first. When Photon fails, our stops still come back.
"""

import hashlib
import json
import logging
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.cache import cache

PHOTON_URL = "https://photon.komoot.io/api/"
CAMPUS = (41.3111, -72.9267)  # latitude, longitude: nearer places come first
CAMPUS_ZOOM = 14  # how tightly results favour campus: 14 puts Sterling Library before Hamden's Sterling Place
AREA = "-73.05,41.24,-72.83,41.40"  # west, south, east, north: New Haven and its neighbours
USER_AGENT = "YaleShuttle/1.0 (a Yale class project)"
TIMEOUT_SECONDS = 3
CACHE_SECONDS = 24 * 60 * 60
SHORTEST_QUERY = 2  # characters
MOST_STOPS = 3
MOST_PLACES = 5

logger = logging.getLogger(__name__)


def search(query, stops):
    """The stops and places matching what the rider typed, best first, each
    {"name", "detail", "lat", "lon"}. `stops` are the stops the routes serve
    (next_buses.served_stops)."""
    query = " ".join(query.split())
    if len(query) < SHORTEST_QUERY:
        return []
    return stops_named(query, stops) + _photon(query)


def stops_named(query, stops):
    """Our stops whose names have every word the rider typed, in any order."""
    words = query.casefold().split()
    if len(" ".join(words)) < SHORTEST_QUERY:
        return []
    named = [stop for stop in stops if all(word in stop["name"].casefold() for word in words)]
    return [
        {"name": stop["name"], "detail": "Shuttle stop", "lat": stop["lat"], "lon": stop["lon"]}
        for stop in named[:MOST_STOPS]
    ]


def _photon(query):
    key = "photon:" + hashlib.sha256(query.casefold().encode()).hexdigest()
    places = cache.get(key)
    if places is not None:
        return places
    params = {"q": query, "limit": 10, "lang": "en", "lat": CAMPUS[0], "lon": CAMPUS[1], "zoom": CAMPUS_ZOOM, "bbox": AREA}
    try:
        request = Request(f"{PHOTON_URL}?{urlencode(params)}", headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            features = json.load(response)["features"]
        places = _distinct([_place(feature) for feature in features])[:MOST_PLACES]
    except (OSError, ValueError, HTTPException, LookupError, TypeError) as error:
        # Timeouts, HTTP errors and answers we can't read: the stops still work.
        logger.warning("The place search is unavailable (%s: %s)", error.__class__.__name__, error)
        return []
    cache.set(key, places, CACHE_SECONDS)
    return places


def _place(feature):
    """One of Photon's results as a place: "Sterling Library", "York Street, New Haven"."""
    properties = feature["properties"]
    longitude, latitude = (float(value) for value in feature["geometry"]["coordinates"][:2])
    street = " ".join(filter(None, [properties.get("housenumber"), properties.get("street")]))
    name = properties.get("name") or street or properties.get("city") or ""
    detail = ", ".join(filter(None, [street if properties.get("name") else "", properties.get("city")]))
    return {"name": name, "detail": "" if detail == name else detail, "lat": latitude, "lon": longitude}


def _distinct(places):
    """Without nameless places or repeats: OpenStreetMap often has a station,
    its building and its platforms under one name, and a street in pieces."""
    seen = set()
    distinct = []
    for place in places:
        name = place["name"].casefold()
        keys = {(name, place["detail"].casefold()), (name, round(place["lat"], 2), round(place["lon"], 2))}
        if place["name"] and not keys & seen:
            seen |= keys
            distinct.append(place)
    return distinct
