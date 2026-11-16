from urllib.parse import urlencode

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from . import downtowner
from .downtowner import FeedUnavailable
from .forms import TripForm
from .next_buses import next_buses, served_stops


@require_safe
def stops(request):
    """Pick a starting stop and, optionally, a destination (#24). Sends the
    rider on to that stop's next-buses page."""
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
    return render(request, "transit/stops.html", {"form": form})


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
        return _feed_down(request, stop_id=stop_id)

    return render(
        request,
        "transit/stop.html",
        {
            "form": form,
            "stop": by_id[stop_id],
            "destination": destination,
            "board": board,
            "updated_at": timezone.now(),
        },
    )


def _feed_down(request, stop_id=None):
    """The feed is down: say so instead of failing (story #5)."""
    return render(request, "transit/feed_down.html", {"stop_id": stop_id}, status=503)
