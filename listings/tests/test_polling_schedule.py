from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from listings.services import polling_schedule as schedule


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("Europe/Moscow"))


@pytest.mark.parametrize("source,mode,now,expected,delay", [
    ("avito", "fast", "2026-11-03T10:00", "2026-11-03T12:15", "10 мин."),
    ("avito", "fast", "2026-11-03T20:15", "2026-11-04T08:15", "10 мин."),
    ("avito", "full", "2026-10-31T04:00", "2026-11-01T03:30", ""),
    ("cian", "fast", "2026-11-03T23:00", "2026-11-04T08:02", ""),
    ("cian", "fast", "2026-11-03T08:02", "2026-11-03T09:02", ""),
    ("cian", "full", "2026-11-08T08:10", "2026-11-15T08:10", "20 мин."),
    ("domclick", "fast", "2026-12-31T23:16", "2027-01-01T00:15", ""),
])
def test_repository_schedule(source, mode, now, expected, delay):
    run = next(row for row in schedule.next_poll_runs(source, moment(now)) if row[0] == mode)
    assert run[2] == moment(expected)
    assert run[2].tzinfo.key == "Asia/Yekaterinburg"
    assert delay in run[3]


def test_all_repository_timers_have_explicit_supported_schedule():
    for path in schedule.TIMER_DIRECTORY.glob("*.timer"):
        if path.name == "home-hunter-scan-health.timer":
            # Относительный таймер зависит от uptime, а не от календаря.
            with pytest.raises(ValueError):
                schedule.timer_schedule(path, moment("2026-11-03T23:00"))
        else:
            at, delay = schedule.timer_schedule(path, moment("2026-11-03T23:00"))
            assert at > moment("2026-11-03T23:00")
            assert delay >= 0


def test_changed_timer_changes_schedule_without_python_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "TIMER_DIRECTORY", tmp_path)
    path = tmp_path / "home-hunter-domclick-fast.timer"
    path.write_text("[Timer]\nOnCalendar=*-*-* 12:34:00 UTC\nRandomizedDelaySec=17s\n")
    run = schedule.next_poll_runs("domclick", moment("2026-11-03T08:00"))[0]
    assert run[2] == moment("2026-11-03T15:34")
    assert "17 сек." in run[3]


@pytest.mark.parametrize("body", [
    "OnCalendar=weekly", "OnCalendar=*-*-* 08:00:00", "OnCalendar=*-*-* 08:00:00 Europe/Berlin",
    "OnCalendar=*-*-* 25:00:00 UTC", "OnCalendar=*-*-* 08:00:00 UTC\nOnBootSec=5min",
    "OnCalendar=*-*-* 08:00:00 UTC\nRandomizedDelaySec=unknown",
    "OnCalendar=*-*-* 08:00:00 UTC\nOnCalendar=unsupported",
])
def test_unknown_timer_is_not_guessed(tmp_path, monkeypatch, body):
    monkeypatch.setattr(schedule, "TIMER_DIRECTORY", tmp_path)
    (tmp_path / "home-hunter-domclick-fast.timer").write_text("[Timer]\n" + body)
    run = schedule.next_poll_runs("domclick", moment("2026-11-03T08:00"))[0]
    assert run[2] is None
    assert run[3] == "Расписание неизвестно"


def test_missing_timer_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "TIMER_DIRECTORY", tmp_path)
    assert schedule.next_poll_runs("cian", moment("2026-11-03T08:00"))[0][2] is None


def test_calendar_reset_and_multiple_entries(tmp_path):
    path = tmp_path / "example.timer"
    path.write_text("[Timer]\nOnCalendar=invalid\nOnCalendar=\n"
                    "OnCalendar=*-*-* 12:00:00 UTC\nOnCalendar=*-*-* 10:00:00 UTC\n")
    at, _ = schedule.timer_schedule(path, moment("2026-11-03T08:00"))
    assert at == moment("2026-11-03T13:00")


def test_summary_handles_unknown_schedule(settings, monkeypatch):
    from unittest.mock import MagicMock
    from listings.services import scan_summary
    settings.TG_FULL_SCAN_CHAT_IDS = ("test-chat",)
    monkeypatch.setattr(scan_summary.Scan.objects, "filter", lambda **kwargs: MagicMock(order_by=lambda *args: []))
    monkeypatch.setattr(scan_summary, "next_poll_runs", lambda *args: [("full", "Полный", None, "Расписание неизвестно")])
    messages = []
    monkeypatch.setattr(scan_summary, "send_telegram_message", lambda text, **kwargs: messages.append(text))
    scan_summary.send_full_scan_summary("cian", moment("2026-11-03T23:00"))
    assert "расписание неизвестно" in messages[0]
