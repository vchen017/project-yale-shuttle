"""The notifications page, the endpoints its JavaScript calls, and the two files
a browser needs for push: the service worker and the app manifest (#27)."""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.views.decorators.http import require_POST, require_safe

from . import push
from .models import PushSubscription

BAD_SUBSCRIPTION = {"error": "That isn't a push subscription."}


@login_required
def notifications(request):
    devices = request.user.push_subscriptions.all()
    return render(
        request,
        "notifications/settings.html",
        {
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
