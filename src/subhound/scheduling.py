# subhound.scheduling
#
# Helpers to run subhound periodically via the OS scheduler (cron on Linux/macOS,
# Task Scheduler on Windows). This is the alternative to keeping the app open
# while it merely waits on a slow-drip provider: once every other source is
# exhausted and only a rate-limited provider (e.g. OpenSubtitles' few downloads a
# day) is left, exit and let the OS restart subhound on a schedule. Each run
# resumes from the persisted run log, grabs whatever new quota has freed up, and
# exits -- so no resources are tied up just waiting.

from __future__ import annotations

import platform
import shlex
import subprocess
import sys
from pathlib import Path

# Marker comment tagging the crontab line subhound manages, so it can be replaced
# or removed without touching the user's other cron entries.
CRON_MARKER = "# subhound-auto"
TASK_NAME = "subhound"


# Function Summary:
#    Build the headless command that a scheduled run should execute. It does NOT
#    wait for quotas (each scheduled run does a single pass and exits; resume
#    carries progress between runs).
#
#  Input (parameters):
#    target_dir [Path]:           the media directory to process
#    languages [list[str]]:       wanted language codes (omitted if empty)
#    python [str | None]:         python executable (defaults to the current one)
#
#  Output:
#    cmd [list[str]]:  the argv for the scheduled run
#
# Example:
#    build_run_command(Path("/media"), ["en"])  ->  [".../python", "-m", "subhound", "--headless", ...]
def build_run_command(
  target_dir: Path,
  languages: list[str],
  python: str | None = None,
) -> list[str]:
  cmd = [python or sys.executable, "-m", "subhound", "--headless", "--once",
         "--dir", str(target_dir)]
  if languages:
    cmd += ["--languages", ",".join(languages)]
  return cmd


# Function Summary:
#    Build a cron timing expression (the five time fields) for a repeat interval.
#
#  Input (parameters):
#    interval_minutes [int]:  how often to run, in minutes
#
#  Output:
#    timing [str]:  a cron schedule expression (e.g. "0 * * * *" for hourly)
#
# Example:
#    _cron_timing(60)  ->  "0 * * * *"
def _cron_timing(interval_minutes: int) -> str:
  minutes = max(1, int(interval_minutes))
  if minutes < 60:
    return f"*/{minutes} * * * *"
  hours = minutes // 60
  if hours < 24:
    return "0 * * * *" if hours == 1 else f"0 */{hours} * * *"
  days = hours // 24
  return f"0 0 */{days} * *" if 1 < days < 31 else "0 0 * * *"


# Function Summary:
#    Produce the exact scheduler entry that would run subhound periodically, for
#    the given (or current) platform -- a crontab line on Linux/macOS or a
#    schtasks command on Windows. Safe to display; performs no changes.
#
#  Input (parameters):
#    target_dir [Path]:        the media directory to process
#    languages [list[str]]:    wanted language codes
#    interval_minutes [int]:   how often to run (default hourly)
#    system [str | None]:      platform name override (defaults to platform.system())
#
#  Output:
#    snippet [str]:  the scheduler entry the user (or install_schedule) would add
#
# Example:
#    schedule_preview(Path("/media"), ["en"], 60, system="Linux")
#      ->  "0 * * * * /usr/bin/python -m subhound --headless --dir /media --languages en  # subhound-auto"
def schedule_preview(
  target_dir: Path,
  languages: list[str],
  interval_minutes: int = 60,
  system: str | None = None,
) -> str:
  system = system or platform.system()
  cmd = build_run_command(target_dir, languages)
  if system == "Windows":
    return (f'schtasks /Create /SC MINUTE /MO {max(1, int(interval_minutes))} '
            f'/TN {TASK_NAME} /TR "{subprocess.list2cmdline(cmd)}" /F')
  command = " ".join(shlex.quote(part) for part in cmd)
  return f"{_cron_timing(interval_minutes)} {command}  {CRON_MARKER}"


# Function Summary:
#    Install (or replace) the periodic subhound schedule on this machine. On
#    Linux/macOS it rewrites the user's crontab, preserving every line except a
#    previous subhound entry; on Windows it creates/replaces the scheduled task.
#    This changes system state and should only be called on explicit user action.
#
#  Input (parameters):
#    target_dir [Path]:        the media directory to process
#    languages [list[str]]:    wanted language codes
#    interval_minutes [int]:   how often to run (default hourly)
#    system [str | None]:      platform name override (defaults to platform.system())
#
#  Output:
#    result [tuple[bool, str]]:  (succeeded, message for the user)
#
# Example:
#    install_schedule(Path("/media"), ["en"], 60)  ->  (True, "Scheduled hourly via cron.")
def install_schedule(
  target_dir: Path,
  languages: list[str],
  interval_minutes: int = 60,
  system: str | None = None,
) -> tuple[bool, str]:
  system = system or platform.system()
  preview = schedule_preview(target_dir, languages, interval_minutes, system)
  if system == "Windows":
    cmd = build_run_command(target_dir, languages)
    args = [
      "schtasks", "/Create", "/SC", "MINUTE", "/MO", str(max(1, int(interval_minutes))),
      "/TN", TASK_NAME, "/TR", subprocess.list2cmdline(cmd), "/F",
    ]
    try:
      proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
      return False, f"Could not run schtasks: {exc}"
    if proc.returncode != 0:
      return False, (proc.stderr or proc.stdout or "schtasks failed").strip()
    return True, f"Scheduled task '{TASK_NAME}' created (every {interval_minutes} min)."
  # cron (Linux/macOS): rewrite the crontab, keeping all non-subhound lines.
  try:
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=30)
  except (OSError, subprocess.SubprocessError) as exc:
    return False, f"Could not read crontab: {exc}"
  lines = []
  if existing.returncode == 0:
    lines = [ln for ln in existing.stdout.splitlines() if CRON_MARKER not in ln]
  lines.append(preview)
  payload = "\n".join(lines) + "\n"
  try:
    proc = subprocess.run(["crontab", "-"], input=payload, capture_output=True,
                          text=True, timeout=30)
  except (OSError, subprocess.SubprocessError) as exc:
    return False, f"Could not write crontab: {exc}"
  if proc.returncode != 0:
    return False, (proc.stderr or "crontab update failed").strip()
  return True, f"Installed cron job (every {interval_minutes} min)."
