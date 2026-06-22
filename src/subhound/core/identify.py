# subhound.core.identify
#
# Identify a video file into structured media info (movie vs TV, show/title,
# year, season, episode) and build a clean subtitle-search query.
#
# Filename parsing is delegated to PTT (parsett), a well-tested parse-torrent-
# title port that robustly strips release-group/codec/resolution noise and pulls
# title/year/seasons/episodes -- including tricky cases like "Blade Runner 2049
# 2017" (title="Blade Runner 2049", year=2017). PTT is NOT directory-aware, so
# subhound adds a layer on top: when the filename lacks a season or a usable show
# name, it inspects the parent directories (typical layout: show/SXX/episode.ext).
#
# When the media type still can't be resolved, media_type is "unknown" and
# `note` explains why; callers should log it and keep going (see orchestrator).

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from PTT import parse_title

MOVIE = "movie"
TV = "tv"
UNKNOWN = "unknown"

# A directory name that denotes a season, e.g. "S01", "Season 1", "season_03".
# Kept strict so show folders like "Stranger Things 4" are not misread.
_SEASON_DIR_RE = re.compile(r"^s(?:eason)?[\s._-]*(\d{1,2})$", re.IGNORECASE)
# Directory/filename stems too generic to use as a show/movie title.
_GENERIC_DIRS = {
  "video", "videos", "movie", "movies", "film", "films", "tv", "series",
  "show", "shows", "media", "downloads", "torrents", "complete", "season",
  "seasons", "misc", "other", "new folder", "sample", "samples", "extras",
}


@dataclass
class MediaInfo:
  # Structured result of identifying a video file.
  media_type: str          # "movie", "tv", or "unknown"
  title_or_show: str       # cleaned movie title or show name (year excluded)
  query: str               # search query string for providers
  year: int | None = None
  season: int | None = None
  episode: int | None = None
  note: str = ""           # explanation when media_type == "unknown"


# A throwaway resolution token appended before parsing. PTT truncates *bare*
# hyphenated titles ("The X-Files" -> "The X", "Spider-Man" -> "Spider"), but
# parses them correctly when a release tag follows. Appending a sentinel makes
# PTT treat the whole leading text as the title. We never use resolution, so this
# has no downside.
_PTT_SENTINEL = " 1080p"


# Function Summary:
#    Parse a string with PTT and return a normalized (title, year, season,
#    episode) tuple, taking the first season/episode when PTT returns lists. A
#    sentinel resolution tag is appended first so clean hyphenated titles are not
#    truncated (PTT bug: "The X-Files" -> "The X" when bare).
#
#  Input (parameters):
#    text [str]:  a filename or directory name
#
#  Output:
#    parsed [tuple[str, int|None, int|None, int|None]]:
#                 (title, year, season, episode); any element may be empty/None
#
# Example:
#    _ptt("The X-Files (1993)")  ->  ("The X-Files", 1993, None, None)
def _ptt(text: str) -> tuple[str, int | None, int | None, int | None]:
  # Parse twice: a bare parse (correct for season/episode/year and for junk-only
  # names like "1080p"), and a sentinel-tagged parse that recovers hyphenated
  # titles PTT truncates when bare. Accept the tagged title only when it strictly
  # EXTENDS a non-empty bare title (so junk-only names stay empty rather than
  # promoting a stray resolution/codec token to the title).
  bare = parse_title(text)
  tagged = parse_title(text + _PTT_SENTINEL)
  bare_title = (bare.get("title") or "").strip()
  tagged_title = (tagged.get("title") or "").strip()
  if bare_title and tagged_title.startswith(bare_title) and len(tagged_title) > len(bare_title):
    title = tagged_title
  else:
    title = bare_title
  seasons = bare.get("seasons") or []
  episodes = bare.get("episodes") or []
  return (
    title,
    bare.get("year"),
    seasons[0] if seasons else None,
    episodes[0] if episodes else None,
  )


# Function Summary:
#    Find a season number from the parent directories by matching strict season-
#    folder names (e.g. "S01", "Season 2"), nearest folder first.
#
#  Input (parameters):
#    parents [list[str]]:  directory names from nearest to farthest
#
#  Output:
#    season [int | None]:  season number from a season folder, or None
#
# Example:
#    season_from_dirs(["Season 03", "Breaking Bad"])  ->  3
def season_from_dirs(parents: list[str]) -> int | None:
  for raw in parents:
    m = _SEASON_DIR_RE.match(raw.strip())
    if m:
      return int(m.group(1))
  return None


# Function Summary:
#    Pick a usable show/movie name (and any year) from the parent directories,
#    skipping season folders and generic container folders. Names are parsed with
#    PTT so trailing year/quality tags are cleaned off.
#
#  Input (parameters):
#    parents [list[str]]:  directory names from nearest to farthest
#
#  Output:
#    result [tuple[str, int|None]]:  (clean name, year) or ("", None) if none fit
#
# Example:
#    name_from_dirs(["Season 01", "Breaking Bad", "TV"])  ->  ("Breaking Bad", None)
def name_from_dirs(parents: list[str]) -> tuple[str, int | None]:
  for raw in parents:
    if _SEASON_DIR_RE.match(raw.strip()) or raw.strip().lower() in _GENERIC_DIRS:
      continue
    title, year, _, _ = _ptt(raw)
    if title and title.lower() not in _GENERIC_DIRS:
      return title, year
  return "", None


