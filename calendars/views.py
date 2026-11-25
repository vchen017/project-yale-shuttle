import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
    require_safe,
)

from notifications import ask, trips
from places.models import Place
from transit.answer import route_segment
from transit.downtowner import FeedUnavailable
from transit.network import network

from . import demo, next_trip, plans, services
from .forms import CalendarForm


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def connect(request):
    """Connect a calendar by its ICS address, and see what it imported (#25).

    Saving the form connects the address and reads it right away; the hourly
    job keeps it fresh after that. Replacing the address re-reads from the new
    one. Until the server can read it, an error explains why and the rider's
    events stay as they were.
    """
    calendar = services.connection(request.user)
    form = CalendarForm(
        request.POST or None,
        initial={"ics_url": calendar.ics_url} if calendar else None,
    )
    if request.method == "POST" and form.is_valid():
        saved = services.save_url(request.user, form.cleaned_data["ics_url"])
        imported = services.try_refresh(saved)
        if imported:
            # The hourly trip watch loaded its trips before these existed: a trip
            # in the next 2 hours gets its first check now, beside this request (#29).
            trips.first_check_soon(request.user)
            messages.success(request, f"Connected. Imported {imported} upcoming events.")
        elif saved.last_error:
            messages.warning(request, f"Connected, but we couldn't read events yet: {saved.last_error}")
        return redirect("calendar")
    return render(
        request,
        "calendars/connect.html",
        {"calendar": calendar, "form": form, "events": services.upcoming(request.user)[:20]},
        status=400 if form.errors else 200,
    )


@login_required
@require_POST
def disconnect(request):
    """Remove the rider's calendar and the events imported from it (#25)."""
    services.remove(request.user)
    messages.success(request, "Disconnected your calendar and deleted its events.")
    return redirect("calendar")


@login_required
@require_safe
def leave(request):
    """When to leave for each event in the next 24 hours (#8), as a calendar:
    each day's events by their start time. A trip shows when to leave, its
    steps from the rider's Home (leave, board, get off, arrive) and a map of
    the walks and the ride. Events whose location we can't match to a
    building, or can't ride to yet, say why. The feed being down never breaks
    the page: the events and their buildings still show, without ride times.
    A rideable event can be put to the trip agent from here (#148)."""
    now = timezone.now()
    # The demo events move on here too, not only in the hourly calendar job:
    # the job clock runs that job on production only, so on staging they'd
    # sit in the past between deploys, with nothing to ask the agent about.
    demo.reanchor(now)
    events = services.upcoming(request.user, now=now)
    home = Place.objects.filter(user=request.user, is_home=True).first()
    try:
        net = network()  # one read of the feed for stops and routes together
        stops, routes_by_id = net["stops"], {route["id"]: route for route in net["routes"]}
    except FeedUnavailable:
        stops, routes_by_id = None, None

    every_plan = [
        plans.plan_event(event, home, now=now, stops=stops, routes_by_id=routes_by_id, running_at=plans.routes_running_at)
        for event in events  # soonest first
    ]
    rideable = [plan for plan in every_plan if plan.leave_by is not None]
    answers = ask.answers_for(request.user, [plan.event for plan in rideable])
    for plan in rideable:
        plan.has_a_bus = ask.has_a_bus(plan)
        plan.ask_from = ask.ask_from(plan.event, now)
        plan.agent_answer = answers.get(ask.key(plan.event))
        _timeline(plan, now)
        plan.map, plan.map_id = _trip_map(plan, home, routes_by_id), f"trip-map-{plan.event.pk}"
    # The next trip, live, on top (#157). Not while the feed is down: "no
    # shuttle links these" can't be told apart from "the feed didn't say".
    # Once it's past "Leave by", the trip the card remembered, with its bus (#163).
    card = None
    record = next_trip.followed(request.session, now) if stops is not None else None
    if record is not None:
        try:
            card = next_trip.follow_card(request.session, record, now)
            card.map = _follow_map(record, routes_by_id)
        except FeedUnavailable:
            card = None
    elif stops is not None:
        upcoming = next_trip.next_plan(every_plan, now)
        if upcoming:
            card = next_trip.read(upcoming, now)
            next_trip.remember(request.session, card, home, now)
    # An answer that would notify offers to turn notifications on, to a rider who hasn't.
    alerts_on = any(answer.notify for answer in answers.values()) and request.user.push_subscriptions.exists()
    return render(
        request,
        "calendars/leave.html",
        {
            "days": _days(every_plan, now),
            "has_home": home is not None,
            "feed_down": stops is None,
            "now": now,
            "alerts_on": alerts_on,
            "next_trip": card,
            "refresh_seconds": next_trip.REFRESH_SECONDS,
        },
    )


