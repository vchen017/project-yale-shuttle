from django.urls import path

from . import views

urlpatterns = [
    path("places/", views.index, name="my-places"),
    path("places/<int:place_id>/edit", views.edit, name="place-edit"),
    path("places/<int:place_id>/delete", views.delete, name="place-delete"),
    path("places/<int:place_id>/home", views.make_home, name="place-home"),
]
