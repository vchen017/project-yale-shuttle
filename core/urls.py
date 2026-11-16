from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("favicon.ico", views.favicon),
    path("healthz", views.healthz, name="healthz"),
    path("network.json", views.network_json, name="network"),
    path("alerts/home", views.home_alerts, name="home-alerts"),
]
