from django.contrib import admin

from .models import JobRun


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    """The run log: read-only, newest first. Only the jobs write to it."""

    list_display = ("job", "status", "started_at", "took", "trigger", "summary")
    list_filter = ("job", "status")
    fields = ("job", "status", "trigger", "started_at", "finished_at", "took", "output")
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Took")
    def took(self, run):
        if run.duration is None:
            return "still running" if run.status == JobRun.Status.RUNNING else "—"
        seconds = round(run.duration.total_seconds())
        return f"{seconds // 60}m {seconds % 60}s" if seconds >= 60 else f"{seconds}s"

    @admin.display(description="Summary")
    def summary(self, run):
        lines = run.output.strip().splitlines()
        if not lines:
            return ""
        # A traceback's last line is the error; a summary's first line is its gist.
        line = lines[-1] if run.status == JobRun.Status.FAILED else lines[0]
        return line if len(line) <= 90 else line[:89] + "…"
