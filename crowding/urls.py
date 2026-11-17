from django.urls import path

from . import views

urlpatterns = [
    path("crowding/", views.index, name="crowding"),
    path("crowding/summary/", views.summary, name="crowding-summary"),
    path("crowding/report", views.report_crowding, name="crowding-report"),
]
