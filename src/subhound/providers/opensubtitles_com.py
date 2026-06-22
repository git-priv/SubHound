# subhound.providers.opensubtitles_com
#
# Provider for the OpenSubtitles.com REST API. Works unauthenticated (a few
# downloads/day per IP) or authenticated via API key + username/password (JWT),
# which raises the daily download allowance. Tracks the remaining quota reported
# by the download endpoint and raises QuotaExceeded when the limit is hit, so the
# orchestrator can wait-list the item and retry after the reset.
#
# Auth model (per the API docs): static "Api-Key" header identifies the app; a
# JWT "Authorization: Bearer" header identifies the user after /login.

from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..core.hashing import opensubtitles_hash
from ..core.identify import MediaInfo
from .base import Candidate, Provider, QuotaExceeded, QuotaState


# Function Summary:
#    Parse a human "reset_time" string like "23 hours and 59 minutes" into a
#    number of seconds, for scheduling quota retries.
#
#  Input (parameters):
#    text [str]:  the reset_time string from the API (may be empty)
#
#  Output:
#    seconds [int | None]:  seconds until reset, or None if unparseable
#
# Example:
#    _parse_reset("23 hours and 59 minutes")  ->  86340
def _parse_reset(text: str) -> int | None:
  if not text:
    return None
  hours = re.search(r"(\d+)\s*hour", text)
  minutes = re.search(r"(\d+)\s*min", text)
  if not hours and not minutes:
    return None
  return (int(hours.group(1)) if hours else 0) * 3600 + (int(minutes.group(1)) if minutes else 0) * 60


