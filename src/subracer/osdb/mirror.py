# subracer.osdb.mirror
#
# MirrorManager orchestrates the Phase 4 full-mirror flow:
#   1. Fetch the latest milahu/opensubtitles-scraper torrent from GitHub releases.
#   2. Use libtorrent with per-file selection to download only wanted language shards.
#   3. Run language_split() on the full metadata DB to build a language-filtered copy.
#   4. Persist mirror_state.json so the provider and TUI can report status.
#
# The resulting files are fed to LocalOsdbProvider so subtitle bytes are served
# entirely offline with zero quota cost.

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import platformdirs

from ..config.settings import Settings
from . import builder, torrent_client

_STATE_FILENAME = "mirror_state.json"
_METADATA_DB_NAME = "metadata.db"

# Patterns that identify the main metadata DB inside a torrent.
_METADATA_PATTERNS = ("subtitles_all.db", "metadata.db", "subz_metadata.db")


@dataclass
class MirrorState:
  release_tag: str       # GitHub release tag, e.g. "2024-01"
  downloaded_at: str     # ISO 8601 UTC
  languages: list[str]   # ISO 639-1 codes that were downloaded
  files: list[str]       # basenames of all downloaded torrent files


class MirrorManager:
  def __init__(self, settings: Settings) -> None:
    self._settings = settings

  # Function Summary:
  #    Absolute directory where mirror files are stored. Uses osdb_storage_path
  #    when set, otherwise falls back to the platformdirs user data directory.
  #
  #  Output:
  #    path [Path]:  the storage directory (not necessarily created yet)
  def storage_dir(self) -> Path:
    if self._settings.osdb_storage_path:
      return Path(self._settings.osdb_storage_path)
    return Path(platformdirs.user_data_dir("subracer")) / "osdb"

  # Function Summary:
  #    Read the persisted mirror state from mirror_state.json, or None if absent.
  #
  #  Output:
  #    state [MirrorState | None]
  def state(self) -> MirrorState | None:
    path = self.storage_dir() / _STATE_FILENAME
    if not path.exists():
      return None
    try:
      data = json.loads(path.read_text(encoding="utf-8"))
      return MirrorState(
        release_tag=data["release_tag"],
        downloaded_at=data["downloaded_at"],
        languages=data["languages"],
        files=data["files"],
      )
    except (KeyError, json.JSONDecodeError, OSError):
      return None

  def _save_state(self, state: MirrorState) -> None:
    path = self.storage_dir() / _STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

  # Function Summary:
  #    Fetch the latest GitHub release for the configured mirror repo and return
  #    a dict with keys "tag" and "torrent_url".
  #
  #  Output:
  #    info [dict]:  {"tag": str, "torrent_url": str}
  #
  #  Raises:
  #    ValueError:  if no .torrent asset is found in the release
  def fetch_latest_release(self) -> dict[str, str]:
    repo = self._settings.osdb_mirror_repo
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    resp = httpx.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    tag = data.get("tag_name", "")
    torrent_url = next(
      (a["browser_download_url"] for a in data.get("assets", [])
       if a["name"].endswith(".torrent")),
      None,
    )
    if not torrent_url:
      raise ValueError(
        f"No .torrent asset found in the latest release of {repo} (tag {tag!r}). "
        "Check the repo's releases page."
      )
    return {"tag": tag, "torrent_url": torrent_url}

  # Function Summary:
  #    Return True when a newer GitHub release is available than the one recorded
  #    in mirror_state.json.
  #
  #  Output:
  #    available [bool]
  def update_available(self) -> bool:
    current = self.state()
    if current is None:
      return True
    try:
      info = self.fetch_latest_release()
    except Exception:
      return False
    return info["tag"] != current.release_tag

  # Function Summary:
  #    Full download flow: fetch release → select language shards via libtorrent
  #    per-file priority → language_split the metadata DB → persist state.
  #
  #  Input (parameters):
  #    progress_cb [Callable | None]:  callback(fraction: float, filename: str)
  def download(self, progress_cb: Callable[[float, str], None] | None = None) -> None:
    release = self.fetch_latest_release()
    languages = self._settings.effective_osdb_languages()
    storage = self.storage_dir()

    def file_filter(torrent_path: str) -> bool:
      name = Path(torrent_path).name.lower()
      # Always include the metadata DB.
      if any(name == p for p in _METADATA_PATTERNS):
        return True
      # Include any data shard whose filename contains a wanted language code.
      return any(f"_{lang}." in name or f"_{lang}_" in name for lang in languages)

    downloaded_paths = torrent_client.download(
      release["torrent_url"], file_filter, storage, progress_cb
    )
    downloaded_names = [p.name for p in downloaded_paths]

    # Identify the raw metadata DB among downloaded files.
    raw_metadata = next(
      (storage / name for name in downloaded_names
       if name.lower() in _METADATA_PATTERNS),
      None,
    )
    if raw_metadata and raw_metadata.exists():
      dst = storage / _METADATA_DB_NAME
      builder.language_split(raw_metadata, dst, languages)
      # Replace the raw file reference with the filtered one.
      if raw_metadata.name != _METADATA_DB_NAME:
        raw_metadata.unlink(missing_ok=True)
        downloaded_names = [
          n for n in downloaded_names if n.lower() not in _METADATA_PATTERNS
        ]
        downloaded_names.append(_METADATA_DB_NAME)

    self._save_state(MirrorState(
      release_tag=release["tag"],
      downloaded_at=datetime.now(timezone.utc).isoformat(),
      languages=languages,
      files=downloaded_names,
    ))

  # Function Summary:
  #    Return the paths of the language shard data DBs (excludes the metadata DB).
  #    Only returns paths that exist on disk.
  #
  #  Output:
  #    paths [list[Path]]
  def available_data_dbs(self) -> list[Path]:
    st = self.state()
    if st is None:
      return []
    storage = self.storage_dir()
    return [
      storage / name
      for name in st.files
      if name != _METADATA_DB_NAME and (storage / name).exists()
    ]

  # Function Summary:
  #    Return the language-filtered metadata DB path, or None if not present.
  #
  #  Output:
  #    path [Path | None]
  def metadata_db(self) -> Path | None:
    path = self.storage_dir() / _METADATA_DB_NAME
    return path if path.exists() else None
