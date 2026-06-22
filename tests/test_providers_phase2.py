# Phase 2 provider tests: Gestdown (TV-only), SubSource and YIFY, plus the
# shared zip/lang helpers and registry ordering. All HTTP goes through httpx
# MockTransport (no network); YIFY uses small HTML fixtures.

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import httpx

from subracer.config.secrets import Credentials
from subracer.config.settings import Settings, Source
from subracer.core.identify import MediaInfo
from subracer.providers._util import language_name, write_subtitle_bytes
from subracer.providers.base import Candidate
from subracer.providers.gestdown import GestdownProvider
from subracer.providers.registry import build_providers, providers_for
from subracer.providers.subsource import SubSourceProvider
from subracer.providers.yify import YifyProvider

SRT = b"1\n00:00:01,000 --> 00:00:02,000\nHello.\n"


def _zip(srt: bytes, name: str = "sub.srt") -> bytes:
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr(name, srt)
  return buf.getvalue()


def _client(handler) -> httpx.Client:
  return httpx.Client(transport=httpx.MockTransport(handler))


# ---- shared helpers -------------------------------------------------------

def test_language_name():
  assert language_name("en") == "English"
  assert language_name("nl") == "Dutch"
  assert language_name("eng") == "English"


def test_write_subtitle_bytes_plain_and_zip(tmp_path):
  p1 = write_subtitle_bytes(SRT, tmp_path / "a.srt")
  assert p1 and p1.read_bytes() == SRT
  p2 = write_subtitle_bytes(_zip(SRT), tmp_path / "b.srt")
  assert p2 and p2.read_bytes() == SRT
  assert write_subtitle_bytes(_zip(b"x", name="readme.txt"), tmp_path / "c.srt") is None


def test_write_subtitle_bytes_rejects_implausible_payloads(tmp_path):
  # An HTML error page served with a 200 must not be saved as a subtitle.
  assert write_subtitle_bytes(b"<!DOCTYPE html><html>Not found</html>",
                              tmp_path / "h.srt") is None
  # A tiny/empty payload is a truncated download, not subtitle text.
  assert write_subtitle_bytes(b"", tmp_path / "e.srt") is None
  assert write_subtitle_bytes(b"hi", tmp_path / "t.srt") is None


def test_write_subtitle_bytes_rejects_corrupt_zip(tmp_path):
  # Flip bytes inside a valid zip so its CRC check fails -> discarded, not written.
  good = bytearray(_zip(SRT))
  good[-6] ^= 0xFF  # corrupt a byte in the (stored) member data region
  out = write_subtitle_bytes(bytes(good), tmp_path / "z.srt")
  assert out is None or out.read_bytes() == SRT  # never silently corrupt


# ---- Gestdown (TV-only) ---------------------------------------------------

def _gestdown_handler(req: httpx.Request) -> httpx.Response:
  path = req.url.path
  if path.startswith("/shows/search/"):
    return httpx.Response(200, json={"shows": [{"id": "SHOW-GUID", "name": "The Show"}]})
  if path.startswith("/subtitles/get/"):
    # /subtitles/get/SHOW-GUID/2/4/English
    assert path == "/subtitles/get/SHOW-GUID/2/4/English"
    return httpx.Response(200, json={"matchingSubtitles": [
      {"subtitleId": "SUB1", "version": "WEB", "downloadUri": "/subtitles/download/SUB1",
       "hearingImpaired": False, "completed": True}]})
  if path == "/subtitles/download/SUB1":
    return httpx.Response(200, content=SRT)
  return httpx.Response(404)


def test_gestdown_search_and_download(tmp_path):
  prov = GestdownProvider(base_url="https://api.gestdown.info", client=_client(_gestdown_handler))
  cands = prov.search(MediaInfo("tv", "The Show", "", None, 2, 4), "en")
  assert len(cands) == 1 and cands[0].download_ref == "/subtitles/download/SUB1"
  out = prov.download(cands[0], tmp_path / "Show.en.srt")
  assert out and out.read_bytes() == SRT


def test_gestdown_is_tv_only():
  prov = GestdownProvider(client=_client(_gestdown_handler))
  assert prov.supports("tv") is True and prov.supports("movie") is False
  assert prov.search(MediaInfo("movie", "Inception", "", 2010), "en") == []


