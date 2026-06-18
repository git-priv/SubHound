# subracer.config.settings
#
# Typed settings model for subracer. This replaces Subservient's hand-edited
# `.config` file: every field here is edited through the TUI, never by hand.
# Non-secret settings are persisted as TOML (see config/store.py); credentials
# are stored separately in an encrypted, hardware-keyed file (see config/secrets.py).

from __future__ import annotations

import enum
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any


class Source(str, enum.Enum):
  # The subtitle sources subracer can use, in the canonical fallback order.
  # Membership/order in Settings.source_order decides which are used and when.
  LOCAL_OSDB = "local_osdb"
  OPENSUBTITLES_COM = "opensubtitles_com"
  SUBSOURCE = "subsource"
  GESTDOWN = "gestdown"
  YIFY = "yify"
  PODNAPISI = "podnapisi"
  TVSUBTITLES = "tvsubtitles"


# Default fallback order, matching the project plan. Movie-only / TV-only
# sources are filtered per media type at run time (see providers/registry.py).
DEFAULT_SOURCE_ORDER: list[Source] = [
  Source.LOCAL_OSDB,
  Source.OPENSUBTITLES_COM,
  Source.SUBSOURCE,
  Source.GESTDOWN,
  Source.YIFY,
  Source.PODNAPISI,
  Source.TVSUBTITLES,
]


class OsdbMode(str, enum.Enum):
  # How the local OpenSubtitles DB feature behaves.
  OFF = "off"            # do not use a local DB at all
  METADATA = "metadata"  # build/keep the SQLite metadata index; fetch subs on demand
  MIRROR = "mirror"      # opt-in: full language-filtered local file mirror


# Valid Source string values, for tolerant config loading.
_SOURCE_VALUES: frozenset[str] = frozenset(s.value for s in Source)

# Sources that only make sense for a given media type. Gestdown proxies Addic7ed
# and TVsubtitles.net are TV-only (their lookups require show/season/episode);
# YIFY (yts-subs.com) is movies-only. Podnapisi handles both.
MOVIE_ONLY_SOURCES: frozenset[Source] = frozenset({Source.YIFY})
TV_ONLY_SOURCES: frozenset[Source] = frozenset({Source.GESTDOWN, Source.TVSUBTITLES})


@dataclass
class Settings:
  # The complete subracer configuration (non-secret). Field names mirror
  # Subservient's `.config` where applicable so behaviour is familiar.

  # --- Languages -------------------------------------------------------
  languages: list[str] = field(default_factory=lambda: ["en"])
  audio_track_languages: list[str] = field(default_factory=lambda: ["en", "ja"])

  # --- Sync thresholds (seconds) ---------------------------------------
  accept_offset_threshold: float = 0.05
  reject_offset_threshold: float = 2.5
  smart_sync: bool = True

  # --- Media handling --------------------------------------------------
  # When None, media type is auto-detected per file/folder. True/False force
  # series/movie mode respectively (manual override).
  series_mode: bool | None = None
  delete_extra_videos: bool = False
  extras_folder_name: str = "extras"
  preserve_forced_subtitles: bool = False
  preserve_unwanted_subtitles: bool = False

  # --- Search / download tuning ----------------------------------------
  max_search_results: int = 10
  top_downloads: int = 3
  download_retry_503: int = 6
  pause_seconds: int = 5

  # --- Discovery filters -----------------------------------------------
  skip_dirs: list[str] = field(default_factory=lambda: [
    "extra", "extras", "extra's", "featurettes", "bonus", "behindthescenes",
    "deletedscenes", "interviews", "makingof", "scenes", "trailer", "trailers",
    "sample", "samples", "other", "misc", "specials", "special_features",
    "documentary", "docs", "docu", "promo", "promos", "bloopers", "outtakes",
  ])
  unwanted_terms: list[str] = field(default_factory=lambda: [
    "sample", "unrated", "uncut", "720p", "1080p", "2160p", "480p", "4k", "uhd",
    "imax", "web", "webrip", "web-dl", "bluray", "brrip", "bdrip", "dvdrip",
    "hdrip", "hdtv", "remux", "x264", "x265", "h.264", "h.265", "hevc", "avc",
    "hdr", "hdr10", "dv", "sdr", "10bit", "8bit", "ddp", "dts", "aac", "ac3",
    "eac3", "truehd", "atmos", "flac", "5.1", "7.1", "2.0", "yts", "yify",
    "rarbg", "proper", "repack", "limited", "dubbed", "subbed", "multi",
  ])

  # --- Sources ---------------------------------------------------------
  source_order: list[Source] = field(default_factory=lambda: list(DEFAULT_SOURCE_ORDER))
  enabled_sources: list[Source] = field(default_factory=lambda: list(DEFAULT_SOURCE_ORDER))

  # --- OpenSubtitles.com -----------------------------------------------
  api_url: str = "https://api.opensubtitles.com/api/v1"
  # The user-agent OpenSubtitles requires consumers to set.
  user_agent: str = "subracer v0.1.0"

  # --- Local OSDB ------------------------------------------------------
  osdb_mode: OsdbMode = OsdbMode.METADATA
  osdb_languages: list[str] = field(default_factory=list)  # empty == use `languages`
  osdb_storage_path: str = ""  # empty == platformdirs data dir
  osdb_mirror_repo: str = "milahu/opensubtitles-scraper"  # GitHub repo for mirror torrent

  # --- Parallelism caps ------------------------------------------------
  max_concurrent_videos: int = 4
  max_concurrent_extract: int = 2  # ffmpeg is CPU/IO heavy
  max_concurrent_sync: int = 2     # ffsubsync is CPU heavy
  max_concurrent_search: int = 6   # network search across providers

  # Function Summary:
  #    Return the effective OSDB language filter: osdb_languages if set,
  #    otherwise fall back to the general `languages` list.
  #
  #  Input (parameters):
  #    self [Settings]:  the settings instance
  #
  #  Output:
  #    langs [list[str]]:  ISO language codes to keep in the local OSDB
  #
  # Example:
  #    Settings(languages=["en"], osdb_languages=[]).effective_osdb_languages()  ->  ["en"]
  def effective_osdb_languages(self) -> list[str]:
    return list(self.osdb_languages) if self.osdb_languages else list(self.languages)

  # Function Summary:
  #    Return the ordered list of sources that are both enabled and present in
  #    source_order, filtered to those valid for the given media type.
  #
  #  Input (parameters):
  #    media_type [str]:  "movie", "tv", or "unknown"; unknown excludes both
  #                       movie-only and tv-only sources (type-neutral only)
  #
  #  Output:
  #    sources [list[Source]]:  enabled sources to try, in fallback order
  #
  # Example:
  #    Settings().sources_for("tv")  ->  [LOCAL_OSDB, OPENSUBTITLES_COM, SUBSOURCE, GESTDOWN]
  def sources_for(self, media_type: str) -> list[Source]:
    enabled = set(self.enabled_sources)
    result: list[Source] = []
    for src in self.source_order:
      if src not in enabled:
        continue
      if media_type == "tv" and src in MOVIE_ONLY_SOURCES:
        continue
      if media_type == "movie" and src in TV_ONLY_SOURCES:
        continue
      if media_type == "unknown" and (src in MOVIE_ONLY_SOURCES or src in TV_ONLY_SOURCES):
        continue
      result.append(src)
    return result


