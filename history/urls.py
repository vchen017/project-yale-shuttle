from django.urls import path

from . import views

urlpatterns = [
    path("reliability/", views.index, name="reliability"),
    path("reliability/report", views.report_miss, name="reliability-report"),
]
