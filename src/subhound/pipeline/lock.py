# subhound.pipeline.lock
#
# A cross-platform, advisory single-run lock so two subhound processes can't work
# the same media directory at once (which would corrupt the shared results /
# diagnostics TSV and run-log sidecar). Backed by an OS file lock (fcntl on
# POSIX, msvcrt on Windows), which the kernel releases automatically if the
# process exits or crashes -- so a stale lock never blocks a later run.

from __future__ import annotations

import os
from pathlib import Path


class RunLockError(RuntimeError):
  # Raised when the lock is already held by another subhound process.
  pass


class RunLock:
  # An OS-level exclusive lock on a single file, used as a per-directory run lock.

  # Function Summary:
  #    Create a lock bound to a file path (not yet acquired).
  #
  #  Input (parameters):
  #    path [Path]:  the lock file to hold (created if missing)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    RunLock(Path("/media/.subhound/lock"))
  def __init__(self, path: Path) -> None:
    self.path = Path(path)
    self._fh = None

  # Function Summary:
  #    Try to take the lock without blocking. Returns whether it was acquired;
  #    records the holding PID in the file for diagnostics.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    acquired [bool]:  True if this process now holds the lock
  #
  # Example:
  #    RunLock(p).acquire()  ->  True
  def acquire(self) -> bool:
    self.path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(self.path, "a+")
    try:
      if os.name == "nt":
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
      else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      fh.close()
      return False
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    self._fh = fh
    return True

  # Function Summary:
  #    Release the lock (no-op if not held). The OS also releases it on process
  #    exit, so this is best-effort cleanup.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    lock.release()
  def release(self) -> None:
    if self._fh is None:
      return
    try:
      if os.name == "nt":
        import msvcrt
        self._fh.seek(0)
        try:
          msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
          pass
      else:
        import fcntl
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
    finally:
      self._fh.close()
      self._fh = None

  # Function Summary:
  #    Context-manager entry: acquire or raise RunLockError.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    lock [RunLock]:  self, with the lock held
  #
  # Example:
  #    with RunLock(p): ...
  def __enter__(self) -> RunLock:
    if not self.acquire():
      raise RunLockError(f"Another subhound run is already active ({self.path})")
    return self

  # Function Summary:
  #    Context-manager exit: release the lock.
  #
  #  Input (parameters):
  #    exc [tuple]:  exception info (ignored)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    (called by the with-statement)
  def __exit__(self, *exc: object) -> None:
    self.release()
