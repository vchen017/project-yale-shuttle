from django.urls import include, path

from . import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    # login/, logout/, password_change/, password_reset/ and friends
    path("", include("django.contrib.auth.urls")),
]
