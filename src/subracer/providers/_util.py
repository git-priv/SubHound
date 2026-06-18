# subracer.providers._util
#
# Small helpers shared by network providers: writing a subtitle out of a possibly
# zipped download, and mapping a 2-letter language code to the English language
# name some providers (e.g. Gestdown/Addic7ed) expect.

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
import zlib
from pathlib import Path

import pycountry

_SUB_SUFFIXES = (".srt", ".ass", ".ssa", ".vtt", ".sub")
# A valid subtitle should be at least this many bytes; smaller payloads are almost
# certainly an empty/truncated download rather than real subtitle text.
_MIN_SUBTITLE_BYTES = 32

_log = logging.getLogger("subracer.providers")


# Function Summary:
#    Sanity-check that a payload is plausibly subtitle text and not an HTML error
#    page, a redirect stub, or an empty/truncated download. This is a structural
#    integrity guard: providers don't supply content hashes, and HTTPS already
#    protects against in-flight corruption, so we validate the shape of the bytes
#    we actually received.
#
#  Input (parameters):
#    payload [bytes]:  the (already unzipped) subtitle bytes
#
#  Output:
#    ok [bool]:  True if the payload looks like a real subtitle file
#
# Example:
#    _looks_like_subtitle(b"1\n00:00:01,000 --> ...")  ->  True
def _looks_like_subtitle(payload: bytes) -> bool:
  if len(payload) < _MIN_SUBTITLE_BYTES:
    return False
  # Subtitle files are text; if it won't decode even leniently it isn't one.
  try:
    text = payload.decode("utf-8", errors="strict")
  except UnicodeDecodeError:
    text = payload.decode("latin-1", errors="replace")
  head = text.lstrip()[:256].lower()
  if head.startswith(("<!doctype", "<html", "<?xml", "{\"")):
    return False  # HTML error page or a JSON error envelope, not a subtitle
  # Accept the common subtitle dialects by a cheap structural marker.
  return ("-->" in text          # SRT / WebVTT cue timing
          or "dialogue:" in text.lower()  # ASS / SSA event line
          or "}{" in text)        # MicroDVD (.sub) frame markers


# Function Summary:
#    Write subtitle bytes to a destination file, transparently extracting the
#    first subtitle member when the bytes are a ZIP archive (YIFY/SubSource ship
#    zips); otherwise the bytes are written as-is. The download is integrity-
#    checked first: ZIP archives are CRC-verified and the resulting subtitle is
#    validated to actually look like subtitle text. Returns None (so the caller
#    moves on to the next candidate) if the payload is corrupt or implausible.
#
#  Input (parameters):
#    data [bytes]:      the downloaded bytes (a subtitle file or a zip of them)
#    dest_path [Path]:  where to write the subtitle
#
#  Output:
#    path [Path | None]:  the written file, or None if no valid subtitle was found
#
# Example:
#    write_subtitle_bytes(zip_bytes, Path("Movie.en.srt"))  ->  PosixPath("Movie.en.srt")
def write_subtitle_bytes(data: bytes, dest_path: Path) -> Path | None:
  payload = data
  if data[:2] == b"PK":  # ZIP magic
    try:
      with zipfile.ZipFile(io.BytesIO(data)) as zf:
        bad = zf.testzip()  # CRC-check every member; returns the first bad name
        if bad is not None:
          _log.warning("Discarding corrupt ZIP download (bad member %s)", bad)
          return None
        member = next(
          (n for n in zf.namelist() if n.lower().endswith(_SUB_SUFFIXES)), None)
        if member is None:
          return None
        payload = zf.read(member)
    except (zipfile.BadZipFile, zlib.error, ValueError, EOFError, OSError):
      # Any failure to read the archive (bad magic, broken central directory,
      # truncated member, CRC/zlib error) means a corrupt download -> discard.
      _log.warning("Discarding malformed/corrupt ZIP download")
      return None
  if not _looks_like_subtitle(payload):
    _log.warning("Discarding implausible subtitle download (%d bytes) for %s",
                 len(payload), dest_path.name)
    return None
  dest_path.parent.mkdir(parents=True, exist_ok=True)
  dest_path.write_bytes(payload)
  _log.debug("Wrote %s (%d bytes, sha256=%s)", dest_path.name, len(payload),
             hashlib.sha256(payload).hexdigest()[:16])
  return dest_path


# Function Summary:
#    Convert a 2-letter ISO 639-1 code to the English language name (e.g. "en" ->
#    "English"), which Gestdown/Addic7ed expect. Falls back to the title-cased
#    input when no mapping exists.
#
#  Input (parameters):
#    code [str]:  a 2-letter language code (or already a name)
#
#  Output:
#    name [str]:  the English language name
#
# Example:
#    language_name("nl")  ->  "Dutch"
def language_name(code: str) -> str:
  code = (code or "").strip()
  if not code:
    return ""
  if len(code) == 2:
    lang = pycountry.languages.get(alpha_2=code.lower())
    if lang is not None:
      return lang.name
  if len(code) == 3:
    lang = pycountry.languages.get(alpha_3=code.lower())
    if lang is not None:
      return lang.name
  return code.title()
