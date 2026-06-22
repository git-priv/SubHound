# Tests for the CLI entry point (__main__): argument parsing and a headless run
# over an empty directory (exercises the full wiring without ffmpeg/network,
# since no videos means no extract/sync/provider calls).

from __future__ import annotations

import tempfile
from pathlib import Path

from subhound.__main__ import main, parse_args, run_headless


def test_parse_args_defaults():
  args = parse_args([])
  assert args.headless is False and args.dir is None


def test_parse_args_headless():
  args = parse_args(["--headless", "--dir", "/media", "--languages", "nl,en", "--resync"])
  assert args.headless and args.dir == Path("/media")
  assert args.languages == "nl,en" and args.resync is True
  # Keep-running is the default; --once is off and the wait ceiling is 24h.
  assert args.once is False and args.max_quota_wait == 24 * 60 * 60


def test_parse_args_once():
  args = parse_args(["--headless", "--dir", "/media", "--once", "--max-quota-wait", "3600"])
  assert args.once is True and args.max_quota_wait == 3600


def test_headless_requires_dir(capsys):
  code = run_headless(parse_args(["--headless"]))
  assert code == 2
  assert "requires --dir" in capsys.readouterr().err


def test_headless_missing_dir(capsys):
  code = run_headless(parse_args(["--headless", "--dir", "/no/such/path/xyz"]))
  assert code == 2
  assert "not found" in capsys.readouterr().err


def test_print_schedule_outputs_cron_or_task(capsys):
  d = Path(tempfile.mkdtemp())
  code = main(["--print-schedule", "--dir", str(d), "--languages", "en",
               "--schedule-interval", "30"])
  assert code == 0
  out = capsys.readouterr().out
  assert "subhound" in out and "--headless" in out


def test_print_schedule_requires_dir(capsys):
  code = main(["--print-schedule"])
  assert code == 2
  assert "requires --dir" in capsys.readouterr().err


def test_headless_empty_dir_runs(capsys):
  d = Path(tempfile.mkdtemp())
  code = main(["--headless", "--dir", str(d), "--languages", "en"])
  assert code == 0
  out = capsys.readouterr().out
  assert "subhound summary" in out
  assert "total pairs     : 0" in out
  # An (empty) results file is produced.
  assert (d / "parallel_pipeline_results.tsv").exists()
