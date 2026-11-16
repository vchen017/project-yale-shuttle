from django.contrib import admin
from django.utils import timezone

from .models import Alert, FeedAnnouncement


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Service alerts for the home page. Riders see each one from its start
    until its end, newest first, next to the Downtowner's announcements."""

    list_display = ("title", "starts_at", "ends_at", "showing_now")
    search_fields = ("title", "message")
    fields = ("title", "message", "starts_at", "ends_at")

    @admin.display(boolean=True, description="Showing now")
    def showing_now(self, alert):
        return alert.starts_at <= timezone.now() < alert.ends_at


@admin.register(FeedAnnouncement)
class FeedAnnouncementAdmin(admin.ModelAdmin):
    """When each Downtowner announcement first appeared. Recorded automatically."""

    list_display = ("announcement_id", "first_seen_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
