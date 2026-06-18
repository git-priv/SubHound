# subracer.providers.yify
#
# Provider for YIFY subtitles (yts-subs.com), a movie-only HTML site (no API, no
# quota). Scrapes the search page for the movie, then the movie page's subtitle
# table, then downloads the per-subtitle ZIP.
#
# Page structure (yts-subs.com):
#   /search/{title}             search results; movie links under
#                               li.media.media-movie-clickable a[href]
#   {movie page}                subtitle rows tr[data-id] with td.flag-cell
#                               span.sub-lang (language name) and an a[href]
#                               to /subtitles/{slug}
#   /subtitle/{slug}.zip        the downloadable zip (one .srt inside)

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from ..core.identify import MediaInfo
from ._util import language_name, write_subtitle_bytes
from .base import Candidate, Provider

DEFAULT_BASE_URL = "https://www.yts-subs.com"


class YifyProvider(Provider):
  name = "yify"
  supports_movies = True
  supports_tv = False  # YIFY is movies/documentaries only

  # Function Summary:
  #    Construct the provider with an injectable HTTP client (for testing).
  #
  #  Input (parameters):
  #    base_url [str]:               site base URL
  #    user_agent [str]:             User-Agent header
  #    max_results [int]:            maximum subtitle candidates to return
  #    client [httpx.Client|None]:   HTTP client (a default is created if None)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    YifyProvider()
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
  #    Search YIFY for a movie's subtitles in the requested language.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video (movies only)
  #    lang [str]:                2-letter language code
  #    video_path [Path | None]:  unused (no hash matching)
  #
  #  Output:
  #    candidates [list[Candidate]]:  matching subtitle candidates
  #
  # Example:
  #    provider.search(MediaInfo("movie","The Matrix","",1999), "en")  ->  [Candidate(...)]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    if media.media_type == "tv" or not media.title_or_show:
      return []
    movie_url = self._find_movie_url(media)
    if not movie_url:
      return []
    try:
      resp = self._client.get(movie_url, headers=self._headers())
    except httpx.HTTPError:
      return []
    if resp.status_code != 200:
      return []
    target = language_name(lang).lower()
    soup = BeautifulSoup(resp.text, "html.parser")
    candidates: list[Candidate] = []
    for row in soup.select("tr[data-id]"):
      lang_el = row.select_one("td.flag-cell span.sub-lang")
      if lang_el is None or lang_el.get_text(strip=True).lower() != target:
        continue
      link = row.select_one('a[href*="/subtitles/"]')
      if link is None:
        continue
      href = link.get("href", "")
      rating_el = row.select_one("td.rating-cell")
      rank = self._to_int(rating_el.get_text(strip=True)) if rating_el else 0
      candidates.append(Candidate(
        source=self.name,
        id=href.rsplit("/", 1)[-1],
        language=lang,
        release_name=link.get_text(strip=True),
        rank=rank,
        download_ref=href,
      ))
      if len(candidates) >= self.max_results:
        break
    return candidates

  # Function Summary:
  #    Find the best-matching movie page URL from the YIFY search results, by
  #    title and (when available) year.
  #
  #  Input (parameters):
  #    media [MediaInfo]:  the identified movie
  #
  #  Output:
  #    url [str]:  absolute movie page URL, or "" if none matched
  #
  # Example:
  #    self._find_movie_url(MediaInfo("movie","The Matrix","",1999))  ->  "https://.../movie-imdb/tt0133093"
  def _find_movie_url(self, media: MediaInfo) -> str:
    try:
      resp = self._client.get(
        f"{self.base_url}/search/{quote(media.title_or_show)}", headers=self._headers())
    except httpx.HTTPError:
      return ""
    if resp.status_code != 200:
      return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    want_title = self._norm(media.title_or_show)
    first = ""
    for link in soup.select("li.media.media-movie-clickable a[href]"):
      href = link.get("href", "")
      if not href:
        continue
      first = first or href
      text = self._norm(link.get_text(" ", strip=True))
      title_ok = want_title in text or text in want_title
      year_ok = media.year is None or str(media.year) in link.get_text(" ", strip=True)
      if title_ok and year_ok:
        return urljoin(self.base_url + "/", href)
    return urljoin(self.base_url + "/", first) if first else ""

  # Function Summary:
  #    Download a candidate's subtitle ZIP and write the contained SRT. Resolves
  #    the zip from the subtitle page (link ending in .zip) or by the conventional
  #    /subtitle/{slug}.zip URL.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  the candidate (download_ref = /subtitles/{slug})
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  the written file, or None on failure
  #
  # Example:
  #    provider.download(cand, Path("The Matrix.en.srt"))  ->  PosixPath("The Matrix.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    zip_url = self._resolve_zip_url(candidate.download_ref)
    if not zip_url:
      return None
    try:
      resp = self._client.get(zip_url, headers=self._headers())
    except httpx.HTTPError:
      return None
    if resp.status_code != 200 or not resp.content:
      return None
    return write_subtitle_bytes(resp.content, dest_path)

  # Function Summary:
  #    Resolve the ZIP download URL for a subtitle page reference, fetching the
  #    page to find a .zip link, falling back to the /subtitle/{slug}.zip pattern.
  #
  #  Input (parameters):
  #    ref [str]:  the subtitle page href (e.g. "/subtitles/the-matrix-...-12345")
  #
  #  Output:
  #    url [str]:  absolute zip URL, or "" if it can't be resolved
  #
  # Example:
  #    self._resolve_zip_url("/subtitles/the-matrix-english-yify-12")  ->  "https://.../subtitle/the-matrix-english-yify-12.zip"
  def _resolve_zip_url(self, ref: str) -> str:
    if not ref:
      return ""
    if ref.endswith(".zip"):
      return urljoin(self.base_url + "/", ref)
    page_url = urljoin(self.base_url + "/", ref)
    try:
      resp = self._client.get(page_url, headers=self._headers())
      if resp.status_code == 200:
        soup = BeautifulSoup(resp.text, "html.parser")
        zip_link = soup.select_one('a[href$=".zip"]')
        if zip_link is not None:
          return urljoin(self.base_url + "/", zip_link.get("href", ""))
    except httpx.HTTPError:
      pass
    # Conventional fallback: /subtitles/{slug} -> /subtitle/{slug}.zip
    slug = ref.rstrip("/").rsplit("/", 1)[-1]
    return f"{self.base_url}/subtitle/{slug}.zip"

  # Function Summary:
  #    Normalize a title for tolerant comparison.
  #
  #  Input (parameters):
  #    text [str]:  a title string
  #
  #  Output:
  #    norm [str]:  lowercase alphanumeric-and-space form
  #
  # Example:
  #    self._norm("The Matrix (1999)")  ->  "the matrix 1999"
  def _norm(self, text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())).strip()

  # Function Summary:
  #    Parse the leading integer from a string (e.g. a rating cell), or 0.
  #
  #  Input (parameters):
  #    text [str]:  the source string
  #
  #  Output:
  #    value [int]:  the parsed integer, or 0
  #
  # Example:
  #    self._to_int("12 ratings")  ->  12
  def _to_int(self, text: str) -> int:
    m = re.search(r"-?\d+", text or "")
    return int(m.group(0)) if m else 0

  # Function Summary:
  #    Standard request headers (a browser-like User-Agent).
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    headers [dict[str, str]]:  HTTP headers
  #
  # Example:
  #    self._headers()["User-Agent"]  ->  "Mozilla/5.0 subracer"
  def _headers(self) -> dict[str, str]:
    return {"User-Agent": self.user_agent}

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
