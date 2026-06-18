# Tests for subracer.scheduling: the headless command, cron timing expressions,
# and the cross-platform schedule preview. install_schedule is not exercised here
# (it mutates the real crontab / Task Scheduler).

from __future__ import annotations

from pathlib import Path

from subracer.scheduling import (
  CRON_MARKER,
  _cron_timing,
  build_run_command,
  schedule_preview,
)


def test_build_run_command_is_headless_single_pass():
  cmd = build_run_command(Path("/media"), ["en", "nl"], python="/usr/bin/python")
  assert cmd[:4] == ["/usr/bin/python", "-m", "subracer", "--headless"]
  assert "/media" in cmd and "en,nl" in cmd
  # Scheduled runs do a single pass and exit (no keep-running), so --once is set.
  assert "--once" in cmd and "--wait-for-quota" not in cmd


def test_build_run_command_omits_languages_when_empty():
  assert "--languages" not in build_run_command(Path("/media"), [])


def test_cron_timing_intervals():
  assert _cron_timing(15) == "*/15 * * * *"
  assert _cron_timing(60) == "0 * * * *"
  assert _cron_timing(120) == "0 */2 * * *"
  assert _cron_timing(24 * 60) == "0 0 * * *"
  assert _cron_timing(0) == "*/1 * * * *"  # clamped to >= 1


def test_schedule_preview_cron():
  line = schedule_preview(Path("/media"), ["en"], 60, system="Linux")
  assert line.startswith("0 * * * * ") and line.endswith(CRON_MARKER)
  assert "-m subracer --headless --once --dir /media --languages en" in line


def test_schedule_preview_windows():
  line = schedule_preview(Path("/media"), ["en"], 30, system="Windows")
  assert line.startswith("schtasks /Create /SC MINUTE /MO 30 /TN subracer")
  assert "--headless" in line
