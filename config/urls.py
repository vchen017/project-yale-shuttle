from django.contrib import admin
from django.urls import include, path

admin.site.site_header = admin.site.site_title = "Yale Shuttle admin"
admin.site.index_title = "Manage Yale Shuttle"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("accounts.urls")),
    path("", include("abtest.urls")),
    path("", include("core.urls")),
]
