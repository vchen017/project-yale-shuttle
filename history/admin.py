from django.contrib import admin

from .models import (
    Announcement,
    BusPosition,
    Eta,
    Poll,
    RouteStatus,
    RouteStopChange,
    RouteStopList,
)


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ("polled_at", "status", "cause", "bus_count", "eta_count")
    list_filter = ("status",)


@admin.register(RouteStatus)
class RouteStatusAdmin(admin.ModelAdmin):
    list_display = ("polled_at", "route_id", "route_name", "short_name", "active", "num_stops")
    list_filter = ("active",)
    search_fields = ("route_name", "short_name")


@admin.register(BusPosition)
class BusPositionAdmin(admin.ModelAdmin):
    list_display = (
        "polled_at",
        "bus_name",
        "route_id",
        "route_name",
        "last_stop_name",
        "last_update",
    )
    search_fields = ("bus_name", "route_name")


@admin.register(Eta)
class EtaAdmin(admin.ModelAdmin):
    list_display = (
        "polled_at",
        "route_id",
        "route_name",
        "stop_id",
        "stop_name",
        "bus_name",
        "eta_minutes",
    )
    search_fields = ("stop_name", "route_name", "bus_name")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("polled_at", "announcement_id", "title")
    search_fields = ("title", "message")


@admin.register(RouteStopList)
class RouteStopListAdmin(admin.ModelAdmin):
    list_display = ("route_id", "route_name", "first_seen", "stop_count", "short_sha")
    list_filter = ("route_id",)
    ordering = ("route_id", "-first_seen")

    @admin.display(description="stops")
    def stop_count(self, obj):
        return len(obj.stop_ids)

    @admin.display(description="commit")
    def short_sha(self, obj):
        return obj.commit_sha[:7]


@admin.register(RouteStopChange)
class RouteStopChangeAdmin(admin.ModelAdmin):
    list_display = ("changed_at", "route_id", "added_stop_ids", "removed_stop_ids", "short_sha")
    list_filter = ("route_id",)
    ordering = ("-changed_at",)

    @admin.display(description="commit")
    def short_sha(self, obj):
        return obj.commit_sha[:7]
