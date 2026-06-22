# subhound.__main__
#
# Entry point. With no arguments it launches the Textual TUI; with --headless it
# runs the pipeline once over a directory and prints a summary (for cron/CI/SSH).

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config.secrets import load_credentials
from .config.settings import Settings
from .config.store import config_dir, load_settings
from .logging_setup import configure_logging


# Function Summary:
#    Parse subhound's command-line arguments.
#
#  Input (parameters):
#    argv [list[str] | None]:  argument list (defaults to sys.argv[1:])
#
#  Output:
#    args [argparse.Namespace]:  parsed arguments
#
# Example:
#    parse_args(["--headless", "--dir", "/media"]).headless  ->  True
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    prog="subhound",
    description="Parallel multi-source subtitle detection, download and sync.",
  )
  parser.add_argument("--headless", action="store_true",
                      help="run once without the TUI and print a summary")
  parser.add_argument("--dir", type=Path, default=None,
                      help="target media directory (required for --headless)")
  parser.add_argument("--languages", type=str, default=None,
                      help="comma-separated language codes overriding the config")
  parser.add_argument("--resync", action="store_true",
                      help="reprocess videos even if a previous run succeeded")
  parser.add_argument("--once", action="store_true",
                      help="do a single pass and exit instead of the default "
                           "(keep running, waiting out quota resets). Use this for "
                           "scheduled/cron runs that exit and are restarted later.")
  parser.add_argument("--max-quota-wait", type=int, default=24 * 60 * 60,
                      help="when keeping running, the longest quota reset to wait "
                           "out (seconds; default 86400)")
  parser.add_argument("--print-schedule", action="store_true",
                      help="print the OS scheduler entry (cron/Task Scheduler) to "
                           "re-run subhound periodically, then exit")
  parser.add_argument("--schedule-interval", type=int, default=60,
                      help="minutes between scheduled runs (with --print-schedule)")
  parser.add_argument("-v", "--verbose", action="store_true",
                      help="verbose (DEBUG) logging")
  return parser.parse_args(argv)


# Function Summary:
#    Load settings and apply any command-line overrides (languages).
#
#  Input (parameters):
#    args [argparse.Namespace]:  parsed CLI arguments
#
#  Output:
#    settings [Settings]:  the effective settings for this run
#
# Example:
#    _effective_settings(parse_args(["--languages", "nl,en"])).languages  ->  ["nl", "en"]
def _effective_settings(args: argparse.Namespace) -> Settings:
  settings = load_settings()
  if args.languages:
    settings.languages = [code.strip() for code in args.languages.split(",") if code.strip()]
  return settings


# Function Summary:
#    Run the pipeline once over a directory and print a summary. Used by
#    --headless.
#
#  Input (parameters):
#    args [argparse.Namespace]:  parsed CLI arguments (needs .dir)
#
#  Output:
#    code [int]:  process exit code (0 on success, 2 on bad arguments)
#
# Example:
#    run_headless(parse_args(["--headless", "--dir", "/media"]))  ->  0
def run_headless(args: argparse.Namespace) -> int:
  if args.dir is None:
    print("error: --headless requires --dir <media directory>", file=sys.stderr)
    return 2
  if not args.dir.exists():
    print(f"error: directory not found: {args.dir}", file=sys.stderr)
    return 2

  # Stream logs to stderr as well as the per-run file.
  level = logging.DEBUG if args.verbose else logging.INFO
  configure_logging(callback=lambda lvl, msg: print(msg, file=sys.stderr), level=level)

  settings = _effective_settings(args)
  credentials = load_credentials(config_dir())

  # Imported here so --help / arg parsing never pays the orchestrator import cost.
  from .pipeline.orchestrator import Orchestrator
  from .pipeline.lock import RunLockError

  try:
    stats = Orchestrator(settings, credentials).run(
      args.dir, resync=args.resync,
      wait_for_quota=not args.once,
      max_quota_wait_seconds=args.max_quota_wait)
  except RunLockError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 3

  print("\n=== subhound summary ===")
  print(f"  target          : {args.dir}")
  print(f"  languages       : {', '.join(settings.languages)}")
  print(f"  total pairs     : {stats.total_pairs}")
  print(f"  skipped         : {stats.skipped}")
  print(f"  processed       : {stats.processed}")
  print(f"  succeeded       : {stats.succeeded}")
  print(f"  failed          : {stats.failed}")
  print(f"  waitlisted      : {stats.waitlisted}")
  print(f"  undetermined    : {stats.undetermined}")
  if stats.by_source:
    by_source = ", ".join(f"{src}={n}" for src, n in sorted(stats.by_source.items()))
    print(f"  by source       : {by_source}")
  return 0


# Function Summary:
#    Program entry point: launch the TUI, or run headless when --headless is set.
#
#  Input (parameters):
#    argv [list[str] | None]:  argument list (defaults to sys.argv[1:])
#
#  Output:
#    code [int]:  process exit code
#
# Example:
#    main(["--headless", "--dir", "/media"])  ->  0
def main(argv: list[str] | None = None) -> int:
  args = parse_args(argv)
  if args.print_schedule:
    if args.dir is None:
      print("error: --print-schedule requires --dir <media directory>", file=sys.stderr)
      return 2
    from .scheduling import schedule_preview
    settings = _effective_settings(args)
    print(schedule_preview(args.dir, settings.languages, args.schedule_interval))
    return 0
  if args.headless:
    return run_headless(args)
  # Default: launch the interactive TUI.
  from .tui.app import SubhoundApp
  SubhoundApp().run()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