# Function Summary:
#    For the canonical show/SXX/episode layout, return the show name (and year)
#    taken from the directory directly ABOVE the season folder, which is more
#    authoritative than the filename (whose "title" is often just the episode
#    title, e.g. "e01 Pilot").
#
#  Input (parameters):
#    parents [list[str]]:  directory names from nearest to farthest
#
#  Output:
#    result [tuple[str, int|None]]:  (show name, year) or ("", None) if not found
#
# Example:
#    show_above_season_folder(["s01", "The X-Files (1993)"])  ->  ("The X-Files", 1993)
def show_above_season_folder(parents: list[str]) -> tuple[str, int | None]:
  for i, raw in enumerate(parents):
    if _SEASON_DIR_RE.match(raw.strip()):
      if i + 1 < len(parents):
        title, year, _, _ = _ptt(parents[i + 1])
        if title and title.lower() not in _GENERIC_DIRS:
          return title, year
      return "", None
  return "", None


# Function Summary:
#    Find a release year from the parent directories (nearest first).
#
#  Input (parameters):
#    parents [list[str]]:  directory names from nearest to farthest
#
#  Output:
#    year [int | None]:  the first year found in a folder name, or None
#
# Example:
#    year_from_dirs(["The Dark Knight (2008)"])  ->  2008
def year_from_dirs(parents: list[str]) -> int | None:
  for raw in parents:
    _, year, _, _ = _ptt(raw)
    if year:
      return year
  return None


# Function Summary:
#    Identify a video file end-to-end: parse the filename with PTT, then use the
#    surrounding folders to recover a missing season or show/movie name. Returns
#    media_type "unknown" (with a note) when it genuinely cannot be resolved.
#
#  Input (parameters):
#    path [Path]:                       the video file path (parents inspected)
#    series_mode [bool | None]:         force tv (True) / movie (False) / auto (None)
#    unwanted_terms [list[str] | None]: accepted for API compatibility; unused
#                                       (PTT strips release noise itself)
#    folder_levels [int]:               how many parent folders to consider (>=2)
#
#  Output:
#    info [MediaInfo]:  the structured identification result
#
# Example:
#    identify(Path("Breaking Bad/S01/E01 - Pilot.mkv"), None).media_type  ->  "tv"
def identify(
  path: Path,
  series_mode: bool | None,
  unwanted_terms: list[str] | None = None,
  folder_levels: int = 3,
) -> MediaInfo:
  # Parse the stem (extension stripped): the _ptt sentinel is appended after the
  # text, so a trailing ".mkv" would otherwise be mis-read as the title.
  title, year, season, episode = _ptt(path.stem)
  parents = [p.name for p in path.parents][:folder_levels]

  forced_tv = series_mode is True
  forced_movie = series_mode is False

  # Recover season from a parent season-folder when an episode is known.
  if episode is not None and season is None:
    season = season_from_dirs(parents)

  # --- Decide media type --------------------------------------------------
  if forced_movie:
    media_type = MOVIE
  elif forced_tv or episode is not None:
    media_type = TV
  else:
    # No episode from the filename: a season folder above still implies TV.
    dir_season = season_from_dirs(parents)
    if dir_season is not None:
      media_type = TV
      season = season if season is not None else dir_season
    else:
      media_type = MOVIE  # provisional; may become UNKNOWN below

  # --- TV branch ----------------------------------------------------------
  if media_type == TV:
    # The folder directly above a season folder is the most reliable show name
    # (the filename's "title" is often just the episode title). Prefer it.
    above_title, above_year = show_above_season_folder(parents)
    if above_title:
      title = above_title
      year = year or above_year
    elif not title or title.lower() in _GENERIC_DIRS:
      folder_title, folder_year = name_from_dirs(parents)
      title = folder_title
      year = year or folder_year
    if year is None:
      year = year_from_dirs(parents)
    # When a season folder makes us certain it's TV but PTT found no episode,
    # try to recover it from a trailing number in the filename (e.g. "Space 09"
    # -> E9). Ignore a trailing 4-digit year.
    if episode is None and season is not None:
      tail = re.search(r"(?<!\d)(\d{1,3})(?!\d)\s*$", path.stem)
      if tail and not re.search(r"(19|20)\d{2}\s*$", path.stem):
        episode = int(tail.group(1))
    # Collapse a 3-digit "NEE" episode where the leading digit matches the
    # season (e.g. "104" inside Season 1 -> E4).
    if episode is not None and episode >= 100 and season is not None and episode // 100 == season:
      episode = episode % 100
    if not title:
      return MediaInfo(
        UNKNOWN, "", path.stem, year, season, episode,
        note=f"TV episode detected (season={season}, episode={episode}) but the "
             f"show name could not be determined from filename or folders: {path}",
      )
    if episode is None:
      return MediaInfo(
        UNKNOWN, title, path.stem, year, season, None,
        note=f"TV show '{title}' detected but the episode number could not be "
             f"determined from filename or folders: {path}",
      )
    # Absolute-numbered releases (common for anime) carry an episode but no
    # season; default such cases to season 1.
    if season is None:
      season = 1
    query = f"{title} S{season:02d}E{episode:02d}"
    return MediaInfo(TV, title, query, year, season, episode)

  # --- Movie branch -------------------------------------------------------
  if not title or title.lower() in _GENERIC_DIRS:
    folder_title, folder_year = name_from_dirs(parents)
    title = folder_title
    year = year or folder_year
  elif year is None:
    year = year_from_dirs(parents)
  if not title:
    return MediaInfo(
      UNKNOWN, "", path.stem, year, None, None,
      note=f"Could not determine movie vs TV: no season/episode markers and no "
           f"usable title from filename or folders: {path}",
    )
  query = f"{title} {year}" if year else title
  return MediaInfo(MOVIE, title, query, year, None, None)