# Function Summary:
#    Recursively convert a Settings dataclass into plain JSON/TOML-friendly
#    types (enums -> their string value, nested dataclasses -> dicts). Used by
#    the TOML store when persisting settings.
#
#  Input (parameters):
#    obj [Any]:  a Settings instance, nested dataclass, list, enum, or scalar
#
#  Output:
#    plain [Any]:  the same data using only dict/list/str/int/float/bool/None
#
# Example:
#    settings_to_dict(Settings())["osdb_mode"]  ->  "metadata"
def settings_to_dict(obj: Any) -> Any:
  if is_dataclass(obj) and not isinstance(obj, type):
    # Skip None-valued fields: TOML has no null type, and an absent key loads
    # back to the field's default (which is None where None is meaningful).
    return {
      f.name: settings_to_dict(getattr(obj, f.name))
      for f in fields(obj)
      if getattr(obj, f.name) is not None
    }
  if isinstance(obj, enum.Enum):
    return obj.value
  if isinstance(obj, (list, tuple)):
    return [settings_to_dict(v) for v in obj]
  if isinstance(obj, dict):
    return {k: settings_to_dict(v) for k, v in obj.items()}
  return obj


# Function Summary:
#    Build a Settings instance from a plain dict (as loaded from TOML),
#    coercing enum-valued fields and ignoring unknown keys so that older/newer
#    config files load without crashing.
#
#  Input (parameters):
#    data [dict]:  plain settings mapping (e.g. parsed TOML)
#
#  Output:
#    settings [Settings]:  a populated, type-coerced Settings instance
#
# Example:
#    settings_from_dict({"languages": ["nl"], "osdb_mode": "off"}).osdb_mode  ->  OsdbMode.OFF
def settings_from_dict(data: dict[str, Any]) -> Settings:
  known = {f.name: f for f in fields(Settings)}
  kwargs: dict[str, Any] = {}
  for name, f in known.items():
    if name not in data:
      continue
    value = data[name]
    if f.type == "OsdbMode" or name == "osdb_mode":
      kwargs[name] = OsdbMode(value)
    elif name in ("source_order", "enabled_sources"):
      # Skip unknown/removed source values (e.g. a retired "addic7ed") so older
      # config files still load.
      kwargs[name] = [Source(v) for v in value if v in _SOURCE_VALUES]
    else:
      kwargs[name] = value
  return Settings(**kwargs)
