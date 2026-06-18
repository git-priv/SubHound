# Tests for subracer.pipeline.results: TSV round-trip, run-log build + skip logic,
# tried-source tracking, per-source quota pools, and run-log persistence.

from __future__ import annotations

import tempfile
from pathlib import Path

from subracer.core.identify import MediaInfo
from subracer.pipeline.results import (
  DIAG_GOOD,
  DIAG_REJECTED,
  PENDING,
  RESULT_COLUMNS,
  SUCCESS,
  WAITLIST,
  ResultRow,
  RunLog,
  diagnostics_columns,
  read_results_tsv,
  result_key,
  write_diagnostics_tsv,
  write_results_tsv,
)


def _tmp() -> Path:
  return Path(tempfile.mkdtemp())


def _movie(path: Path, title: str = "A Movie", year: int = 2020) -> tuple[Path, MediaInfo]:
  return path, MediaInfo("movie", title, f"{title} {year}", year)


def test_tsv_roundtrip_preserves_types():
  d = _tmp()
  rows = [
    ResultRow(
      video_path="/m/A.mkv", video_size=123, video_mtime_ns=456, updated_at="t",
      type="movie", title_or_show="A", year=2020, season=None, episode=None,
      video_filename="A.mkv", lang="en", extracted_from_video=1, existing_subs=0,
      db_candidates=2, api_candidates=3, sync_offset=0.25, good_subtitle=True,
      result="local_osdb", status=SUCCESS, subtitle_file="/m/A.en.srt",
    ),
    ResultRow(
      video_path="/t/S.mkv", video_size=9, video_mtime_ns=9, updated_at="t",
      type="tv", title_or_show="Show", year=None, season=1, episode=2,
      video_filename="S.mkv", lang="nl", status=PENDING,
    ),
  ]
  p = d / "results.tsv"
  write_results_tsv(p, rows)
  back = read_results_tsv(p)
  a = back[result_key("/m/A.mkv", "en")]
  assert a.year == 2020 and a.sync_offset == 0.25 and a.good_subtitle is True
  assert a.db_candidates == 2 and a.status == SUCCESS
  s = back[result_key("/t/S.mkv", "nl")]
  assert s.season == 1 and s.episode == 2 and s.year is None and s.good_subtitle is False


def test_build_skips_previous_success_unless_resync():
  d = _tmp()
  video = d / "A.mkv"
  video.write_bytes(b"x" * 1000)
  st = video.stat()
  prev = {
    result_key(str(video), "en"): ResultRow(
      video_path=str(video), video_size=st.st_size, video_mtime_ns=st.st_mtime_ns,
      updated_at="t", type="movie", title_or_show="A", year=2020, season=None,
      episode=None, video_filename="A.mkv", lang="en", good_subtitle=True,
      status=SUCCESS, subtitle_file="/m/A.en.srt",
    )
  }
  # Not resync: unchanged successful video is carried over and NOT pending.
  log = RunLog.build([_movie(video)], ["en"], prev, resync=False)
  assert log.entries[result_key(str(video), "en")].is_success()
  assert log.pending() == []

  # Resync: it is reprocessed (pending again).
  log2 = RunLog.build([_movie(video)], ["en"], prev, resync=True)
  assert len(log2.pending()) == 1
  assert log2.entries[result_key(str(video), "en")].row.status == PENDING


def test_changed_video_is_not_skipped():
  d = _tmp()
  video = d / "A.mkv"
  video.write_bytes(b"x" * 1000)
  prev = {
    result_key(str(video), "en"): ResultRow(
      video_path=str(video), video_size=999999, video_mtime_ns=1,  # stale size
      updated_at="t", type="movie", title_or_show="A", year=2020, season=None,
      episode=None, video_filename="A.mkv", lang="en", good_subtitle=True,
      status=SUCCESS,
    )
  }
  log = RunLog.build([_movie(video)], ["en"], prev, resync=False)
  assert len(log.pending()) == 1  # fingerprint mismatch -> reprocess


