# Pytest session setup for subhound.
#
# Before any test runs, verify the labeled test dataset against its SHA-256
# ledger. If anything is missing or corrupted, abort the entire session with a
# clear error so tests never run against bad data. This makes a single
# `uv run pytest` both verify the data and run the suite.

from __future__ import annotations

import pytest

from tests.eval_identify import DATASET, SHA256SUMS, verify_checksums


# Function Summary:
#    Pytest hook that runs once at session start: verify the test dataset's
#    checksums and abort the run if the data is missing or corrupted.
#
#  Input (parameters):
#    session [pytest.Session]:  the pytest session (unused, required by the hook)
#
#  Output:
#    (none):  returns normally when intact; calls pytest.exit(code=1) otherwise
#
# Example:
#    (invoked automatically by pytest at startup)
def pytest_sessionstart(session: pytest.Session) -> None:
  if not DATASET.exists():
    # No dataset checked out (e.g. a slim clone): let dataset-dependent tests
    # skip themselves rather than failing the whole session here.
    return
  if not SHA256SUMS.exists():
    pytest.exit(f"checksum ledger not found: {SHA256SUMS}", returncode=1)
  problems = verify_checksums(DATASET, SHA256SUMS)
  if problems:
    shown = "\n".join(f"  - {p}" for p in problems[:20])
    extra = "" if len(problems) <= 20 else f"\n  ... and {len(problems) - 20} more"
    pytest.exit(
      "test data is corrupted - SHA-256 checksums do not match:\n" + shown + extra,
      returncode=1,
    )
  total = sum(1 for ln in SHA256SUMS.read_text(encoding="utf-8").splitlines() if ln.strip())
  print(f"\n[conftest] checksums OK: {total} files verified against {SHA256SUMS.name}")
