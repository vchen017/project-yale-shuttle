from django.urls import path

from . import views

urlpatterns = [
    path("calendars/", views.connect, name="calendar"),
    path("calendars/disconnect/", views.disconnect, name="calendar-disconnect"),
]
