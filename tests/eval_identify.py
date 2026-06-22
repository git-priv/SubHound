# Evaluate subhound.core.identify against the labeled portable_media_test_set.
#
# Ground truth = manifest.csv. We score only real video files whose kind is
# "movie" or "episode" (subtitles/artwork/nfo/etc. are not identify's job).
# Reports per-field accuracy (type, title, year, season, episode) and the count
# of exact full matches, plus a list of mismatches for inspection.
#
# Run:  uv run python tests/eval_identify.py

from __future__ import annotations

import csv
import hashlib
import re
import sys
from pathlib import Path

from subhound.core.identify import MOVIE, TV, identify
from subhound.core.scan import is_video_file

DATASET = Path(__file__).parent / "data" / "portable_media_test_set"
MANIFEST = DATASET / "manifest.csv"
SHA256SUMS = DATASET / "SHA256SUMS.txt"

KIND_TO_TYPE = {"movie": MOVIE, "episode": TV}


# Function Summary:
#    Verify every file listed in SHA256SUMS.txt against its recorded SHA-256.
#    Returns the list of problems (missing files or hash mismatches); an empty
#    list means the dataset is intact.
#
#  Input (parameters):
#    dataset_dir [Path]:  the dataset root that the sums are relative to
#    sums_file [Path]:    the SHA256SUMS.txt ledger to check against
#
#  Output:
#    problems [list[str]]:  human-readable issues; empty when all checksums match
#
# Example:
#    verify_checksums(DATASET, SHA256SUMS)  ->  []
def verify_checksums(dataset_dir: Path, sums_file: Path) -> list[str]:
  problems: list[str] = []
  for line in sums_file.read_text(encoding="utf-8").splitlines():
    line = line.rstrip("\n")
    if not line.strip():
      continue
    # Format: "<64-hex-digest>  <relative/path>" (two spaces).
    expected, _, rel = line.partition("  ")
    rel = rel.strip()
    if not rel:
      problems.append(f"malformed checksum line: {line!r}")
      continue
    target = dataset_dir / rel
    if not target.is_file():
      problems.append(f"missing file: {rel}")
      continue
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != expected:
      problems.append(f"hash mismatch: {rel}")
  return problems


# Function Summary:
#    Verify the dataset checksums and abort the program with a clear error when
#    the test data is corrupted (any missing file or hash mismatch).
#
#  Input (parameters):
#    (none)
#
#  Output:
#    (none):  returns normally when intact; calls sys.exit(1) when corrupted
#
# Example:
#    require_intact_dataset()  ->  (prints "checksums OK ..." and returns)
def require_intact_dataset() -> None:
  if not SHA256SUMS.exists():
    sys.exit(f"ERROR: checksum ledger not found: {SHA256SUMS}")
  problems = verify_checksums(DATASET, SHA256SUMS)
  if problems:
    print("ERROR: test data is corrupted - SHA-256 checksums do not match.",
          file=sys.stderr)
    for p in problems[:20]:
      print(f"  - {p}", file=sys.stderr)
    if len(problems) > 20:
      print(f"  ... and {len(problems) - 20} more", file=sys.stderr)
    sys.exit(1)
  total = sum(1 for ln in SHA256SUMS.read_text(encoding="utf-8").splitlines() if ln.strip())
  print(f"checksums OK: {total} files verified against {SHA256SUMS.name}\n")


# Function Summary:
#    Normalize a title for tolerant comparison: lowercase, drop non-alphanumerics,
#    collapse whitespace.
#
#  Input (parameters):
#    s [str]:  a title string
#
#  Output:
#    norm [str]:  normalized comparison key
#
# Example:
#    norm_title("The Office (US)")  ->  "the office us"
def norm_title(s: str) -> str:
  s = re.sub(r"[^a-z0-9]+", " ", (s or "").lower())
  return re.sub(r"\s+", " ", s).strip()


# Function Summary:
#    Parse the first episode number from a manifest "episodes" cell, which may be
#    empty, a single number, or a list like "1,2".
#
#  Input (parameters):
#    cell [str]:  the manifest episodes value
#
#  Output:
#    ep [int | None]:  the first episode number, or None
#
# Example:
#    first_episode("1,2")  ->  1
def first_episode(cell: str) -> int | None:
  nums = re.findall(r"\d+", cell or "")
  return int(nums[0]) if nums else None


# Function Summary:
#    Coerce a possibly-empty manifest integer cell to int or None.
#
#  Input (parameters):
#    cell [str]:  a manifest cell expected to hold an int
#
#  Output:
#    value [int | None]:  the integer, or None when blank/non-numeric
#
# Example:
#    as_int("2008")  ->  2008
def as_int(cell: str) -> int | None:
  cell = (cell or "").strip()
  return int(cell) if cell.isdigit() else None


def main() -> None:
  require_intact_dataset()
  rows = list(csv.DictReader(MANIFEST.open()))
  scored = 0
  hits = {"type": 0, "title": 0, "year": 0, "season": 0, "episode": 0, "all": 0}
  mismatches: list[str] = []

  for r in rows:
    exp_type = KIND_TO_TYPE.get(r["kind"])
    if exp_type is None:
      continue
    rel = r["path"]
    if not is_video_file(Path(rel)):
      continue
    scored += 1
    info = identify(DATASET / rel, None)

    exp_title = norm_title(r["expected_title"])
    exp_year = as_int(r["year"])
    exp_season = as_int(r["season"])
    exp_episode = first_episode(r["episodes"])

    ok_type = info.media_type == exp_type
    ok_title = norm_title(info.title_or_show) == exp_title
    ok_year = (info.year == exp_year) if exp_year is not None else True
    ok_season = (info.season == exp_season) if exp_type == TV else True
    ok_episode = (info.episode == exp_episode) if exp_type == TV else True

    hits["type"] += ok_type
    hits["title"] += ok_title
    hits["year"] += ok_year
    hits["season"] += ok_season
    hits["episode"] += ok_episode
    all_ok = ok_type and ok_title and ok_year and ok_season and ok_episode
    hits["all"] += all_ok

    if not all_ok:
      flags = "".join(
        c if ok else "." for c, ok in zip(
          "TYSE", [ok_type, ok_year, ok_season, ok_episode]
        )
      )
      tflag = "t" if ok_title else "."
      mismatches.append(
        f"[{flags}{tflag}] {rel}\n"
        f"      got : type={info.media_type} title={info.title_or_show!r} "
        f"year={info.year} S={info.season} E={info.episode}\n"
        f"      want: type={exp_type} title={r['expected_title']!r} "
        f"year={exp_year} S={exp_season} E={exp_episode}"
      )

  print(f"Scored {scored} video files (kind movie/episode)\n")
  for k in ("type", "title", "year", "season", "episode", "all"):
    print(f"  {k:8}: {hits[k]:3}/{scored}  ({100 * hits[k] / scored:.1f}%)")
  print(f"\nMismatches: {len(mismatches)}")
  for m in mismatches:
    print(m)


if __name__ == "__main__":
  main()
