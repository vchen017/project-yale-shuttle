from django.contrib import admin

from .models import TripShare


@admin.register(TripShare)
class TripShareAdmin(admin.ModelAdmin):
    """Shared trips (#10), for spotting abuse. Read-only: riders make and stop them."""

    list_display = ("created_at", "user", "route_name", "get_off_stop_name", "expires_at", "stopped_at")
    list_select_related = ("user",)
    search_fields = ("user__username",)
    readonly_fields = (
        "user", "route_name", "bus_id", "board_stop_name", "get_off_stop_name",
        "created_at", "expires_at", "stopped_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
