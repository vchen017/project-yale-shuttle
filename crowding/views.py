import datetime as dt

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import time_format
from django.views.decorators.http import require_POST, require_safe

from transit.downtowner import FeedUnavailable
from transit.network import DEFAULT_COLOR, network

from .forms import ReportForm
from .models import CrowdReport
from .services import (
    HOURLY_LIMIT,
    MAX_SUMMARY_DAYS,
    TYPICAL_WINDOW,
    Blocked,
    TooMany,
    TooSoon,
    crowding,
    crowding_summary,
    is_blocked,
    report,
)


@require_safe
def index(request):
    """Each route's crowding now and by time of day, and the form to report it (#12)."""
    return _render(request, *_routes())


@staff_member_required
@require_safe
def summary(request):
    """Average reported crowding per route and hour of day, over a chosen date
    range, with the runs riders reported full highlighted (#18)."""
    start, end, error = _range(request)
    rows, hours, includes_samples = crowding_summary(start, end)
    return render(
        request,
        "crowding/summary.html",
        {
            "start": start,
            "end": end,
            "error": error,
            "today": timezone.localdate(),
            "rows": rows,
            "hours": [dt.time(hour) for hour in hours],
            "includes_samples": includes_samples,
            "max_days": MAX_SUMMARY_DAYS,
        },
    )


def _range(request):
    """(start, end, error): the Eastern date range to show, defaulting to the
    last 28 days. A bad or oversized range falls back to it with a message."""
    today = timezone.localdate()
    default_start, default_end = today - TYPICAL_WINDOW + dt.timedelta(days=1), today
    start, end = _date(request.GET.get("from")), _date(request.GET.get("to"))
    if start is None and request.GET.get("from"):
        return default_start, default_end, "Use YYYY-MM-DD dates, like 2026-11-10."
    if end is None and request.GET.get("to"):
        return default_start, default_end, "Use YYYY-MM-DD dates, like 2026-11-16."
    start, end = start or default_start, end or default_end
    if start > end:
        return default_start, default_end, "'From' is after 'To'."
    if (end - start).days >= MAX_SUMMARY_DAYS:
        return default_start, default_end, f"Pick at most {MAX_SUMMARY_DAYS} days at a time."
    return start, end, None


def _date(value):
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


@login_required
@require_POST
def report_crowding(request):
    """Save a logged-in rider's report, one per route every 10 minutes."""
    routes, running, feed_ok = _routes()
    if is_blocked(request.user):
        return _render(request, routes, running, feed_ok, status=403)
    form = ReportForm(request.POST, routes=routes if running else [])
    if form.is_valid():
        route = form.routes[form.cleaned_data["route"]]
        try:
            report(request.user, route["id"], route["name"], form.cleaned_data["level"])
        except Blocked:  # blocked since the check above
            return _render(request, routes, running, feed_ok, status=403)
        except TooMany as error:
            form.add_error(
                None,
                f"You can send at most {HOURLY_LIMIT} reports an hour. "
                f"You can report again at {time_format(timezone.localtime(error.args[0]), 'g:i A')}.",
            )
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


def _render(request, routes, running, feed_ok, form=None, status=200):
    blocked = request.user.is_authenticated and is_blocked(request.user)
    if form is None and running and not blocked:
        form = ReportForm(routes=routes)
    return render(
        request,
        "crowding/index.html",
        {
            "form": form if running and not blocked else None,
            "blocked": blocked,
            "hourly_limit": HOURLY_LIMIT,
            "routes": crowding(routes),
            "running": running,
            "feed_ok": feed_ok,
            "levels": CrowdReport.Level,
            "now": timezone.now(),
        },
        status=status,
    )