def test_tried_sources_and_pools():
  d = _tmp()
  v1 = d / "A.mkv"; v1.write_bytes(b"x")
  v2 = d / "B.mkv"; v2.write_bytes(b"x")
  log = RunLog.build([_movie(v1, "A"), _movie(v2, "B")], ["en"], {}, resync=False)
  k1 = result_key(str(v1), "en")
  k2 = result_key(str(v2), "en")

  # v1 succeeds via local DB; v2 only checked (no good sub) on opensubtitles.
  log.record_success(k1, "local_osdb", str(d / "A.en.srt"), 0.1)
  log.mark_source_tried(k2, "opensubtitles_com", candidates=4)

  # Pool for opensubtitles: v1 is done, v2 already tried -> empty.
  assert log.pool_for_source("opensubtitles_com") == []
  # Pool for subsource: only v2 still needs it.
  pool = log.pool_for_source("subsource")
  assert [e.row.video_path for e in pool] == [str(v2)]
  # api_candidates count recorded on v2.
  assert log.entries[k2].row.api_candidates == 4
  assert log.entries[k1].is_success() and not log.entries[k2].is_success()


def test_runlog_save_load_roundtrip():
  d = _tmp()
  v = d / "A.mkv"; v.write_bytes(b"x")
  log = RunLog.build([_movie(v)], ["en"], {}, resync=False)
  k = result_key(str(v), "en")
  log.mark_source_tried(k, "embedded", candidates=1)
  log.record_diag(k, "opensubtitles_com", candidates=3, lang_match=3, outcome=DIAG_REJECTED)
  log.add_diag_offset(k, "opensubtitles_com", 3.21)
  log.mark_unresolved(k, WAITLIST)
  p = d / "run_log.json"
  log.save(p)

  restored = RunLog.load(p)
  e = restored.entries[k]
  assert e.tried_sources == {"embedded"}
  assert e.row.status == WAITLIST and e.row.extracted_from_video == 1
  # Per-source diagnostics survive the sidecar round-trip.
  diag = e.diagnostics["opensubtitles_com"]
  assert diag.candidates == 3 and diag.outcome == DIAG_REJECTED and diag.offsets == [3.21]


def test_diagnostics_columns_layout():
  cols = diagnostics_columns()
  # Mandated columns come first, unchanged.
  assert cols[:len(RESULT_COLUMNS)] == RESULT_COLUMNS
  # Then five fields per source, starting with the embedded stage.
  assert cols[len(RESULT_COLUMNS):len(RESULT_COLUMNS) + 5] == [
    "embedded_tried", "embedded_candidates", "embedded_lang_match",
    "embedded_offsets", "embedded_outcome",
  ]
  assert "opensubtitles_com_outcome" in cols and "tvsubtitles_offsets" in cols


def test_write_diagnostics_tsv_flattens_per_source(tmp_path):
  v = tmp_path / "A.mkv"; v.write_bytes(b"x")
  log = RunLog.build([_movie(v)], ["en"], {}, resync=False)
  k = result_key(str(v), "en")
  log.record_diag(k, "embedded", candidates=2, lang_match=1, outcome=DIAG_REJECTED)
  log.add_diag_offset(k, "embedded", 4.5)
  log.record_success(k, "subsource", str(tmp_path / "A.en.srt"), 0.12)
  log.add_diag_offset(k, "subsource", 0.12)

  p = tmp_path / "diag.tsv"
  write_diagnostics_tsv(p, log.entries.values())
  header, row = p.read_text(encoding="utf-8").splitlines()
  cells = dict(zip(header.split("\t"), row.split("\t")))
  assert cells["video_filename"] == "A.mkv" and cells["status"] == SUCCESS
  assert cells["embedded_candidates"] == "2" and cells["embedded_lang_match"] == "1"
  assert cells["embedded_offsets"] == "4.500" and cells["embedded_outcome"] == DIAG_REJECTED
  assert cells["subsource_outcome"] == DIAG_GOOD and cells["subsource_offsets"] == "0.120"
  # An untried source is reported as not-tried with empty outcome.
  assert cells["yify_tried"] == "false" and cells["yify_outcome"] == ""
