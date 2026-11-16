from django.contrib import admin, messages
from django.utils import timezone

from .models import Alert, FeedAnnouncement


class StatusFilter(admin.SimpleListFilter):
    """Filter alerts by what riders see now: scheduled, showing or ended."""

    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("scheduled", Alert.SCHEDULED), ("showing", Alert.SHOWING), ("ended", Alert.ENDED)]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "scheduled":
            return queryset.filter(starts_at__gt=now)
        if self.value() == "showing":
            return queryset.current(now)
        if self.value() == "ended":
            return queryset.filter(ends_at__lte=now)
        return queryset


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """Service alerts for the home page (#13, #20): detours, events, road
    closures. Riders see each one from its start until its end, newest first,
    next to the Downtowner's announcements. Edit one to change it; end it now
    to take it down but keep it here; delete it to remove it for good."""

    list_display = ("title", "starts_at", "ends_at", "status")
    list_filter = (StatusFilter,)
    search_fields = ("title", "message")
    fields = ("title", "message", "starts_at", "ends_at")
    actions = ["end_now"]

    @admin.display(description="Status")
    def status(self, alert):
        return alert.status()

    @admin.action(description="End the selected alerts now")
    def end_now(self, request, queryset):
        now = timezone.now()
        # Showing ones end now. Scheduled ones never start: both times become
        # now, so the end still isn't before the start. Ended ones stay as they are.
        showing = queryset.current(now).update(ends_at=now)
        scheduled = queryset.filter(starts_at__gt=now).update(starts_at=now, ends_at=now)
        ended = showing + scheduled
        if ended:
            self.message_user(request, f"Ended {ended} alert{'s' if ended != 1 else ''}. Riders no longer see {'them' if ended != 1 else 'it'}.")
        else:
            self.message_user(request, "Those alerts had already ended.", messages.WARNING)


@admin.register(FeedAnnouncement)
class FeedAnnouncementAdmin(admin.ModelAdmin):
    """When each Downtowner announcement first appeared. Recorded automatically."""

    list_display = ("announcement_id", "first_seen_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
