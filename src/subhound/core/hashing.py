# subhound.core.hashing
#
# File fingerprinting helpers:
#  - the OpenSubtitles "moviehash" used by OpenSubtitles.com for exact
#    hash-based subtitle matching, and
#  - a cheap (size, mtime_ns) fingerprint used by the results store to decide
#    whether a previously-processed video is unchanged and can be skipped.

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

# OpenSubtitles hashes the first and last 64 KiB of the file.
_CHUNK = 64 * 1024
_LONG = 8  # bytes per 64-bit little-endian word
_MOD = 2 ** 64


@dataclass(frozen=True)
class Fingerprint:
  # A cheap identity for a video file, used for results skip-logic.
  size: int       # file size in bytes
  mtime_ns: int   # modification time in nanoseconds


# Function Summary:
#    Compute the (size, mtime_ns) fingerprint of a file without reading content.
#
#  Input (parameters):
#    path [Path]:  path to the file
#
#  Output:
#    fp [Fingerprint]:  the file's size and nanosecond mtime
#
# Example:
#    fingerprint(Path("movie.mkv"))  ->  Fingerprint(size=734003200, mtime_ns=1700000000000000000)
def fingerprint(path: Path) -> Fingerprint:
  st = path.stat()
  return Fingerprint(size=st.st_size, mtime_ns=st.st_mtime_ns)


# Function Summary:
#    Compute the OpenSubtitles moviehash: 64-bit sum of the file size plus every
#    64-bit little-endian word in the first and last 64 KiB of the file. Returned
#    as a zero-padded 16-char lowercase hex string. Files smaller than 64 KiB
#    cannot be hashed and return None.
#
#  Input (parameters):
#    path [Path]:  path to the video file
#
#  Output:
#    moviehash [str | None]:  16-char hex hash, or None if the file is too small
#
# Example:
#    opensubtitles_hash(Path("movie.avi"))  ->  "8e245d9679d31e12"
def opensubtitles_hash(path: Path) -> str | None:
  size = path.stat().st_size
  if size < _CHUNK:
    return None
  value = size
  with path.open("rb") as fh:
    head = fh.read(_CHUNK)
    fh.seek(max(0, size - _CHUNK), os.SEEK_SET)
    tail = fh.read(_CHUNK)
  for chunk in (head, tail):
    for offset in range(0, _CHUNK, _LONG):
      (word,) = struct.unpack_from("<Q", chunk, offset)
      value = (value + word) % _MOD
  return f"{value:016x}"
