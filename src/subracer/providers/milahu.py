# subracer.providers.milahu
#
# Provider backed by milahu's get-subtitles service at
# http://milahu.duckdns.org/bin/get-subtitles — a single HTTP GET returns a
# ZIP archive containing SRT files for the requested video + language. No API
# key or account required. This is the first-priority network provider because
# it is fast, free, and covers both movies and TV.
#
# The call is a combined search+download: one GET request returns the ZIP,
# candidates are extracted immediately, and download() is just a temp→dest copy.

from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx

from ..core.identify import MediaInfo
from .base import Candidate, Provider, QuotaState

_BASE_URL = "http://milahu.duckdns.org/bin/get-subtitles"


class MilahuProvider(Provider):
  name = "milahu"
  supports_movies = True
  supports_tv = True

  # Function Summary:
  #    Build the provider.
  #
  #  Input (parameters):
  #    max_results [int]:  maximum candidates to return per search call
  #    timeout [float]:    HTTP request timeout in seconds
  def __init__(self, max_results: int = 10, timeout: float = 60.0) -> None:
    self.max_results = max_results
    self._client = httpx.Client(timeout=timeout)

  # Function Summary:
  #    Search by hitting the milahu endpoint with the video filename + language.
  #    The response is a ZIP; SRT files are extracted to a temp dir and returned
  #    as Candidates whose download_ref is the temp file path.
  #
  #  Input (parameters):
  #    media [MediaInfo]:         the identified video
  #    lang [str]:                2-letter ISO 639-1 language code
  #    video_path [Path | None]:  the video file; its filename is passed to the
  #                               service for best parsing accuracy
  #
  #  Output:
  #    candidates [list[Candidate]]
  #
  # Example:
  #    provider.search(info, "en", Path("Inception.2010.mkv"))  ->  [Candidate(...)]
  def search(self, media: MediaInfo, lang: str, video_path: Path | None = None) -> list[Candidate]:
    movie = video_path.name if video_path else _synthetic_filename(media)
    try:
      resp = self._client.get(_BASE_URL, params={"movie": movie, "lang": lang})
      resp.raise_for_status()
    except httpx.HTTPStatusError:
      return []
    except httpx.HTTPError:
      return []
    if not _is_zip(resp.content):
      return []
    return self._extract_candidates(resp.content, lang)[: self.max_results]

  # Function Summary:
  #    Copy the already-extracted SRT from its temp location to dest_path.
  #
  #  Input (parameters):
  #    candidate [Candidate]:  candidate with download_ref = temp file path
  #    dest_path [Path]:       where to write the subtitle
  #
  #  Output:
  #    path [Path | None]:  dest_path on success, None if the temp file is gone
  #
  # Example:
  #    provider.download(cand, Path("Movie.en.srt"))  ->  PosixPath("Movie.en.srt")
  def download(self, candidate: Candidate, dest_path: Path) -> Path | None:
    src = Path(candidate.download_ref)
    if not src.exists():
      return None
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_path)
    return dest_path

  # Function Summary:
  #    No quota — milahu's service has no documented rate limiting.
  #
  #  Output:
  #    quota [QuotaState | None]:  always None
  def quota(self) -> QuotaState | None:
    return None

  def _extract_candidates(self, zip_bytes: bytes, lang: str) -> list[Candidate]:
    tmpdir = Path(tempfile.mkdtemp(prefix="subracer_milahu_"))
    candidates: list[Candidate] = []
    try:
      with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for i, name in enumerate(zf.namelist()):
          if not name.lower().endswith(".srt"):
            continue
          dest = tmpdir / name
          dest.write_bytes(zf.read(name))
          candidates.append(Candidate(
            source=self.name,
            id=str(i),
            language=lang,
            release_name=name,
            download_ref=str(dest),
            fmt="srt",
          ))
    except zipfile.BadZipFile:
      pass
    return candidates


def _is_zip(data: bytes) -> bool:
  return data[:2] == b"PK"


def _synthetic_filename(media: MediaInfo) -> str:
  title = (media.title_or_show or "Unknown").replace(" ", ".")
  if media.media_type == "tv" and media.season is not None and media.episode is not None:
    return f"{title}.S{media.season:02d}E{media.episode:02d}.mkv"
  year = f".{media.year}" if media.year else ""
  return f"{title}{year}.mkv"
