"""The notifications page, the endpoints its JavaScript calls, and the two files
a browser needs for push: the service worker and the app manifest (#27). Also
the endpoints for asking the trip agent from the when-to-leave page (#148)."""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST, require_safe

from calendars.models import Calendar, Event
from places.models import Place

from . import ask, push
from .models import AgentAnswer, PushSubscription

BAD_SUBSCRIPTION = {"error": "That isn't a push subscription."}


def setup_steps(user, devices):
    """What a rider needs before we can tell them when to leave for a trip
    (#11): a device with notifications on, a calendar with their events, and a
    Home to start from. Each step says whether it's done and, when it isn't,
    where to do it; the page shows them in order so a rider who turned
    notifications on but connected nothing sees why nothing arrives."""
    has_device = bool(devices)
    has_calendar = Calendar.objects.filter(user=user).exists()
    has_home = Place.objects.filter(user=user, is_home=True).exists()
    return [
        {
            "done": has_device,
            "title": "Notifications on",
            "detail": "On for the devices below." if has_device else "Turn them on for this device above.",
            "url": "",
            "action": "",
        },
        {
            "done": has_calendar,
            "title": "Calendar connected",
            "detail": "We plan a trip for each class and event on it." if has_calendar
            else "We plan a trip for each of your classes and events.",
            "url": reverse("calendar"),
            "action": "Connect a calendar",
        },
        {
            "done": has_home,
            "title": "Home saved",
            "detail": "Your trips start from there." if has_home else "We need to know where your trips start.",
            "url": reverse("my-places"),
            "action": "Set your Home",
        },
    ]


@login_required
def notifications(request):
    devices = request.user.push_subscriptions.all()
    steps = setup_steps(request.user, devices)
    return render(
        request,
        "notifications/settings.html",
        {
            "steps": steps,
            "all_set": all(step["done"] for step in steps),
            "devices": devices,
            "endpoints": [device.endpoint for device in devices],
            "public_key": settings.VAPID_PUBLIC_KEY if push.is_configured() else "",
        },
    )


@login_required
@require_POST
def subscribe(request):
    """Save this device's subscription: the JSON of the browser's PushSubscription."""
    try:
        data = json.loads(request.body)
        endpoint, p256dh, auth = data["endpoint"], data["keys"]["p256dh"], data["keys"]["auth"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse(BAD_SUBSCRIPTION, status=400)
    fields = (endpoint, 1000), (p256dh, 200), (auth, 100)
    if not all(isinstance(value, str) and 0 < len(value) <= limit for value, limit in fields):
        return JsonResponse(BAD_SUBSCRIPTION, status=400)
    if not push.valid_keys(p256dh, auth):
        return JsonResponse(BAD_SUBSCRIPTION, status=400)
    if not push.is_push_service(endpoint):
        return JsonResponse({"error": "That isn't a push service we know."}, status=400)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.headers.get("User-Agent", "")[:300],
        },
    )
    return JsonResponse({"ok": True}, status=201)


@login_required
@require_POST
def unsubscribe(request):
    """Forget this device's subscription, when the rider turns notifications off."""
    try:
        endpoint = json.loads(request.body)["endpoint"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse(BAD_SUBSCRIPTION, status=400)
    request.user.push_subscriptions.filter(endpoint=endpoint).delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def remove_device(request, pk):
    device = get_object_or_404(PushSubscription, pk=pk, user=request.user)
    device.delete()
    messages.success(request, f"Removed {device.device}. It won't get notifications any more.")
    return redirect("notifications")


@login_required
@require_POST
def send_test(request):
    try:
        counts = push.send_test(request.user.push_subscriptions.all())
    except push.PushNotConfigured:
        messages.error(request, "Notifications aren't set up on this server yet.")
    else:
        level, text = push.describe(*counts)
        messages.add_message(request, level, text)
    return redirect("notifications")


@login_required
@require_POST
def ask_agent(request):
    """Ask the trip agent about one of the rider's upcoming events (#148). The
    answer comes from a thread of its own: the when-to-leave page's JavaScript
    polls agent_answer for it, and without JavaScript the page shows it once
    reloaded."""
    event = _posted_event(request)
    if event is None:
        gone = "That event isn't on your calendar anymore."
        if _wants_json(request):
            return JsonResponse({"error": gone}, status=404)
        messages.warning(request, gone)
        return redirect("when-to-leave")
    answer = ask.start(request.user, event)
    if _wants_json(request):
        return JsonResponse(_answer_json(request, answer))
    return redirect(f"{reverse('when-to-leave')}#event-{event.pk}")


@login_required
@require_safe
def agent_answer(request, pk):
    """The agent's answer so far, which the when-to-leave page polls for (#148)."""
    answer = get_object_or_404(AgentAnswer, pk=pk, user=request.user)
    return JsonResponse(_answer_json(request, answer))


def _posted_event(request):
    """The rider's event the form names by its calendar, UID and start, the
    way it outlasts the hourly calendar refresh; None when it's gone."""
    calendar = request.POST.get("calendar", "")
    try:
        start = parse_datetime(request.POST.get("start", ""))
    except ValueError:
        start = None
    if start is None or not calendar.isdigit():
        return None
    return (
        Event.objects.filter(
            calendar_id=int(calendar), calendar__user=request.user, uid=request.POST.get("uid", ""), start=start
        )
        .select_related("calendar")
        .first()
    )


def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _answer_json(request, answer):
    """The answer as the page's JavaScript swaps it in: its state, and its
    HTML, rendered and escaped here rather than built from the model's text
    in the browser."""
    event = {"calendar_id": answer.calendar_id, "uid": answer.event_uid, "start": answer.event_start}
    alerts_on = bool(answer.notify) and request.user.push_subscriptions.exists()
    html = render_to_string(
        "notifications/_agent.html", {"event": event, "answer": answer, "alerts_on": alerts_on}, request=request
    )
    state = AgentAnswer.ASKING if answer.is_asking else AgentAnswer.FAILED if answer.is_failed else answer.status
    return {"status": state, "html": html}


@require_safe
def service_worker(request):
    """Served from the site's root, so it can handle notifications for every page."""
    response = render(request, "notifications/sw.js", content_type="application/javascript")
    response["Cache-Control"] = "no-cache"  # browsers check for a new version each visit
    return response


@require_safe
def manifest(request):
    """Lets riders add the site to their Home Screen, which iPhones need for push."""
    icon = {"type": "image/png"}
    return JsonResponse(
        {
            "name": "Yale Shuttle",
            "short_name": "Yale Shuttle",
            "description": "See when your Yale shuttle will actually arrive.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#00356b",
            "icons": [
                {**icon, "src": static("notifications/icon-192.png"), "sizes": "192x192"},
                {**icon, "src": static("notifications/icon-512.png"), "sizes": "512x512"},
                {
                    **icon,
                    "src": static("notifications/maskable-512.png"),
                    "sizes": "512x512",
                    "purpose": "maskable",
                },
            ],
        },
        content_type="application/manifest+json",
    )
