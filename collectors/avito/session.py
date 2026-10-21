"""Persistent request timing shared by successive collector runs."""
import json
import math
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


def retry_after_seconds(value: str | None, now: float) -> float:
    if not value:
        return 0
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def profile_state_directory(directory: str | Path, profile_code: str | None) -> Path:
    """Return a safe state directory scoped to one polling profile."""
    root = Path(directory)
    if not profile_code:
        return root
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", profile_code):
        raise ValueError("Invalid Avito polling profile code")
    return root / "profiles" / profile_code


class SessionState:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.path = self.directory / "request-state.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {"next_request_at": 0, "blocked_until": 0}
        # Fail closed if state is corrupt rather than accidentally bypass a pause.
        data = json.loads(self.path.read_text())
        for key in ("next_request_at", "blocked_until"):
            if not isinstance(data.get(key), (int, float)) or not math.isfinite(data[key]):
                raise ValueError(f"Invalid Avito session state: {key}")
        return data

    def write_json(self, name: str, data: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(path)

    def update(self, **values) -> None:
        data = self.read()
        data.update(values)
        self.write_json(self.path.name, data)

    def block(self, cooldown: float, retry_after: str | None) -> float:
        now = time.time()
        until = max(self.read()["blocked_until"], now + cooldown,
                    now + retry_after_seconds(retry_after, now))
        self.update(blocked_until=until)
        return until
