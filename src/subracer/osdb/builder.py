# subracer.osdb.builder
#
# Create and populate the local OpenSubtitles metadata database (subz_metadata),
# compatible with the milahu/opensubtitles-scraper schema plus an optional
# MovieHash column so our own-built indexes support exact hash matching.
#
# Typical flows:
#   * Ingest records parsed from the OpenSubtitles export (subtitles_all.txt.gz)
#     or copied from a milahu DB.
#   * language_split(): copy only the wanted languages into a smaller DB -- the
#     "shrink stored files" option the user requested.

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .index import METADATA_TABLE

# Columns of subz_metadata, in creation order. MovieHash is a subracer addition
# (the milahu builds may or may not have it) enabling hash-exact lookups.
_SCHEMA_COLUMNS = [
  ("IDSubtitle", "INTEGER PRIMARY KEY"),
  ("MovieName", "TEXT"),
  ("_MovieNameClean", "TEXT"),
  ("MovieYear", "INTEGER"),
  ("ISO639", "TEXT"),
  ("SubAddDate", "TEXT"),
  ("ImdbID", "TEXT"),
  ("SubSumCD", "TEXT"),
  ("MovieReleaseName", "TEXT"),
  ("SeriesSeason", "INTEGER"),
  ("SeriesEpisode", "INTEGER"),
  ("MovieKind", "TEXT"),
  ("MovieHash", "TEXT"),
]
_COLUMN_NAMES = [name for name, _ in _SCHEMA_COLUMNS]


# Function Summary:
#    Create the subz_metadata table (if absent) plus indexes used by lookups.
#
#  Input (parameters):
#    db_path [Path]:  destination SQLite DB path
#
#  Output:
#    path [Path]:  the database path created/initialized
#
# Example:
#    create_index(Path("subtitles_all.db"))  ->  PosixPath("subtitles_all.db")
def create_index(db_path: Path) -> Path:
  db_path.parent.mkdir(parents=True, exist_ok=True)
  cols = ", ".join(f"{name} {decl}" for name, decl in _SCHEMA_COLUMNS)
  with sqlite3.connect(db_path) as conn:
    conn.execute(f"CREATE TABLE IF NOT EXISTS {METADATA_TABLE} ({cols})")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_lang ON {METADATA_TABLE}(ISO639)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_name ON {METADATA_TABLE}(MovieName)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_year ON {METADATA_TABLE}(MovieYear)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_hash ON {METADATA_TABLE}(MovieHash)")
    conn.commit()
  return db_path


# Function Summary:
#    Insert/replace metadata records into the database. Each record is a mapping
#    of subz_metadata column names to values; missing columns default to NULL.
#
#  Input (parameters):
#    db_path [Path]:                  the metadata DB (created if needed)
#    records [Iterable[dict]]:        rows keyed by subz_metadata column names
#
#  Output:
#    count [int]:  number of rows written
#
# Example:
#    ingest(Path("db.sqlite"), [{"IDSubtitle": 1, "MovieName": "A", "ISO639": "en"}])  ->  1
def ingest(db_path: Path, records: Iterable[dict]) -> int:
  create_index(db_path)
  placeholders = ", ".join(f":{c}" for c in _COLUMN_NAMES)
  sql = f"INSERT OR REPLACE INTO {METADATA_TABLE} ({', '.join(_COLUMN_NAMES)}) VALUES ({placeholders})"
  count = 0
  with sqlite3.connect(db_path) as conn:
    for rec in records:
      params = {c: rec.get(c) for c in _COLUMN_NAMES}
      conn.execute(sql, params)
      count += 1
    conn.commit()
  return count


# Function Summary:
#    Copy only the rows for the wanted languages into a new (smaller) DB. This is
#    the language filter that keeps the local store small when the user only
#    wants certain languages.
#
#  Input (parameters):
#    src_db [Path]:                source metadata DB
#    dst_db [Path]:                destination DB (created/overwritten schema)
#    languages [Iterable[str]]:    ISO639 codes to keep (case-insensitive)
#
#  Output:
#    count [int]:  number of rows copied
#
# Example:
#    language_split(Path("all.db"), Path("en.db"), ["en"])  ->  1234
def language_split(src_db: Path, dst_db: Path, languages: Iterable[str]) -> int:
  langs = [l.lower() for l in languages if l.strip()]
  if not langs:
    return 0
  create_index(dst_db)
  cols = ", ".join(_COLUMN_NAMES)
  placeholders = ",".join("?" for _ in langs)
  count = 0
  with sqlite3.connect(src_db) as src, sqlite3.connect(dst_db) as dst:
    src.row_factory = sqlite3.Row
    # Only select columns that actually exist in the source (milahu builds may
    # lack MovieHash); fill the rest with NULL.
    existing = {r[1] for r in src.execute(f"PRAGMA table_info({METADATA_TABLE})")}
    select_cols = ", ".join(c if c in existing else f"NULL AS {c}" for c in _COLUMN_NAMES)
    rows = src.execute(
      f"SELECT {select_cols} FROM {METADATA_TABLE} WHERE LOWER(ISO639) IN ({placeholders})",
      langs,
    )
    insert = f"INSERT OR REPLACE INTO {METADATA_TABLE} ({cols}) VALUES ({', '.join('?' for _ in _COLUMN_NAMES)})"
    for row in rows:
      dst.execute(insert, [row[c] for c in _COLUMN_NAMES])
      count += 1
    dst.commit()
  return count
