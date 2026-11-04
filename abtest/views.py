import random

from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from .models import Visit
from .team import TEAM_NICKNAME, members

SESSION_KEY = "abtest_visit_id"


@never_cache
@require_http_methods(["GET", "POST"])
def page(request):
    visit_id = request.session.get(SESSION_KEY)
    visit = Visit.objects.filter(pk=visit_id).first() if visit_id else None

    if request.method == "POST":
        # The button was clicked: count at most one click per visitor, then go
        # back to the page, which finds this visitor's visit and counts nothing.
        if visit:
            Visit.objects.filter(pk=visit.pk, clicked_at=None).update(clicked_at=timezone.now())
        return redirect("abtest:page")

    if visit is None:
        # A new visitor: pick a variant at random and count one impression.
        visit = Visit.objects.create(variant=random.choice(Visit.VARIANTS))
        request.session[SESSION_KEY] = visit.pk

    return render(
        request,
        "abtest/page.html",
        {"team": TEAM_NICKNAME, "members": members(), "variant": visit.variant},
    )


@never_cache
@require_safe
def stats(request):
    counts = {variant: {"impressions": 0, "clicks": 0} for variant in Visit.VARIANTS}
    rows = Visit.objects.values("variant").annotate(impressions=Count("id"), clicks=Count("clicked_at"))
    for row in rows:
        counts[row["variant"]] = {"impressions": row["impressions"], "clicks": row["clicks"]}

    def click_rate(variant):
        c = counts[variant]
        return c["clicks"] / c["impressions"] if c["impressions"] else 0.0

    preferred = Visit.THANKS if click_rate(Visit.THANKS) > click_rate(Visit.KUDOS) else Visit.KUDOS
    return JsonResponse({"preferred": preferred, **counts})
