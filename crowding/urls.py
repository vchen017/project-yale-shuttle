from django.urls import path

from . import views

urlpatterns = [
    path("crowding/", views.index, name="crowding"),
    path("crowding/report", views.report_crowding, name="crowding-report"),
]
