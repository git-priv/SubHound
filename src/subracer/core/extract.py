# subracer.core.extract
#
# Two local subtitle sources for the per-video pipeline, both tried before any
# network lookup:
#   * embedded subtitle tracks inside the video container (probed with ffprobe,
#     extracted to .srt with ffmpeg), and
#   * existing external subtitle files already sitting next to the video.
#
# Only text-based tracks are extracted (SubRip/ASS/SSA/mov_text/WebVTT); image
# tracks (PGS/VobSub/DVB) need OCR and are skipped. Cleaned-up port of the
# Subservient extraction phase, without its global config coupling.

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .subtitle_lang import detect_subtitle_language, to_iso639_1
from .tools import ffmpeg, ffprobe

# Text subtitle codecs ffmpeg can convert straight to SRT.
TEXT_SUB_CODECS = frozenset({
  "subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text",
})
# Subtitle file extensions we recognise on disk.
SUBTITLE_EXTENSIONS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub"})


@dataclass
class SubtitleStream:
  # One subtitle track inside a video container.
  index: int            # subtitle-relative index for ffmpeg's 0:s:<index> map
  codec: str            # codec_name from ffprobe (e.g. "subrip", "hdmv_pgs_subtitle")
  language: str         # 2-letter ISO code if known, else "" (empty)
  title: str            # track title tag, if any
  forced: bool          # disposition.forced
  default: bool         # disposition.default
  hearing_impaired: bool  # disposition.hearing_impaired

  # Function Summary:
  #    Whether this track is text-based (extractable to SRT) rather than image-based.
  #
  #  Input (parameters):
  #    self [SubtitleStream]:  the stream
  #
  #  Output:
  #    is_text [bool]:  True if the codec is a known text subtitle codec
  #
  # Example:
  #    SubtitleStream(0,"subrip","en","",False,True,False).is_text()  ->  True
  def is_text(self) -> bool:
    return self.codec in TEXT_SUB_CODECS


@dataclass
class ExtractedSubtitle:
  # A subtitle file produced by extraction or found on disk.
  path: Path            # the subtitle file
  language: str         # 2-letter ISO code if known, else ""
  forced: bool          # whether it is a forced track
  source: str           # "embedded" or "existing"


# Function Summary:
#    Probe a video file for its subtitle tracks using ffprobe.
#
#  Input (parameters):
#    video_path [Path]:  the video container to inspect
#
#  Output:
#    streams [list[SubtitleStream]]:  subtitle tracks (empty if none / no ffprobe)
#
# Example:
#    probe_subtitle_streams(Path("movie.mkv"))  ->  [SubtitleStream(0, "subrip", "en", ...)]
def probe_subtitle_streams(video_path: Path) -> list[SubtitleStream]:
  probe = ffprobe()
  if not probe or not video_path.exists():
    return []
  cmd = [
    probe, "-v", "quiet", "-select_streams", "s",
    "-show_streams", "-of", "json", str(video_path),
  ]
  try:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
  except (OSError, subprocess.SubprocessError):
    return []
  if out.returncode != 0:
    return []
  try:
    data = json.loads(out.stdout or "{}")
  except json.JSONDecodeError:
    return []
  streams: list[SubtitleStream] = []
  for idx, s in enumerate(data.get("streams", [])):
    tags = s.get("tags", {}) or {}
    disp = s.get("disposition", {}) or {}
    lang_tag = (tags.get("language") or "").strip()
    streams.append(SubtitleStream(
      index=idx,
      codec=(s.get("codec_name") or "").lower(),
      language=to_iso639_1(lang_tag) if lang_tag and lang_tag.lower() != "und" else "",
      title=(tags.get("title") or "").strip(),
      forced=bool(disp.get("forced")),
      default=bool(disp.get("default")),
      hearing_impaired=bool(disp.get("hearing_impaired")),
    ))
  return streams


# Function Summary:
#    Probe a video's frame rate (frames per second) with ffprobe. Needed to time
#    frame-based MicroDVD subtitles when converting them to SRT.
#
#  Input (parameters):
#    video_path [Path]:  the video to inspect
#
#  Output:
#    fps [float | None]:  the average frame rate, or None if it can't be probed
#
# Example:
#    video_frame_rate(Path("movie.mkv"))  ->  23.976023976023978
def video_frame_rate(video_path: Path) -> float | None:
  probe = ffprobe()
  if not probe or not video_path.exists():
    return None
  cmd = [
    probe, "-v", "quiet", "-select_streams", "v:0",
    "-show_entries", "stream=avg_frame_rate,r_frame_rate",
    "-of", "json", str(video_path),
  ]
  try:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
  except (OSError, subprocess.SubprocessError):
    return None
  if out.returncode != 0:
    return None
  try:
    streams = json.loads(out.stdout or "{}").get("streams", [])
  except json.JSONDecodeError:
    return None
  if not streams:
    return None
  for field in ("avg_frame_rate", "r_frame_rate"):
    value = (streams[0].get(field) or "").strip()
    num, _, den = value.partition("/")
    try:
      n, d = float(num), float(den or "1")
      if n > 0 and d > 0:
        return n / d
    except ValueError:
      continue
  return None


