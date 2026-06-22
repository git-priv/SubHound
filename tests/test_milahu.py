# tests/test_milahu.py — MilahuProvider tests
#
# All HTTP calls are mocked; no real network requests are made.

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from subhound.core.identify import MediaInfo
from subhound.providers.milahu import MilahuProvider, _is_zip, _synthetic_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _media(title="Inception", year=2010, media_type="movie", season=None, episode=None) -> MediaInfo:
  return MediaInfo(
    media_type=media_type,
    title_or_show=title,
    year=year,
    season=season,
    episode=episode,
    query=title,
  )


def _make_zip(*srt_names: str) -> bytes:
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w") as zf:
    for name in srt_names:
      zf.writestr(name, f"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
  return buf.getvalue()


def _mock_response(content: bytes, status_code: int = 200):
  resp = MagicMock()
  resp.content = content
  resp.status_code = status_code
  resp.raise_for_status = MagicMock()
  if status_code >= 400:
    import httpx
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
      "error", request=MagicMock(), response=resp
    )
  return resp


# ---------------------------------------------------------------------------
# _is_zip
# ---------------------------------------------------------------------------

def test_is_zip_true_for_pk_header():
  assert _is_zip(b"PK\x03\x04rest")


def test_is_zip_false_for_non_zip():
  assert not _is_zip(b"<html>error</html>")
  assert not _is_zip(b"")


# ---------------------------------------------------------------------------
# _synthetic_filename
# ---------------------------------------------------------------------------

def test_synthetic_filename_movie():
  name = _synthetic_filename(_media("Scary Movie", 2000))
  assert name == "Scary.Movie.2000.mkv"


def test_synthetic_filename_tv():
  name = _synthetic_filename(_media("Breaking Bad", season=1, episode=5, media_type="tv"))
  assert name == "Breaking.Bad.S01E05.mkv"


def test_synthetic_filename_movie_no_year():
  name = _synthetic_filename(_media("Unknown", year=None))
  assert name == "Unknown.mkv"


# ---------------------------------------------------------------------------
# MilahuProvider.search
# ---------------------------------------------------------------------------

def test_search_returns_candidates_from_zip(tmp_path):
  provider = MilahuProvider()
  zip_bytes = _make_zip("Movie.1.en.srt", "Movie.2.en.srt")

  with patch.object(provider._client, "get", return_value=_mock_response(zip_bytes)):
    candidates = provider.search(_media(), "en", Path("Inception.2010.mkv"))

  assert len(candidates) == 2
  assert all(c.source == "milahu" for c in candidates)
  assert all(c.language == "en" for c in candidates)
  assert all(c.release_name.endswith(".srt") for c in candidates)
  # download_ref points to an existing temp file
  assert all(Path(c.download_ref).exists() for c in candidates)


def test_search_uses_video_path_name(tmp_path):
  provider = MilahuProvider()
  zip_bytes = _make_zip("Movie.1.en.srt")
  captured = {}

  def fake_get(url, params=None, **kwargs):
    captured["params"] = params
    return _mock_response(zip_bytes)

  with patch.object(provider._client, "get", side_effect=fake_get):
    provider.search(_media(), "en", Path("/media/Inception.2010.BluRay.mkv"))

  assert captured["params"]["movie"] == "Inception.2010.BluRay.mkv"
  assert captured["params"]["lang"] == "en"


def test_search_uses_synthetic_name_when_no_path():
  provider = MilahuProvider()
  zip_bytes = _make_zip("Movie.1.en.srt")
  captured = {}

  def fake_get(url, params=None, **kwargs):
    captured["params"] = params
    return _mock_response(zip_bytes)

  with patch.object(provider._client, "get", side_effect=fake_get):
    provider.search(_media("The Matrix", 1999), "en", None)

  assert "The.Matrix" in captured["params"]["movie"]


def test_search_returns_empty_on_http_error():
  import httpx
  provider = MilahuProvider()

  with patch.object(provider._client, "get", return_value=_mock_response(b"", 404)):
    candidates = provider.search(_media(), "en")

  assert candidates == []


def test_search_returns_empty_on_non_zip_response():
  provider = MilahuProvider()

  with patch.object(provider._client, "get", return_value=_mock_response(b"<html>not found</html>")):
    candidates = provider.search(_media(), "en")

  assert candidates == []


def test_search_returns_empty_on_network_error():
  import httpx
  provider = MilahuProvider()

  with patch.object(provider._client, "get", side_effect=httpx.ConnectError("no route")):
    candidates = provider.search(_media(), "en")

  assert candidates == []


def test_search_skips_non_srt_entries():
  provider = MilahuProvider()
  buf = io.BytesIO()
  with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("Movie.1.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    zf.writestr("README.txt", "read me")
    zf.writestr("cover.jpg", b"\xff\xd8\xff")
  zip_bytes = buf.getvalue()

  with patch.object(provider._client, "get", return_value=_mock_response(zip_bytes)):
    candidates = provider.search(_media(), "en")

  assert len(candidates) == 1
  assert candidates[0].release_name == "Movie.1.en.srt"


def test_search_respects_max_results():
  provider = MilahuProvider(max_results=2)
  zip_bytes = _make_zip(*[f"Movie.{i}.en.srt" for i in range(5)])

  with patch.object(provider._client, "get", return_value=_mock_response(zip_bytes)):
    candidates = provider.search(_media(), "en")

  assert len(candidates) == 2


# ---------------------------------------------------------------------------
# MilahuProvider.download
# ---------------------------------------------------------------------------

def test_download_copies_temp_file_to_dest(tmp_path):
  provider = MilahuProvider()
  zip_bytes = _make_zip("Movie.1.en.srt")

  with patch.object(provider._client, "get", return_value=_mock_response(zip_bytes)):
    candidates = provider.search(_media(), "en")

  assert candidates
  dest = tmp_path / "output.en.srt"
  result = provider.download(candidates[0], dest)

  assert result == dest
  assert dest.exists()
  assert "Hello" in dest.read_text()


def test_download_returns_none_when_temp_gone(tmp_path):
  from subhound.providers.base import Candidate
  provider = MilahuProvider()
  c = Candidate(source="milahu", id="0", language="en",
                release_name="x.srt", download_ref="/nonexistent/path.srt")
  assert provider.download(c, tmp_path / "out.srt") is None


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------

def test_quota_is_none():
  assert MilahuProvider().quota() is None
