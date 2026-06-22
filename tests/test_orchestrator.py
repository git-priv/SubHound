# End-to-end orchestrator tests using fakes (no ffmpeg/ffsubsync/network):
# injected extract/existing/sync functions and fake providers exercise the
# stage order, short-circuit, results TSV + skip-on-rerun, and quota -> WAITLIST.

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from subhound.config.secrets import Credentials
from subhound.config.settings import Settings, Source
from subhound.core.extract import ExtractedSubtitle
from subhound.core.sync import SyncResult
from subhound.pipeline.orchestrator import Orchestrator
from subhound.pipeline.results import SUCCESS, WAITLIST, read_results_tsv, result_key
from subhound.providers.base import Candidate, Provider, QuotaExceeded


def _settings(**kw) -> Settings:
  base = dict(languages=["en"],
              enabled_sources=[Source.OPENSUBTITLES_COM],
              source_order=[Source.OPENSUBTITLES_COM], max_concurrent_videos=2)
  base.update(kw)
  return Settings(**base)


def _media_tree() -> Path:
  d = Path(tempfile.mkdtemp())
  movie = d / "The Matrix (1999)"
  movie.mkdir()
  (movie / "The Matrix (1999).mkv").write_bytes(b"x" * 100000)
  return d


def _media_tree_n(n: int) -> Path:
  d = Path(tempfile.mkdtemp())
  for i in range(n):
    sub = d / f"Movie{i} (2020)"
    sub.mkdir()
    (sub / f"Movie{i} (2020).mkv").write_bytes(b"x" * 100000)
  return d


def _good_sync(video, sub, out):
  # Pretend ffsubsync succeeded with a tiny offset; "produce" the output file.
  Path(out).parent.mkdir(parents=True, exist_ok=True)
  Path(out).write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nHi.\n")
  return SyncResult(True, 0.05, Path(out))


def _no_subs(*a, **k):
  return []


def _passthru_normalize(src, dest, fps_fn=None):
  # Tests use trivial fake subtitle payloads; skip real format conversion and
  # sync the original file as-is. (normalize_to_srt is unit-tested separately.)
  return Path(src)


class FakeProvider(Provider):
  name = "opensubtitles_com"

  def __init__(self, *, raise_quota=False):
    self.raise_quota = raise_quota
    self.searched = 0

  def search(self, media, lang, video_path=None):
    self.searched += 1
    if self.raise_quota:
      raise QuotaExceeded(self.name, reset_seconds=1800)
    return [Candidate(self.name, "c1", lang, "Release", download_ref="c1")]

  def download(self, candidate, dest_path):
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(b"sub")
    return Path(dest_path)


def test_provider_success_writes_results_and_places_srt():
  d = _media_tree()
  prov = FakeProvider()
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  stats = orch.run(d)
  assert stats.succeeded == 1 and stats.failed == 0
  # The named subtitle landed next to the video.
  assert (d / "The Matrix (1999)" / "The Matrix (1999).en.srt").exists()
  # Results TSV recorded the success from the provider.
  rows = read_results_tsv(d / "parallel_pipeline_results.tsv")
  row = next(iter(rows.values()))
  assert row.status == SUCCESS and row.result == "opensubtitles_com"
  assert row.api_candidates == 1 and row.good_subtitle is True


def test_run_refuses_when_directory_already_locked():
  from subhound.pipeline.lock import RunLock, RunLockError
  d = _media_tree()
  held = RunLock(d / ".subhound" / "lock")
  assert held.acquire()
  try:
    orch = Orchestrator(
      _settings(), Credentials(),
      providers={Source.OPENSUBTITLES_COM: FakeProvider()},
      embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
      normalize_fn=_passthru_normalize,
    )
    with pytest.raises(RunLockError):
      orch.run(d)
  finally:
    held.release()


def test_diagnostics_tsv_written_with_per_source_detail():
  d = _media_tree()
  prov = FakeProvider()
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  orch.run(d)
  diag = d / "parallel_pipeline_diagnostics.tsv"
  assert diag.exists()
  header, row = diag.read_text(encoding="utf-8").splitlines()
  cells = dict(zip(header.split("\t"), row.split("\t")))
  # The winning provider is recorded as "good"; the unused local stages as tried/none.
  assert cells["opensubtitles_com_outcome"] == "good"
  assert cells["opensubtitles_com_candidates"] == "1"
  assert cells["embedded_tried"] == "true" and cells["embedded_outcome"] == "none"


def test_embedded_short_circuits_before_providers():
  d = _media_tree()
  video = next((d).rglob("*.mkv"))

  def fake_embedded(vp, langs, dest):
    out = Path(dest) / "emb.en.srt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"emb")
    return [ExtractedSubtitle(out, "en", False, "embedded")]

  prov = FakeProvider()
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov},
    embedded_fn=fake_embedded, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  stats = orch.run(d)
  assert stats.succeeded == 1
  assert prov.searched == 0  # provider never consulted -> short-circuited
  rows = read_results_tsv(d / "parallel_pipeline_results.tsv")
  assert next(iter(rows.values())).result == "embedded"


def test_rerun_skips_previous_success():
  d = _media_tree()
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: FakeProvider()},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  orch.run(d)
  # Second run: the prior success is carried over and not reprocessed.
  prov2 = FakeProvider()
  orch2 = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov2},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  stats = orch2.run(d)
  assert stats.skipped == 1 and stats.processed == 0
  assert prov2.searched == 0


