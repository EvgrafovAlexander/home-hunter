import os
import time
from dataclasses import asdict
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urljoin

import requests
from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from collectors.domclick.collector import DomclickCollector


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


class Command(BaseCommand):
    help = "Run the local manual Domclick agent and send results to Home Hunter."

    def add_arguments(self, parser):
        parser.add_argument("--server", default=os.getenv("HOME_HUNTER_URL", ""))
        parser.add_argument("--token", default=os.getenv("DOMCLICK_AGENT_TOKEN", ""))
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--wait-for-captcha", type=float, default=300)

    def handle(self, *args, **options):
        server, token = options["server"].rstrip("/"), options["token"]
        if not server.startswith(("https://", "http://")) or not token:
            raise CommandError("Set HOME_HUNTER_URL and DOMCLICK_AGENT_TOKEN, or pass --server and --token")
        headers = {"X-Home-Hunter-Agent-Token": token}
        while True:
            try:
                response = requests.post(urljoin(server + "/", "api/domclick-agent/jobs/next/"), headers=headers, timeout=20)
                response.raise_for_status()
                job = response.json()["job"]
            except requests.RequestException as exc:
                self.stderr.write(f"Agent connection error: {exc}")
                if options["once"]:
                    raise CommandError("Could not contact Home Hunter") from exc
                time.sleep(settings.DOMCLICK_AGENT_POLL_SECONDS)
                continue
            if not job:
                if options["once"]:
                    self.stdout.write("No queued Domclick task")
                    return
                time.sleep(settings.DOMCLICK_AGENT_POLL_SECONDS)
                continue
            self.stdout.write(f"Running Domclick task {job['id']}: {job['name']}")
            self._run_job(server, headers, job, options["wait_for_captcha"])
            if options["once"]:
                return

    def _run_job(self, server, headers, job, wait_for_captcha):
        endpoint = urljoin(server + "/", f"api/domclick-agent/jobs/{job['id']}/complete/")
        try:
            search = SimpleNamespace(url=job["url"], source="domclick")
            async def collect():
                async with DomclickCollector(headless=False, manual_wait_seconds=wait_for_captcha) as collector:
                    return await collector.collect(search, mode="fast")
            result = async_to_sync(collect)()
            payload = {"status": "success", "pages_scanned": result.pages_scanned, "items_seen": result.items_seen,
                       "listings": [_json_value(asdict(item)) for item in result.listings]}
        except Exception as exc:
            payload = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:700]}"}
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            self.stdout.write(f"Domclick task {job['id']} completed")
        except requests.RequestException as exc:
            self.stderr.write(f"Could not return task result: {exc}")