@login_required
@require_safe
@never_cache
def next_trip_card(request):
    """The next trip card's live part (#157), which the when-to-leave page
    swaps in every next_trip.REFRESH_SECONDS while it's watching for a bus:
    {"key", "html", "bus", "watching"}, the bus being where it is on the map
    now, and "watching" whether to keep asking. The key says which event the card
    is about: the page reloads when it changes, so a new trip gets its map.
    {"key": null} when there's no next trip; {"feed_down": true} while the
    feed is down, which the page waits out with the card it has. Once it's
    past "Leave by", the card follows the remembered trip's bus (#163),
    under the same key."""
    now = timezone.now()
    record = next_trip.followed(request.session, now)
    if record is not None:
        try:
            card = next_trip.follow_card(request.session, record, now)
        except FeedUnavailable:
            return JsonResponse({"feed_down": True})
        html = render_to_string("calendars/_next_trip_live.html", {"trip": card}, request=request)
        return JsonResponse({"key": card.key, "html": html, "bus": card.bus, "watching": card.watching})
    home = Place.objects.filter(user=request.user, is_home=True).first()
    try:
        net = network()
    except FeedUnavailable:
        return JsonResponse({"feed_down": True})
    routes_by_id = {route["id"]: route for route in net["routes"]}
    upcoming = None
    for event in services.upcoming(request.user, now=now):  # soonest first: plan only up to the next trip
        plan = plans.plan_event(
            event, home, now=now, stops=net["stops"], routes_by_id=routes_by_id, running_at=plans.routes_running_at
        )
        if next_trip.next_plan([plan], now) is plan:
            upcoming = plan
            break
    if upcoming is None:
        return JsonResponse({"key": None})
    if upcoming.leave_by is not None:
        _timeline(upcoming, now)
    trip = next_trip.read(upcoming, now)
    next_trip.remember(request.session, trip, home, now)
    html = render_to_string("calendars/_next_trip_live.html", {"trip": trip}, request=request)
    return JsonResponse({"key": trip.key, "html": html, "bus": trip.bus, "watching": trip.watching})


@login_required
@require_POST
def stop_following(request):
    """Stop following the trip (#163), say when the rider isn't going after
    all: the card goes back to their next trip."""
    next_trip.forget(request.session)
    return redirect("when-to-leave")


def _timeline(plan, now):
    """The minutes until the plan says to leave, and its bus's time: the
    walk, then the minutes to spare at the stop (#155)."""
    plan.leave_in = round((plan.leave_by - now).total_seconds() / 60)
    ride = plan.plan is not None and not plan.walking_is_quicker
    plan.board_time = plan.leave_by + dt.timedelta(minutes=plan.plan.walk_to_stop + plan.spare_minutes) if ride else None


def _follow_map(record, routes_by_id):
    """A followed trip's map (#163), as _trip_map draws a plan's, from the
    trip the card remembered, with the route's stretch from the live network."""
    board, get_off = record["board"], record["get_off"]
    data = {
        "origin": record["home"],
        "destination": [record["building"]["lat"], record["building"]["lon"]],
        "board": {"name": board["name"], "at": [board["lat"], board["lon"]]},
        "get_off": {"name": get_off["name"], "at": [get_off["lat"], get_off["lon"]]},
    }
    route = (routes_by_id or {}).get(record["route"]["id"])
    if route:
        path = route_segment(route["path"], board, get_off)
        data["route"] = {"name": route["name"], "color": route["color"], "path": [list(point) for point in path]}
    return data


def _days(every_plan, now):
    """The plans by the day their event starts, in Eastern time, as the page's
    calendar lists them: [{"date", "label", "plans"}], each in start order."""
    today = timezone.localdate(now)
    labels = {today: "Today", today + dt.timedelta(days=1): "Tomorrow"}
    days = {}
    for plan in every_plan:
        days.setdefault(timezone.localtime(plan.event.start).date(), []).append(plan)
    return [{"date": date, "label": labels.get(date, ""), "plans": day} for date, day in days.items()]


def _trip_map(plan, home, routes_by_id):
    """What a trip's map draws, for core/trip-map.js as on the trip page
    (#131): Home and the building, and for a ride the two stops and the
    stretch of the route between them. Coordinates are [lat, lon]."""
    data = {"origin": [home.lat, home.lon], "destination": [plan.building["lat"], plan.building["lon"]]}
    if plan.walking_is_quicker:
        return data
    board, get_off = plan.plan.board_at, plan.plan.get_off_at
    if plan.row:
        route = routes_by_id.get(plan.row.route_id)
    else:  # an estimate: the first route that links the two stops, as the page names them
        linking = sorted(set(board["routes"]) & set(get_off["routes"]))
        route = next((routes_by_id[route_id] for route_id in linking if route_id in routes_by_id), None)
    data["board"] = {"name": board["name"], "at": [board["lat"], board["lon"]]}
    data["get_off"] = {"name": get_off["name"], "at": [get_off["lat"], get_off["lon"]]}
    if route:
        path = route_segment(route["path"], board, get_off)
        data["route"] = {"name": route["name"], "color": route["color"], "path": [list(point) for point in path]}
    return data
