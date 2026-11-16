"""The live Downtowner feeds at yale.downtownerapp.com, read by our server (#36).

One function per feed: routes(), stops(), buses(), etas(stop_id) and
announcements(). Pages call these instead of the feed itself:

- Each answer is cached for 30 seconds, so a busy page doesn't hit the feed on
  every request.
- Requests give up after 5 seconds. When the feed is down, slow, or sends
  something unreadable, the function raises FeedUnavailable, so a page can say
  so instead of crashing. That outcome is cached for 10 seconds, so a feed
  outage doesn't make every page wait out the timeout.

Routes are identified by their numeric id: short names aren't unique ("BW" is
both Blue - Weekend Daytime and Blue West).
"""

import json
import logging
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

TIMEOUT_SECONDS = 5
CACHE_SECONDS = 30
OUTAGE_CACHE_SECONDS = 10
USER_AGENT = "Mozilla/5.0 (compatible; YaleShuttle/1.0)"

logger = logging.getLogger(__name__)


class FeedUnavailable(Exception):
    """The Downtowner feed didn't answer in time, or sent something unreadable."""


def routes():
    """Every route, running or not: id, name, short_name, color, active, stops, path."""
    return _get("routes_routes.php", {"inactive": "true"})


def stops():
    """Every stop: id, name, lat, lon, description."""
    return _get("routes_stops.php")


def buses():
    """Every bus on the road now: id, name, route, lat, lon, heading, lastStop, lastUpdate."""
    return _get("routes_buses.php")


def announcements():
    """The service announcements in effect now ([] when there are none)."""
    return _get("routes_announcements.php") or []


def etas(stop_id):
    """The official predicted arrivals at one stop, soonest first.

    A list of {"route_id", "bus_id", "bus_name", "minutes"}, flattened from the
    feed's nested answer. Entries without a route or a time are left out.
    """
    data = _get("routes_eta.php", {"stop": stop_id})
    if not isinstance(data, dict):
        return []
    arrivals = []
    for entry in _values(data.get("etas")):
        for eta in _values(entry.get("etas") if isinstance(entry, dict) else None):
            try:
                arrival = {
                    "route_id": int(eta["route"]),
                    "bus_id": int(eta["bus_id"]) if eta.get("bus_id") is not None else None,
                    "bus_name": eta.get("bus_name") or "",
                    "minutes": int(eta["avg"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
            arrivals.append(arrival)
    return sorted(arrivals, key=lambda arrival: arrival["minutes"])


def _values(collection):
    """The items of a dict's values or a list; nothing for anything else."""
    if isinstance(collection, dict):
        return list(collection.values())
    if isinstance(collection, list):
        return collection
    return []


def _get(path, params=None):
    """One feed's parsed JSON, from the cache when it's fresh."""
    query = f"?{urlencode(params)}" if params else ""
    key = f"downtowner:{path}{query}"
    cached = cache.get(key)
    if cached is not None:
        state, value = cached
        if state == "down":
            raise FeedUnavailable(value)
        return value

    url = f"{settings.DOWNTOWNER_URL.rstrip('/')}/{path}{query}"
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except (OSError, ValueError, HTTPException) as error:
        # OSError covers timeouts, refused connections and HTTP errors;
        # ValueError covers an answer that isn't JSON.
        message = f"The Downtowner feed is unavailable ({error.__class__.__name__}: {error})"
        logger.warning("%s: %s", url, message)
        cache.set(key, ("down", message), OUTAGE_CACHE_SECONDS)
        raise FeedUnavailable(message) from error

    cache.set(key, ("ok", data), CACHE_SECONDS)
    return data