def test_quota_exhaustion_waitlists():
  d = _media_tree()
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: FakeProvider(raise_quota=True)},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  stats = orch.run(d, wait_for_quota=False)  # single pass: just verify wait-listing
  assert stats.waitlisted == 1 and stats.succeeded == 0
  rows = read_results_tsv(d / "parallel_pipeline_results.tsv")
  assert next(iter(rows.values())).status == WAITLIST
  assert "opensubtitles_com" in orch.quota.exhausted_sources()


class QuotaThenOkProvider(Provider):
  # Raises QuotaExceeded on the first search, then succeeds on later searches.
  name = "opensubtitles_com"

  def __init__(self, reset_seconds=900):
    self.reset_seconds = reset_seconds
    self.searched = 0

  def search(self, media, lang, video_path=None):
    self.searched += 1
    if self.searched == 1:
      raise QuotaExceeded(self.name, reset_seconds=self.reset_seconds)
    return [Candidate(self.name, "c1", lang, "Release", download_ref="c1")]

  def download(self, candidate, dest_path):
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(b"sub")
    return Path(dest_path)


def test_quota_reset_drains_waitlist_to_success():
  d = _media_tree()
  prov = QuotaThenOkProvider(reset_seconds=900)
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  slept: list[float] = []
  orch._sleep = slept.append  # don't actually wait for the reset window

  stats = orch.run(d, wait_for_quota=True)

  # The first pass wait-listed; the drain waited out the reset and retried.
  # (The slept value is the remaining countdown, ~900s minus tiny elapsed time.)
  assert len(slept) == 1 and 895 <= slept[0] <= 900
  assert prov.searched == 2
  assert stats.succeeded == 1 and stats.waitlisted == 0 and stats.failed == 0
  rows = read_results_tsv(d / "parallel_pipeline_results.tsv")
  row = next(iter(rows.values()))
  assert row.status == SUCCESS and row.result == "opensubtitles_com"
  assert "opensubtitles_com" not in orch.quota.exhausted_sources()


def test_resume_waitlisted_entry_across_runs_skips_local_restages():
  d = _media_tree()
  # Run 1 (e.g. a scheduled run): provider is out of quota -> entry wait-listed,
  # state persisted to the sidecar.
  orch1 = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: FakeProvider(raise_quota=True)},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  assert orch1.run(d, wait_for_quota=False).waitlisted == 1  # single pass, then "exit"

  # Run 2 (a separate process): provider works now. Resume must re-attempt the
  # wait-listed pair WITHOUT re-extracting embedded subs (already tried in run 1).
  calls = {"embedded": 0}

  def counting_embedded(vp, langs, dest):
    calls["embedded"] += 1
    return []

  orch2 = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: FakeProvider()},
    embedded_fn=counting_embedded, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  stats = orch2.run(d)
  assert calls["embedded"] == 0  # embedded already tried -> skipped on resume
  assert stats.succeeded == 1 and stats.waitlisted == 0
  rows = read_results_tsv(d / "parallel_pipeline_results.tsv")
  assert next(iter(rows.values())).status == SUCCESS


class DripProvider(Provider):
  # Serves `per_cycle` downloads, then raises QuotaExceeded (reset 0) and refills
  # on the next cycle -- simulating a small daily quota (e.g. OpenSubtitles).
  name = "opensubtitles_com"

  def __init__(self, per_cycle=1):
    self.per_cycle = per_cycle
    self.served = 0
    self.cycles = 0

  def search(self, media, lang, video_path=None):
    return [Candidate(self.name, "c1", lang, "Release", download_ref="c1")]

  def download(self, candidate, dest_path):
    if self.served >= self.per_cycle:
      self.served = 0          # quota refills next cycle
      self.cycles += 1
      raise QuotaExceeded(self.name, reset_seconds=100)
    self.served += 1
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(dest_path).write_bytes(b"sub")
    return Path(dest_path)


def test_multi_cycle_drain_drips_until_all_resolved():
  d = _media_tree_n(3)
  prov = DripProvider(per_cycle=1)
  orch = Orchestrator(
    _settings(max_concurrent_videos=1), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  slept: list[float] = []
  orch._sleep = slept.append

  stats = orch.run(d, wait_for_quota=True)

  # All three resolved despite only one download per reset cycle.
  assert stats.succeeded == 3 and stats.waitlisted == 0 and stats.failed == 0
  assert prov.cycles >= 2  # required multiple reset cycles to finish


def test_quota_reset_skipped_when_beyond_max_wait():
  d = _media_tree()
  prov = QuotaThenOkProvider(reset_seconds=7200)
  orch = Orchestrator(
    _settings(), Credentials(),
    providers={Source.OPENSUBTITLES_COM: prov},
    embedded_fn=_no_subs, existing_fn=_no_subs, sync_fn=_good_sync,
    normalize_fn=_passthru_normalize,
  )
  slept: list[float] = []
  orch._sleep = slept.append

  # Reset is 2h out but we only allow waiting 1h -> leave it wait-listed.
  stats = orch.run(d, wait_for_quota=True, max_quota_wait_seconds=3600)

  assert slept == []
  assert prov.searched == 1
  assert stats.waitlisted == 1 and stats.succeeded == 0
  assert "opensubtitles_com" in orch.quota.exhausted_sources()
