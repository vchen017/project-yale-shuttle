import os

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from transit.downtowner import FeedUnavailable
from transit.network import network


def home(request):
    return render(request, "core/home.html")


@require_safe
def network_json(request):
    """The routes running now and their stops, for the home page's map (#71).

    The page fetches it after loading, so the page itself never waits on the
    shuttle feed. Browsers may reuse it for 30 seconds, as long as the server
    caches the feed.
    """
    try:
        data = network()
    except FeedUnavailable:
        return JsonResponse({"error": "Live shuttle data is unavailable right now."}, status=503)
    response = JsonResponse(data)
    response["Cache-Control"] = "max-age=30"
    return response


def favicon(request):
    """Browsers and bots ask for /favicon.ico; send them to our SVG icon."""
    return redirect(static("core/favicon.svg"), permanent=True)


@never_cache
@require_safe
def healthz(request):
    """Is the app up, and which commit is it running?

    Render's health check uses it, so a deploy that can't serve pages never
    goes live, and the promote workflow waits until production reports the
    commit it promoted. It deliberately doesn't query the database: Render
    calls it every few seconds, which would keep Neon's free compute from
    ever sleeping.
    """
    return JsonResponse({"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:7]})
