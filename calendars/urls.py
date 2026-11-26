from django.urls import path

from . import views

urlpatterns = [
    path("calendars/", views.connect, name="calendar"),
    path("calendars/disconnect/", views.disconnect, name="calendar-disconnect"),
    path("when-to-leave/", views.leave, name="when-to-leave"),
    path("when-to-leave/next/", views.next_trip_card, name="next-trip"),
    path("when-to-leave/next/stop/", views.stop_following, name="next-trip-stop"),
]
