from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.formats import date_format

from .models import CrowdReport, ReportBlock


class RouteFilter(admin.SimpleListFilter):
    """Filter by route, by its numeric id: short names aren't unique, and a
    route's name can change, so each id lists every name it was reported under."""

    title = "route"
    parameter_name = "route"

    def lookups(self, request, model_admin):
        names = {}
        for route_id, route_name in CrowdReport.objects.order_by().values_list("route_id", "route_name").distinct():
            names.setdefault(route_id, set()).add(route_name)
        return sorted(
            ((str(route_id), f"{' / '.join(sorted(found))} (route {route_id})") for route_id, found in names.items()),
            key=lambda choice: choice[1].casefold(),
        )

    def queryset(self, request, queryset):
        if self.value() and self.value().isdigit():
            return queryset.filter(route_id=int(self.value()))
        return queryset


class StatusFilter(admin.SimpleListFilter):
    """Counted toward crowding, or removed by an admin."""

    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("counted", "Counted"), ("removed", "Removed")]

    def queryset(self, request, queryset):
        if self.value() == "counted":
            return queryset.counted()
        if self.value() == "removed":
            return queryset.filter(removed_at__isnull=False)
        return queryset


@admin.register(CrowdReport)
class CrowdReportAdmin(admin.ModelAdmin):
    """Riders' crowding reports (#12), newest first, for moderating (#16).

    Pick bad reports and "Remove" them: they stop counting toward crowding
    at once, but stay here, marked removed, so they can be restored and we
    can see who keeps posting bad ones (#19). Reports can't be added or
    edited here, and there's no bulk delete, so the record stays complete.
    (Deleting one report stays possible: without it, the admin couldn't
    delete a rider's account, which deletes their reports.)
    """

    list_display = ("created_at", "rider", "route", "level", "status", "is_sample")
    list_display_links = ("created_at",)
    list_filter = (RouteFilter, StatusFilter, "level", "is_sample")
    list_select_related = ("user",)
    search_fields = ("route_name", "user__username", "user__email")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = (
        "user", "route_id", "route_name", "level", "created_at", "is_sample", "removed_at", "removed_by",
    )
    actions = ["remove_reports", "restore_reports"]

    @admin.display(description="Rider", ordering="user__username")
    def rider(self, report):
        return report.user

    @admin.display(description="Route", ordering="route_name")
    def route(self, report):
        return f"{report.route_name} (route {report.route_id})"

    @admin.display(description="Status", ordering="removed_at")
    def status(self, report):
        if report.removed_at:
            return f"Removed {date_format(timezone.localtime(report.removed_at), 'M j, g:i A')}"
        return "Counted"

    @admin.action(description="Remove the selected reports (they stop counting toward crowding)")
    def remove_reports(self, request, queryset):
        removed = queryset.counted().update(removed_at=timezone.now(), removed_by=request.user)
        if removed:
            self.message_user(
                request,
                f"Removed {removed} report{'s' if removed != 1 else ''}. "
                f"{'They no longer count' if removed != 1 else 'It no longer counts'} toward crowding.",
            )
        else:
            self.message_user(request, "Those reports were already removed.", messages.WARNING)

    @admin.action(description="Restore the selected reports (they count toward crowding again)")
    def restore_reports(self, request, queryset):
        restored = queryset.filter(removed_at__isnull=False).update(removed_at=None, removed_by=None)
        if restored:
            self.message_user(
                request,
                f"Restored {restored} report{'s' if restored != 1 else ''}. "
                f"{'They count' if restored != 1 else 'It counts'} toward crowding again.",
            )
        else:
            self.message_user(request, "None of those reports were removed.", messages.WARNING)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)  # remove instead, which keeps the record
        return actions

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ReportBlockForm(forms.ModelForm):
    class Meta:
        model = ReportBlock
        fields = ["user", "reason"]

    def clean_user(self):
        user = self.cleaned_data["user"]
        if ReportBlock.objects.filter(user=user, unblocked_at__isnull=True).exists():
            raise forms.ValidationError("This rider is already blocked.")
        return user


@admin.register(ReportBlock)
class ReportBlockAdmin(admin.ModelAdmin):
    """Block a rider from sending crowd reports, with a reason, and unblock
    them again (#19). Add a block, then pick it and "Unblock". Blocks can't be
    edited, and there's no bulk delete, so the history of who was blocked and
    why stays. (Deleting a rider's account still deletes their blocks.)"""

    form = ReportBlockForm
    list_display = ("user", "reason", "blocked_at", "blocked_by", "status")
    list_filter = ("unblocked_at",)
    list_select_related = ("user", "blocked_by", "unblocked_by")
    search_fields = ("user__username", "user__email", "reason")
    autocomplete_fields = ("user",)
    ordering = ("-blocked_at",)
    actions = ["unblock"]

    @admin.display(description="Status", ordering="unblocked_at")
    def status(self, block):
        if block.unblocked_at:
            return f"Unblocked {date_format(timezone.localtime(block.unblocked_at), 'M j, g:i A')}"
        return "Blocked"

    def get_readonly_fields(self, request, obj=None):
        return ("user", "reason", "blocked_at", "blocked_by", "unblocked_at", "unblocked_by") if obj else ()

    def get_fields(self, request, obj=None):
        if obj:
            return ("user", "reason", "blocked_at", "blocked_by", "unblocked_at", "unblocked_by")
        return ("user", "reason")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.blocked_by = request.user
            obj.blocked_at = timezone.now()
        super().save_model(request, obj, form, change)

    @admin.action(description="Unblock the selected riders (they can report again)")
    def unblock(self, request, queryset):
        count = queryset.filter(unblocked_at__isnull=True).update(
            unblocked_at=timezone.now(), unblocked_by=request.user
        )
        if count:
            self.message_user(request, f"Unblocked {count} rider{'s' if count != 1 else ''}.")
        else:
            self.message_user(request, "Those riders weren't blocked.", messages.WARNING)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)
