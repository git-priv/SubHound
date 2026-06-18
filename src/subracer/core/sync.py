# subracer.core.sync
#
# Synchronise a subtitle to a video with ffsubsync and judge the result against
# the configured accept/reject offset thresholds. This is the "sync-test" used at
# every stage of the per-video pipeline (embedded, existing, local DB, network):
# a candidate is accepted as soon as its synced offset is good enough.
#
# Ported from the parallel Subservient template (synchronize_subtitle_with_ffsubsync,
# calculate_subtitle_offset, apply_srt_offset), with the global state removed.

from __future__ import annotations

import enum
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .tools import ensure_tools_on_path, ffsubsync_command

# Matches an SRT cue timing line: "00:01:02,500 --> 00:01:05,000".
_SRT_TIME_LINE = re.compile(
  r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})$"
)
# Matches the start timestamp of a cue (for offset estimation).
_SRT_START = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->")


class Verdict(str, enum.Enum):
  # Outcome of judging a synced subtitle against the thresholds.
  ACCEPT = "accept"   # offset <= accept threshold: use without manual checking
  VERIFY = "verify"   # between thresholds: usable but flag for manual review
  REJECT = "reject"   # offset >= reject threshold: treat as drift, discard


@dataclass
class SyncResult:
  # Result of synchronising one subtitle against a video.
  success: bool                 # ffsubsync ran and produced an output file
  offset: float | None          # estimated applied offset in seconds
  output_path: Path | None      # the synchronised subtitle file
  error: str | None = None      # error detail when success is False


# Function Summary:
#    Return the start time (in seconds) of the first cue in an SRT file, used to
#    estimate how far ffsubsync shifted the subtitle.
#
#  Input (parameters):
#    path [Path]:  path to an SRT subtitle file
#
#  Output:
#    seconds [float | None]:  first cue start time in seconds, or None if none
#
# Example:
#    first_timestamp(Path("movie.srt"))  ->  62.5
def first_timestamp(path: Path) -> float | None:
  try:
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
      for line in fh:
        m = _SRT_START.search(line)
        if m:
          h, mn, s, ms = m.groups()
          return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0
  except OSError:
    return None
  return None


# Function Summary:
#    Estimate the offset between an original and a synchronised subtitle as the
#    absolute difference of their first cue start times.
#
#  Input (parameters):
#    original_path [Path]:     the subtitle before synchronisation
#    synchronized_path [Path]: the subtitle produced by ffsubsync
#
#  Output:
#    offset [float]:  absolute first-cue time difference in seconds (0.0 if unknown)
#
# Example:
#    subtitle_offset(Path("a.srt"), Path("a.synced.srt"))  ->  1.250
def subtitle_offset(original_path: Path, synchronized_path: Path) -> float:
  a = first_timestamp(original_path)
  b = first_timestamp(synchronized_path)
  if a is None or b is None:
    return 0.0
  return abs(a - b)


# Function Summary:
#    Classify a synced subtitle's offset against the accept/reject thresholds.
#
#  Input (parameters):
#    offset [float]:            estimated applied offset in seconds
#    accept_threshold [float]:  at/below this, accept without manual review
#    reject_threshold [float]:  at/above this, reject as drift
#
#  Output:
#    verdict [Verdict]:  ACCEPT, VERIFY, or REJECT
#
# Example:
#    classify_offset(0.02, 0.05, 2.5)  ->  Verdict.ACCEPT
def classify_offset(offset: float, accept_threshold: float, reject_threshold: float) -> Verdict:
  if offset <= accept_threshold:
    return Verdict.ACCEPT
  if offset >= reject_threshold:
    return Verdict.REJECT
  return Verdict.VERIFY


# Function Summary:
#    Synchronise a subtitle to a video using ffsubsync and report success plus
#    the estimated offset. Does not judge the result -- callers use
#    classify_offset() with their thresholds.
#
#  Input (parameters):
#    video_path [Path]:    the reference video file
#    subtitle_path [Path]: the subtitle to synchronise
#    output_path [Path]:   where to write the synchronised subtitle
#    timeout [int]:        seconds before ffsubsync is killed
#
#  Output:
#    result [SyncResult]:  success flag, offset, output path, optional error
#
# Example:
#    synchronize(Path("v.mkv"), Path("s.srt"), Path("s.synced.srt"))
#      ->  SyncResult(success=True, offset=0.8, output_path=Path("s.synced.srt"))
def synchronize(
  video_path: Path,
  subtitle_path: Path,
  output_path: Path,
  timeout: int = 600,
) -> SyncResult:
  if not video_path.exists():
    return SyncResult(False, None, None, f"video not found: {video_path}")
  if not subtitle_path.exists():
    return SyncResult(False, None, None, f"subtitle not found: {subtitle_path}")
  output_path.parent.mkdir(parents=True, exist_ok=True)
  ensure_tools_on_path()  # let ffsubsync find ffmpeg (system or bundled)
  cmd = [
    *ffsubsync_command(),
    str(video_path),
    "-i", str(subtitle_path),
    "-o", str(output_path),
  ]
  try:
    proc = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
  except subprocess.TimeoutExpired:
    return SyncResult(False, None, None, f"ffsubsync timed out after {timeout}s")
  except OSError as exc:
    return SyncResult(False, None, None, f"failed to run ffsubsync: {exc}")
  if proc.returncode != 0 or not output_path.exists():
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return SyncResult(False, None, None,
                      f"ffsubsync exit {proc.returncode}: {detail[-1] if detail else ''}")
  offset = subtitle_offset(subtitle_path, output_path)
  return SyncResult(True, offset, output_path)


# Function Summary:
#    Shift every timestamp in an SRT file by a fixed millisecond offset, writing
#    the result back in place. Used for manual offset correction. Negative
#    offsets move cues earlier (clamped at zero).
#
#  Input (parameters):
#    sub_path [Path]:   the SRT file to modify in place
#    ms_offset [int]:   milliseconds to add to every timestamp (may be negative)
#
#  Output:
#    ok [bool]:  True if the file was rewritten successfully
#
# Example:
#    apply_offset_ms(Path("movie.srt"), -500)  ->  True
def apply_offset_ms(sub_path: Path, ms_offset: int) -> bool:
  def shift(ts: str) -> str:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    total = max(0, (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms) + ms_offset)
    return (f"{total // 3600000:02}:{(total % 3600000) // 60000:02}:"
            f"{(total % 60000) // 1000:02},{total % 1000:03}")

  try:
    lines = sub_path.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
  except OSError:
    return False
  out: list[str] = []
  for line in lines:
    m = _SRT_TIME_LINE.match(line.strip())
    if m:
      out.append(f"{shift(m.group(1))} --> {shift(m.group(2))}\n")
    else:
      out.append(line)
  try:
    sub_path.write_text("".join(out), encoding="utf-8")
  except OSError:
    return False
  return True
