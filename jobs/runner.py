"""Running one job: record the run, keep runs of the same job from overlapping,
and turn any failure into a failed run rather than a crash (#40)."""

import logging
import traceback

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from .models import JobRun

logger = logging.getLogger(__name__)

OUTPUT_LIMIT = 10_000  # characters of summary or traceback kept per run
ABANDONED = "Never finished: its process stopped before it could record a result."
HANDED_OVER = "Handed over to the next run, which took over while this one was still going."


def run(job, trigger=""):
    """Run `job` now and return its JobRun.

    Returns None, and runs nothing, while another run of the same job is still
    going: the job is never run twice at once. A job registered with
    takes_over=True instead closes the older run and starts. A job that raises
    becomes a failed run with the traceback in its output, and doesn't stop
    the caller.
    """
    record = _start(job, trigger)
    if record is None:
        logger.warning("Skipping %s: another run of it is still going", job.name)
        return None

    logger.info("Starting %s", job.name)
    try:
        result = job.func()
    except Exception:
        logger.exception("%s failed", job.name)
        record.status = JobRun.Status.FAILED
        record.output = _trim(traceback.format_exc())
    else:
        logger.info("%s succeeded", job.name)
        record.status = JobRun.Status.SUCCEEDED
        record.output = _trim("" if result is None else str(result))
    record.finished_at = timezone.now()
    _save_result(record)
    return record


def _start(job, trigger):
    """Record a new running run, or return None if one is already going."""
    now = timezone.now()
    # A run older than the job's timeout that never finished died with its
    # process (a cancelled workflow, a crash). Close it, so it doesn't block
    # every later run.
    JobRun.objects.filter(
        job=job.name, status=JobRun.Status.RUNNING, started_at__lt=now - job.timeout
    ).update(status=JobRun.Status.FAILED, finished_at=now, output=ABANDONED)
    if job.takes_over:
        # The older run hands over: its watch ends where this one's starts. If
        # its process is still alive, its own result overwrites this note.
        JobRun.objects.filter(job=job.name, status=JobRun.Status.RUNNING).update(
            status=JobRun.Status.SUCCEEDED, finished_at=now, output=HANDED_OVER
        )
    try:
        with transaction.atomic():
            return JobRun.objects.create(job=job.name, trigger=trigger[:64], started_at=now)
    except IntegrityError:
        return None


def _save_result(record):
    fields = ["status", "output", "finished_at"]
    try:
        record.save(update_fields=fields)
    except Exception:
        # The connection can drop during a long job (Neon closes idle ones).
        # Reconnect once, so the result isn't lost.
        connection.close()
        record.save(update_fields=fields)


def _trim(text):
    """Keep the end, where a traceback says what went wrong."""
    if len(text) <= OUTPUT_LIMIT:
        return text
    return "…" + text[-(OUTPUT_LIMIT - 1):]
