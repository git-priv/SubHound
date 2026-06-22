# subhound.pipeline.orchestrator
#
# Executes the per-run pipeline from docs/PIPELINE.md with bounded parallelism.
# For each (video, language):
#   1. embedded subtitles (extract + sync-test)
#   2. existing subtitle files in the directory (sync-test)
#   3. external providers in order (local OSDB, then APIs) -- search, download,
#      sync-test, stopping at the first good sync.
# Per-(video, lang) state lives in the RunLog (tried sources, status); quota
# blocks feed the QuotaTracker and a WAITLIST status. Work is parallelised across
# entries with a thread pool, with semaphores bounding the heavy stages.

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ..config.secrets import Credentials
from ..config.settings import Settings
from ..core.extract import (
  ExtractedSubtitle,
  extract_embedded_subtitles,
  find_existing_subtitles,
)
from ..core.extract import video_frame_rate
from ..core.identify import UNKNOWN, MediaInfo, identify
from ..core.scan import scan_videos
from ..core.subtitle_convert import normalize_to_srt
from ..core.sync import Verdict, classify_offset, synchronize
from ..logging_setup import configure_logging, get_logger
from ..providers.base import Provider, QuotaExceeded
from ..providers.registry import build_providers, providers_for
from .lock import RunLock, RunLockError
from .quota import QuotaTracker
from .results import (
  DIAG_DOWNLOAD_FAILED,
  DIAG_NONE,
  DIAG_QUOTA,
  DIAG_REJECTED,
  FAILED,
  SOURCE_EMBEDDED,
  SOURCE_EXISTING,
  SUCCESS,
  WAITLIST,
  ResultRow,
  RunLog,
  read_results_tsv,
  result_key,
  write_diagnostics_tsv,
  write_results_tsv,
)

RESULTS_FILENAME = "parallel_pipeline_results.tsv"
DIAGNOSTICS_FILENAME = "parallel_pipeline_diagnostics.tsv"
STATE_DIRNAME = ".subhound"


@dataclass
class RunStats:
  # Summary counters surfaced in the TUI.
  total_pairs: int = 0       # total (video, language) pairs in the run
  skipped: int = 0           # carried-over successes (not reprocessed)
  processed: int = 0         # pairs actually worked this run
  succeeded: int = 0         # good subtitle obtained this run
  failed: int = 0            # exhausted all sources, no good sub
  waitlisted: int = 0        # blocked on a quota; awaiting reset
  undetermined: int = 0      # videos whose media type could not be determined
  by_source: dict[str, int] = field(default_factory=dict)  # successes per source


@dataclass
class PipelineEvents:
  # Optional callbacks for live UI updates; all default to no-ops.
  on_entry: Callable[[ResultRow], None] | None = None   # an entry finished
  on_stats: Callable[[RunStats], None] | None = None     # stats changed


