from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from . import services
from .forms import PlaceForm
from .models import Place


def _rider_place(request, place_id):
    """One of the logged-in rider's places. Anyone else's is "not found", the
    same as a place that doesn't exist, so ids give nothing away."""
    return get_object_or_404(Place, pk=place_id, user=request.user)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def index(request):
    """The rider's saved places, and the form to add one (#7)."""
    form = PlaceForm(request.POST or None, user=request.user)
    if request.method == "POST" and _saved(request, form):
        messages.success(request, f"Saved {form.instance.name}.")
        return redirect("my-places")
    return render(
        request,
        "places/index.html",
        {"places": request.user.places.all(), "form": form, "most": services.MOST_PLACES},
        status=400 if form.errors else 200,
    )


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def edit(request, place_id):
    """Rename a place, move it, or make it (or stop it being) Home."""
    place = _rider_place(request, place_id)
    old_name = place.name
    form = PlaceForm(request.POST or None, instance=place, user=request.user)
    if request.method == "POST" and _saved(request, form):
        renamed = f" (it was {old_name})" if form.instance.name != old_name else ""
        messages.success(request, f"Saved {form.instance.name}{renamed}.")
        return redirect("my-places")
    return render(request, "places/edit.html", {"place": place, "old_name": old_name, "form": form})


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def delete(request, place_id):
    """Ask first, then delete one of the rider's places."""
    place = _rider_place(request, place_id)
    if request.method == "POST":
        place.delete()
        messages.success(request, f"Deleted {place.name}.")
        return redirect("my-places")
    return render(request, "places/delete.html", {"place": place})


@login_required
@require_POST
def make_home(request, place_id):
    """Make one of the rider's places their Home, in place of the old one."""
    place = services.make_home(request.user, _rider_place(request, place_id))
    messages.success(request, f"{place.name} is now your Home.")
    return redirect("my-places")


def _saved(request, form):
    """Save the form's place, or add the error that stopped it."""
    if not form.is_valid():
        return False
    try:
        services.save(request.user, form.instance)
    except services.NameTaken:
        # Another tab saved one with this name since the form was checked.
        form.add_error("name", f"You already have a place called “{form.instance.name}”. Pick another name.")
        return False
    return True
