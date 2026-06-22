# Tests for subhound.core.extract: existing-subtitle discovery (no tools needed),
# graceful probing of non-videos, and a real ffmpeg round-trip (embed a subtitle
# track, then probe + extract it) that is skipped when ffmpeg/ffprobe are absent.

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from subhound.core.extract import (
  SubtitleStream,
  extract_embedded_subtitles,
  find_existing_subtitles,
  probe_subtitle_streams,
)
from subhound.core.tools import ffmpeg, ffprobe

SRT = (
  "1\n00:00:00,500 --> 00:00:02,000\nHello there, this is a test subtitle.\n\n"
  "2\n00:00:02,500 --> 00:00:04,000\nGoodbye now, see you later friend.\n"
)


def test_is_text():
  assert SubtitleStream(0, "subrip", "en", "", False, True, False).is_text()
  assert not SubtitleStream(0, "hdmv_pgs_subtitle", "en", "", False, False, False).is_text()


def test_find_existing_subtitles_language_and_filter():
  d = Path(tempfile.mkdtemp())
  (d / "Movie.mkv").write_bytes(b"x")
  (d / "Movie.en.srt").write_text(SRT, encoding="utf-8")
  (d / "Movie.es.forced.srt").write_text(SRT, encoding="utf-8")
  (d / "Movie.synced.srt").write_text(SRT, encoding="utf-8")  # our artifact -> skip
  (d / "Unrelated.en.srt").write_text(SRT, encoding="utf-8")  # different video -> skip

  any_lang = find_existing_subtitles(d / "Movie.mkv", [])
  names = sorted(p.path.name for p in any_lang)
  assert names == ["Movie.en.srt", "Movie.es.forced.srt"]
  forced = {p.path.name: p.forced for p in any_lang}
  assert forced["Movie.es.forced.srt"] is True and forced["Movie.en.srt"] is False

  only_en = find_existing_subtitles(d / "Movie.mkv", ["en"])
  assert [p.path.name for p in only_en] == ["Movie.en.srt"]


def test_probe_non_video_is_graceful():
  d = Path(tempfile.mkdtemp())
  f = d / "notvideo.mkv"
  f.write_text("this is not a video", encoding="utf-8")
  assert probe_subtitle_streams(f) == []  # no crash, empty result


@pytest.mark.skipif(not (ffmpeg() and ffprobe()), reason="ffmpeg/ffprobe required")
def test_embedded_extraction_roundtrip():
  d = Path(tempfile.mkdtemp())
  sub = d / "track.srt"
  sub.write_text(SRT, encoding="utf-8")
  video = d / "Sample.mkv"
  # Build a tiny video with one embedded English SRT subtitle track.
  build = subprocess.run([
    ffmpeg(), "-y", "-v", "quiet",
    "-f", "lavfi", "-i", "testsrc=duration=5:size=128x96:rate=5",
    "-i", str(sub),
    "-map", "0:v", "-map", "1",
    "-c:v", "libx264", "-c:s", "srt",
    "-metadata:s:s:0", "language=eng",
    str(video),
  ], capture_output=True, text=True)
  if build.returncode != 0 or not video.exists():
    pytest.skip(f"ffmpeg could not build sample mkv: {build.stderr[-200:]}")

  streams = probe_subtitle_streams(video)
  assert len(streams) == 1
  assert streams[0].is_text() and streams[0].language == "en"

  dest = d / "out"
  subs = extract_embedded_subtitles(video, ["en"], dest)
  assert len(subs) == 1
  s = subs[0]
  assert s.source == "embedded" and s.language == "en"
  assert s.path.exists() and "Hello there" in s.path.read_text(encoding="utf-8")
