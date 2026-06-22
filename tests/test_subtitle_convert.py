# Tests for core.subtitle_convert.normalize_to_srt: every text subtitle format
# (SRT/ASS/SSA/VTT/MicroDVD) converts to clean UTF-8 SRT with timing preserved,
# MicroDVD uses the probed fps, and non-subtitle payloads are rejected.

from __future__ import annotations

from pathlib import Path

from subhound.core.subtitle_convert import detect_subtitle_format, normalize_to_srt

ASS = """[Script Info]
ScriptType: v4.00+
[V4+ Styles]
Format: Name
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello {\\i1}world{\\i0}
"""

VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHi there\n"

SRT = "1\n00:00:01,000 --> 00:00:02,500\nAlready srt\n"

MICRODVD = "{0}{25}Hello\n{26}{50}World\n"


def test_detect_format():
  assert detect_subtitle_format(VTT) == "vtt"
  assert detect_subtitle_format(ASS) == "ass"
  assert detect_subtitle_format(MICRODVD) == "microdvd"
  assert detect_subtitle_format(SRT) == "srt"
  assert detect_subtitle_format("<!DOCTYPE html><html>nope") is None
  assert detect_subtitle_format("") is None


def test_ass_to_srt_preserves_timing_and_drops_styling(tmp_path):
  src = tmp_path / "in.ass"
  src.write_text(ASS, encoding="utf-8")
  out = normalize_to_srt(src, tmp_path / "out.srt")
  assert out is not None
  text = out.read_text(encoding="utf-8")
  assert "00:00:01,000 --> 00:00:02,500" in text  # timing preserved
  assert "<i>world</i>" in text                    # inline style -> srt tag
  assert "Dialogue:" not in text                   # ASS scaffolding gone


def test_vtt_to_srt(tmp_path):
  src = tmp_path / "in.vtt"
  src.write_text(VTT, encoding="utf-8")
  out = normalize_to_srt(src, tmp_path / "out.srt")
  assert out is not None
  text = out.read_text(encoding="utf-8")
  assert "00:00:01,000 --> 00:00:02,500" in text and "Hi there" in text
  assert "WEBVTT" not in text


def test_microdvd_uses_probed_fps(tmp_path):
  src = tmp_path / "in.sub"
  src.write_text(MICRODVD, encoding="utf-8")
  # 25 fps -> frame 25 = 1.000s; frame 26 = 1.040s.
  out = normalize_to_srt(src, tmp_path / "out.srt", fps_fn=lambda: 25.0)
  assert out is not None
  text = out.read_text(encoding="utf-8")
  assert "00:00:00,000 --> 00:00:01,000" in text
  assert "00:00:01,040 --> 00:00:02,000" in text


def test_srt_passthrough_is_clean_utf8(tmp_path):
  # A latin-1 encoded SRT with an accented char should come back as valid UTF-8.
  src = tmp_path / "in.srt"
  src.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nCafé\n".encode("latin-1"))
  out = normalize_to_srt(src, tmp_path / "out.srt")
  assert out is not None
  assert out.read_text(encoding="utf-8").count("Café") == 1


def test_extension_is_ignored_in_favour_of_content(tmp_path):
  # ASS content mislabeled as .srt is still detected and converted correctly.
  src = tmp_path / "mislabeled.srt"
  src.write_text(ASS, encoding="utf-8")
  out = normalize_to_srt(src, tmp_path / "out.srt")
  assert out is not None and "<i>world</i>" in out.read_text(encoding="utf-8")


def test_non_subtitle_rejected(tmp_path):
  src = tmp_path / "err.srt"
  src.write_text("<!DOCTYPE html><html><body>404 Not Found</body></html>", encoding="utf-8")
  assert normalize_to_srt(src, tmp_path / "out.srt") is None


def test_missing_file_returns_none(tmp_path):
  assert normalize_to_srt(tmp_path / "nope.srt", tmp_path / "out.srt") is None
