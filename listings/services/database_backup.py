"""Create a PostgreSQL backup for download by an authenticated staff user."""

import os
import fcntl
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings


class BackupError(RuntimeError):
    """Raised when pg_dump cannot create a backup."""


class UnlinkingFile:
    """File wrapper that removes the temporary dump when Django closes it."""

    def __init__(self, file_obj, path: str):
        self._file = file_obj
        self._path = path

    def close(self):
        try:
            self._file.close()
        finally:
            Path(self._path).unlink(missing_ok=True)

    def __getattr__(self, name):
        return getattr(self._file, name)


def create_database_backup():
    """Return ``(file, filename, size)`` for a freshly-created PostgreSQL dump."""
    database = settings.DATABASES["default"]
    if database.get("ENGINE") != "django.db.backends.postgresql":
        raise BackupError("Резервное копирование поддерживается только для PostgreSQL.")

    lock_path = "/tmp/home_hunter_database_backup.lock"
    lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise BackupError("Другой backup уже создаётся. Повторите через несколько минут.") from exc

    fd, path = tempfile.mkstemp(prefix="home_hunter_backup_", suffix=".dump")
    os.close(fd)
    env = os.environ.copy()
    env.update({
        "PGHOST": str(database.get("HOST") or "127.0.0.1"),
        "PGPORT": str(database.get("PORT") or "5432"),
        "PGUSER": str(database.get("USER") or ""),
        "PGDATABASE": str(database.get("NAME") or ""),
    })
    if database.get("PASSWORD"):
        env["PGPASSWORD"] = str(database["PASSWORD"])

    command = ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", path]
    try:
        try:
            completed = subprocess.run(
                command, env=env, capture_output=True, text=True,
                timeout=15 * 60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            Path(path).unlink(missing_ok=True)
            raise BackupError("Не удалось запустить pg_dump.") from exc
        if completed.returncode != 0:
            Path(path).unlink(missing_ok=True)
            raise BackupError("pg_dump завершился с ошибкой.")

        file_obj = open(path, "rb")
        filename = f"home_hunter_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.dump"
        return UnlinkingFile(file_obj, path), filename, os.path.getsize(path)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
