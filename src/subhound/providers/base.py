# subhound.providers.base
#
# The uniform interface every external subtitle source implements. A provider
# turns a (MediaInfo, language) request into a list of downloadable Candidates,
# and downloads a chosen candidate to a file. Providers are synchronous (the
# orchestrator runs them in a worker pool, mirroring the Subservient template).
#
# Rate-limited providers expose a QuotaState and raise QuotaExceeded when their
# daily limit is hit, so the orchestrator can move the (video, lang) to the
# wait-list and build a per-source pool to retry once the quota resets.

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..core.identify import MediaInfo


@dataclass
class Candidate:
  # One downloadable subtitle returned by a provider's search().
  source: str                      # provider name (matches tried_sources / config Source)
  id: str                          # provider-specific identifier (for dedup/logging)
  language: str                    # 2-letter ISO code
  release_name: str = ""           # release/title string, for ranking against the video
  fmt: str = "srt"                 # subtitle format
  hearing_impaired: bool = False
  forced: bool = False
  rank: int = 0                    # provider-assigned relevance (higher = better)
  download_ref: str = ""           # URL or token used by download()
  meta: dict = field(default_factory=dict)


@dataclass
class QuotaState:
  # A rate-limited provider's current quota picture.
  remaining: int | None = None     # downloads left in the current window
  limit: int | None = None         # total allowance for the window
  reset_seconds: int | None = None # seconds until the window resets
  exhausted: bool = False          # True when no downloads remain


class QuotaExceeded(Exception):
  # Raised by a provider when its rate limit is reached for this window.

  # Function Summary:
  #    Construct a quota-exceeded error carrying the source and reset timing.
  #
  #  Input (parameters):
  #    source [str]:               the provider name that is exhausted
  #    reset_seconds [int|None]:   seconds until the quota resets, if known
  #    message [str]:              optional human-readable detail
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    raise QuotaExceeded("opensubtitles_com", reset_seconds=1800)
  def __init__(self, source: str, reset_seconds: int | None = None, message: str = "") -> None:
    self.source = source
    self.reset_seconds = reset_seconds
    super().__init__(message or f"{source} quota exceeded")


class Provider(ABC):
  # Base class for all subtitle sources.
  name: str = ""                   # stable identifier (matches config Source value)
  supports_movies: bool = True
  supports_tv: bool = True

  # Function Summary:
  #    Search the source for subtitle candidates for one media item + language.
  #
  #  Input (parameters):
  #    media [MediaInfo]:           the identified video
  #    lang [str]:                  2-letter language code to search for
  #    video_path [Path | None]:    the video file (for hash-based matching), if available
  #
  #  Output:
  #    candidates [list[Candidate]]:  downloadable candidates (best first)
  #
  # Example:
  #    provider.search(info, "en", Path("Movie.mkv"))  ->  [Candidate(...), ...]
  @abstractmethod
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    ...

  # Function Summary:
  #    Download a candidate's subtitle to a destination file.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate to fetch
  #    dest_path [Path]:       where to write the subtitle file
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on failure
  #
  # Example:
  #    provider.download(cand, Path("Movie.en.srt"))  ->  PosixPath("Movie.en.srt")
  @abstractmethod
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    ...

  # Function Summary:
  #    Current quota for rate-limited providers; None for unlimited sources.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    quota [QuotaState | None]:  the quota picture, or None if not rate-limited
  #
  # Example:
  #    provider.quota()  ->  QuotaState(remaining=18, limit=20, exhausted=False)
  def quota(self) -> QuotaState | None:
    return None

  # Function Summary:
  #    Release any network resources held by the provider.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    provider.close()
  def close(self) -> None:
    return None

  # Function Summary:
  #    Whether this provider applies to a given media type.
  #
  #  Input (parameters):
  #    media_type [str]:  "movie", "tv", or "unknown"
  #
  #  Output:
  #    ok [bool]:  True if the provider should be used for this media type
  #
  # Example:
  #    provider.supports("tv")  ->  True
  def supports(self, media_type: str) -> bool:
    if media_type == "tv":
      return self.supports_tv
    if media_type == "movie":
      return self.supports_movies
    # "unknown": only use sources valid for both movies and TV.
    return self.supports_movies and self.supports_tv
