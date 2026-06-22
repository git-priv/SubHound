# subhound.providers.subsource
#
# Provider for SubSource (api.subsource.net) v1 JSON API. No per-day quota.
#
# Flow:
#   GET /api/v1/movies/search?query={title}        -> movies[] (movie_id, type, ...)
#   GET /api/v1/subtitles?movieId=&language=&limit -> subtitles[] (subtitle_id, ...)
#   GET /api/v1/subtitles/{subtitleId}/download    -> zip/srt
#
# SubSource models TV seasons as separate "movie" entries carrying a `season`
# field, so for TV we pick the entry matching the season.

from __future__ import annotations

from pathlib import Path

import httpx

from ..core.identify import MediaInfo
from ._util import language_name, write_subtitle_bytes
from .base import Candidate, Provider

DEFAULT_BASE_URL = "https://api.subsource.net"


# Function Summary:
#    Pull the list payload out of a SubSource response envelope, tolerating the
#    several wrapper key names the API has used (results/data/list/items/...).
#
#  Input (parameters):
#    payload [dict]:  the parsed JSON response
#    extra_keys [tuple[str, ...]]:  additional candidate keys to check first
#
#  Output:
#    items [list]:  the list of records (possibly empty)
#
# Example:
#    _envelope_list({"results": [1, 2]})  ->  [1, 2]
def _envelope_list(payload: dict, *extra_keys: str) -> list:
  for key in (*extra_keys, "results", "data", "list", "items", "subtitles", "movies"):
    value = payload.get(key)
    if isinstance(value, list):
      return value
  return []


class SubSourceProvider(Provider):
  name = "subsource"
  supports_movies = True
  supports_tv = True

  # Function Summary:
  #    Construct the provider with an injectable HTTP client (for testing).
  #
  #  Input (parameters):
  #    base_url [str]:               API base URL
  #    user_agent [str]:             User-Agent header (the site 403s blank bots)
  #    max_results [int]:            maximum subtitles to request
  #    client [httpx.Client|None]:   HTTP client (a default is created if None)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    SubSourceProvider()
  def __init__(
    self,
    base_url: str = DEFAULT_BASE_URL,
    user_agent: str = "subhound",
    max_results: int = 10,
    client: httpx.Client | None = None,
  ) -> None:
    self.base_url = base_url.rstrip("/")
    self.user_agent = user_agent
    self.max_results = max_results
    self._client = client or httpx.Client(timeout=30.0, follow_redirects=True)

  # Function Summary:
  #    Search SubSource for subtitle candidates: resolve the movie/season, then
  #    fetch its subtitles for the language.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video
  #    lang [str]:                2-letter language code
  #    video_path [Path | None]:  unused (no hash matching)
  #
  #  Output:
  #    candidates [list[Candidate]]:  matching subtitles
  #
  # Example:
  #    provider.search(MediaInfo("movie","Inception","",2010), "en")  ->  [Candidate(...)]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    movie_id = self._find_movie_id(media)
    if movie_id is None:
      return []
    params = {"movieId": movie_id, "language": language_name(lang), "limit": self.max_results}
    try:
      resp = self._client.get(
        f"{self.base_url}/api/v1/subtitles", headers=self._headers(), params=params)
    except httpx.HTTPError:
      return []
    if resp.status_code != 200:
      return []
    wanted = {lang.lower(), language_name(lang).lower()}
    candidates: list[Candidate] = []
    for sub in _envelope_list(resp.json() or {}):
      sub_id = sub.get("subtitle_id") or sub.get("subtitleId") or sub.get("id")
      if sub_id is None:
        continue
      sub_lang = (sub.get("language") or "").lower()
      if sub_lang and sub_lang not in wanted:
        continue
      release = sub.get("release_info") or sub.get("releaseInfo") or []
      candidates.append(Candidate(
        source=self.name,
        id=str(sub_id),
        language=lang,
        release_name=", ".join(release) if isinstance(release, list) else str(release),
        hearing_impaired=bool(sub.get("hearing_impaired") or sub.get("hearingImpaired")),
        forced=bool(sub.get("foreign_parts") or sub.get("foreignParts")),
        rank=int(sub.get("downloads", 0) or 0),
        download_ref=str(sub_id),
      ))
    return candidates

  # Function Summary:
  #    Resolve a media item to a SubSource movie_id, matching type, year (movies)
  #    and season (TV) where possible, else the first result.
  #
  #  Input (parameters):
  #    media [MediaInfo]:  the identified video
  #
  #  Output:
  #    movie_id [int | None]:  the chosen movie id, or None if no results
  #
  # Example:
  #    self._find_movie_id(MediaInfo("movie","Inception","",2010))  ->  12345
  def _find_movie_id(self, media: MediaInfo) -> int | None:
    if not media.title_or_show:
      return None
    try:
      resp = self._client.get(
        f"{self.base_url}/api/v1/movies/search",
        headers=self._headers(), params={"query": media.title_or_show})
    except httpx.HTTPError:
      return None
    if resp.status_code != 200:
      return None
    movies = _envelope_list(resp.json() or {})
    if not movies:
      return None

    def movie_id_of(m: dict):
      return m.get("movie_id") or m.get("movieId") or m.get("id")

    is_tv = media.media_type == "tv"
    best = None
    for m in movies:
      m_type = (m.get("type") or "").lower()
      if is_tv:
        if m_type in ("tv", "series", "tvseries", "show"):
          season = m.get("season")
          if media.season is None or season is None or int(season) == media.season:
            best = m
            break
      else:
        year = m.get("release_year") or m.get("releaseYear") or m.get("year")
        if m_type in ("movie", "film", ""):
          if media.year is None or (year and int(year) == media.year):
            best = m
            break
    chosen = best or movies[0]
    mid = movie_id_of(chosen)
    return int(mid) if mid is not None else None

  # Function Summary:
  #    Download a candidate subtitle (a zip or srt) and write the SRT out.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate (download_ref = subtitle id)
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on failure
  #
  # Example:
  #    provider.download(cand, Path("Inception.en.srt"))  ->  PosixPath("Inception.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    url = f"{self.base_url}/api/v1/subtitles/{candidate.download_ref}/download"
    try:
      resp = self._client.get(url, headers=self._headers())
    except httpx.HTTPError:
      return None
    if resp.status_code != 200 or not resp.content:
      return None
    return write_subtitle_bytes(resp.content, dest_path)

  # Function Summary:
  #    Standard request headers (a real User-Agent; the site rejects blank ones).
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    headers [dict[str, str]]:  HTTP headers
  #
  # Example:
  #    self._headers()["User-Agent"]  ->  "subhound"
  def _headers(self) -> dict[str, str]:
    return {"User-Agent": self.user_agent, "Accept": "application/json"}

  # Function Summary:
  #    Close the HTTP client.
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
    self._client.close()
