# Tests for the per-directory run lock that stops two subhound processes from
# clobbering the same TSVs.

from __future__ import annotations

import pytest

from subhound.pipeline.lock import RunLock, RunLockError


def test_second_acquire_is_blocked_until_released(tmp_path):
  p = tmp_path / "lock"
  first = RunLock(p)
  assert first.acquire() is True
  second = RunLock(p)
  assert second.acquire() is False  # already held
  first.release()
  assert second.acquire() is True   # now free
  second.release()


def test_context_manager_raises_when_already_held(tmp_path):
  p = tmp_path / "lock"
  with RunLock(p):
    with pytest.raises(RunLockError):
      with RunLock(p):
        pass


def test_lock_records_pid(tmp_path):
  import os
  p = tmp_path / "lock"
  lock = RunLock(p)
  lock.acquire()
  assert p.read_text().strip() == str(os.getpid())
  lock.release()
