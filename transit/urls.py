from django.urls import path

from . import views

urlpatterns = [
    path("stops/", views.stops, name="stops"),
    path("stops/<int:stop_id>/", views.stop, name="stop"),
]
