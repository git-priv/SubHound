# Tests for Podnapisi (JSON) and TVsubtitles.net (multi-step HTML scrape).
# All HTTP via httpx MockTransport (no network); small fixtures stand in for the
# real responses.

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx

from subracer.config.secrets import Credentials
from subracer.config.settings import Settings
from subracer.core.identify import MediaInfo
from subracer.providers.podnapisi import PodnapisiProvider
from subracer.providers.registry import build_providers, providers_for
from subracer.providers.tvsubtitles import TVsubtitlesProvider

SRT = b"1\n00:00:01,000 --> 00:00:02,000\nHi.\n"


def _zip(srt: bytes) -> bytes:
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("sub.srt", srt)
  return buf.getvalue()


def _client(handler) -> httpx.Client:
  return httpx.Client(transport=httpx.MockTransport(handler))


# ---- Podnapisi ------------------------------------------------------------

def _podnapisi_handler(req: httpx.Request) -> httpx.Response:
  if req.url.path.endswith("/search/advanced"):
    assert req.url.params["keywords"] == "Inception"
    assert req.url.params["movie_type"] == "movie"
    return httpx.Response(200, json={"data": [
      {"id": "abc1", "language": "en", "flags": [], "url": "https://p/abc1",
       "releases": ["BluRay"], "custom_releases": [],
       "movie": {"title": "Inception", "type": "movie", "year": 2010,
                 "episode_info": {}}}]})
  if req.url.path.endswith("/abc1/download"):
    return httpx.Response(200, content=_zip(SRT))
  return httpx.Response(404)


def test_podnapisi_movie_search_and_download(tmp_path):
  prov = PodnapisiProvider(base_url="https://www.podnapisi.net/subtitles",
                           client=_client(_podnapisi_handler))
  cands = prov.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en")
  assert len(cands) == 1 and cands[0].download_ref == "abc1" and "BluRay" in cands[0].release_name
  out = prov.download(cands[0], tmp_path / "Inception.en.srt")
  assert out and out.read_bytes() == SRT


def test_podnapisi_episode_params():
  seen = {}

  def handler(req: httpx.Request) -> httpx.Response:
    seen["params"] = dict(req.url.params)
    return httpx.Response(200, json={"data": []})

  prov = PodnapisiProvider(client=_client(handler))
  prov.search(MediaInfo("tv", "The Show", "", None, 2, 4), "en")
  assert seen["params"]["seasons"] == "2" and seen["params"]["episodes"] == "4"
  assert "tv-series" in seen["params"].get("movie_type", "")


# ---- TVsubtitles ----------------------------------------------------------

_SEARCH = '<div class="left"><ul><li><div><a href="/tvshow-1234-1.html">The Show (2008-2013)</a></div></li></ul></div>'
_SEASON = ('<table id="table5"><tr><td>2x03</td><td><a href="/episode-500.html">Three</a></td></tr>'
           '<tr><td>2x04</td><td><a href="/episode-555.html">Four</a></td></tr></table>')
# In real TVsubtitles the flag img and the release text both live in the <h5>.
_EPISODE = (
  '<a href="/subtitle-999.html"><div class="subtitlen"><h5><img src="/images/flags/en.png">720p WEB-DL</h5>'
  '<p title="rip">web</p></div></a>'
  '<a href="/subtitle-111.html"><div class="subtitlen"><h5><img src="/images/flags/nl.png">1080p</h5></div></a>'
)


def _tvsub_handler(req: httpx.Request) -> httpx.Response:
  path = req.url.path
  if path == "/search.php":
    return httpx.Response(200, text=_SEARCH)
  if path == "/tvshow-1234-2.html":
    return httpx.Response(200, text=_SEASON)
  if path == "/episode-555.html":
    return httpx.Response(200, text=_EPISODE)
  if path == "/download-999.html":
    return httpx.Response(200, content=_zip(SRT))
  return httpx.Response(404)


def test_tvsubtitles_full_flow_and_language_filter(tmp_path):
  prov = TVsubtitlesProvider(base_url="https://www.tvsubtitles.net",
                             client=_client(_tvsub_handler))
  cands = prov.search(MediaInfo("tv", "The Show", "", None, 2, 4), "en")
  # Only the English subtitle (999) is returned, not the Dutch (111).
  assert len(cands) == 1 and cands[0].download_ref == "999"
  assert "WEB-DL" in cands[0].release_name
  out = prov.download(cands[0], tmp_path / "Show.en.srt")
  assert out and out.read_bytes() == SRT


def test_tvsubtitles_skips_movies():
  prov = TVsubtitlesProvider(client=_client(_tvsub_handler))
  assert prov.search(MediaInfo("movie", "Inception", "", 2010), "en") == []


# ---- registry ordering with all providers ---------------------------------

def test_registry_full_ordering():
  s = Settings()
  built = build_providers(s, Credentials())
  movie = [p.name for p in providers_for(built, s, "movie")]
  tv = [p.name for p in providers_for(built, s, "tv")]
  assert movie == ["milahu", "opensubtitles_com", "subsource", "yify", "podnapisi"]
  assert tv == ["milahu", "opensubtitles_com", "subsource", "gestdown", "podnapisi", "tvsubtitles"]
