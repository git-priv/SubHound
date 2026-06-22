# subhound.core.subtitle_convert
#
# Normalises any text subtitle (SubRip, ASS/SSA, WebVTT, MicroDVD) to clean,
# UTF-8 SubRip (.srt). SRT is the most broadly supported sidecar format across
# Plex / Emby / Jellyfin / VLC (delivered as text, no transcode), so every
# subtitle the pipeline obtains -- downloaded, found on disk, or extracted -- is
# converted to SRT before syncing and placement.
#
# Time-based formats (SRT/ASS/SSA/VTT) carry absolute timestamps, so conversion
# preserves timing exactly (only styling is dropped). MicroDVD is frame-based, so
# it needs the video's frame rate to time its cues; callers pass an fps_fn that is
# only invoked for MicroDVD input.

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pysubs2

# Default frame rate used for MicroDVD only when the real one can't be probed.
_DEFAULT_FPS = 23.976


# Function Summary:
#    Decode raw subtitle bytes to text, tolerating non-UTF-8 encodings (BOM,
#    legacy single-byte, chardet-detected) so we never reject a real subtitle for
#    its encoding alone.
#
#  Input (parameters):
#    raw [bytes]:  the raw subtitle file bytes
#
#  Output:
#    text [str]:  the decoded text (lossy as a last resort, never raises)
#
# Example:
#    _decode(b"\xef\xbb\xbf1\n...")  ->  "1\n..."
def _decode(raw: bytes) -> str:
  for enc in ("utf-8-sig", "utf-8"):
    try:
      return raw.decode(enc)
    except UnicodeDecodeError:
      pass
  try:
    import chardet  # bundled transitively via ffsubsync
    guess = chardet.detect(raw) or {}
    enc = guess.get("encoding")
    # Trust chardet only when it's reasonably sure; on short inputs it guesses
    # exotic encodings with low confidence and mangles plain Western text.
    if enc and (guess.get("confidence") or 0) >= 0.6:
      return raw.decode(enc, errors="replace")
  except Exception:  # noqa: BLE001 - detection is best-effort
    pass
  # cp1252 (a Windows superset of latin-1) is the most common legacy subtitle
  # encoding and decodes every byte, so it's a safe final fallback.
  return raw.decode("cp1252", errors="replace")


# Function Summary:
#    Identify a subtitle's format from its content (not its extension, which may
#    be wrong), returning the pysubs2 format name to parse it with.
#
#  Input (parameters):
#    text [str]:  the decoded subtitle text
#
#  Output:
#    fmt [str | None]:  "vtt" | "ass" | "microdvd" | "srt", or None if not a
#                       recognised text subtitle
#
# Example:
#    detect_subtitle_format("WEBVTT\n\n00:00:01.000 --> ...")  ->  "vtt"
def detect_subtitle_format(text: str) -> str | None:
  head = text.lstrip()
  low = head[:512].lower()
  if low.startswith("webvtt"):
    return "vtt"
  if "[script info]" in low or "[v4+ styles]" in low or "[v4 styles]" in low:
    return "ass"  # pysubs2's Substation reader handles both ASS and SSA
  if re.match(r"^\s*\{\d+\}\{\d+\}", head):
    return "microdvd"
  if "-->" in text:
    return "srt"
  return None


# Function Summary:
#    Convert a subtitle file to clean UTF-8 SubRip (.srt), detecting the source
#    format from its bytes. ASS/SSA/VTT convert with timing preserved (styling is
#    dropped); MicroDVD is timed using the frame rate from fps_fn (probed only for
#    that format). Returns None if the file isn't a parseable text subtitle, so
#    the caller can move on to the next candidate.
#
#  Input (parameters):
#    src [Path]:                            the source subtitle file (any format)
#    dest [Path]:                           destination .srt path to write
#    fps_fn [Callable[[], float|None]|None]: supplies the video's fps for MicroDVD
#
#  Output:
#    path [Path | None]:  the written .srt, or None on failure
#
# Example:
#    normalize_to_srt(Path("a.ass"), Path("a.srt"))  ->  PosixPath("a.srt")
def normalize_to_srt(
  src: Path,
  dest: Path,
  fps_fn: Callable[[], float | None] | None = None,
) -> Path | None:
  try:
    raw = Path(src).read_bytes()
  except OSError:
    return None
  text = _decode(raw)
  fmt = detect_subtitle_format(text)
  if fmt is None:
    return None
  fps = _DEFAULT_FPS
  if fmt == "microdvd" and fps_fn is not None:
    probed = fps_fn()
    if probed and probed > 0:
      fps = probed
  try:
    subs = pysubs2.SSAFile.from_string(text, fps=fps, format_=fmt)
  except Exception:  # noqa: BLE001 - any parse failure means "not usable"
    return None
  if len(subs) == 0:
    return None
  dest.parent.mkdir(parents=True, exist_ok=True)
  dest.write_text(subs.to_string("srt"), encoding="utf-8")
  return dest