class Orchestrator:
  # Runs the subtitle pipeline over a target directory.

  # Function Summary:
  #    Construct the orchestrator. Heavy collaborators (providers, extract/sync
  #    functions) are injectable so the engine can be tested without ffmpeg.
  #
  #  Input (parameters):
  #    settings [Settings]:          run configuration
  #    credentials [Credentials]:    provider credentials
  #    events [PipelineEvents|None]: UI callbacks
  #    providers [dict|None]:        prebuilt providers (default: built from settings)
  #    embedded_fn / existing_fn / sync_fn / normalize_fn / fps_fn: overridable
  #                                  core ops for testing
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    Orchestrator(Settings(), Credentials()).run(Path("/media"))
  def __init__(
    self,
    settings: Settings,
    credentials: Credentials,
    events: PipelineEvents | None = None,
    providers: dict | None = None,
    embedded_fn: Callable = extract_embedded_subtitles,
    existing_fn: Callable = find_existing_subtitles,
    sync_fn: Callable = synchronize,
    normalize_fn: Callable = normalize_to_srt,
    fps_fn: Callable = video_frame_rate,
  ) -> None:
    self.settings = settings
    self.credentials = credentials
    self.events = events or PipelineEvents()
    self._providers = providers
    self._embedded_fn = embedded_fn
    self._existing_fn = existing_fn
    self._sync_fn = sync_fn
    self._normalize_fn = normalize_fn
    self._fps_fn = fps_fn
    self._fps_cache: dict[str, float | None] = {}
    self.quota = QuotaTracker()
    self.stats = RunStats()
    self.log = get_logger()
    self._lock = threading.Lock()
    # Heavy-stage concurrency limits (shared across entry workers).
    self._extract_sem = threading.Semaphore(max(1, settings.max_concurrent_extract))
    self._sync_sem = threading.Semaphore(max(1, settings.max_concurrent_sync))
    self._search_sem = threading.Semaphore(max(1, settings.max_concurrent_search))
    self.run_log = RunLog()
    # Keys carried over as already-successful (skipped); excluded from derived
    # success/processed counts. Injectable sleep for quota-reset waits (tests).
    self._skipped_keys: set[str] = set()
    self._sleep: Callable[[float], None] = time.sleep

  # Function Summary:
  #    Run the full pipeline over a directory, holding a per-directory lock so two
  #    subhound processes can't clobber the same TSVs. By default it keeps running
  #    after the main pass -- waiting out quota resets and draining wait-lists
  #    across days (wait_for_quota=True); pass wait_for_quota=False for a single
  #    pass (e.g. a scheduled/cron run that exits and is restarted later).
  #
  #  Input (parameters):
  #    target_dir [Path]:               directory (or file) to process
  #    resync [bool]:                   True = reprocess even past successes
  #    wait_for_quota [bool]:           True (default) = keep running, waiting for
  #                                     exhausted sources to reset and retrying;
  #                                     False = one pass then return
  #    max_quota_wait_seconds [int]:    skip a reset that is further off than this
  #
  #  Output:
  #    stats [RunStats]:  the final run summary
  #
  # Example:
  #    Orchestrator(s, c).run(Path("/media"))  ->  RunStats(succeeded=3, ...)
  def run(
    self,
    target_dir: Path,
    resync: bool = False,
    wait_for_quota: bool = True,
    max_quota_wait_seconds: int = 24 * 60 * 60,
  ) -> RunStats:
    target_dir = Path(target_dir)
    state_dir = target_dir / STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    # One run per directory: a second concurrent process would corrupt the shared
    # results/diagnostics TSVs and run-log sidecar.
    lock = RunLock(state_dir / "lock")
    if not lock.acquire():
      raise RunLockError(f"Another subhound run is already active for {target_dir}")
    try:
      return self._execute(
        target_dir, state_dir, resync, wait_for_quota, max_quota_wait_seconds)
    finally:
      lock.release()

  # Function Summary:
  #    The locked run body: discover, identify, build the run log (skipping
  #    previous successes unless resync; resuming prior in-progress state),
  #    process every pending (video, lang) in parallel, optionally keep draining
  #    quota pools, and persist the results/diagnostics TSVs + run-log sidecar.
  #
  #  Input (parameters):
  #    target_dir [Path]:               directory being processed
  #    state_dir [Path]:                the .subhound state directory
  #    resync [bool]:                   reprocess past successes
  #    wait_for_quota [bool]:           keep running across quota resets
  #    max_quota_wait_seconds [int]:    longest reset to wait out
  #
  #  Output:
  #    stats [RunStats]:  the final run summary
  #
  # Example:
  #    self._execute(target_dir, state_dir, False, True, 86400)
  def _execute(
    self,
    target_dir: Path,
    state_dir: Path,
    resync: bool,
    wait_for_quota: bool,
    max_quota_wait_seconds: int,
  ) -> RunStats:
    work_dir = state_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(state_dir / "logs", logging.INFO)
    results_path = target_dir / RESULTS_FILENAME
    run_log_path = state_dir / "run_log.json"

    self.log.info("Scanning %s", target_dir)
    videos = scan_videos(target_dir, self.settings.skip_dirs)
    self.log.info("Found %d video files", len(videos))

    identified: list[tuple[Path, MediaInfo]] = []
    info_by_path: dict[str, MediaInfo] = {}
    for video in videos:
      info = identify(video, self.settings.series_mode, self.settings.unwanted_terms)
      identified.append((video, info))
      info_by_path[str(video)] = info
      if info.media_type == UNKNOWN:
        self.stats.undetermined += 1
        self.log.warning("Undetermined media type: %s", info.note or video)

    previous = read_results_tsv(results_path)
    # Resume in-progress state (wait-lists, tried-sources, diagnostics) from the
    # prior sidecar so a restart -- after a reboot or a scheduled re-run -- picks
    # up where it left off instead of redoing work. This is what makes the
    # slow-drip / multi-day quota case work across separate runs.
    prior_log = RunLog.load(run_log_path)
    self.run_log = RunLog.build(identified, self.settings.languages, previous, resync,
                                prior_entries=prior_log.entries)
    self.stats.total_pairs = len(self.run_log.entries)
    self._skipped_keys = {k for k, e in self.run_log.entries.items() if e.is_success()}
    self.stats.skipped = len(self._skipped_keys)
    self._emit_stats()

    providers = self._providers if self._providers is not None else build_providers(
      self.settings, self.credentials)
    pending = self.run_log.pending()
    self.log.info("Processing %d pairs (%d skipped)", len(pending), self.stats.skipped)

    workers = max(1, self.settings.max_concurrent_videos)
    self._process_pool(
      [result_key(e.row.video_path, e.row.lang) for e in pending],
      info_by_path, providers, work_dir, workers)

    diag_path = target_dir / DIAGNOSTICS_FILENAME
    self._persist(results_path, diag_path, run_log_path)

    if wait_for_quota:
      self._drain_quota_pools(
        info_by_path, providers, work_dir, workers, max_quota_wait_seconds,
        persist=lambda: self._persist(results_path, diag_path, run_log_path))

    self._persist(results_path, diag_path, run_log_path)
    self.log.info(
      "Done: %d succeeded, %d failed, %d waitlisted, %d skipped",
      self.stats.succeeded, self.stats.failed, self.stats.waitlisted, self.stats.skipped)
    self._emit_stats()
    return self.stats

  # Function Summary:
  #    Process a batch of entry keys in parallel through the full per-entry
  #    pipeline, surfacing any unexpected worker exceptions.
  #
  #  Input (parameters):
  #    keys [list[str]]:      run-log entry keys to process
  #    info_by_path [dict]:   video_path -> MediaInfo
  #    providers [dict]:      built providers
  #    work_dir [Path]:       scratch dir
  #    workers [int]:         max concurrent entry workers
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._process_pool(keys, info_by_path, providers, work_dir, 4)
  def _process_pool(
    self,
    keys: list[str],
    info_by_path: dict,
    providers: dict,
    work_dir: Path,
    workers: int,
  ) -> None:
    if not keys:
      return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
      futures = [
        pool.submit(self._process_entry, key, info_by_path, providers, work_dir)
        for key in keys
      ]
      for fut in as_completed(futures):
        fut.result()  # surface unexpected exceptions

  # Function Summary:
  #    Persist the run's state: the mandated results TSV, the wide diagnostics TSV,
  #    and the run-log JSON sidecar (used for resume). Called at the end of the run
  #    and after each quota-drain cycle so progress survives a reboot or restart.
  #
  #  Input (parameters):
  #    results_path [Path]:   the mandated results TSV path
  #    diag_path [Path]:      the diagnostics TSV path
  #    run_log_path [Path]:   the run-log sidecar path
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._persist(results_path, diag_path, run_log_path)
  def _persist(self, results_path: Path, diag_path: Path, run_log_path: Path) -> None:
    write_results_tsv(results_path, self.run_log.to_rows())
    write_diagnostics_tsv(diag_path, self.run_log.entries.values())
    self.run_log.save(run_log_path)

  # Function Summary:
  #    Drain the wait-lists of quota-exhausted sources, cycling across successive
  #    quota resets. Each iteration takes the soonest-resetting source (OpenSubtitles
  #    preferred on ties) whose reset is within max_wait and that still has blocked
  #    entries, waits out the reset, clears it, and reprocesses its pool -- then
  #    persists progress. Because a small daily quota (e.g. OpenSubtitles' few
  #    downloads/day) re-exhausts immediately, this keeps looping over days for a
  #    long-running app. A source whose whole cycle resolves nothing is "stalled"
  #    and left wait-listed for a later run (avoids spinning); sources with an
  #    unknown reset, or a reset beyond max_wait, are also left for later. The user
  #    can instead exit and schedule periodic re-runs (see scheduling).
  #
  #  Input (parameters):
  #    info_by_path [dict]:        video_path -> MediaInfo
  #    providers [dict]:           built providers
  #    work_dir [Path]:            scratch dir
  #    workers [int]:              max concurrent entry workers
  #    max_wait [int]:             skip a reset further off than this many seconds
  #    persist [Callable|None]:    called after each cycle to save progress
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._drain_quota_pools(info_by_path, providers, work_dir, 4, 3600)
  def _drain_quota_pools(
    self,
    info_by_path: dict,
    providers: dict,
    work_dir: Path,
    workers: int,
    max_wait: int,
    persist: Callable[[], None] | None = None,
  ) -> None:
    stalled: set[str] = set()
    while True:
      # Soonest-resetting source first, OpenSubtitles preferred on ties; only
      # sources still within max_wait, not stalled, and with blocked entries left.
      ready = sorted(
        ((s, w) for (s, w) in self.quota.resettable()
         if s not in stalled and w <= max_wait and self.run_log.pool_for_source(s)),
        key=lambda sw: (sw[1], sw[0] != "opensubtitles_com"))
      if not ready:
        break
      source, wait = ready[0]
      pool = [result_key(e.row.video_path, e.row.lang)
              for e in self.run_log.pool_for_source(source)]
      self.quota.clear(source)
      if wait > 0:
        self.log.info("Waiting %ds for %s quota to reset (%d waitlisted)",
                      wait, source, len(pool))
        self._sleep(wait)
      self.log.info("Retrying %s for %d waitlisted pairs", source, len(pool))
      self._process_pool(pool, info_by_path, providers, work_dir, workers)
      if persist is not None:
        persist()
      # If a whole reset cycle resolved nothing for this source, waiting again now
      # won't help -- leave the rest wait-listed for a later run / scheduled restart.
      if not any(self.run_log.entries[k].is_success() for k in pool):
        stalled.add(source)

  # Function Summary:
  #    Process one (video, lang) entry through every stage until a good sub is
  #    found or all eligible sources are exhausted, then record the outcome.
  #
  #  Input (parameters):
  #    key [str]:                       the run-log entry key
  #    info_by_path [dict]:             video_path -> MediaInfo
  #    providers [dict]:                built providers
  #    work_dir [Path]:                 scratch dir for downloads / synced files
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._process_entry(key, info_by_path, providers, work_dir)
  def _process_entry(self, key: str, info_by_path: dict, providers: dict, work_dir: Path) -> None:
    entry = self.run_log.entries[key]
    video_path = Path(entry.row.video_path)
    lang = entry.row.lang
    media = info_by_path[entry.row.video_path]

    try:
      # Stage 1: embedded subtitles (skipped if already tried, e.g. on a
      # quota-reset retry of a wait-listed entry).
      if entry.needs_source(SOURCE_EMBEDDED):
        with self._extract_sem:
          embedded = self._embedded_fn(video_path, [lang], work_dir)
        if self._try_local(key, video_path, lang, embedded, SOURCE_EMBEDDED, work_dir):
          return
      # Stage 2: existing subtitle files in the directory.
      if entry.needs_source(SOURCE_EXISTING):
        existing = self._existing_fn(video_path, [lang])
        if self._try_local(key, video_path, lang, existing, SOURCE_EXISTING, work_dir):
          return
      # Stage 3: external providers in fallback order.
      order = providers_for(providers, self.settings, media.media_type)
      blocked = self._try_providers(key, video_path, media, lang, order, work_dir)
      if not entry.is_success():
        self.run_log.mark_unresolved(key, WAITLIST if blocked else FAILED)
    except Exception as exc:  # noqa: BLE001 - one bad entry must not kill the run
      self.log.exception("Error processing %s [%s]: %s", video_path, lang, exc)
      self.run_log.mark_unresolved(key, FAILED)
    finally:
      self._finish_entry(entry.row)

  # Function Summary:
  #    Sync-test a list of local subtitles (embedded or existing) for the entry's
  #    language; on the first non-rejected sync, place the named .srt next to the
  #    video and mark the entry successful.
  #
  #  Input (parameters):
  #    key [str]:                          run-log entry key
  #    video_path [Path]:                  the video
  #    lang [str]:                         language code
  #    subs [list[ExtractedSubtitle]]:     local subtitle candidates
  #    source [str]:                       SOURCE_EMBEDDED or SOURCE_EXISTING
  #    work_dir [Path]:                    scratch dir for synced output
  #
  #  Output:
  #    ok [bool]:  True if a good subtitle was placed
  #
  # Example:
  #    self._try_local(key, video, "en", subs, SOURCE_EMBEDDED, work)  ->  True
  def _try_local(
    self,
    key: str,
    video_path: Path,
    lang: str,
    subs: list[ExtractedSubtitle],
    source: str,
    work_dir: Path,
  ) -> bool:
    usable = [s for s in subs if not s.language or s.language.lower() == lang.lower()]
    self.run_log.mark_source_tried(key, source, candidates=len(usable))
    self.run_log.record_diag(key, source, candidates=len(subs), lang_match=len(usable))
    for sub in usable:
      out = work_dir / f"{video_path.stem}.{lang}.{source}.synced.srt"
      if self._sync_and_place(key, video_path, sub.path, lang, sub.forced, source, out):
        return True
    # No good local sub: none usable -> "none"; some tested but rejected -> "rejected".
    self.run_log.record_diag(key, source, outcome=DIAG_NONE if not usable else DIAG_REJECTED)
    return False

  # Function Summary:
  #    Try external providers in order for one entry: skip sources already tried
  #    or currently quota-exhausted, search + download up to top_downloads
  #    candidates each, sync-test, and stop at the first good sub. Quota errors
  #    mark the source exhausted and wait-list the entry.
  #
  #  Input (parameters):
  #    key [str]:                  run-log entry key
  #    video_path [Path]:          the video
  #    media [MediaInfo]:          identification
  #    lang [str]:                 language code
  #    order [list[Provider]]:     providers in fallback order for this media type
  #    work_dir [Path]:            scratch dir
  #
  #  Output:
  #    blocked [bool]:  True if the entry was left blocked on a quota (not failed)
  #
  # Example:
  #    self._try_providers(key, video, info, "en", provs, work)  ->  False
  def _try_providers(
    self,
    key: str,
    video_path: Path,
    media: MediaInfo,
    lang: str,
    order: list[Provider],
    work_dir: Path,
  ) -> bool:
    entry = self.run_log.entries[key]
    blocked = False
    for provider in order:
      if not entry.needs_source(provider.name):
        continue
      if self.quota.is_exhausted(provider.name):
        self.quota.add_waitlist(provider.name, key)
        self.run_log.record_diag(key, provider.name, outcome=DIAG_QUOTA, tried=False)
        blocked = True
        continue
      try:
        with self._search_sem:
          candidates = provider.search(media, lang, video_path)
      except QuotaExceeded as exc:
        self.quota.mark_exhausted(provider.name, exc.reset_seconds)
        self.quota.add_waitlist(provider.name, key)
        self.run_log.record_diag(key, provider.name, outcome=DIAG_QUOTA)
        blocked = True
        continue
      # Count candidates now, but don't mark the source "tried" yet: a download
      # that hits a quota below must leave the source retriable.
      self.run_log.mark_source_tried(key, provider.name, candidates=len(candidates),
                                     mark_tried=False)
      # Provider searches are language-specific, so every candidate is a match.
      self.run_log.record_diag(key, provider.name,
                               candidates=len(candidates), lang_match=len(candidates))
      downloaded_any = False
      for cand in candidates[: max(1, self.settings.top_downloads)]:
        work_sub = work_dir / f"{video_path.stem}.{lang}.{provider.name}.{cand.id}.srt"
        try:
          downloaded = provider.download(cand, work_sub)
        except QuotaExceeded as exc:
          self.quota.mark_exhausted(provider.name, exc.reset_seconds)
          self.quota.add_waitlist(provider.name, key)
          self.run_log.record_diag(key, provider.name, outcome=DIAG_QUOTA)
          blocked = True
          break
        if not downloaded:
          continue  # download/integrity failure; try the next candidate
        downloaded_any = True
        out = work_dir / f"{video_path.stem}.{lang}.{provider.name}.synced.srt"
        if self._sync_and_place(key, video_path, downloaded, lang, cand.forced,
                                provider.name, out):
          return False  # success; not blocked
      else:
        # Provider fully tried without a good sub and without a quota block: mark
        # it done (so it's never re-queried) and classify why for diagnostics.
        self.run_log.mark_source_tried(key, provider.name)
        if not candidates:
          outcome = DIAG_NONE
        elif not downloaded_any:
          outcome = DIAG_DOWNLOAD_FAILED
        else:
          outcome = DIAG_REJECTED
        self.run_log.record_diag(key, provider.name, outcome=outcome)
    return blocked

  # Function Summary:
  #    Synchronise one subtitle against the video, judge it against the
  #    thresholds, and on a non-rejected result copy it to the final named file
  #    beside the video and record success.
  #
  #  Input (parameters):
  #    key [str]:            run-log entry key
  #    video_path [Path]:    the video
  #    subtitle [Path]:      the candidate subtitle file to sync
  #    lang [str]:           language code
  #    forced [bool]:        whether this is a forced track
  #    source [str]:         the source name (for the result + stats)
  #    out [Path]:           scratch path for the synced output
  #
  #  Output:
  #    ok [bool]:  True if a good subtitle was produced and placed
  #
  # Example:
  #    self._sync_and_place(key, video, sub, "en", False, "local_osdb", out)  ->  True
  def _sync_and_place(
    self,
    key: str,
    video_path: Path,
    subtitle: Path,
    lang: str,
    forced: bool,
    source: str,
    out: Path,
  ) -> bool:
    # Normalise to clean UTF-8 SRT (the universally supported sidecar format)
    # before syncing. Image/garbage payloads fail here and the candidate is skipped.
    pre = out.parent / f"{out.stem}.pre.srt"
    normalized = self._normalize_fn(subtitle, pre, lambda: self._video_fps(video_path))
    if normalized is None:
      return False
    with self._sync_sem:
      result = self._sync_fn(video_path, normalized, out)
    if not result.success or result.output_path is None:
      return False
    # Record the measured offset (even when rejected) so the diagnostics TSV shows
    # how close each candidate was to the acceptance thresholds.
    self.run_log.add_diag_offset(key, source, result.offset)
    verdict = classify_offset(
      result.offset or 0.0,
      self.settings.accept_offset_threshold,
      self.settings.reject_offset_threshold,
    )
    if verdict == Verdict.REJECT:
      return False
    final = self._final_path(video_path, lang, forced)
    try:
      shutil.copyfile(result.output_path, final)
    except OSError as exc:
      self.log.error("Could not place subtitle %s: %s", final, exc)
      return False
    self.run_log.record_success(key, source, str(final), result.offset)
    self.log.info("[%s] %s [%s] offset=%.3fs verdict=%s", source, video_path.name,
                  lang, result.offset or 0.0, verdict.value)
    return True

  # Function Summary:
  #    Compute the final Plex-style subtitle path beside the video.
  #
  #  Input (parameters):
  #    video_path [Path]:  the video
  #    lang [str]:         language code
  #    forced [bool]:      whether to add a ".forced" tag
  #
  #  Output:
  #    path [Path]:  e.g. "/m/Movie.en.srt" or "/m/Movie.en.forced.srt"
  #
  # Example:
  #    self._final_path(Path("/m/Movie.mkv"), "en", False)  ->  PosixPath("/m/Movie.en.srt")
  def _final_path(self, video_path: Path, lang: str, forced: bool) -> Path:
    suffix = ".forced" if forced else ""
    return video_path.parent / f"{video_path.stem}.{lang}{suffix}.srt"

  # Function Summary:
  #    The video's frame rate (probed once and cached), used only to time
  #    frame-based MicroDVD subtitles during normalisation.
  #
  #  Input (parameters):
  #    video_path [Path]:  the video to probe
  #
  #  Output:
  #    fps [float | None]:  frames per second, or None if unprobeable
  #
  # Example:
  #    self._video_fps(Path("/m/Movie.mkv"))  ->  23.976
  def _video_fps(self, video_path: Path) -> float | None:
    key = str(video_path)
    with self._lock:
      if key in self._fps_cache:
        return self._fps_cache[key]
    fps = self._fps_fn(video_path)
    with self._lock:
      self._fps_cache[key] = fps
    return fps

  # Function Summary:
  #    Recompute the derived summary counters from the run-log entries. Counting
  #    from current state (rather than incrementing) keeps the stats correct when
  #    an entry is reprocessed during quota-pool draining (no double-counting).
  #    Carried-over successes (self._skipped_keys) are excluded from the worked
  #    counts and reported only as `skipped`.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._recompute_stats()
  def _recompute_stats(self) -> None:
    succeeded = failed = waitlisted = processed = 0
    by_source: dict[str, int] = {}
    for key, entry in self.run_log.entries.items():
      if key in self._skipped_keys:
        continue
      status = entry.row.status
      if status == SUCCESS:
        succeeded += 1
        processed += 1
        by_source[entry.row.result] = by_source.get(entry.row.result, 0) + 1
      elif status == WAITLIST:
        waitlisted += 1
        processed += 1
      elif status == FAILED:
        failed += 1
        processed += 1
    with self._lock:
      self.stats.processed = processed
      self.stats.succeeded = succeeded
      self.stats.failed = failed
      self.stats.waitlisted = waitlisted
      self.stats.by_source = by_source

  # Function Summary:
  #    Mark an entry finished: refresh the derived stats and emit UI callbacks.
  #
  #  Input (parameters):
  #    row [ResultRow]:  the entry's final row
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._finish_entry(row)
  def _finish_entry(self, row: ResultRow) -> None:
    self._recompute_stats()
    if self.events.on_entry:
      self.events.on_entry(row)
    self._emit_stats()

  # Function Summary:
  #    Emit the current stats to the UI callback, if any.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._emit_stats()
  def _emit_stats(self) -> None:
    if self.events.on_stats:
      self.events.on_stats(self.stats)
