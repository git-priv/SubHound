# subracer.osdb.index
#
# Query layer over a local OpenSubtitles metadata database. The schema matches
# the milahu/opensubtitles-scraper "subz_metadata" table so subracer works
# against a database the user built from that project:
#
#   subz_metadata(IDSubtitle, MovieName, _MovieNameClean, MovieYear, ISO639,
#                 SubAddDate, ImdbID, SubSumCD, MovieReleaseName,
#                 SeriesSeason, SeriesEpisode, MovieKind)
#
# Discovery is by title (+year) and, for TV, season/episode, filtered by language
# (ISO639). The actual subtitle bytes live in separate data DBs (see local_osdb).

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..core.hashing import opensubtitles_hash
from ..core.identify import MediaInfo

METADATA_TABLE = "subz_metadata"

_SELECT_COLUMNS = (
  "IDSubtitle, MovieName, _MovieNameClean, MovieYear, ISO639, SubAddDate, "
  "ImdbID, SubSumCD, MovieReleaseName, SeriesSeason, SeriesEpisode, MovieKind"
)


@dataclass
class SubRecord:
  # One subtitle metadata row from the local DB.
  id: int                  # IDSubtitle (a.k.a. num in the data DBs)
  language: str            # ISO639
  movie_name: str
  movie_year: int | None
  release_name: str
  imdb_id: str
  season: int | None
  episode: int | None
  kind: str                # MovieKind ("movie"/"episode"/...)


# Function Summary:
#    Convert a sqlite3.Row from subz_metadata into a SubRecord.
#
#  Input (parameters):
#    row [sqlite3.Row]:  a result row with the _SELECT_COLUMNS fields
#
#  Output:
#    record [SubRecord]:  the typed record
#
# Example:
#    _to_record(row).id  ->  10231
def _to_record(row: sqlite3.Row) -> SubRecord:
  def opt_int(v) -> int | None:
    try:
      return int(v) if v not in (None, "", 0) else (0 if v == 0 else None)
    except (TypeError, ValueError):
      return None

  season = opt_int(row["SeriesSeason"])
  episode = opt_int(row["SeriesEpisode"])
  return SubRecord(
    id=int(row["IDSubtitle"]),
    language=(row["ISO639"] or "").lower(),
    movie_name=row["MovieName"] or "",
    movie_year=opt_int(row["MovieYear"]),
    release_name=row["MovieReleaseName"] or "",
    imdb_id=str(row["ImdbID"] or ""),
    season=season if season else None,
    episode=episode if episode else None,
    kind=row["MovieKind"] or "",
  )


