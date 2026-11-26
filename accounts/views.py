from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.views.decorators.http import require_safe

from .forms import SignUpForm


@require_safe
def me(request):
    """The tab bar's "Me" (#164): a rider's own pages and logging out, or
    logging in and signing up for a visitor."""
    return render(request, "accounts/me.html")


def signup(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            login(request, form.save())
            return redirect("home")
    else:
        form = SignUpForm()
    return render(request, "registration/signup.html", {"form": form})
