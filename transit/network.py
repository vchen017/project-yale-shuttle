"""The shuttle network for the home page's map: the routes running now and the
stops they serve, from the live feed (#71).

    network() -> {"running": True, "routes": [...], "stops": [...]}

Each stop links to its next-buses page (#24) once that page exists.
"""

import re
from collections import defaultdict

from django.urls import NoReverseMatch, reverse

from . import downtowner

DEFAULT_COLOR = "00356b"  # Yale blue, for a route the feed gives no usable colour
_HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")


def network():
    """The routes running now, and the stops they serve.

    Late at night, when no route runs, it returns every route and its stops,
    with `running` False, so the map still shows where the shuttles go.
    Raises downtowner.FeedUnavailable when the feed is down.
    """
    every_route = downtowner.routes()
    running = [route for route in every_route if route.get("active")]
    shown = running or every_route

    routes_at = defaultdict(list)  # stop id -> ids of the shown routes that serve it
    for route in shown:
        for stop_id in route.get("stops") or []:
            routes_at[stop_id].append(route["id"])

    stops = [
        {
            "id": stop["id"],
            "name": stop["name"],
            "lat": stop["lat"],
            "lon": stop["lon"],
            "routes": routes_at[stop["id"]],
            "url": stop_url(stop["id"]),
        }
        for stop in downtowner.stops()
        if stop["id"] in routes_at and stop.get("lat") is not None and stop.get("lon") is not None
    ]
    return {
        "running": bool(running),
        "routes": [
            {
                "id": route["id"],
                "name": route["name"],
                # "Blue - Weekday Daytime" -> "Blue": short enough for a chip
                "label": route["name"].split(" - ")[0],
                "color": _color(route.get("color")),
                "path": route.get("path") or [],
            }
            for route in shown
        ],
        "stops": stops,
    }


def stop_url(stop_id):
    """The stop's next-buses page (#24, the URL named "stop"), or None until it exists."""
    try:
        return reverse("stop", args=[stop_id])
    except NoReverseMatch:
        return None


def text_color(color):
    """Text that reads on a route colour (six hex digits): dark on a light
    colour, white on a dark one."""
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return "#212529" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.6 else "#fff"


def _color(value):
    """The route's colour as six hex digits; the map puts it in a style."""
    return value if isinstance(value, str) and _HEX_COLOR.match(value) else DEFAULT_COLOR
