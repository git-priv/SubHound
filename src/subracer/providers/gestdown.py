# subracer.providers.gestdown
#
# Provider for Gestdown (api.gestdown.info), a JSON proxy over Addic7ed. Addic7ed
# is TV-only, so this provider only yields results when a season + episode are
# known. No authentication and no per-day quota.
#
# Flow:
#   GET /shows/search/{query}                          -> shows[].id (showUniqueId)
#   GET /subtitles/get/{showId}/{season}/{episode}/{language_name}
#                                                      -> matchingSubtitles[]
#   GET {downloadUri}                                  -> the subtitle file

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from ..core.identify import MediaInfo
from ._util import language_name, write_subtitle_bytes
from .base import Candidate, Provider

DEFAULT_BASE_URL = "https://api.gestdown.info"


class GestdownProvider(Provider):
  name = "gestdown"
  # Gestdown proxies Addic7ed, which only hosts TV subtitles (its API requires
  # show/season/episode), so this provider is TV-only.
  supports_movies = False
  supports_tv = True

  # Function Summary:
  #    Construct the provider with an injectable HTTP client (for testing).
  #
  #  Input (parameters):
  #    base_url [str]:               API base URL
  #    user_agent [str]:             User-Agent header
  #    client [httpx.Client|None]:   HTTP client (a default is created if None)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    GestdownProvider()
  def __init__(
    self,
    base_url: str = DEFAULT_BASE_URL,
    user_agent: str = "subracer",
    client: httpx.Client | None = None,
  ) -> None:
    self.base_url = base_url.rstrip("/")
    self.user_agent = user_agent
    self._client = client or httpx.Client(timeout=30.0, follow_redirects=True)

  # Function Summary:
  #    Search Gestdown for subtitle candidates. Requires a known season+episode
  #    (Addic7ed is TV-only); returns [] otherwise or when the show isn't found.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video
  #    lang [str]:                2-letter language code
  #    video_path [Path | None]:  unused (no hash matching here)
  #
  #  Output:
  #    candidates [list[Candidate]]:  matching subtitles
  #
  # Example:
  #    provider.search(MediaInfo("tv","The Show","",None,2,4), "en")  ->  [Candidate(...)]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    if media.media_type != "tv" or media.season is None or media.episode is None:
      return []
    show_id = self._find_show_id(media.title_or_show)
    if not show_id:
      return []
    url = (f"{self.base_url}/subtitles/get/{show_id}/{media.season}/"
           f"{media.episode}/{quote(language_name(lang))}")
    try:
      resp = self._client.get(url, headers=self._headers())
    except httpx.HTTPError:
      return []
    if resp.status_code != 200:
      return []
    candidates: list[Candidate] = []
    for sub in (resp.json() or {}).get("matchingSubtitles", []):
      download_uri = sub.get("downloadUri")
      if not download_uri:
        continue
      candidates.append(Candidate(
        source=self.name,
        id=str(sub.get("subtitleId", download_uri)),
        language=lang,
        release_name=sub.get("version", "") or "",
        hearing_impaired=bool(sub.get("hearingImpaired")),
        download_ref=download_uri,
        meta={"completed": sub.get("completed")},
      ))
    return candidates

  # Function Summary:
  #    Resolve a show name to its Gestdown showUniqueId (first match).
  #
  #  Input (parameters):
  #    show_name [str]:  the series name to search for
  #
  #  Output:
  #    show_id [str]:  the showUniqueId, or "" if not found
  #
  # Example:
  #    self._find_show_id("The Show")  ->  "0a1b2c3d-..."
  def _find_show_id(self, show_name: str) -> str:
    if not show_name:
      return ""
    try:
      resp = self._client.get(
        f"{self.base_url}/shows/search/{quote(show_name)}", headers=self._headers())
    except httpx.HTTPError:
      return ""
    if resp.status_code != 200:
      return ""
    shows = (resp.json() or {}).get("shows", [])
    return str(shows[0].get("id", "")) if shows else ""

  # Function Summary:
  #    Download a candidate subtitle via its (relative) downloadUri.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate (download_ref = downloadUri)
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on failure
  #
  # Example:
  #    provider.download(cand, Path("Show.en.srt"))  ->  PosixPath("Show.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    ref = candidate.download_ref
    url = ref if ref.startswith("http") else f"{self.base_url}{ref}"
    try:
      resp = self._client.get(url, headers=self._headers())
    except httpx.HTTPError:
      return None
    if resp.status_code != 200 or not resp.content:
      return None
    return write_subtitle_bytes(resp.content, dest_path)

  # Function Summary:
  #    Standard request headers.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    headers [dict[str, str]]:  HTTP headers
  #
  # Example:
  #    self._headers()["Accept"]  ->  "application/json"
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
