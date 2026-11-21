from django.urls import path

from . import views

urlpatterns = [
    path("share/", views.create, name="share-create"),
    path("share/<str:token>/", views.shared_trip, name="share"),
    path("share/<str:token>/stop/", views.stop, name="share-stop"),
]
