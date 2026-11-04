from django.db import models


class Visit(models.Model):
    """One visitor to the A/B test page: one impression and at most one click."""

    KUDOS = "kudos"
    THANKS = "thanks"
    VARIANTS = [KUDOS, THANKS]

    variant = models.CharField(max_length=6, choices=[(v, v) for v in VARIANTS])
    seen_at = models.DateTimeField(auto_now_add=True)  # the impression
    clicked_at = models.DateTimeField(null=True, blank=True)  # the click, if any

    def __str__(self):
        return f"{self.variant} visit {self.pk}"
