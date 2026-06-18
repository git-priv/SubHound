# subracer.pipeline.quota
#
# Tracks which rate-limited sources are currently exhausted and when they reset,
# plus the wait-list of (video, lang) keys that were blocked on each source.
# Thread-safe so the orchestrator's worker pool can update it concurrently.
#
# Per docs/PIPELINE.md: when an API's limit is hit, items needing only that
# source go on its wait-list; once the reset elapses, the source's pool is
# reprocessed. The run log's pool_for_source() supplies the actual entries.

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _Exhausted:
  # Bookkeeping for one exhausted source.
  since: float                     # monotonic time the source was marked exhausted
  reset_seconds: int | None        # seconds-until-reset reported by the provider
  # (video, lang) keys blocked here, in arrival order (FIFO). A dict is used as an
  # insertion-ordered set: re-adding an existing key keeps its original position so
  # the earliest-blocked videos are retried first when the quota resets.
  waitlist: dict[str, None] = field(default_factory=dict)


class QuotaTracker:
  # Thread-safe record of exhausted sources, their reset times, and wait-lists.

  # Function Summary:
  #    Create an empty quota tracker.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    QuotaTracker()
  def __init__(self) -> None:
    self._lock = threading.Lock()
    self._exhausted: dict[str, _Exhausted] = {}

  # Function Summary:
  #    Mark a source exhausted (idempotent: keeps the earliest record) and record
  #    its reported reset time.
  #
  #  Input (parameters):
  #    source [str]:               the source name
  #    reset_seconds [int|None]:   seconds until the quota resets, if known
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    tracker.mark_exhausted("opensubtitles_com", 1800)
  def mark_exhausted(self, source: str, reset_seconds: int | None) -> None:
    with self._lock:
      existing = self._exhausted.get(source)
      if existing is None:
        self._exhausted[source] = _Exhausted(time.monotonic(), reset_seconds)
      elif reset_seconds is not None:
        existing.reset_seconds = reset_seconds

  # Function Summary:
  #    Add a (video, lang) key to a source's wait-list.
  #
  #  Input (parameters):
  #    source [str]:  the exhausted source
  #    key [str]:     the (video, lang) run-log key blocked on it
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    tracker.add_waitlist("opensubtitles_com", "/m/A.mkv\ten")
  def add_waitlist(self, source: str, key: str) -> None:
    with self._lock:
      record = self._exhausted.get(source)
      if record is None:
        record = _Exhausted(time.monotonic(), None)
        self._exhausted[source] = record
      # Reassigning an existing key leaves its insertion order unchanged (FIFO).
      record.waitlist[key] = None

  # Function Summary:
  #    Whether a source is currently considered exhausted (and not yet past its
  #    reset window).
  #
  #  Input (parameters):
  #    source [str]:  the source name
  #
  #  Output:
  #    exhausted [bool]:  True if exhausted and the reset has not yet elapsed
  #
  # Example:
  #    tracker.is_exhausted("opensubtitles_com")  ->  True
  def is_exhausted(self, source: str) -> bool:
    with self._lock:
      record = self._exhausted.get(source)
      if record is None:
        return False
      if record.reset_seconds is None:
        return True
      return (time.monotonic() - record.since) < record.reset_seconds

  # Function Summary:
  #    Seconds remaining until a source's quota resets (0 if already due / unknown
  #    reset and currently exhausted).
  #
  #  Input (parameters):
  #    source [str]:  the source name
  #
  #  Output:
  #    seconds [int]:  seconds until reset (0 if due or no record)
  #
  # Example:
  #    tracker.seconds_until_reset("opensubtitles_com")  ->  1723
  def seconds_until_reset(self, source: str) -> int:
    with self._lock:
      record = self._exhausted.get(source)
      if record is None or record.reset_seconds is None:
        return 0
      remaining = record.reset_seconds - (time.monotonic() - record.since)
      return max(0, int(remaining))

  # Function Summary:
  #    The set of source names currently marked exhausted.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    sources [set[str]]:  exhausted source names
  #
  # Example:
  #    tracker.exhausted_sources()  ->  {"opensubtitles_com"}
  def exhausted_sources(self) -> set[str]:
    with self._lock:
      return set(self._exhausted.keys())

  # Function Summary:
  #    List exhausted sources that reported a known reset time, with the seconds
  #    remaining until each resets. Sources with an unknown reset are omitted
  #    (the orchestrator can't schedule a retry for those).
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    pending [list[tuple[str, int]]]:  (source, seconds_until_reset) pairs
  #
  # Example:
  #    tracker.resettable()  ->  [("opensubtitles_com", 1723)]
  def resettable(self) -> list[tuple[str, int]]:
    now = time.monotonic()
    with self._lock:
      out = []
      for source, record in self._exhausted.items():
        if record.reset_seconds is None:
          continue
        out.append((source, max(0, int(record.reset_seconds - (now - record.since)))))
      return out

  # Function Summary:
  #    Clear a source's exhausted state and return the wait-list it had collected
  #    (used to reprocess that source's pool after its quota resets).
  #
  #  Input (parameters):
  #    source [str]:  the source to clear
  #
  #  Output:
  #    waitlist [list[str]]:  the (video, lang) keys that were waiting on it, in
  #                           arrival order (FIFO)
  #
  # Example:
  #    tracker.clear("opensubtitles_com")  ->  ["/m/A.mkv\ten"]
  def clear(self, source: str) -> list[str]:
    with self._lock:
      record = self._exhausted.pop(source, None)
      return list(record.waitlist) if record else []
