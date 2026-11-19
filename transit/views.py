from urllib.parse import urlencode

from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from places import services as saved_places

from . import answer as trip_answer
from . import downtowner, places, trips
from .downtowner import FeedUnavailable
from .forms import TripForm
from .network import network
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
        walk_all_the_way = trips.walk_minutes(trips.meters_between(origin, destination))
        now = timezone.now()
        answer = trip_answer.answer(plan, board, walk_all_the_way, now)
        map_data = _trip_map(origin, destination, plan, answer)
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
            "walk_all_the_way": walk_all_the_way,
            "answer": answer,
            "map_data": map_data,
            "updated_at": now,
            "search_from": ends["from"],
            "search_to": ends["to"],
        },
    )


def _trip_map(origin, destination, plan, answer):
    """What the trip page's map draws (#131), as plain lists the page hands to
    its script: the two places, the stops on and off, and the stretch of the
    route between them when there is a bus to ride. Coordinates are [lat, lon]."""
    data = {"origin": list(origin), "destination": list(destination), "walking": answer.kind == trip_answer.WALK}
    if plan is None or answer.kind != trip_answer.RIDE:
        return data
    route = next((r for r in network()["routes"] if r["id"] == answer.route_id), None)
    segment = trip_answer.route_segment(route["path"], plan.board_at, plan.get_off_at) if route else []
    data.update(
        {
            "board": {"name": plan.board_at["name"], "at": [plan.board_at["lat"], plan.board_at["lon"]]},
            "get_off": {"name": plan.get_off_at["name"], "at": [plan.get_off_at["lat"], plan.get_off_at["lon"]]},
            "route": {"name": answer.route_name, "color": answer.route_color, "path": [list(p) for p in segment]},
        }
    )
    return data


@require_safe
def places_json(request):
    """The places matching what the rider types, ?q=, for the trip search's
    suggestions (story #5): with ?mine=1, a logged-in rider's saved places
    first (#7), all of them when nothing is typed, then our stops and Yale
    buildings (issue #98). The browser adds other buildings and addresses from
    Photon itself (see transit/places.py)."""
    query = request.GET.get("q", "")
    mine = saved_places.matching(request.user, query) if request.GET.get("mine") else []
    try:
        choices = served_stops()
    except FeedUnavailable:
        choices = []  # saved places, our buildings, and the browser's addresses still work
    response = JsonResponse({"places": mine + places.our_places(query, choices)})
    # Saved places are the rider's own: never kept where someone else could get them.
    response["Cache-Control"] = "private, no-cache" if request.GET.get("mine") else "max-age=300"
    return response


def _place(request, end, choices):
    """One end of the trip, "from" or "to": the place ({"name", "lat", "lon"}),
    or what was typed when we can't find it, and an error to show, if any."""
    typed = " ".join(request.GET.get(end, "").split())
    at = places.point(request.GET.get(f"{end}_at", ""))
    if at:
        return {"name": typed or "The place you picked", "lat": at[0], "lon": at[1]}, None
    if not typed:
        return None, "Type where you're starting." if end == "from" else "Type where you're going."
    saved = saved_places.named(request.user, typed)  # "Home", typed without JavaScript (#7)
    if saved:
        return {"name": saved.name, "lat": saved.lat, "lon": saved.lon}, None
    found = places.search(typed, choices)
    if not found:
        return {"name": typed}, f"We couldn't find “{typed}” near New Haven. Try a building, a street address or a stop."
    return found[0], None


def _feed_down(request):
    """The feed is down: say so instead of failing (story #5)."""
    return render(request, "transit/feed_down.html", status=503)
