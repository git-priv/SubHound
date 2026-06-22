# subhound.logging_setup
#
# Structured logging for a run: a per-run log file plus an optional in-process
# callback handler so the TUI can stream log lines live. Returns the log file
# path so the UI can show / link it.

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

LOGGER_NAME = "subhound"
_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class CallbackHandler(logging.Handler):
  # A logging handler that forwards each formatted record to a callback, used to
  # stream log lines into the TUI's live log view.

  # Function Summary:
  #    Build the handler around a callback receiving (levelname, message).
  #
  #  Input (parameters):
  #    callback [Callable[[str, str], None]]:  receives (level, formatted_message)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    CallbackHandler(lambda lvl, msg: view.append(msg))
  def __init__(self, callback: Callable[[str, str], None]) -> None:
    super().__init__()
    self._callback = callback

  # Function Summary:
  #    Emit a record by formatting it and invoking the callback. Never raises.
  #
  #  Input (parameters):
  #    record [logging.LogRecord]:  the record to emit
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    handler.emit(record)
  def emit(self, record: logging.LogRecord) -> None:
    try:
      self._callback(record.levelname, self.format(record))
    except Exception:  # noqa: BLE001 - a logging sink must never crash the run
      pass


# Function Summary:
#    Return the shared subhound logger.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    logger [logging.Logger]:  the "subhound" logger
#
# Example:
#    get_logger().info("hello")
def get_logger() -> logging.Logger:
  return logging.getLogger(LOGGER_NAME)


# Function Summary:
#    Configure logging for a run: attach a timestamped per-run file handler and,
#    optionally, a callback handler for live streaming. Clears any handlers from
#    a previous run so repeated runs don't duplicate output.
#
#  Input (parameters):
#    log_dir [Path | None]:                     directory for the log file (None = no file)
#    level [int]:                               logging level (e.g. logging.INFO)
#    callback [Callable[[str,str],None]|None]:  live log sink for the TUI
#
#  Output:
#    log_path [Path | None]:  the created log file path, or None when log_dir is None
#
# Example:
#    configure_logging(Path("/run/logs"), logging.INFO)  ->  PosixPath(".../subhound-20260615-101500.log")
def configure_logging(
  log_dir: Path | None = None,
  level: int = logging.INFO,
  callback: Callable[[str, str], None] | None = None,
) -> Path | None:
  logger = get_logger()
  logger.setLevel(level)
  logger.propagate = False
  for handler in list(logger.handlers):
    logger.removeHandler(handler)
    handler.close()
  formatter = logging.Formatter(_FORMAT, _DATEFMT)

  log_path: Path | None = None
  if log_dir is not None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"subhound-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

  if callback is not None:
    cb_handler = CallbackHandler(callback)
    cb_handler.setFormatter(formatter)
    logger.addHandler(cb_handler)

  return log_path
