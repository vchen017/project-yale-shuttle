from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import time_format
from django.views.decorators.http import require_POST, require_safe

from transit.downtowner import FeedUnavailable
from transit.network import DEFAULT_COLOR, network

from .forms import ReportForm
from .models import CrowdReport
from .services import TYPICAL_WINDOW, TooSoon, crowding, report


@require_safe
def index(request):
    """Each route's crowding now and by time of day, and the form to report it (#12)."""
    return _render(request, *_routes())


@login_required
@require_POST
def report_crowding(request):
    """Save a logged-in rider's report, one per route every 10 minutes."""
    routes, running, feed_ok = _routes()
    form = ReportForm(request.POST, routes=routes if running else [])
    if form.is_valid():
        route = form.routes[form.cleaned_data["route"]]
        try:
            report(request.user, route["id"], route["name"], form.cleaned_data["level"])
        except TooSoon as error:
            form.add_error(
                "route",
                f"You reported {route['name']} in the last 10 minutes. "
                f"You can report it again at {time_format(timezone.localtime(error.args[0]), 'g:i A')}.",
            )
        else:
            messages.success(request, f"Thanks! Riders now see your report for {route['name']}.")
            return redirect("crowding")
    return _render(request, routes, running, feed_ok, form=form)


def _routes():
    """(routes to show, whether shuttles are running, whether the feed answered).

    While shuttles run: the routes running now, which riders can report on.
    Otherwise (late at night, or the feed is down): the routes with reports in
    the last four weeks, so typical crowding still shows, but nothing to report on.
    """
    try:
        net = network()
    except FeedUnavailable:
        net, feed_ok = None, False
    else:
        feed_ok = True
    if net and net["running"]:
        return net["routes"], True, feed_ok

    known = {route["id"]: route for route in net["routes"]} if net else {}
    reported = (
        CrowdReport.objects.counted().filter(created_at__gt=timezone.now() - TYPICAL_WINDOW)
        .order_by("route_id", "-created_at")
        .values_list("route_id", "route_name")
    )
    routes = {}
    for route_id, route_name in reported:
        routes.setdefault(
            route_id,
            known.get(route_id)
            or {"id": route_id, "name": route_name, "label": route_name.split(" - ")[0], "color": DEFAULT_COLOR},
        )
    return sorted(routes.values(), key=lambda route: route["name"]), False, feed_ok


def _render(request, routes, running, feed_ok, form=None):
    if form is None and running:
        form = ReportForm(routes=routes)
    return render(
        request,
        "crowding/index.html",
        {
            "form": form if running else None,
            "routes": crowding(routes),
            "running": running,
            "feed_ok": feed_ok,
            "levels": CrowdReport.Level,
            "now": timezone.now(),
        },
    )
