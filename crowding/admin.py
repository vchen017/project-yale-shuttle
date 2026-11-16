from django.contrib import admin

from .models import CrowdReport


@admin.register(CrowdReport)
class CrowdReportAdmin(admin.ModelAdmin):
    """Riders' crowding reports (#12). Riders add them from /crowding/; admins
    can look through them, or delete one that's wrong."""

    list_display = ("route_name", "level", "user", "created_at", "is_sample")
    list_filter = ("level", "is_sample")
    list_select_related = ("user",)
    search_fields = ("route_name", "user__username")
    readonly_fields = ("user", "route_id", "route_name", "level", "created_at", "is_sample")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
