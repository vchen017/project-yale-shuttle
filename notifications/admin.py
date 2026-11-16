from django.contrib import admin, messages

from . import push
from .models import PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    """Riders' devices. Devices add themselves from /notifications/; admins can
    send them a test notification, or delete one."""

    list_display = ("user", "device", "created_at", "last_sent_at")
    list_select_related = ("user",)
    search_fields = ("user__username",)
    readonly_fields = ("user", "device", "user_agent", "endpoint", "created_at", "last_sent_at")
    fields = readonly_fields
    actions = ["send_test_notification"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Send a test notification")
    def send_test_notification(self, request, queryset):
        try:
            counts = push.send_test(queryset)
        except push.PushNotConfigured:
            self.message_user(
                request,
                "Notifications aren't set up: set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY.",
                messages.ERROR,
            )
            return
        level, text = push.describe(*counts)
        self.message_user(request, text, level)
