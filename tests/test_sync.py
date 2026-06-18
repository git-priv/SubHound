# Tests for subracer.core.sync (offset math, threshold classification, offset
# application, and ffsubsync error handling). The actual ffsubsync alignment is
# not unit-tested here (it needs a real spoken-audio video); the orchestrator's
# end-to-end test covers a real run.

from __future__ import annotations

import tempfile
from pathlib import Path

from subracer.core.sync import (
  Verdict,
  apply_offset_ms,
  classify_offset,
  first_timestamp,
  subtitle_offset,
  synchronize,
)

SRT = (
  "1\n"
  "00:00:05,000 --> 00:00:07,500\n"
  "Hello there.\n\n"
  "2\n"
  "00:00:09,000 --> 00:00:11,000\n"
  "General Kenobi.\n"
)


def _write(text: str) -> Path:
  p = Path(tempfile.mkdtemp()) / "sub.srt"
  p.write_text(text, encoding="utf-8")
  return p


def test_first_timestamp():
  assert first_timestamp(_write(SRT)) == 5.0


def test_subtitle_offset():
  a = _write(SRT)
  shifted = SRT.replace("00:00:05,000", "00:00:06,500")
  b = _write(shifted)
  assert abs(subtitle_offset(a, b) - 1.5) < 1e-6


def test_classify_offset_boundaries():
  assert classify_offset(0.02, 0.05, 2.5) is Verdict.ACCEPT
  assert classify_offset(0.05, 0.05, 2.5) is Verdict.ACCEPT  # inclusive
  assert classify_offset(1.0, 0.05, 2.5) is Verdict.VERIFY
  assert classify_offset(2.5, 0.05, 2.5) is Verdict.REJECT   # inclusive
  assert classify_offset(9.0, 0.05, 2.5) is Verdict.REJECT


def test_apply_offset_ms_positive():
  p = _write(SRT)
  assert apply_offset_ms(p, 1000) is True
  assert first_timestamp(p) == 6.0


def test_apply_offset_ms_clamps_at_zero():
  p = _write(SRT)
  assert apply_offset_ms(p, -10_000) is True  # would go negative -> clamp to 0
  assert first_timestamp(p) == 0.0


def test_synchronize_missing_inputs():
  d = Path(tempfile.mkdtemp())
  r = synchronize(d / "nope.mkv", d / "nope.srt", d / "out.srt")
  assert r.success is False and r.error and "video not found" in r.error
  (d / "v.mkv").write_bytes(b"x")
  r2 = synchronize(d / "v.mkv", d / "nope.srt", d / "out.srt")
  assert r2.success is False and "subtitle not found" in r2.error