# Function Summary:
#    Extract one subtitle track to an SRT file using ffmpeg.
#
#  Input (parameters):
#    video_path [Path]:       the source video
#    stream [SubtitleStream]: the track to extract (uses its subtitle-relative index)
#    out_path [Path]:         destination .srt file
#
#  Output:
#    ok [bool]:  True if the SRT file was written successfully
#
# Example:
#    extract_stream(Path("v.mkv"), stream, Path("v.en.srt"))  ->  True
def extract_stream(video_path: Path, stream: SubtitleStream, out_path: Path) -> bool:
  binary = ffmpeg()
  if not binary or not stream.is_text():
    return False
  out_path.parent.mkdir(parents=True, exist_ok=True)
  cmd = [
    binary, "-y", "-v", "quiet",
    "-i", str(video_path),
    "-map", f"0:s:{stream.index}",
    "-c:s", "srt",
    str(out_path),
  ]
  try:
    rc = subprocess.run(cmd, capture_output=True, text=True, timeout=300).returncode
  except (OSError, subprocess.SubprocessError):
    return False
  return rc == 0 and out_path.exists() and out_path.stat().st_size > 0


# Function Summary:
#    Extract embedded text subtitle tracks whose language is wanted (or unknown)
#    to SRT files in a destination directory, detecting the language from text
#    when the container did not tag it.
#
#  Input (parameters):
#    video_path [Path]:           the source video
#    wanted_languages [list[str]]: 2-letter ISO codes to keep (empty = keep all)
#    dest_dir [Path]:             where to write extracted .srt files
#
#  Output:
#    subs [list[ExtractedSubtitle]]:  the extracted subtitles
#
# Example:
#    extract_embedded_subtitles(Path("v.mkv"), ["en"], Path("/tmp"))
#      ->  [ExtractedSubtitle(path=..., language="en", forced=False, source="embedded")]
def extract_embedded_subtitles(
  video_path: Path,
  wanted_languages: list[str],
  dest_dir: Path,
) -> list[ExtractedSubtitle]:
  wanted = {w.lower() for w in wanted_languages}
  results: list[ExtractedSubtitle] = []
  for stream in probe_subtitle_streams(video_path):
    if not stream.is_text():
      continue
    # Keep tracks that match a wanted language, are untagged (detect later), or
    # when no language filter is configured.
    if wanted and stream.language and stream.language not in wanted:
      continue
    tag = stream.language or "und"
    suffix = ".forced" if stream.forced else ""
    out_path = dest_dir / f"{video_path.stem}.{tag}{suffix}.s{stream.index}.srt"
    if not extract_stream(video_path, stream, out_path):
      continue
    lang = stream.language or (detect_subtitle_language(out_path) or "")
    # If we detected a language and it isn't wanted, drop the file.
    if wanted and lang and lang.lower() not in wanted:
      out_path.unlink(missing_ok=True)
      continue
    results.append(ExtractedSubtitle(out_path, lang, stream.forced, "embedded"))
  return results


# Function Summary:
#    Find external subtitle files already sitting next to a video (e.g.
#    "movie.en.srt"), excluding subracer's own working/synced files.
#
#  Input (parameters):
#    video_path [Path]:           the video whose siblings to scan
#    wanted_languages [list[str]]: 2-letter ISO codes to keep (empty = keep all)
#
#  Output:
#    subs [list[ExtractedSubtitle]]:  existing subtitle files found
#
# Example:
#    find_existing_subtitles(Path("/m/Movie.mkv"), ["en"])
#      ->  [ExtractedSubtitle(path=Path("/m/Movie.en.srt"), language="en", ...)]
def find_existing_subtitles(
  video_path: Path,
  wanted_languages: list[str],
) -> list[ExtractedSubtitle]:
  wanted = {w.lower() for w in wanted_languages}
  folder = video_path.parent
  stem = video_path.stem
  results: list[ExtractedSubtitle] = []
  if not folder.is_dir():
    return results
  for entry in sorted(folder.iterdir()):
    if not entry.is_file() or entry.suffix.lower() not in SUBTITLE_EXTENSIONS:
      continue
    if not entry.name.startswith(stem):
      continue
    # Skip our own synced artifacts (extracted files go to a separate dest_dir).
    if ".synced." in entry.name.lower():
      continue
    # Derive language from the Plex-style suffix between stem and extension.
    middle = entry.name[len(stem):].lstrip(".")
    forced = "forced" in middle.lower()
    parts = [p for p in middle.split(".") if p and p.lower() not in ("forced", "srt", "ass", "ssa", "vtt", "sub")]
    lang = to_iso639_1(parts[0]) if parts else ""
    if not lang:
      lang = detect_subtitle_language(entry) or ""
    if wanted and lang and lang.lower() not in wanted:
      continue
    results.append(ExtractedSubtitle(entry, lang, forced, "existing"))
  return results
