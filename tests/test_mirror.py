# tests/test_mirror.py — Phase 4 mirror tests
#
# Covers MirrorState persistence, GitHub release parsing, update detection,
# download flow (mocked torrent + language_split), and graceful failure when
# libtorrent is absent.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from subracer.config.settings import OsdbMode, Settings
from subracer.osdb.mirror import MirrorManager, MirrorState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(tmp_path: Path, **kwargs) -> Settings:
  s = Settings(osdb_storage_path=str(tmp_path / "osdb"), **kwargs)
  return s


def _make_manager(tmp_path: Path, **kwargs) -> MirrorManager:
  return MirrorManager(_settings(tmp_path, **kwargs))


def _github_response(tag: str = "2024-01", torrent_url: str = "https://example.com/shards.torrent") -> dict:
  return {
    "tag_name": tag,
    "assets": [
      {"name": "shards.torrent", "browser_download_url": torrent_url},
      {"name": "README.md", "browser_download_url": "https://example.com/README.md"},
    ],
  }


# ---------------------------------------------------------------------------
# MirrorState persistence
# ---------------------------------------------------------------------------

def test_state_roundtrip(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  assert mgr.state() is None

  state = MirrorState(
    release_tag="2024-01",
    downloaded_at="2024-01-15T12:00:00+00:00",
    languages=["en", "es"],
    files=["metadata.db", "shard_en.db", "shard_es.db"],
  )
  mgr._save_state(state)

  loaded = mgr.state()
  assert loaded is not None
  assert loaded.release_tag == "2024-01"
  assert loaded.downloaded_at == "2024-01-15T12:00:00+00:00"
  assert loaded.languages == ["en", "es"]
  assert loaded.files == ["metadata.db", "shard_en.db", "shard_es.db"]


def test_state_returns_none_on_corrupt_json(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  state_path = mgr.storage_dir() / "mirror_state.json"
  state_path.parent.mkdir(parents=True, exist_ok=True)
  state_path.write_text("not json")
  assert mgr.state() is None


# ---------------------------------------------------------------------------
# GitHub release fetch
# ---------------------------------------------------------------------------

def test_fetch_latest_release_parses_assets(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  mock_resp = MagicMock()
  mock_resp.json.return_value = _github_response("2024-02", "https://example.com/v2.torrent")
  mock_resp.raise_for_status = MagicMock()

  with patch("httpx.get", return_value=mock_resp) as mock_get:
    info = mgr.fetch_latest_release()

  assert info["tag"] == "2024-02"
  assert info["torrent_url"] == "https://example.com/v2.torrent"
  assert "milahu/opensubtitles-scraper" in mock_get.call_args[0][0]


def test_fetch_latest_release_raises_when_no_torrent_asset(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  mock_resp = MagicMock()
  mock_resp.json.return_value = {"tag_name": "2024-01", "assets": []}
  mock_resp.raise_for_status = MagicMock()

  with patch("httpx.get", return_value=mock_resp):
    with pytest.raises(ValueError, match="No .torrent asset"):
      mgr.fetch_latest_release()


def test_fetch_uses_custom_repo(tmp_path: Path) -> None:
  mgr = MirrorManager(_settings(tmp_path, osdb_mirror_repo="myfork/opensubtitles-scraper"))
  mock_resp = MagicMock()
  mock_resp.json.return_value = _github_response()
  mock_resp.raise_for_status = MagicMock()

  with patch("httpx.get", return_value=mock_resp) as mock_get:
    mgr.fetch_latest_release()

  assert "myfork/opensubtitles-scraper" in mock_get.call_args[0][0]


# ---------------------------------------------------------------------------
# Update detection
# ---------------------------------------------------------------------------

def test_update_available_true_when_no_state(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  assert mgr.update_available() is True


def test_update_available_true_when_newer_release(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  mgr._save_state(MirrorState("2024-01", "2024-01-01T00:00:00+00:00", ["en"], []))

  mock_resp = MagicMock()
  mock_resp.json.return_value = _github_response("2024-02")
  mock_resp.raise_for_status = MagicMock()

  with patch("httpx.get", return_value=mock_resp):
    assert mgr.update_available() is True


def test_update_available_false_when_same_release(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  mgr._save_state(MirrorState("2024-01", "2024-01-01T00:00:00+00:00", ["en"], []))

  mock_resp = MagicMock()
  mock_resp.json.return_value = _github_response("2024-01")
  mock_resp.raise_for_status = MagicMock()

  with patch("httpx.get", return_value=mock_resp):
    assert mgr.update_available() is False


def test_update_available_false_on_network_error(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  mgr._save_state(MirrorState("2024-01", "2024-01-01T00:00:00+00:00", ["en"], []))

  with patch("httpx.get", side_effect=httpx.ConnectError("no network")):
    assert mgr.update_available() is False


# ---------------------------------------------------------------------------
# Download flow
# ---------------------------------------------------------------------------

def test_download_flow(tmp_path: Path) -> None:
  mgr = MirrorManager(_settings(tmp_path, languages=["en"]))
  storage = mgr.storage_dir()
  storage.mkdir(parents=True, exist_ok=True)

  mock_resp = MagicMock()
  mock_resp.json.return_value = _github_response("2024-01", "https://example.com/shards.torrent")
  mock_resp.raise_for_status = MagicMock()

  fake_metadata = storage / "subtitles_all.db"
  fake_shard = storage / "shard_en.db"

  def fake_torrent_download(source, file_filter, dest_dir, progress_cb):
    # Simulate torrent client writing files to dest_dir.
    fake_metadata.touch()
    fake_shard.touch()
    return [fake_metadata, fake_shard]

  with (
    patch("httpx.get", return_value=mock_resp),
    patch("subracer.osdb.mirror.torrent_client") as mock_tc,
    patch("subracer.osdb.mirror.builder.language_split", return_value=0) as mock_ls,
  ):
    mock_tc.download.side_effect = fake_torrent_download
    mgr.download()

  # language_split was called on the raw metadata DB.
  mock_ls.assert_called_once()
  call_kwargs = mock_ls.call_args
  assert call_kwargs[0][2] == ["en"]

  # State was persisted.
  state = mgr.state()
  assert state is not None
  assert state.release_tag == "2024-01"
  assert "en" in state.languages
  assert "metadata.db" in state.files


def test_download_progress_callback_called(tmp_path: Path) -> None:
  mgr = MirrorManager(_settings(tmp_path, languages=["en"]))
  storage = mgr.storage_dir()
  storage.mkdir(parents=True, exist_ok=True)

  mock_resp = MagicMock()
  mock_resp.json.return_value = _github_response()
  mock_resp.raise_for_status = MagicMock()

  calls: list[tuple[float, str]] = []

  def fake_torrent_download(source, file_filter, dest_dir, progress_cb):
    if progress_cb:
      progress_cb(0.5, "shard_en.db")
    (storage / "subtitles_all.db").touch()
    return [storage / "subtitles_all.db"]

  with (
    patch("httpx.get", return_value=mock_resp),
    patch("subracer.osdb.mirror.torrent_client") as mock_tc,
    patch("subracer.osdb.mirror.builder.language_split", return_value=0),
  ):
    mock_tc.download.side_effect = fake_torrent_download
    mgr.download(progress_cb=lambda f, n: calls.append((f, n)))

  assert any(f == 0.5 for f, _ in calls)


# ---------------------------------------------------------------------------
# available_data_dbs / metadata_db
# ---------------------------------------------------------------------------

def test_available_data_dbs_empty_when_no_state(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  assert mgr.available_data_dbs() == []


def test_available_data_dbs_returns_existing_shards(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  storage = mgr.storage_dir()
  storage.mkdir(parents=True, exist_ok=True)
  (storage / "shard_en.db").touch()
  # shard_missing.db intentionally NOT created.
  mgr._save_state(MirrorState(
    "2024-01", "2024-01-01T00:00:00+00:00", ["en"],
    ["metadata.db", "shard_en.db", "shard_missing.db"],
  ))

  dbs = mgr.available_data_dbs()
  assert len(dbs) == 1
  assert dbs[0].name == "shard_en.db"


def test_metadata_db_returns_none_when_absent(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  assert mgr.metadata_db() is None


def test_metadata_db_returns_path_when_present(tmp_path: Path) -> None:
  mgr = _make_manager(tmp_path)
  storage = mgr.storage_dir()
  storage.mkdir(parents=True, exist_ok=True)
  (storage / "metadata.db").touch()
  assert mgr.metadata_db() == storage / "metadata.db"


# ---------------------------------------------------------------------------
# torrent_client — missing libtorrent
# ---------------------------------------------------------------------------

def test_torrent_client_raises_on_missing_libtorrent(tmp_path: Path) -> None:
  import builtins
  real_import = builtins.__import__

  def mock_import(name, *args, **kwargs):
    if name == "libtorrent":
      raise ImportError("No module named 'libtorrent'")
    return real_import(name, *args, **kwargs)

  with patch("builtins.__import__", side_effect=mock_import):
    from subracer.osdb import torrent_client
    import importlib
    importlib.reload(torrent_client)
    with pytest.raises(RuntimeError, match="subracer\\[mirror\\]"):
      torrent_client.download("magnet:?xt=...", lambda _: True, tmp_path)
