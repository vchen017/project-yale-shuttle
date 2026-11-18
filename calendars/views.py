from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import services
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