class OpenSubtitlesComProvider(Provider):
  name = "opensubtitles_com"
  supports_movies = True
  supports_tv = True

  # Function Summary:
  #    Construct the provider. Network calls are made through an injectable httpx
  #    client so tests can supply a mock transport.
  #
  #  Input (parameters):
  #    api_url [str]:                base API URL
  #    api_key [str]:               OpenSubtitles consumer API key ("" = none)
  #    user_agent [str]:            required User-Agent identifying the app
  #    username [str]:              account username (optional)
  #    password [str]:              account password (optional)
  #    token [str]:                 cached JWT (optional; refreshed on login)
  #    client [httpx.Client|None]:  HTTP client (a default is created if None)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    OpenSubtitlesComProvider(api_url="https://api.opensubtitles.com/api/v1",
  #                             api_key="k", user_agent="subhound v0.1.0")
  def __init__(
    self,
    api_url: str,
    api_key: str,
    user_agent: str,
    username: str = "",
    password: str = "",
    token: str = "",
    client: httpx.Client | None = None,
  ) -> None:
    self.api_url = api_url.rstrip("/")
    self.api_key = api_key
    self.user_agent = user_agent
    self.username = username
    self.password = password
    self.token = token
    self._client = client or httpx.Client(timeout=30.0)
    self._quota = QuotaState()

  # Function Summary:
  #    Build request headers, including the API key, user-agent and (when logged
  #    in) the JWT bearer token.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    headers [dict[str, str]]:  HTTP headers for an API request
  #
  # Example:
  #    self._headers()["Api-Key"]  ->  "k"
  def _headers(self) -> dict[str, str]:
    headers = {
      "Api-Key": self.api_key,
      "User-Agent": self.user_agent,
      "Accept": "application/json",
      "Content-Type": "application/json",
    }
    if self.token:
      headers["Authorization"] = f"Bearer {self.token}"
    return headers

  # Function Summary:
  #    Log in to obtain a JWT when credentials are present and no token is cached.
  #    Safe to call repeatedly; a no-op without full credentials or when already
  #    holding a token.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    ok [bool]:  True if authenticated (token available)
  #
  # Example:
  #    provider.login()  ->  True
  def login(self) -> bool:
    if self.token:
      return True
    if not (self.api_key and self.username and self.password):
      return False
    try:
      resp = self._client.post(
        f"{self.api_url}/login",
        headers=self._headers(),
        json={"username": self.username, "password": self.password},
      )
    except httpx.HTTPError:
      return False
    if resp.status_code != 200:
      return False
    self.token = (resp.json() or {}).get("token", "")
    return bool(self.token)

  # Function Summary:
  #    Search OpenSubtitles for candidates matching a media item + language,
  #    using the movie hash when available plus title/year or show/season/episode.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video
  #    lang [str]:                2-letter language code
  #    video_path [Path | None]:  the video file (for moviehash), if available
  #
  #  Output:
  #    candidates [list[Candidate]]:  candidates ordered by API relevance
  #
  # Example:
  #    provider.search(info, "en", Path("Movie.mkv"))  ->  [Candidate(...), ...]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    params: dict[str, str] = {"languages": lang}
    if media.media_type == "tv":
      params["type"] = "episode"
      if media.title_or_show:
        params["query"] = media.title_or_show
      if media.season is not None:
        params["season_number"] = str(media.season)
      if media.episode is not None:
        params["episode_number"] = str(media.episode)
    else:
      params["type"] = "movie"
      if media.title_or_show:
        params["query"] = media.title_or_show
      if media.year:
        params["year"] = str(media.year)
    if video_path is not None:
      try:
        h = opensubtitles_hash(video_path)
      except OSError:
        h = None
      if h:
        params["moviehash"] = h
    try:
      resp = self._client.get(
        f"{self.api_url}/subtitles", headers=self._headers(), params=params)
    except httpx.HTTPError:
      return []
    if resp.status_code != 200:
      return []
    return self._parse_search(resp.json() or {}, lang)

  # Function Summary:
  #    Turn an OpenSubtitles /subtitles response into Candidate objects, taking
  #    the first downloadable file of each result.
  #
  #  Input (parameters):
  #    payload [dict]:  the parsed JSON response
  #    lang [str]:      the requested language (fallback when a result omits it)
  #
  #  Output:
  #    candidates [list[Candidate]]:  parsed candidates
  #
  # Example:
  #    self._parse_search({"data": [...]}, "en")  ->  [Candidate(...)]
  def _parse_search(self, payload: dict, lang: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for item in payload.get("data", []):
      attrs = item.get("attributes", {}) or {}
      files = attrs.get("files", []) or []
      if not files:
        continue
      file_id = files[0].get("file_id")
      if file_id is None:
        continue
      candidates.append(Candidate(
        source=self.name,
        id=str(item.get("id", file_id)),
        language=(attrs.get("language") or lang or "").lower(),
        release_name=attrs.get("release", "") or "",
        hearing_impaired=bool(attrs.get("hearing_impaired")),
        forced=bool(attrs.get("foreign_parts_only")),
        rank=int(attrs.get("download_count", 0) or 0),
        download_ref=str(file_id),
        meta={"ratings": attrs.get("ratings"), "fps": attrs.get("fps")},
      ))
    return candidates

  # Function Summary:
  #    Download a candidate subtitle. Requests a time-limited link from the API
  #    (which consumes quota), updates the tracked quota from the response, then
  #    fetches the file. Raises QuotaExceeded when the daily limit is reached.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate to download (download_ref = file_id)
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on non-quota failure
  #
  # Example:
  #    provider.download(cand, Path("Movie.en.srt"))  ->  PosixPath("Movie.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    self.login()
    try:
      resp = self._client.post(
        f"{self.api_url}/download",
        headers=self._headers(),
        json={"file_id": int(candidate.download_ref)},
      )
    except (httpx.HTTPError, ValueError):
      return None

    # 406/429 => quota exhausted for this window.
    if resp.status_code in (406, 429):
      self._quota.exhausted = True
      self._quota.remaining = 0
      raise QuotaExceeded(self.name, self._quota.reset_seconds,
                          "OpenSubtitles download limit reached")
    if resp.status_code != 200:
      return None

    data = resp.json() or {}
    self._update_quota(data)
    if self._quota.remaining is not None and self._quota.remaining <= 0:
      self._quota.exhausted = True

    link = data.get("link")
    if not link:
      # No link but a quota message => treat as exhausted.
      if self._quota.exhausted:
        raise QuotaExceeded(self.name, self._quota.reset_seconds)
      return None
    try:
      file_resp = self._client.get(link, headers={"User-Agent": self.user_agent})
    except httpx.HTTPError:
      return None
    if file_resp.status_code != 200:
      return None
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(file_resp.content)
    return dest_path

  # Function Summary:
  #    Update the tracked quota from a /download response body.
  #
  #  Input (parameters):
  #    data [dict]:  the parsed /download JSON
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._update_quota({"remaining": 17, "requests": 3, "reset_time": "23 hours"})
  def _update_quota(self, data: dict) -> None:
    if "remaining" in data and data["remaining"] is not None:
      try:
        self._quota.remaining = int(data["remaining"])
      except (TypeError, ValueError):
        pass
    reset = _parse_reset(str(data.get("reset_time", "")))
    if reset is not None:
      self._quota.reset_seconds = reset

  # Function Summary:
  #    Return the tracked quota state.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    quota [QuotaState]:  the current quota picture
  #
  # Example:
  #    provider.quota().remaining  ->  17
  def quota(self) -> QuotaState:
    return self._quota

  # Function Summary:
  #    Close the underlying HTTP client.
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
