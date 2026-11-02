from django.contrib import admin

from .models import Visit


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ["id", "variant", "seen_at", "clicked_at"]
    list_filter = ["variant"]
