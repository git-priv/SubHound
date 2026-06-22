# subhound.providers.podnapisi
#
# Provider for Podnapisi (podnapisi.net). Movies + TV, no auth, no published
# per-day quota. Uses the JSON advanced-search endpoint, then downloads a zip.
#
#   GET /subtitles/search/advanced?keywords=&language=&movie_type=&seasons=&episodes=&year=
#                                                  -> {"data": [ {id, url, releases, movie} ]}
#   GET /subtitles/{pid}/download                  -> zip (one .srt inside)
#
# Podnapisi's TLS uses weak DH params, so the default client lowers the OpenSSL
# security level (as subliminal does); tests inject their own client.

from __future__ import annotations

import ssl
from pathlib import Path

import httpx

from ..core.identify import MediaInfo
from ._util import write_subtitle_bytes
from .base import Candidate, Provider

DEFAULT_BASE_URL = "https://www.podnapisi.net/subtitles"


# Function Summary:
#    Build an httpx client whose TLS accepts Podnapisi's weak DH parameters
#    (OpenSSL security level 1). Falls back to a default client on error.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    client [httpx.Client]:  a client configured for podnapisi.net
#
# Example:
#    _seclevel1_client()  ->  <httpx.Client>
def _seclevel1_client() -> httpx.Client:
  try:
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return httpx.Client(verify=ctx, timeout=30.0, follow_redirects=True)
  except (ssl.SSLError, ValueError):
    return httpx.Client(timeout=30.0, follow_redirects=True)


class PodnapisiProvider(Provider):
  name = "podnapisi"
  supports_movies = True
  supports_tv = True

  # Function Summary:
  #    Construct the provider with an injectable HTTP client (for testing).
  #
  #  Input (parameters):
  #    base_url [str]:               API base URL
  #    user_agent [str]:             User-Agent header
  #    max_results [int]:            maximum candidates to return
  #    client [httpx.Client|None]:   HTTP client (default lowers TLS seclevel)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    PodnapisiProvider()
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
    self._client = client or _seclevel1_client()

  # Function Summary:
  #    Search Podnapisi for subtitle candidates for a media item + language.
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
    if not media.title_or_show:
      return []
    params: dict[str, object] = {"keywords": media.title_or_show, "language": lang}
    is_episode = media.media_type == "tv" and media.season is not None and media.episode is not None
    if is_episode:
      params["seasons"] = media.season
      params["episodes"] = media.episode
      params["movie_type"] = ["tv-series", "mini-series"]
    else:
      params["movie_type"] = "movie"
      if media.year:
        params["year"] = media.year
    try:
      resp = self._client.get(
        f"{self.base_url}/search/advanced", headers=self._headers(), params=params)
    except httpx.HTTPError:
      return []
    if resp.status_code != 200:
      return []
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for data in (resp.json() or {}).get("data", []):
      pid = str(data.get("id", ""))
      if not pid or pid in seen:
        continue
      seen.add(pid)
      movie = data.get("movie", {}) or {}
      if is_episode and movie.get("type") == "movie":
        continue
      releases = (data.get("releases") or []) + (data.get("custom_releases") or [])
      candidates.append(Candidate(
        source=self.name,
        id=pid,
        language=lang,
        release_name=", ".join(releases),
        hearing_impaired="hearing_impaired" in (data.get("flags") or []),
        download_ref=pid,
        meta={"url": data.get("url"), "title": movie.get("title")},
      ))
      if len(candidates) >= self.max_results:
        break
    return candidates

  # Function Summary:
  #    Download a candidate's subtitle zip and write the contained SRT.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate (download_ref = pid)
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on failure
  #
  # Example:
  #    provider.download(cand, Path("Inception.en.srt"))  ->  PosixPath("Inception.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    try:
      resp = self._client.get(
        f"{self.base_url}/{candidate.download_ref}/download", headers=self._headers())
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