class LocalOsdbIndex:
  # Read-only query interface over a local subz_metadata database.

  # Function Summary:
  #    Open the metadata database for querying.
  #
  #  Input (parameters):
  #    metadata_db_path [Path]:  path to the subz_metadata SQLite DB
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    LocalOsdbIndex(Path("subtitles_all.db"))
  def __init__(self, metadata_db_path: Path) -> None:
    self.metadata_db_path = Path(metadata_db_path)

  # Function Summary:
  #    Whether the metadata database file exists.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    ok [bool]:  True if the DB file is present
  #
  # Example:
  #    index.available()  ->  True
  def available(self) -> bool:
    return self.metadata_db_path.exists()

  # Function Summary:
  #    Whether subz_metadata has a MovieHash column (it isn't in every milahu
  #    build). When present, we can do exact hash matching against the video.
  #
  #  Input (parameters):
  #    conn [sqlite3.Connection]:  an open connection to the metadata DB
  #
  #  Output:
  #    has [bool]:  True if a MovieHash column exists
  #
  # Example:
  #    self._has_hash_column(conn)  ->  False
  def _has_hash_column(self, conn: sqlite3.Connection) -> bool:
    try:
      cols = {r[1] for r in conn.execute(f"PRAGMA table_info({METADATA_TABLE})")}
    except sqlite3.Error:
      return False
    return "MovieHash" in cols

  # Function Summary:
  #    Find candidates by exact OpenSubtitles moviehash, when the DB carries a
  #    MovieHash column and the video file is hashable. This is the most reliable
  #    match (file-exact); callers fall back to title search to fill the limit.
  #
  #  Input (parameters):
  #    conn [sqlite3.Connection]:  open metadata connection
  #    moviehash [str]:            16-char hex moviehash of the video
  #    lang [str]:                 2-letter language code
  #    limit [int]:                maximum rows
  #
  #  Output:
  #    records [list[SubRecord]]:  hash-matched rows (possibly empty)
  #
  # Example:
  #    self._search_by_hash(conn, "8e245d9679d31e12", "en", 10)  ->  [SubRecord(...)]
  def _search_by_hash(self, conn: sqlite3.Connection, moviehash: str, lang: str, limit: int) -> list[SubRecord]:
    sql = f"""
      SELECT {_SELECT_COLUMNS}
      FROM {METADATA_TABLE}
      WHERE LOWER(ISO639) = :language AND LOWER(MovieHash) = :hash
      ORDER BY SubAddDate DESC, IDSubtitle DESC
      LIMIT :limit
    """
    try:
      rows = conn.execute(
        sql, {"language": lang.lower(), "hash": moviehash.lower(), "limit": limit}
      ).fetchall()
    except sqlite3.Error:
      return []
    return [_to_record(r) for r in rows]

  # Function Summary:
  #    Find subtitle candidates for a media item + language. When the DB has a
  #    MovieHash column and the video is hashable, exact hash matches come first
  #    (most reliable); the remaining slots are filled by a title match against
  #    MovieName / _MovieNameClean / MovieReleaseName, constrained by year and
  #    (for TV) season/episode, newest first.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video
  #    lang [str]:                2-letter language code
  #    limit [int]:               maximum rows to return
  #    video_path [Path | None]:  the video file, for exact hash matching
  #
  #  Output:
  #    records [list[SubRecord]]:  matching rows (hash matches first)
  #
  # Example:
  #    index.search(info, "en", 10, Path("Movie.mkv"))  ->  [SubRecord(id=...), ...]
  def search(
    self,
    media: MediaInfo,
    lang: str,
    limit: int = 10,
    video_path: Path | None = None,
  ) -> list[SubRecord]:
    if not self.available():
      return []
    title = (media.title_or_show or "").strip()
    try:
      with sqlite3.connect(self.metadata_db_path) as conn:
        conn.row_factory = sqlite3.Row
        results: list[SubRecord] = []
        seen: set[int] = set()

        # 1) Exact moviehash matches (only if the column exists and we can hash).
        if video_path is not None and self._has_hash_column(conn):
          try:
            moviehash = opensubtitles_hash(video_path)
          except OSError:
            moviehash = None
          if moviehash:
            for rec in self._search_by_hash(conn, moviehash, lang, limit):
              if rec.id not in seen:
                seen.add(rec.id)
                results.append(rec)

        # 2) Title-based fill for any remaining slots.
        if len(results) < limit and title:
          for rec in self._search_by_title(conn, media, lang, title, limit):
            if rec.id not in seen:
              seen.add(rec.id)
              results.append(rec)
              if len(results) >= limit:
                break
        return results[:limit]
    except sqlite3.Error:
      return []

  # Function Summary:
  #    Title-based candidate search (MovieName/_MovieNameClean/MovieReleaseName
  #    LIKE), constrained by language, year and, for TV, season/episode.
  #
  #  Input (parameters):
  #    conn [sqlite3.Connection]:  open metadata connection
  #    media [MediaInfo]:          the identified video
  #    lang [str]:                 2-letter language code
  #    title [str]:                the title/show to match
  #    limit [int]:                maximum rows
  #
  #  Output:
  #    records [list[SubRecord]]:  title-matched rows
  #
  # Example:
  #    self._search_by_title(conn, info, "en", "Inception", 10)  ->  [SubRecord(...)]
  def _search_by_title(
    self,
    conn: sqlite3.Connection,
    media: MediaInfo,
    lang: str,
    title: str,
    limit: int,
  ) -> list[SubRecord]:
    params: dict[str, object] = {
      "language": lang.lower(),
      "like": f"%{title.lower()}%",
      "year": media.year,
      "limit": limit,
    }
    series_filter = ""
    if media.media_type == "tv" and media.season is not None and media.episode is not None:
      params["season"] = media.season
      params["episode"] = media.episode
      # Accept exact match or unset (0/0) episodes, mirroring the milahu data.
      series_filter = (
        " AND ((SeriesSeason = 0 AND SeriesEpisode = 0) "
        "OR (SeriesSeason = :season AND SeriesEpisode = :episode))"
      )
    sql = f"""
      SELECT {_SELECT_COLUMNS}
      FROM {METADATA_TABLE}
      WHERE LOWER(ISO639) = :language
        AND (
          LOWER(COALESCE(_MovieNameClean, '')) LIKE :like
          OR LOWER(COALESCE(MovieName, '')) LIKE :like
          OR LOWER(COALESCE(MovieReleaseName, '')) LIKE :like
        )
        AND (:year IS NULL OR MovieYear = :year)
        {series_filter}
      ORDER BY SubAddDate DESC, IDSubtitle DESC
      LIMIT :limit
    """
    try:
      rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
      return []
    return [_to_record(r) for r in rows]
