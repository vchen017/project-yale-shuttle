import os

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


def home(request):
    return render(request, "core/home.html")


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
