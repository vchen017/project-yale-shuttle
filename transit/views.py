from urllib.parse import urlencode

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from . import downtowner, places, trips
from .downtowner import FeedUnavailable
from .forms import TripForm
from .next_buses import next_buses, served_stops


@require_safe
def stops(request):
    """The trip search: where the rider is starting and where they're going,
    as an address, a building or a stop (#24, story #5). Links that still
    name stops by id (?start=10&to=40) go on to that stop's next-buses page."""
    try:
        choices = served_stops()
    except FeedUnavailable:
        return _feed_down(request)
    form = TripForm(request.GET or None, stops=choices)
    if form.is_bound and form.is_valid():
        url = reverse("stop", args=[form.cleaned_data["start"]])
        if form.cleaned_data["to"] is not None:
            url += "?" + urlencode({"to": form.cleaned_data["to"]})
        return redirect(url)
    return render(request, "transit/stops.html")


@require_safe
def stop(request, stop_id):
    """The next buses at one stop, to the destination in ?to= if there is one,
    with our prediction next to the official time (#24, story #5)."""
    try:
        choices = served_stops()
        by_id = {stop["id"]: stop for stop in choices}
        if stop_id not in by_id:
            # A real stop that no running route serves still gets a page.
            known = {stop["id"]: stop for stop in downtowner.stops()}
            if stop_id not in known:
                raise Http404("No such stop")
            by_id[stop_id] = known[stop_id]
            choices = sorted([*choices, known[stop_id]], key=lambda s: s["name"].casefold())

        form = TripForm({"start": stop_id, "to": request.GET.get("to", "")}, stops=choices)
        destination = None
        if form.is_valid() and form.cleaned_data["to"] is not None:
            destination = by_id[form.cleaned_data["to"]]
        board = next_buses(stop_id, destination["id"] if destination else None)
    except FeedUnavailable:
        return _feed_down(request)

    return render(
        request,
        "transit/stop.html",
        {
            "stop": by_id[stop_id],
            "destination": destination,
            "board": board,
            "updated_at": timezone.now(),
            # The search to change the trip starts out filled in with this one.
            "search_from": by_id[stop_id],
            "search_to": destination,
            "search_errors": {"to": form.errors["to"][0]} if "to" in form.errors else {},
        },
    )


@require_safe
def trip(request):
    """From one place to another, typed or picked (story #5): the stop to walk
    to, the next buses from there to the stop nearest where the rider is
    going, and the walk at each end.

    Each end is ?from= and ?to=, the place's name, with its coordinates in
    ?from_at= and ?to_at= ("41.3112,-72.9290") when the rider picked it from
    the suggestions or the map. Without coordinates, we search for the name.
    """
    try:
        choices = served_stops()
        ends, errors = {}, {}
        for end in ("from", "to"):
            ends[end], error = _place(request, end, choices)
            if error:
                errors[end] = error
        if errors:
            context = {"search_from": ends["from"], "search_to": ends["to"], "search_errors": errors}
            return render(request, "transit/stops.html", context)
        origin = (ends["from"]["lat"], ends["from"]["lon"])
        destination = (ends["to"]["lat"], ends["to"]["lon"])
        plan = trips.plan(origin, destination, choices)
        board = next_buses(plan.board_at["id"], plan.get_off_at["id"]) if plan else None
    except FeedUnavailable:
        return _feed_down(request)

    return render(
        request,
        "transit/trip.html",
        {
            "origin": ends["from"],
            "destination": ends["to"],
            "plan": plan,
            "board": board,
            "walk_all_the_way": trips.walk_minutes(trips.meters_between(origin, destination)),
            "updated_at": timezone.now(),
            "search_from": ends["from"],
            "search_to": ends["to"],
        },
    )


@require_safe
def places_json(request):
    """Suggestions for the trip search as the rider types ?q= (story #5): our
    stops by name, then buildings and addresses."""
    try:
        choices = served_stops()
    except FeedUnavailable:
        choices = []  # buildings and addresses still work without the feed
    response = JsonResponse({"places": places.search(request.GET.get("q", ""), choices)})
    response["Cache-Control"] = "max-age=300"
    return response


def _place(request, end, choices):
    """One end of the trip, "from" or "to": the place ({"name", "lat", "lon"}),
    or what was typed when we can't find it, and an error to show, if any."""
    typed = " ".join(request.GET.get(end, "").split())
    at = _coordinates(request.GET.get(f"{end}_at", ""))
    if at:
        return {"name": typed or "The place you picked", "lat": at[0], "lon": at[1]}, None
    if not typed:
        return None, "Type where you're starting." if end == "from" else "Type where you're going."
    found = places.search(typed, choices)
    if not found:
        return {"name": typed}, f"We couldn't find “{typed}” near New Haven. Try a building, a street address or a stop."
    return found[0], None


def _coordinates(value):
    """"41.3112,-72.9290" as (41.3112, -72.929), or None if it isn't a point on Earth."""
    try:
        lat, lon = (float(part) for part in value.split(","))
    except ValueError:
        return None
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _feed_down(request):
    """The feed is down: say so instead of failing (story #5)."""
    return render(request, "transit/feed_down.html", status=503)
