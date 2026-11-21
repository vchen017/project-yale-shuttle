"""Sharing a live trip with a friend (#10): make the link from the trip page,
follow it without logging in, and stop it any time."""

import datetime as dt
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST, require_safe

from transit import downtowner
from transit.answer import route_segment
from transit.downtowner import FeedUnavailable
from transit.network import network

from .models import TripShare

REFRESH_SECONDS = 30  # the shared page reloads this often; the feed is cached as long
STALE_SECONDS = 180  # a bus position older than this gets its age shown


@login_required
@require_POST
def create(request):
    """Share the trip page's bus: its route, the bus the feed named, and the
    stops to get on and off. They're checked against the live network, so a
    link only ever names a real route and its stops. A rider shares one trip
    at a time: a new share ends the last one."""
    try:
        route_id = int(request.POST["route_id"])
        board_id = int(request.POST["board_stop_id"])
        get_off_id = int(request.POST["get_off_stop_id"])
        bus_text = request.POST.get("bus_id", "").strip()
        bus_id = int(bus_text) if bus_text else None
    except (KeyError, ValueError):
        return HttpResponseBadRequest("That isn't a trip we can share.")
    try:
        net = network()
    except FeedUnavailable:
        messages.warning(request, "The shuttle feed is down, so a trip can't be shared right now.")
        return redirect(_back(request))
    routes = {route["id"]: route for route in net["routes"]}
    stops = {stop["id"]: stop for stop in net["stops"]}
    route, board, get_off = routes.get(route_id), stops.get(board_id), stops.get(get_off_id)
    if not (route and board and get_off and route_id in board["routes"] and route_id in get_off["routes"]):
        return HttpResponseBadRequest("That isn't a trip we can share.")

    now = timezone.now()
    TripShare.objects.filter(user=request.user, stopped_at=None, expires_at__gt=now).update(stopped_at=now)
    share = TripShare.objects.create(
        user=request.user, route_id=route_id, route_name=route["name"], route_color=route["color"],
        bus_id=bus_id, board_stop_id=board_id, board_stop_name=board["name"],
        get_off_stop_id=get_off_id, get_off_stop_name=get_off["name"], created_at=now,
    )
    return redirect("share", token=share.token)


@require_safe
def shared_trip(request, token):
    """The shared trip, for anyone with the link: where the bus is, and when it
    reaches the stop where the rider gets off, reloaded every REFRESH_SECONDS.
    Nothing about the rider shows: no name, none of their places. Gone (410)
    once they stop sharing or it expires."""
    share = get_object_or_404(TripShare, token=token)
    is_owner = request.user.is_authenticated and request.user.pk == share.user_id
    now = timezone.now()
    if not share.is_live(now):
        return _private(render(request, "sharing/ended.html", {"share": share, "is_owner": is_owner}, status=410))
    context = {
        "share": share,
        "is_owner": is_owner,
        "live": _live(share, now),
        "share_url": request.build_absolute_uri(),
        "refresh_seconds": REFRESH_SECONDS,
        "now": now,
    }
    return _private(render(request, "sharing/shared_trip.html", context))


@login_required
@require_POST
def stop(request, token):
    """Stop sharing: the link shows nothing from now on. Only the rider who
    shared it can; anyone else gets a 404, as if there were no such link."""
    share = get_object_or_404(TripShare, token=token, user=request.user)
    if share.stopped_at is None:
        share.stopped_at = timezone.now()
        share.save(update_fields=["stopped_at"])
    messages.success(request, "Stopped sharing. The link no longer shows your trip.")
    return redirect("share", token=token)


def _live(share, now):
    """What the feed says now: the bus's position, and its minutes to the stop
    where the rider gets off. {"feed_down": True} when the feed is down."""
    try:
        buses = downtowner.buses()
        arrivals = downtowner.etas(share.get_off_stop_id)
        net = network()
    except FeedUnavailable:
        return {"feed_down": True}

    bus = _bus(buses, share.bus_id)
    times = [
        arrival["minutes"]
        for arrival in arrivals
        if arrival["route_id"] == share.route_id
        and (share.bus_id is None or arrival["bus_id"] == share.bus_id)
        and arrival["minutes"] >= 0
    ]
    minutes = min(times) if times else None

    route = next((route for route in net["routes"] if route["id"] == share.route_id), None)
    stops = {stop["id"]: stop for stop in net["stops"]}
    board, get_off = stops.get(share.board_stop_id), stops.get(share.get_off_stop_id)
    path = route_segment(route["path"], board, get_off) if route and board and get_off else []
    map_data = {
        "route": {"color": share.route_color, "path": [list(point) for point in path]},
        "get_off": {"name": share.get_off_stop_name, "at": [get_off["lat"], get_off["lon"]]} if get_off else None,
        "bus": {"at": [bus["lat"], bus["lon"]]} if bus else None,
    }
    return {
        "feed_down": False,
        "bus": bus,
        "minutes": minutes,
        "arrives_at": now + dt.timedelta(minutes=minutes) if minutes is not None else None,
        "map": map_data,
    }


def _bus(buses, bus_id):
    """The followed bus from the feed's buses answer: its position, its name,
    and how many minutes old its position is once that's stale. None when the
    feed doesn't name a bus to follow or doesn't show it."""
    if bus_id is None:
        return None
    for bus in buses if isinstance(buses, list) else []:
        try:
            if int(bus["id"]) != bus_id:
                continue
            age = max(0, time.time() - float(bus["lastUpdate"]))
            return {
                "lat": float(bus["lat"]),
                "lon": float(bus["lon"]),
                "name": str(bus.get("name") or ""),
                "age_minutes": round(age / 60) if age > STALE_SECONDS else None,
            }
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _private(response):
    """Keep a shared trip out of caches, search engines and other sites' logs."""
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _back(request):
    """Where the rider came from, when that's this site; else the stops map."""
    referer = request.META.get("HTTP_REFERER", "")
    if url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return referer
    return "stops"
