# subracer.osdb.torrent_client
#
# Thin wrapper around libtorrent (optional dep: pip install subracer[mirror]).
# Downloads a torrent with per-file priority selection so only wanted language
# shards are fetched. Runs blocking in the calling thread — callers should run
# it in a thread pool / background thread.

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path


def download(
    torrent_source: str,
    file_filter: Callable[[str], bool],
    dest_dir: Path,
    progress_cb: Callable[[float, str], None] | None = None,
) -> list[Path]:
  """Download selected files from a torrent to dest_dir.

  Args:
    torrent_source: URL to a .torrent file, or a magnet: URI.
    file_filter:    Called with each file path inside the torrent; return True
                    to download that file, False to skip it.
    dest_dir:       Directory to write downloaded files into.
    progress_cb:    Optional callback(progress_fraction, current_filename).

  Returns:
    List of Path objects for every file that was downloaded.

  Raises:
    RuntimeError: if libtorrent is not installed.
  """
  try:
    import libtorrent as lt  # type: ignore[import-untyped]
  except ImportError:
    raise RuntimeError(
      "libtorrent is not installed. "
      "Install the mirror extra: pip install 'subracer[mirror]'"
    )

  dest_dir.mkdir(parents=True, exist_ok=True)

  session = lt.session()
  session.listen_on(6881, 6891)

  params = _build_params(lt, torrent_source, dest_dir)
  handle = session.add_torrent(params)

  # Wait for torrent metadata (needed for magnet links).
  _wait_for_metadata(handle)

  info = handle.torrent_file()
  _apply_file_priorities(handle, info, file_filter)

  wanted = _wanted_file_names(info, file_filter)

  _download_loop(handle, info, wanted, progress_cb)

  session.remove_torrent(handle)
  session.pause()

  return [dest_dir / name for name in wanted if (dest_dir / name).exists()]


def _build_params(lt, torrent_source: str, dest_dir: Path):
  params = {
    "save_path": str(dest_dir),
    "storage_mode": lt.storage_mode_t.storage_mode_sparse,
  }
  if torrent_source.startswith("magnet:"):
    params["url"] = torrent_source
  else:
    # Treat as URL to a .torrent file; fetch with httpx.
    import httpx
    resp = httpx.get(torrent_source, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    ti = lt.torrent_info(lt.bdecode(resp.content))
    params["ti"] = ti
  return params


def _wait_for_metadata(handle, timeout: float = 120.0) -> None:
  deadline = time.monotonic() + timeout
  while not handle.has_metadata():
    if time.monotonic() > deadline:
      raise TimeoutError("Timed out waiting for torrent metadata")
    time.sleep(0.5)


def _apply_file_priorities(handle, info, file_filter: Callable[[str], bool]) -> None:
  priorities = []
  for i in range(info.num_files()):
    name = info.files().file_path(i)
    priorities.append(7 if file_filter(name) else 0)
  handle.prioritize_files(priorities)


def _wanted_file_names(info, file_filter: Callable[[str], bool]) -> list[str]:
  return [
    info.files().file_path(i)
    for i in range(info.num_files())
    if file_filter(info.files().file_path(i))
  ]


def _download_loop(
  handle,
  info,
  wanted: list[str],
  progress_cb: Callable[[float, str], None] | None,
  poll_interval: float = 1.0,
) -> None:
  total_wanted = sum(
    info.files().file_size(i)
    for i in range(info.num_files())
    if info.files().file_path(i) in set(wanted)
  ) or 1

  while True:
    s = handle.status()
    downloaded = s.total_wanted_done
    fraction = min(downloaded / total_wanted, 1.0)
    current = _current_file(handle, info, wanted)
    if progress_cb is not None:
      progress_cb(fraction, current)
    if s.is_seeding or (s.total_wanted > 0 and s.total_wanted_done >= s.total_wanted):
      break
    time.sleep(poll_interval)


def _current_file(handle, info, wanted: list[str]) -> str:
  # Best-effort: find the first incomplete wanted file.
  fp = handle.file_progress()
  for i, name in enumerate(info.files().file_path(j) for j in range(info.num_files())):
    if name in set(wanted):
      size = info.files().file_size(i)
      if i < len(fp) and fp[i] < size:
        return name
  return wanted[-1] if wanted else ""
