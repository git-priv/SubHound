# Tests for the provider layer: OpenSubtitles.com search parsing, hash/query
# request building, download + quota tracking, QuotaExceeded on limit, and the
# registry ordering. All network calls go through an httpx MockTransport -- no
# real requests are made.

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from subhound.config.secrets import Credentials
from subhound.config.settings import Settings, Source
from subhound.core.identify import MediaInfo
from subhound.providers.base import QuotaExceeded
from subhound.providers.opensubtitles_com import OpenSubtitlesComProvider, _parse_reset
from subhound.providers.registry import build_providers, providers_for

API = "https://api.opensubtitles.com/api/v1"


def _provider(handler) -> OpenSubtitlesComProvider:
  client = httpx.Client(transport=httpx.MockTransport(handler), base_url="")
  return OpenSubtitlesComProvider(
    api_url=API, api_key="KEY", user_agent="subhound test", client=client)


def test_parse_reset():
  assert _parse_reset("23 hours and 59 minutes") == 23 * 3600 + 59 * 60
  assert _parse_reset("30 minutes") == 1800
  assert _parse_reset("") is None


def test_search_builds_episode_query_and_parses():
  seen = {}

  def handler(req: httpx.Request) -> httpx.Response:
    seen["path"] = req.url.path
    seen["params"] = dict(req.url.params)
    seen["api_key"] = req.headers.get("Api-Key")
    return httpx.Response(200, json={"data": [
      {"id": "9", "attributes": {
        "language": "en", "release": "Show.S02E04.WEB", "download_count": 42,
        "hearing_impaired": False, "foreign_parts_only": False,
        "files": [{"file_id": 555, "file_name": "Show.S02E04.srt"}],
      }},
      {"id": "10", "attributes": {"language": "en", "files": []}},  # no files -> skipped
    ]})

  prov = _provider(handler)
  info = MediaInfo("tv", "The Show", "The Show S02E04", None, 2, 4)
  cands = prov.search(info, "en")
  assert seen["path"].endswith("/subtitles")
  assert seen["params"]["type"] == "episode"
  assert seen["params"]["season_number"] == "2" and seen["params"]["episode_number"] == "4"
  assert seen["params"]["query"] == "The Show" and seen["params"]["languages"] == "en"
  assert seen["api_key"] == "KEY"
  assert len(cands) == 1
  c = cands[0]
  assert c.source == "opensubtitles_com" and c.download_ref == "555"
  assert c.language == "en" and c.rank == 42


def test_search_movie_includes_year():
  seen = {}

  def handler(req: httpx.Request) -> httpx.Response:
    seen["params"] = dict(req.url.params)
    return httpx.Response(200, json={"data": []})

  prov = _provider(handler)
  prov.search(MediaInfo("movie", "Inception", "Inception 2010", 2010), "en")
  assert seen["params"]["type"] == "movie" and seen["params"]["year"] == "2010"


def test_download_success_updates_quota_and_writes_file():
  d = Path(tempfile.mkdtemp())
  content = b"1\n00:00:01,000 --> 00:00:02,000\nHi.\n"

  def handler(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/download"):
      return httpx.Response(200, json={
        "link": "https://dl.opensubtitles.com/file.srt",
        "remaining": 17, "requests": 3, "reset_time": "23 hours and 10 minutes",
      })
    return httpx.Response(200, content=content)  # the file link

  prov = _provider(handler)
  from subhound.providers.base import Candidate
  out = prov.download(Candidate("opensubtitles_com", "9", "en", download_ref="555"), d / "out.srt")
  assert out and out.read_bytes() == content
  q = prov.quota()
  assert q.remaining == 17 and q.reset_seconds == 23 * 3600 + 10 * 60 and not q.exhausted


def test_download_quota_exhausted_raises():
  def handler(req: httpx.Request) -> httpx.Response:
    return httpx.Response(406, json={"message": "download limit reached"})

  prov = _provider(handler)
  from subhound.providers.base import Candidate
  with pytest.raises(QuotaExceeded) as ei:
    prov.download(Candidate("opensubtitles_com", "9", "en", download_ref="555"), Path("/tmp/x.srt"))
  assert ei.value.source == "opensubtitles_com"
  assert prov.quota().exhausted is True


def test_download_remaining_zero_marks_exhausted():
  def handler(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/download"):
      return httpx.Response(200, json={"link": "", "remaining": 0,
                                       "reset_time": "1 hour"})
    return httpx.Response(200, content=b"x")

  prov = _provider(handler)
  from subhound.providers.base import Candidate
  with pytest.raises(QuotaExceeded):
    prov.download(Candidate("opensubtitles_com", "9", "en", download_ref="555"), Path("/tmp/x.srt"))
  assert prov.quota().exhausted and prov.quota().reset_seconds == 3600


def test_registry_builds_and_orders():
  # Milahu is first; all network providers are built in configured order.
  # Full per-media-type ordering is covered in test_providers_phase2.
  s = Settings()
  built = build_providers(s, Credentials(api_key="k"))
  assert Source.MILAHU in built
  assert Source.OPENSUBTITLES_COM in built
  for mt in ("movie", "tv", "unknown"):
    names = [p.name for p in providers_for(built, s, mt)]
    assert names[0] == "milahu"
