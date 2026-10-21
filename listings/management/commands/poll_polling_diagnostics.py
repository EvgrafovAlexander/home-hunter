"""Run one-off polling diagnostics requested from the profiles page."""

import logging
from queue import Queue
import time
from tempfile import TemporaryDirectory
from threading import Thread

from asgiref.sync import async_to_sync, sync_to_async
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from collectors.registry import get_collector
from collectors.routing import resolve_profile
from collectors.avito.session import SessionState
from listings.models import PollingDiagnosticJob, Scan
from listings.services.scans import run_scan


LOCK_ID = 724198501
MAX_LOG_LENGTH = 100_000


class JobLogHandler(logging.Handler):
    """Persist a bounded copy of collector logs for the UI."""

    def __init__(self, job_id):
        super().__init__(level=logging.INFO)
        self.job_id = job_id
        self._queue = Queue()
        self._writer = Thread(target=self._write_loop, name=f"diagnostic-log-{job_id}", daemon=True)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        self._writer.start()

    def emit(self, record):
        # Collector log records can be emitted from an asyncio thread.  ORM
        # writes are deliberately moved to a regular worker thread.
        self._queue.put(self.format(record))

    def _write_loop(self):
        while True:
            line = self._queue.get()
            if line is None:
                return
            try:
                close_old_connections()
                job = PollingDiagnosticJob.objects.filter(pk=self.job_id).only("log").first()
                if job is not None:
                    job.log = (job.log + line + "\n")[-MAX_LOG_LENGTH:]
                    job.save(update_fields=("log",))
            except Exception:
                # A diagnostic must still finish if its log sink is briefly unavailable.
                close_old_connections()

    def close(self):
        if getattr(self, "_writer", None) is not None:
            self._queue.put(None)
            self._writer.join(timeout=10)
            self._writer = None
        super().close()


class Command(BaseCommand):
    help = "Process one-off polling diagnostics queued from the polling profiles page."

    def add_arguments(self, parser):
        parser.add_argument("--poll-seconds", type=float, default=2, help="Delay between queue checks")
        parser.add_argument("--once", action="store_true", help="Process at most one queued job and exit")

    def handle(self, *args, **options):
        delay = max(0.5, options["poll_seconds"])
        while True:
            processed = self.process_one()
            if options["once"]:
                return
            if not processed:
                time.sleep(delay)

    def process_one(self):
        """Claim a queued job only when regular collectors are not running."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            acquired = cursor.fetchone()[0]
        if not acquired:
            return False
        try:
            with transaction.atomic():
                job = (PollingDiagnosticJob.objects.select_for_update(skip_locked=True)
                       .filter(status=PollingDiagnosticJob.Status.QUEUED)
                       .select_related("search_query", "profile", "profile__route")
                       .order_by("created_at", "pk").first())
                if job is None:
                    return False
                job.status = PollingDiagnosticJob.Status.RUNNING
                job.started_at = timezone.now()
                job.log = ""
                job.error = ""
                job.save(update_fields=("status", "started_at", "log", "error"))
            self.run_job(job)
            return True
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    def run_job(self, job):
        handler = JobLogHandler(job.pk)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            self.append_log(job.pk, f"Диагностика: {job.source} / {job.search_query.name} / режим {job.mode}")
            self.append_log(job.pk, f"Профиль: {job.profile.name} ({job.profile.code})")
            self.append_log(job.pk, f"Маршрут: {job.profile.route.name} ({job.profile.route.code})")
            if job.profile.route.endpoint:
                self.append_log(job.pk, f"Источник маршрута: {job.profile.route.endpoint}")
            profile = resolve_profile(job.source, job.mode, job.profile.code)
            async_to_sync(self.collect)(job, profile)
        except Exception as exc:  # noqa: BLE001 - persist diagnostics for unexpected setup errors
            self.finish(job.pk, PollingDiagnosticJob.Status.FAILED, error=f"{type(exc).__name__}: {exc}")
        finally:
            root_logger.removeHandler(handler)
            handler.close()

    async def collect(self, job, profile):
        collector = get_collector(job.source, headless=True, manual_wait_seconds=0)
        collector.route = profile.route
        collector.session_mode = profile.session_mode
        # A button-initiated diagnostic must not inherit a production cooldown,
        # nor write a test block back into the state used by scheduled polling.
        with TemporaryDirectory(prefix=f"polling-diagnostic-{job.source}-") as state_dir:
            if hasattr(collector, "state"):
                collector.state = SessionState(state_dir)
            await sync_to_async(self.append_log)(job.pk, "Изолированное состояние теста: cooldown планового опроса не используется")
            async with collector:
                scan = await sync_to_async(run_scan)(job.search_query, collector, mode=job.mode, profile=profile)
        status = (PollingDiagnosticJob.Status.SUCCESS
                  if scan.status == Scan.Status.SUCCESS else PollingDiagnosticJob.Status.FAILED)
        await sync_to_async(self.finish)(job.pk, status, scan=scan, error=scan.error)

    @staticmethod
    def append_log(job_id, message):
        job = PollingDiagnosticJob.objects.filter(pk=job_id).only("log").first()
        if job is None:
            return
        job.log = (job.log + message + "\n")[-MAX_LOG_LENGTH:]
        job.save(update_fields=("log",))

    @staticmethod
    def finish(job_id, status, *, scan=None, error=""):
        PollingDiagnosticJob.objects.filter(pk=job_id).update(
            status=status, scan_id=getattr(scan, "pk", None), error=error or "", finished_at=timezone.now(),
        )
