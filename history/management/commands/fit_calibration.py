"""Refit the countdown corrections from the recorded history (#117).

For every poll in the last HISTORY_DAYS, the fit asks how far the shown
countdown was from the truth (predictions._shown_residuals) and takes the
median of those residuals per (route, countdown bucket), with the
all-routes medians beneath and the overall median as the last fallback.
The nightly job runs this after the history import; predict_minutes reads
the stored rows through the cache. Safe to run twice, late, or on a fresh
database: a run replaces every row, and a thin fit simply corrects less.
"""

import datetime as dt

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from history import predictions
from history.models import Calibration


class Command(BaseCommand):
    help = (
        "Refit the countdown corrections from the recorded history, for "
        "predict_minutes' fused live answer (#117)."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        since = now - dt.timedelta(days=predictions.HISTORY_DAYS)
        residuals = predictions._shown_residuals(since, now)
        corrections = predictions._fit_corrections(residuals)
        fitted = now
        rows = [
            Calibration(
                route_id=route_id,
                bucket=bucket,
                delta_minutes=round(delta, 2),
                samples=len(residuals[(route_id, bucket)]),
                fitted_at=fitted,
            )
            for (route_id, bucket), delta in corrections.by_route.items()
        ]
        rows += [
            Calibration(
                route_id=None,
                bucket=bucket,
                delta_minutes=round(delta, 2),
                samples=sum(
                    len(values)
                    for (route_id, that), values in residuals.items()
                    if that == bucket
                ),
                fitted_at=fitted,
            )
            for bucket, delta in corrections.by_bucket.items()
        ]
        everything = sum(len(values) for values in residuals.values())
        if everything:
            rows.append(
                Calibration(
                    route_id=None,
                    bucket=predictions.OVERALL_BUCKET,
                    delta_minutes=round(corrections.overall, 2),
                    samples=everything,
                    fitted_at=fitted,
                )
            )
        with transaction.atomic():
            Calibration.objects.all().delete()
            Calibration.objects.bulk_create(rows)
        self.stdout.write(
            f"Fitted {len(rows)} correction rows from {everything} residuals "
            f"over the last {predictions.HISTORY_DAYS} days "
            f"(overall median {corrections.overall:+.1f} min)."
        )
