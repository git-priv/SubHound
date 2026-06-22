# subhound.core.tools
#
# Discovery of the external command-line tools subhound shells out to. The app is
# self-contained: ffmpeg and ffprobe come from a system install when present, and
# otherwise from the bundled `static-ffmpeg` package (platform binaries fetched
# and cached on first use). ffsubsync is a Python dependency and is invoked as
# "<python> -m ffsubsync"; it locates ffmpeg via PATH, so ensure_tools_on_path()
# makes the resolved ffmpeg directory available to it.

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Cached (ffmpeg, ffprobe) resolution so we only probe / fetch once per process.
_resolved: tuple[str | None, str | None] | None = None


# Function Summary:
#    Fetch the bundled ffmpeg/ffprobe binaries from static-ffmpeg, downloading
#    and caching them on first use. Returns (None, None) if static-ffmpeg is
#    unavailable or the fetch fails (e.g. no network on first run).
#
#  Input (parameters):
#    (none)
#
#  Output:
#    paths [tuple[str|None, str|None]]:  (ffmpeg_path, ffprobe_path)
#
# Example:
#    _bundled()  ->  ("/home/priv/.local/.../ffmpeg", "/home/priv/.local/.../ffprobe")
def _bundled() -> tuple[str | None, str | None]:
  try:
    from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise
    ffmpeg_path, ffprobe_path = get_or_fetch_platform_executables_else_raise()
    return ffmpeg_path, ffprobe_path
  except Exception:
    return None, None


# Function Summary:
#    Resolve ffmpeg and ffprobe paths, preferring a system install and falling
#    back to the bundled static-ffmpeg binaries. Cached after the first call.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    paths [tuple[str|None, str|None]]:  (ffmpeg_path, ffprobe_path); either may
#                                        be None if unresolved
#
# Example:
#    _resolve()  ->  ("/usr/bin/ffmpeg", "/usr/bin/ffprobe")
def _resolve() -> tuple[str | None, str | None]:
  global _resolved
  if _resolved is not None:
    return _resolved
  system_ffmpeg = shutil.which("ffmpeg")
  system_ffprobe = shutil.which("ffprobe")
  if system_ffmpeg and system_ffprobe:
    _resolved = (system_ffmpeg, system_ffprobe)
    return _resolved
  # One or both missing -> get the bundled pair and fill the gaps.
  bundled_ffmpeg, bundled_ffprobe = _bundled()
  _resolved = (system_ffmpeg or bundled_ffmpeg, system_ffprobe or bundled_ffprobe)
  return _resolved


# Function Summary:
#    Return the path to ffmpeg (system or bundled), or None if unavailable.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    path [str | None]:  ffmpeg path, or None
#
# Example:
#    ffmpeg()  ->  "/usr/bin/ffmpeg"
def ffmpeg() -> str | None:
  return _resolve()[0]


# Function Summary:
#    Return the path to ffprobe (system or bundled), or None if unavailable.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    path [str | None]:  ffprobe path, or None
#
# Example:
#    ffprobe()  ->  "/usr/bin/ffprobe"
def ffprobe() -> str | None:
  return _resolve()[1]


# Function Summary:
#    Ensure the resolved ffmpeg/ffprobe directories are on PATH for the current
#    process, so child processes (notably ffsubsync) can find ffmpeg even when we
#    are using the bundled binaries. Idempotent.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    (none)
#
# Example:
#    ensure_tools_on_path()  ->  (ffmpeg's directory is prepended to os.environ["PATH"])
def ensure_tools_on_path() -> None:
  current = os.environ.get("PATH", "")
  parts = current.split(os.pathsep) if current else []
  changed = False
  for tool in (_resolve()):
    if not tool:
      continue
    directory = str(Path(tool).parent)
    if directory not in parts:
      parts.insert(0, directory)
      changed = True
  if changed:
    os.environ["PATH"] = os.pathsep.join(parts)


# Function Summary:
#    Build the command prefix used to run ffsubsync (a Python dependency invoked
#    via the current interpreter, so no console script needs to be on PATH).
#
#  Input (parameters):
#    (none)
#
#  Output:
#    cmd [list[str]]:  argv prefix, e.g. ["/venv/bin/python", "-m", "ffsubsync"]
#
# Example:
#    ffsubsync_command()  ->  ["/venv/bin/python", "-m", "ffsubsync"]
def ffsubsync_command() -> list[str]:
  return [sys.executable, "-m", "ffsubsync"]


@dataclass
class ToolStatus:
  # Availability of the external tools subhound uses, and where they came from.
  ffmpeg: str | None
  ffprobe: str | None
  ffsubsync: bool
  bundled: bool  # True if ffmpeg/ffprobe came from static-ffmpeg rather than PATH

  # Function Summary:
  #    Whether all tools required for extract+sync are available.
  #
  #  Input (parameters):
  #    self [ToolStatus]:  the status instance
  #
  #  Output:
  #    ok [bool]:  True if ffmpeg, ffprobe and ffsubsync are all available
  #
  # Example:
  #    check_tools().required_ok()  ->  True
  def required_ok(self) -> bool:
    return bool(self.ffmpeg and self.ffprobe and self.ffsubsync)

  # Function Summary:
  #    Names of any missing required tools, for user-facing warnings.
  #
  #  Input (parameters):
  #    self [ToolStatus]:  the status instance
  #
  #  Output:
  #    missing [list[str]]:  names of missing required tools (possibly empty)
  #
  # Example:
  #    check_tools().missing_required()  ->  ["ffprobe"]
  def missing_required(self) -> list[str]:
    missing = []
    if not self.ffmpeg:
      missing.append("ffmpeg")
    if not self.ffprobe:
      missing.append("ffprobe")
    if not self.ffsubsync:
      missing.append("ffsubsync")
    return missing


# Function Summary:
#    Probe the environment for all external tools subhound uses, resolving
#    bundled binaries if needed. Note: this may trigger a one-time download of
#    the static-ffmpeg binaries when no system ffmpeg/ffprobe is present.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    status [ToolStatus]:  discovered tool paths / availability
#
# Example:
#    check_tools()  ->  ToolStatus(ffmpeg="/usr/bin/ffmpeg", ffprobe="...", ffsubsync=True, bundled=False)
def check_tools() -> ToolStatus:
  try:
    import ffsubsync  # noqa: F401
    have_ffsubsync = True
  except ImportError:
    have_ffsubsync = False
  ffmpeg_path, ffprobe_path = _resolve()
  used_bundled = bool(
    (ffmpeg_path and not shutil.which("ffmpeg"))
    or (ffprobe_path and not shutil.which("ffprobe"))
  )
  return ToolStatus(
    ffmpeg=ffmpeg_path,
    ffprobe=ffprobe_path,
    ffsubsync=have_ffsubsync,
    bundled=used_bundled,
  )
