# subracer.providers.tvsubtitles
#
# Provider for TVsubtitles.net. TV-only, no auth, no published quota. The site
# has no API, so this scrapes the multi-step flow:
#
#   POST /search.php {qs}                -> show page links /tvshow-{id}-...
#   GET  /tvshow-{id}-{season}.html      -> episode rows -> /episode-{eid}.html
#   GET  /episode-{eid}.html             -> subtitle rows (.subtitlen) per language
#   GET  /download-{subId}.html          -> zip (one .srt inside)

from __future__ import annotations

import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from ..core.identify import MediaInfo
from ..core.subtitle_lang import to_iso639_1
from ._util import write_subtitle_bytes
from .base import Candidate, Provider

DEFAULT_BASE_URL = "https://www.tvsubtitles.net"

_TVSHOW_RE = re.compile(r"/tvshow-(\d+)")
_EPISODE_RE = re.compile(r"/episode-(\d+)")
_SUBTITLE_RE = re.compile(r"/subtitle-(\d+)")
_FLAG_RE = re.compile(r"flags?/([a-z]{2,3})", re.IGNORECASE)
_EP_NUM_RE = re.compile(r"\d+\s*x\s*(\d+)", re.IGNORECASE)


class TVsubtitlesProvider(Provider):
  name = "tvsubtitles"
  supports_movies = False  # TV only
  supports_tv = True

  # Function Summary:
  #    Construct the provider with an injectable HTTP client (for testing).
  #
  #  Input (parameters):
  #    base_url [str]:               site base URL
  #    user_agent [str]:             User-Agent header
  #    max_results [int]:            maximum candidates to return
  #    client [httpx.Client|None]:   HTTP client (a default is created if None)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    TVsubtitlesProvider()
  def __init__(
    self,
    base_url: str = DEFAULT_BASE_URL,
    user_agent: str = "Mozilla/5.0 subracer",
    max_results: int = 10,
    client: httpx.Client | None = None,
  ) -> None:
    self.base_url = base_url.rstrip("/")
    self.user_agent = user_agent
    self.max_results = max_results
    self._client = client or httpx.Client(timeout=30.0, follow_redirects=True)

  # Function Summary:
  #    Search TVsubtitles for an episode's subtitles in the requested language.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video (TV with season+episode)
  #    lang [str]:                2-letter language code
  #    video_path [Path | None]:  unused
  #
  #  Output:
  #    candidates [list[Candidate]]:  matching subtitles
  #
  # Example:
  #    provider.search(MediaInfo("tv","The Show","",None,2,4), "en")  ->  [Candidate(...)]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    if media.media_type != "tv" or media.season is None or media.episode is None:
      return []
    show_id = self._search_show_id(media.title_or_show)
    if show_id is None:
      return []
    episode_id = self._episode_id(show_id, media.season, media.episode)
    if episode_id is None:
      return []
    try:
      resp = self._client.get(
        f"{self.base_url}/episode-{episode_id}.html", headers=self._headers())
    except httpx.HTTPError:
      return []
    if resp.status_code != 200:
      return []
    want = lang.lower()
    soup = BeautifulSoup(resp.text, "html.parser")
    candidates: list[Candidate] = []
    for row in soup.select(".subtitlen"):
      sub_lang = self._row_language(row)
      if sub_lang and sub_lang != want:
        continue
      sub_id = self._row_subtitle_id(row)
      if sub_id is None:
        continue
      heading = row.find("h5")
      candidates.append(Candidate(
        source=self.name,
        id=str(sub_id),
        language=lang,
        release_name=heading.get_text(strip=True) if heading else "",
        download_ref=str(sub_id),
      ))
      if len(candidates) >= self.max_results:
        break
    return candidates

  # Function Summary:
  #    Resolve a series name to a TVsubtitles show id via the search page.
  #
  #  Input (parameters):
  #    series [str]:  the show name
  #
  #  Output:
  #    show_id [int | None]:  the show id, or None if not found
  #
  # Example:
  #    self._search_show_id("The Show")  ->  1234
  def _search_show_id(self, series: str) -> int | None:
    if not series:
      return None
    try:
      resp = self._client.post(
        f"{self.base_url}/search.php", headers=self._headers(), data={"qs": series})
    except httpx.HTTPError:
      return None
    if resp.status_code != 200:
      return None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one('a[href*="/tvshow-"]')
    if link is None:
      return None
    m = _TVSHOW_RE.search(link.get("href", ""))
    return int(m.group(1)) if m else None

  # Function Summary:
  #    Find the TVsubtitles episode id for a show's season + episode.
  #
  #  Input (parameters):
  #    show_id [int]:  the show id
  #    season [int]:   the season number
  #    episode [int]:  the episode number
  #
  #  Output:
  #    episode_id [int | None]:  the episode id, or None if not listed
  #
  # Example:
  #    self._episode_id(1234, 2, 4)  ->  56789
  def _episode_id(self, show_id: int, season: int, episode: int) -> int | None:
    try:
      resp = self._client.get(
        f"{self.base_url}/tvshow-{show_id}-{season}.html", headers=self._headers())
    except httpx.HTTPError:
      return None
    if resp.status_code != 200:
      return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for row in soup.select("table#table5 tr"):
      link = row.select_one('a[href*="/episode-"]')
      if link is None:
        continue
      ep_match = _EP_NUM_RE.search(row.get_text(" ", strip=True))
      id_match = _EPISODE_RE.search(link.get("href", ""))
      if ep_match and id_match and int(ep_match.group(1)) == episode:
        return int(id_match.group(1))
    return None

  # Function Summary:
  #    Extract the ISO 639-1 language from a subtitle row's flag image.
  #
  #  Input (parameters):
  #    row [bs4 Tag]:  a .subtitlen row
  #
  #  Output:
  #    lang [str]:  2-letter code, or "" if undetermined
  #
  # Example:
  #    self._row_language(row)  ->  "en"
  def _row_language(self, row) -> str:
    img = row.find("img")
    if img is None or not img.get("src"):
      return ""
    m = _FLAG_RE.search(img.get("src", ""))
    return to_iso639_1(m.group(1)).lower() if m else ""

  # Function Summary:
  #    Extract the subtitle id from a subtitle row's link.
  #
  #  Input (parameters):
  #    row [bs4 Tag]:  a .subtitlen row
  #
  #  Output:
  #    sub_id [int | None]:  the subtitle id, or None
  #
  # Example:
  #    self._row_subtitle_id(row)  ->  98765
  def _row_subtitle_id(self, row) -> int | None:
    link = row.find("a", href=_SUBTITLE_RE) or row.find_parent("a", href=_SUBTITLE_RE)
    if link is None:
      return None
    m = _SUBTITLE_RE.search(link.get("href", ""))
    return int(m.group(1)) if m else None

  # Function Summary:
  #    Download a candidate subtitle zip and write the contained SRT.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate (download_ref = subtitle id)
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on failure
  #
  # Example:
  #    provider.download(cand, Path("Show.en.srt"))  ->  PosixPath("Show.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    try:
      resp = self._client.get(
        f"{self.base_url}/download-{candidate.download_ref}.html", headers=self._headers())
    except httpx.HTTPError:
      return None
    if resp.status_code != 200 or not resp.content:
      return None
    return write_subtitle_bytes(resp.content, dest_path)

  # Function Summary:
  #    Standard request headers (TVsubtitles requires a Referer).
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    headers [dict[str, str]]:  HTTP headers
  #
  # Example:
  #    self._headers()["Referer"]  ->  "https://www.tvsubtitles.net/"
  def _headers(self) -> dict[str, str]:
    return {"User-Agent": self.user_agent, "Referer": f"{self.base_url}/"}

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
