# subracer.osdb.local_osdb
#
# A Provider backed by the local OpenSubtitles database. Discovery uses the
# subz_metadata index (osdb/index.py); the actual subtitle bytes are resolved
# from milahu-style data DBs that store zstd-compressed SRT keyed by subtitle
# num: `SELECT srt_zstd FROM subtitles WHERE num = ?`.
#
# This is the first, network-free external source in the pipeline (tried after
# embedded + existing subs). If only the metadata DB is present (no data DBs),
# search() still yields candidates but download() returns None, so the
# orchestrator falls through to the online providers.

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..core.identify import MediaInfo
from ..providers.base import Candidate, Provider, QuotaState
from .index import LocalOsdbIndex

# Data-DB table/column holding the zstd-compressed SRT, keyed by subtitle num.
_DATA_TABLE = "subtitles"
_NUM_COLUMN = "num"
_BLOB_COLUMN = "srt_zstd"


class LocalOsdbProvider(Provider):
  name = "local_osdb"
  supports_movies = True
  supports_tv = True

  # Function Summary:
  #    Build the provider over a metadata DB and zero or more data DBs.
  #
  #  Input (parameters):
  #    metadata_db_path [Path]:        path to the subz_metadata DB (discovery)
  #    data_db_paths [list[Path]|None]: data DBs holding zstd SRT blobs (download)
  #    max_results [int]:              maximum candidates returned per search
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    LocalOsdbProvider(Path("subtitles_all.db"), [Path("shard1.db")])
  def __init__(
    self,
    metadata_db_path: Path,
    data_db_paths: list[Path] | None = None,
    max_results: int = 10,
  ) -> None:
    self.index = LocalOsdbIndex(Path(metadata_db_path))
    self.data_db_paths = [Path(p) for p in (data_db_paths or [])]
    self.max_results = max_results

  # Function Summary:
  #    Whether the metadata database is present (the provider is usable).
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    ok [bool]:  True if the metadata DB exists
  #
  # Example:
  #    provider.available()  ->  True
  def available(self) -> bool:
    return self.index.available()

  # Function Summary:
  #    Search the local metadata index for subtitle candidates (hash-exact first
  #    when possible, then title), returning them as Candidates.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video
  #    lang [str]:                2-letter language code
  #    video_path [Path | None]:  the video file (enables hash matching)
  #
  #  Output:
  #    candidates [list[Candidate]]:  local candidates (download_ref = subtitle id)
  #
  # Example:
  #    provider.search(info, "en", Path("Movie.mkv"))  ->  [Candidate(...), ...]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    records = self.index.search(media, lang, self.max_results, video_path)
    candidates: list[Candidate] = []
    for rec in records:
      candidates.append(Candidate(
        source=self.name,
        id=str(rec.id),
        language=rec.language or lang,
        release_name=rec.release_name or rec.movie_name,
        download_ref=str(rec.id),
        meta={"imdb_id": rec.imdb_id, "movie_name": rec.movie_name,
              "year": rec.movie_year, "kind": rec.kind},
      ))
    return candidates

  # Function Summary:
  #    Resolve and write a candidate's subtitle from the data DBs, decompressing
  #    the zstd SRT blob. Returns None when no data DB holds that subtitle (e.g.
  #    metadata-only install).
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate (download_ref = subtitle num)
  #    dest_path [Path]:       where to write the .srt
  #
  #  Output:
  #    path [Path | None]:  the written file, or None if the blob was not found
  #
  # Example:
  #    provider.download(cand, Path("Movie.en.srt"))  ->  PosixPath("Movie.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    try:
      num = int(candidate.download_ref)
    except (TypeError, ValueError):
      return None
    blob = self._find_blob(num)
    if blob is None:
      return None
    srt = self._decompress(blob)
    if srt is None:
      return None
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(srt)
    return dest_path

  # Function Summary:
  #    Look up the zstd SRT blob for a subtitle num across all configured data DBs.
  #
  #  Input (parameters):
  #    num [int]:  the subtitle id / num
  #
  #  Output:
  #    blob [bytes | None]:  the compressed blob, or None if not found
  #
  # Example:
  #    self._find_blob(555)  ->  b"(\xb5/\xfd..."
  def _find_blob(self, num: int) -> bytes | None:
    for db_path in self.data_db_paths:
      if not db_path.exists():
        continue
      try:
        with sqlite3.connect(db_path) as conn:
          row = conn.execute(
            f"SELECT {_BLOB_COLUMN} FROM {_DATA_TABLE} WHERE {_NUM_COLUMN} = ? LIMIT 1",
            (num,),
          ).fetchone()
      except sqlite3.Error:
        continue
      if row and row[0] is not None:
        return bytes(row[0])
    return None

  # Function Summary:
  #    Decompress a zstd blob into SRT bytes. zstandard is imported lazily so the
  #    dependency is only required when actually reading a local data DB.
  #
  #  Input (parameters):
  #    blob [bytes]:  the zstd-compressed SRT
  #
  #  Output:
  #    srt [bytes | None]:  decompressed bytes, or None on failure / missing dep
  #
  # Example:
  #    self._decompress(blob)  ->  b"1\n00:00:01,000 --> ..."
  def _decompress(self, blob: bytes) -> bytes | None:
    try:
      import zstandard
    except ImportError:
      return None
    try:
      return zstandard.ZstdDecompressor().decompress(blob)
    except (zstandard.ZstdError, ValueError):
      return None

  # Function Summary:
  #    Local DB is not rate-limited.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    quota [QuotaState | None]:  always None (unlimited)
  #
  # Example:
  #    provider.quota()  ->  None
  def quota(self) -> QuotaState | None:
    return None
