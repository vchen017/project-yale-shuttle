from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("favicon.ico", views.favicon),
    path("healthz", views.healthz, name="healthz"),
]
