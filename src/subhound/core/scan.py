# subhound.core.scan
#
# Recursive discovery of video files under a directory, honoring a skip-list of
# directory names (case-insensitive). The first stage of the pipeline.

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

# Container/video extensions we consider "videos" worth finding subtitles for.
VIDEO_EXTENSIONS: frozenset[str] = frozenset({
  ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".webm",
  ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".ogv", ".divx",
})


# Function Summary:
#    Decide whether a path is a video file we should process, based on its
#    extension (case-insensitive).
#
#  Input (parameters):
#    path [Path]:  a filesystem path
#
#  Output:
#    is_video [bool]:  True if the extension is a known video container
#
# Example:
#    is_video_file(Path("Movie.MKV"))  ->  True
def is_video_file(path: Path) -> bool:
  return path.suffix.lower() in VIDEO_EXTENSIONS


# Function Summary:
#    Recursively yield video files under a root directory, skipping any directory
#    whose name matches the skip-list (case-insensitive) at any depth.
#
#  Input (parameters):
#    root [Path]:                  directory to scan (a single file is also accepted)
#    skip_dirs [Iterable[str]]:    directory names to skip (case-insensitive)
#
#  Output:
#    videos [Iterator[Path]]:  paths to discovered video files (sorted per dir)
#
# Example:
#    list(iter_videos(Path("/media"), ["extras"]))  ->  [PosixPath("/media/Movie/Movie.mkv")]
def iter_videos(root: Path, skip_dirs: Iterable[str]) -> Iterator[Path]:
  skip = {d.strip().lower() for d in skip_dirs if d.strip()}
  if root.is_file():
    if is_video_file(root):
      yield root
    return
  # Manual walk so we can prune skipped directories before descending.
  stack: list[Path] = [root]
  while stack:
    current = stack.pop()
    try:
      entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, FileNotFoundError):
      continue
    dirs: list[Path] = []
    for entry in entries:
      if entry.is_dir():
        if entry.name.lower() in skip:
          continue
        dirs.append(entry)
      elif is_video_file(entry):
        yield entry
    # Push dirs reversed so the sorted order is preserved when popping.
    stack.extend(reversed(dirs))


# Function Summary:
#    Eagerly collect all video files under a root into a sorted list.
#
#  Input (parameters):
#    root [Path]:                  directory (or file) to scan
#    skip_dirs [Iterable[str]]:    directory names to skip (case-insensitive)
#
#  Output:
#    videos [list[Path]]:  all discovered video file paths, sorted by full path
#
# Example:
#    scan_videos(Path("/media"), ["samples"])  ->  [PosixPath("/media/A/A.mkv"), ...]
def scan_videos(root: Path, skip_dirs: Iterable[str]) -> list[Path]:
  return sorted(iter_videos(root, skip_dirs), key=lambda p: str(p).lower())
