from django.shortcuts import redirect, render
from django.templatetags.static import static


def home(request):
    return render(request, "core/home.html")


def favicon(request):
    """Browsers and bots ask for /favicon.ico; send them to our SVG icon."""
    return redirect(static("core/favicon.svg"), permanent=True)
