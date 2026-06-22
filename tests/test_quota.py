# Tests for pipeline.quota.QuotaTracker: exhaustion/reset state and FIFO wait-list
# ordering (videos blocked first are retried first when the quota resets).

from __future__ import annotations

from subhound.pipeline.quota import QuotaTracker


def test_waitlist_is_fifo_ordered():
  q = QuotaTracker()
  q.mark_exhausted("opensubtitles_com", 1800)
  for key in ("c", "a", "b", "a"):  # "a" added twice -> keeps first position
    q.add_waitlist("opensubtitles_com", key)
  assert q.clear("opensubtitles_com") == ["c", "a", "b"]


def test_clear_unknown_source_returns_empty_list():
  assert QuotaTracker().clear("nope") == []


def test_resettable_lists_only_known_resets():
  q = QuotaTracker()
  q.mark_exhausted("opensubtitles_com", 600)
  q.mark_exhausted("subsource", None)  # unknown reset -> omitted
  resettable = dict(q.resettable())
  assert "opensubtitles_com" in resettable and "subsource" not in resettable
  assert 0 <= resettable["opensubtitles_com"] <= 600


def test_exhausted_state_and_clear():
  q = QuotaTracker()
  q.mark_exhausted("opensubtitles_com", 1800)
  assert q.is_exhausted("opensubtitles_com")
  q.clear("opensubtitles_com")
  assert not q.is_exhausted("opensubtitles_com")
