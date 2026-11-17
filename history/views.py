"""Where riders report a bus that never came or skipped their stop (#6), and
where admins see how the predictions really serve each route and stop (#17)."""

import datetime as dt

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import time_format
from django.views.decorators.http import require_POST, require_safe

from transit import downtowner

from . import accuracy, reports
from .forms import ReliabilityForm
from .models import AccuracySnapshot, RiderReport


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


@staff_member_required
@require_safe
def accuracy_dashboard(request):
    """Per-route, per-stop and per-hour accuracy over a chosen date range (#17).

    Shows the score the nightly score_accuracy job saved for that range: the
    page itself never computes, because scoring a week of history takes
    minutes and no request may wait for that."""
    start, end, error = _range(request)
    snapshot = AccuracySnapshot.objects.filter(range_start=start, range_end=end).first()
    context = {"start": start, "end": end, "error": error, "today": timezone.localdate()}
    if snapshot is None:
        context["saved"] = AccuracySnapshot.objects.all()[:6]
    else:
        context.update(result=accuracy.from_storage(snapshot.payload), snapshot=snapshot)
    return render(request, "history/dashboard.html", context)


def _range(request):
    """(start, end, error): the Eastern date range to show, defaulting to
    the last seven days. A bad or oversized range falls back to the default
    with a message."""
    today = timezone.localdate()
    default_start, default_end = today - dt.timedelta(days=6), today
    start, end = _date(request.GET.get("from")), _date(request.GET.get("to"))
    if start is None and request.GET.get("from"):
        return default_start, default_end, "Use YYYY-MM-DD dates, like 2026-11-10."
    if end is None and request.GET.get("to"):
        return default_start, default_end, "Use YYYY-MM-DD dates, like 2026-11-16."
    start, end = start or default_start, end or default_end
    if start > end:
        return default_start, default_end, "'From' is after 'To'."
    if (end - start).days >= accuracy.MAX_RANGE_DAYS:
        return default_start, default_end, f"Pick at most {accuracy.MAX_RANGE_DAYS} days at a time."
    return start, end, None


def _date(value):
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None