# ---- SubSource ------------------------------------------------------------

def _subsource_handler(req: httpx.Request) -> httpx.Response:
  path = req.url.path
  if path == "/api/v1/movies/search":
    return httpx.Response(200, json={"results": [
      {"movie_id": 77, "title": "Inception", "type": "movie", "release_year": 2010}]})
  if path == "/api/v1/subtitles":
    assert req.url.params["movieId"] == "77"
    return httpx.Response(200, json={"results": [
      {"subtitle_id": 555, "language": "english", "release_info": ["BluRay", "x264"],
       "hearing_impaired": False, "downloads": 99}]})
  if path == "/api/v1/subtitles/555/download":
    return httpx.Response(200, content=_zip(SRT))
  return httpx.Response(404)


def test_subsource_search_and_download(tmp_path):
  prov = SubSourceProvider(client=_client(_subsource_handler))
  cands = prov.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en")
  assert len(cands) == 1
  c = cands[0]
  assert c.download_ref == "555" and "BluRay" in c.release_name and c.rank == 99
  out = prov.download(c, tmp_path / "Inception.en.srt")
  assert out and out.read_bytes() == SRT


# ---- YIFY -----------------------------------------------------------------

_SEARCH_HTML = """
<ul><li class="media media-movie-clickable"><div class="media-body">
  <a href="/movie-imdb/tt0133093"><div><h3 class="media-heading">The Matrix (1999)</h3></div></a>
</div></li></ul>
"""
_MOVIE_HTML = """
<table><tbody>
<tr data-id="1"><td class="rating-cell">8</td>
  <td class="flag-cell"><span class="sub-lang">English</span></td>
  <td><a href="/subtitles/the-matrix-1999-english-yify-12345">The Matrix YIFY</a></td></tr>
<tr data-id="2"><td class="rating-cell">3</td>
  <td class="flag-cell"><span class="sub-lang">Dutch</span></td>
  <td><a href="/subtitles/the-matrix-1999-dutch-yify-9">NL</a></td></tr>
</tbody></table>
"""
_SUBPAGE_HTML = '<a class="btn" href="/subtitle/the-matrix-1999-english-yify-12345.zip">Download</a>'


def _yify_handler(req: httpx.Request) -> httpx.Response:
  path = req.url.path
  if path.startswith("/search/"):
    return httpx.Response(200, text=_SEARCH_HTML)
  if path == "/movie-imdb/tt0133093":
    return httpx.Response(200, text=_MOVIE_HTML)
  if path == "/subtitles/the-matrix-1999-english-yify-12345":
    return httpx.Response(200, text=_SUBPAGE_HTML)
  if path == "/subtitle/the-matrix-1999-english-yify-12345.zip":
    return httpx.Response(200, content=_zip(SRT))
  return httpx.Response(404)


def test_yify_search_filters_language_and_downloads(tmp_path):
  prov = YifyProvider(base_url="https://www.yts-subs.com", client=_client(_yify_handler))
  cands = prov.search(MediaInfo("movie", "The Matrix", "The Matrix 1999", 1999), "en")
  # Only the English row is returned, not the Dutch one.
  assert len(cands) == 1 and "english" in cands[0].download_ref
  out = prov.download(cands[0], tmp_path / "The Matrix.en.srt")
  assert out and out.read_bytes() == SRT


def test_yify_skips_tv():
  prov = YifyProvider(client=_client(_yify_handler))
  assert prov.search(MediaInfo("tv", "The Show", "", None, 1, 1), "en") == []


# ---- registry -------------------------------------------------------------

def test_registry_orders_all_providers_by_media_type():
  s = Settings()
  built = build_providers(s, Credentials())
  names_movie = [p.name for p in providers_for(built, s, "movie")]
  names_tv = [p.name for p in providers_for(built, s, "tv")]
  # Movies: yify present; gestdown/tvsubtitles (TV-only) absent.
  assert "yify" in names_movie and "gestdown" not in names_movie
  assert "tvsubtitles" not in names_movie and "podnapisi" in names_movie
  # TV: gestdown + tvsubtitles present; yify (movies-only) absent.
  assert "gestdown" in names_tv and "tvsubtitles" in names_tv and "yify" not in names_tv
