"""Refit the countdown corrections from the recorded history (#117).

For every poll in the last HISTORY_DAYS, the fit asks how far the shown
countdown was from the truth (predictions._shown_residuals) and takes the
median of those residuals per (route, countdown bucket), with the
all-routes medians beneath and the overall median as the last fallback.
Each row also stores its cushion (#155): how much earlier than the corrected
time to leave so "Leave by" gets riders to the stop in time 90% of the
time (predictions._fit_cushions); and its arrival cushion (#174): how much
later than the corrected time a bus can reach a stop, so "There by" holds
90% of the time. The nightly job runs this after the
history import; predict_minutes and the trip plans read the stored rows
through the cache. Safe to run twice, late, or on a fresh
database: a run replaces every row, and a thin fit simply corrects less.
"""

import datetime as dt

from django.core.cache import cache
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
        own_bus = predictions._own_bus_residuals(since, now)  # each bus against its own arrival (#174)
        corrections = predictions._fit_corrections(residuals, own_bus=own_bus)
        fitted = now
        rows = [
            Calibration(
                route_id=route_id,
                bucket=bucket,
                delta_minutes=round(delta, 2),
                cushion_minutes=round(corrections.cushion_by_route[(route_id, bucket)], 2),
                arrival_cushion_minutes=round(corrections.late_by_route[(route_id, bucket)], 2),
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
                cushion_minutes=round(corrections.cushion_by_bucket[bucket], 2),
                arrival_cushion_minutes=round(corrections.late_by_bucket[bucket], 2),
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
                    cushion_minutes=round(corrections.cushion_overall, 2),
                    arrival_cushion_minutes=round(corrections.late_overall, 2),
                    samples=everything,
                    fitted_at=fitted,
                )
            )
        with transaction.atomic():
            Calibration.objects.all().delete()
            Calibration.objects.bulk_create(rows)
        # predict_minutes reads the corrections through a 10-minute cache:
        # drop it so the new fit answers at once instead of up to ten
        # minutes late (the nightly fit would otherwise go unnoticed until
        # the cache expired — including the first-ever fit, which would
        # leave raw countdowns serving that whole window).
        cache.delete("history.calibration")
        self.stdout.write(
            f"Fitted {len(rows)} correction rows from {everything} residuals "
            f"over the last {predictions.HISTORY_DAYS} days "
            f"(overall median {corrections.overall:+.1f} min, overall cushion {corrections.cushion_overall:.1f} min, "
            f"overall arrival cushion {corrections.late_overall:.1f} min)."
        )
