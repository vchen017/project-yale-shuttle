from django.contrib.auth import views as auth_views
from django.urls import include, path

from . import views
from .forms import LoginForm

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(form_class=LoginForm), name="login"),
    # logout/, password_change/, password_reset/ and friends
    path("", include("django.contrib.auth.urls")),
]
