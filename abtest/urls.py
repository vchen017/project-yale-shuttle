from django.urls import re_path

from . import views
from .team import PAGE_HASH

app_name = "abtest"
urlpatterns = [
    re_path(rf"^{PAGE_HASH}/?$", views.page, name="page"),
    re_path(rf"^{PAGE_HASH}/stats/?$", views.stats, name="stats"),
]
