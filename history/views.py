"""Where riders report a bus that never came or skipped their stop (#6)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import time_format
from django.views.decorators.http import require_POST, require_safe

from transit import downtowner

from . import reports
from .forms import ReliabilityForm
from .models import RiderReport


@require_safe
def index(request):
    """The missed-bus report page: the form, and the last day's reports."""
    return _render(request, *_routes())


@login_required
@require_POST
def report_miss(request):
    """Save a logged-in rider's report, one per route and stop every 15 minutes."""
    routes, running, feed_ok = _routes()
    form = ReliabilityForm(request.POST, routes=routes if running else [])
    if form.is_valid():
        route = form.routes[form.cleaned_data["route"]]
        stop = next(stop for stop in route["stops"] if stop["id"] == form.cleaned_data["stop"])
        try:
            reports.report(request.user, route, stop, form.cleaned_data["kind"])
        except reports.TooSoon as error:
            form.add_error(
                "route",
                f"You reported {route['name']} at {stop['name']} in the last 15 minutes. "
                f"You can report it again at {time_format(timezone.localtime(error.args[0]), 'g:i A')}.",
            )
        else:
            kind = RiderReport.Kind(form.cleaned_data["kind"]).label.lower()
            messages.success(
                request,
                f"Thanks! We logged your report for {route['name']} at {stop['name']}: {kind}.",
            )
            return redirect("reliability")
    return _render(request, routes, running, feed_ok, form=form)


def _routes():
    """(routes to report on, whether shuttles are running, whether the feed answered)."""
    try:
        routes = reports.running_routes()
    except downtowner.FeedUnavailable:
        return [], False, False
    return routes, bool(routes), True


def _render(request, routes, running, feed_ok, form=None):
    if form is None and running:
        form = ReliabilityForm(routes=routes)
    return render(
        request,
        "history/reliability.html",
        {
            "form": form if running else None,
            "running": running,
            "feed_ok": feed_ok,
            "routes": routes,
            "recent": reports.recent(),
            "kinds": RiderReport.Kind,
            "now": timezone.now(),
        },
    )
