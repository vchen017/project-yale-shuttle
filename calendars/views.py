from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
    require_safe,
)

from notifications import ask, trips
from places.models import Place
from transit.downtowner import FeedUnavailable
from transit.network import network

from . import demo, plans, services
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
    """When to leave for each event in the next 24 hours (#8): the route, the
    stop to walk to, and the time to leave, from the rider's Home. Events
    whose location we can't match to a building are listed as location
    unknown. The feed being down never breaks the page: the events and their
    buildings still show, without ride times. A rideable event can be put to
    the trip agent from here (#148)."""
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

    rideable, stranded, unknown = [], [], []
    for event in events:
        plan = plans.plan_event(
            event, home, now=now, stops=stops, routes_by_id=routes_by_id, running_at=plans.routes_running_at
        )
        if plan.location_unknown:
            unknown.append(plan)
        elif plan.leave_by is not None:
            rideable.append(plan)
        else:
            stranded.append(plan)
    rideable.sort(key=lambda plan: plan.leave_by)
    answers = ask.answers_for(request.user, [plan.event for plan in rideable])
    for plan in rideable:
        plan.has_a_bus = ask.has_a_bus(plan)
        plan.ask_from = ask.ask_from(plan.event, now)
        plan.agent_answer = answers.get(ask.key(plan.event))
    return render(
        request,
        "calendars/leave.html",
        {
            "rideable": rideable,
            "stranded": stranded,
            "unknown": unknown,
            "has_home": home is not None,
            "feed_down": stops is None,
            "now": now,
        },
    )
