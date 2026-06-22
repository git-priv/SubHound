# subhound.pipeline.results
#
# The results TSV and the in-run "run log" described in docs/PIPELINE.md.
#
#  * ResultRow            – one (video, language) row, matching the mandated TSV schema.
#  * read/write_results_tsv – persist/load the results file.
#  * RunLog               – the working state for a run: all (video, lang) entries, which
#                           are already SUCCESS (skipped), which sources have been tried per
#                           entry, and helpers to build per-source pools for quota refresh.
#
# tried_sources is tracked per entry but is NOT a TSV column; the run log persists
# to a JSON sidecar so an interrupted run resumes without repeating work.

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..core.hashing import Fingerprint, fingerprint
from ..core.identify import MediaInfo

# Exact TSV column order (see docs/PIPELINE.md). Do not reorder.
RESULT_COLUMNS = [
  "video_path", "video_size", "video_mtime_ns", "updated_at", "type",
  "title_or_show", "year", "season", "episode", "video_filename", "lang",
  "extracted_from_video", "existing_subs", "db_candidates", "api_candidates",
  "sync_offset", "good_subtitle", "result", "status", "subtitle_file",
]

# Processing states stored in the `status` column.
PENDING = "PENDING"     # not yet resolved this run
SUCCESS = "SUCCESS"     # a good synced subtitle was obtained
FAILED = "FAILED"       # all eligible sources exhausted with no good sub
WAITLIST = "WAITLIST"   # blocked on an API quota; retry when the quota resets
SKIPPED = "SKIPPED"     # carried over from a previous successful run

# Candidate-source identifiers used in tried_sources and the `result` column.
# The first two are local pipeline stages; the rest mirror config Source values.
SOURCE_EMBEDDED = "embedded"
SOURCE_EXISTING = "existing"

# Every source a (video, lang) pair can be checked against, in pipeline order.
# Drives the per-source columns of the wide diagnostics TSV.
DIAG_SOURCES = [
  SOURCE_EMBEDDED, SOURCE_EXISTING, "milahu", "opensubtitles_com",
  "subsource", "gestdown", "yify", "podnapisi", "tvsubtitles",
]

# Per-source outcome values recorded in diagnostics.
DIAG_GOOD = "good"               # this source produced the accepted subtitle
DIAG_REJECTED = "rejected"       # had candidates but none synced within thresholds
DIAG_NONE = "none"               # tried but no usable candidate found
DIAG_QUOTA = "quota"             # skipped/blocked because the source was out of quota
DIAG_DOWNLOAD_FAILED = "download_failed"  # candidate(s) failed to download/validate
DIAG_ERROR = "error"             # an unexpected error while trying this source


@dataclass
class SourceDiag:
  # Per-(video, lang) diagnostics for one source: what it offered and why it did
  # or didn't yield the winning subtitle. Persisted in the run-log sidecar and
  # flattened into the wide diagnostics TSV.
  tried: bool = False
  candidates: int = 0            # total candidates/subtitles this source offered
  lang_match: int = 0            # how many matched the wanted language
  offsets: list[float] = field(default_factory=list)  # measured sync offsets (s)
  outcome: str = ""              # one of the DIAG_* values ("" = not tried)


@dataclass
class ResultRow:
  # One (video, language) result row. Field order matches RESULT_COLUMNS.
  video_path: str
  video_size: int
  video_mtime_ns: int
  updated_at: str
  type: str
  title_or_show: str
  year: int | None
  season: int | None
  episode: int | None
  video_filename: str
  lang: str
  extracted_from_video: int = 0
  existing_subs: int = 0
  db_candidates: int = 0
  api_candidates: int = 0
  sync_offset: float | None = None
  good_subtitle: bool = False
  result: str = ""
  status: str = PENDING
  subtitle_file: str = ""


# Function Summary:
#    Build the stable key identifying a (video, language) pair.
#
#  Input (parameters):
#    video_path [str]:  the video file path
#    lang [str]:        the 2-letter language code
#
#  Output:
#    key [str]:  "<video_path>\t<lang>" used to index run-log entries
#
# Example:
#    result_key("/m/Movie.mkv", "en")  ->  "/m/Movie.mkv\ten"
def result_key(video_path: str, lang: str) -> str:
  return f"{video_path}\t{lang}"


# Function Summary:
#    Serialize one ResultRow field to its TSV string form (None -> "", bools ->
#    "true"/"false", tabs/newlines stripped to keep the TSV well-formed).
#
#  Input (parameters):
#    value [object]:  a ResultRow field value
#
#  Output:
#    text [str]:  the TSV-safe string
#
# Example:
#    _to_cell(True)  ->  "true"
def _to_cell(value: object) -> str:
  if value is None:
    return ""
  if isinstance(value, bool):
    return "true" if value else "false"
  return str(value).replace("\t", " ").replace("\n", " ")


# Function Summary:
#    Write result rows to a TSV file (overwrites), including the header.
#
#  Input (parameters):
#    path [Path]:                 destination .tsv file
#    rows [Iterable[ResultRow]]:  the rows to write
#
#  Output:
#    written [Path]:  the path written
#
# Example:
#    write_results_tsv(Path("results.tsv"), rows)  ->  PosixPath("results.tsv")
def write_results_tsv(path: Path, rows: Iterable[ResultRow]) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(path.suffix + ".tmp")
  with tmp.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
    writer.writerow(RESULT_COLUMNS)
    for row in rows:
      d = asdict(row)
      writer.writerow([_to_cell(d[c]) for c in RESULT_COLUMNS])
  tmp.replace(path)
  return path


# Per-source diagnostic fields appended (per source) to the wide diagnostics TSV.
_DIAG_FIELDS = ["tried", "candidates", "lang_match", "offsets", "outcome"]


# Function Summary:
#    The column order of the wide diagnostics TSV: every mandated results column,
#    then five columns per source ("<source>_tried", "<source>_candidates",
#    "<source>_lang_match", "<source>_offsets", "<source>_outcome").
#
#  Input (parameters):
#    (none)
#
#  Output:
#    columns [list[str]]:  the full ordered column list
#
# Example:
#    diagnostics_columns()[20:25]  ->  ["embedded_tried", "embedded_candidates", ...]
def diagnostics_columns() -> list[str]:
  columns = list(RESULT_COLUMNS)
  for source in DIAG_SOURCES:
    columns.extend(f"{source}_{field_}" for field_ in _DIAG_FIELDS)
  return columns


# Function Summary:
#    Write the wide diagnostics TSV: one row per (video, lang) with the mandated
#    result columns plus per-source detail (candidates found, language matches,
#    measured sync offsets, and the per-source outcome). The mandated results TSV
#    is left untouched; this is a separate, opt-in file for troubleshooting/analysis.
#
#  Input (parameters):
#    path [Path]:                  destination diagnostics .tsv file
#    entries [Iterable[RunEntry]]: the run-log entries to write
#
#  Output:
#    written [Path]:  the path written
#
# Example:
#    write_diagnostics_tsv(Path("diag.tsv"), run_log.entries.values())  ->  PosixPath("diag.tsv")
def write_diagnostics_tsv(path: Path, entries: Iterable[RunEntry]) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  ordered = sorted(entries, key=lambda e: (e.row.video_path, e.row.lang))
  tmp = path.with_suffix(path.suffix + ".tmp")
  with tmp.open("w", encoding="utf-8", newline="") as fh:
    writer = csv.writer(fh, delimiter="\t", lineterminator="\n")
    writer.writerow(diagnostics_columns())
    for entry in ordered:
      row = asdict(entry.row)
      cells = [_to_cell(row[c]) for c in RESULT_COLUMNS]
      for source in DIAG_SOURCES:
        diag = entry.diagnostics.get(source)
        if diag is None:
          cells.extend(["false", "0", "0", "", ""])
        else:
          offsets = ";".join(f"{o:.3f}" for o in diag.offsets)
          cells.extend([
            _to_cell(diag.tried), str(diag.candidates), str(diag.lang_match),
            offsets, diag.outcome,
          ])
      writer.writerow(cells)
  tmp.replace(path)
  return path


# Function Summary:
#    Read a results TSV into ResultRow objects keyed by (video_path, lang).
#    Missing files yield an empty mapping; unknown/extra columns are ignored.
#
#  Input (parameters):
#    path [Path]:  the results .tsv file to read
#
#  Output:
#    rows [dict[str, ResultRow]]:  keyed by result_key(video_path, lang)
#
# Example:
#    read_results_tsv(Path("results.tsv"))["/m/A.mkv\ten"].status  ->  "SUCCESS"
def read_results_tsv(path: Path) -> dict[str, ResultRow]:
  if not path.exists():
    return {}

  def as_int(v: str) -> int:
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else 0

  def opt_int(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None

  def opt_float(v: str) -> float | None:
    v = (v or "").strip()
    try:
      return float(v)
    except ValueError:
      return None

  out: dict[str, ResultRow] = {}
  with path.open("r", encoding="utf-8", newline="") as fh:
    for r in csv.DictReader(fh, delimiter="\t"):
      row = ResultRow(
        video_path=r.get("video_path", ""),
        video_size=as_int(r.get("video_size", "")),
        video_mtime_ns=as_int(r.get("video_mtime_ns", "")),
        updated_at=r.get("updated_at", ""),
        type=r.get("type", ""),
        title_or_show=r.get("title_or_show", ""),
        year=opt_int(r.get("year", "")),
        season=opt_int(r.get("season", "")),
        episode=opt_int(r.get("episode", "")),
        video_filename=r.get("video_filename", ""),
        lang=r.get("lang", ""),
        extracted_from_video=as_int(r.get("extracted_from_video", "")),
        existing_subs=as_int(r.get("existing_subs", "")),
        db_candidates=as_int(r.get("db_candidates", "")),
        api_candidates=as_int(r.get("api_candidates", "")),
        sync_offset=opt_float(r.get("sync_offset", "")),
        good_subtitle=(r.get("good_subtitle", "").strip().lower() in ("1", "true", "yes")),
        result=r.get("result", ""),
        status=r.get("status", ""),
        subtitle_file=r.get("subtitle_file", ""),
      )
      out[result_key(row.video_path, row.lang)] = row
  return out


@dataclass
class RunEntry:
  # One (video, language) unit of work plus the sources already tried for it.
  row: ResultRow
  tried_sources: set[str] = field(default_factory=set)
  diagnostics: dict[str, SourceDiag] = field(default_factory=dict)  # per-source detail

  # Function Summary:
  #    Whether this entry already has a good subtitle (terminal success).
  #
  #  Input (parameters):
  #    self [RunEntry]:  the entry
  #
  #  Output:
  #    ok [bool]:  True if the entry's status is SUCCESS
  #
  # Example:
  #    RunEntry(ResultRow(..., status="SUCCESS")).is_success()  ->  True
  def is_success(self) -> bool:
    return self.row.status == SUCCESS

  # Function Summary:
  #    Whether this entry still needs the given source tried (not yet successful
  #    and not already checked against that source).
  #
  #  Input (parameters):
  #    source [str]:  a source identifier (e.g. "opensubtitles_com")
  #
  #  Output:
  #    needs [bool]:  True if the source should be tried for this entry
  #
  # Example:
  #    entry.needs_source("subsource")  ->  True
  def needs_source(self, source: str) -> bool:
    return not self.is_success() and source not in self.tried_sources


class RunLog:
  # The mutable working state for one run: all (video, lang) entries plus their
  # tried-source sets. Build it from a scan + previous results, drive the
  # pipeline through it, then emit the final results TSV.

  # Function Summary:
  #    Create an empty run log.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    RunLog()  ->  <empty RunLog>
  def __init__(self) -> None:
    self.entries: dict[str, RunEntry] = {}

  # Function Summary:
  #    Build a run log from discovered videos, the previous results, and (for
  #    resume) the prior run-log sidecar. Each (video, language) becomes an entry.
  #    Pairs whose previous status is SUCCESS and whose video fingerprint is
  #    unchanged are carried over as a success (not reprocessed) unless resync.
  #    Other unchanged pairs that have prior sidecar state (e.g. wait-listed on a
  #    quota across days) resume that state: their tried-sources + diagnostics are
  #    kept so already-exhausted sources aren't re-queried and local subtitles
  #    aren't re-extracted, and the status is reset to PENDING so the run
  #    re-attempts whatever they still need.
  #
  #  Input (parameters):
  #    videos [list[tuple[Path, MediaInfo]]]:  discovered videos + identification
  #    languages [list[str]]:                  wanted language codes
  #    previous [dict[str, ResultRow]]:        rows from the last results TSV
  #    resync [bool]:                          True = reprocess even past successes
  #    prior_entries [dict[str, RunEntry]|None]: entries from the prior run-log
  #                                            sidecar, for resume (default: none)
  #
  #  Output:
  #    log [RunLog]:  a populated run log
  #
  # Example:
  #    RunLog.build([(Path("A.mkv"), info)], ["en"], {}, False)  ->  <RunLog with 1 entry>
  @classmethod
  def build(
    cls,
    videos: list[tuple[Path, MediaInfo]],
    languages: list[str],
    previous: dict[str, ResultRow],
    resync: bool,
    prior_entries: dict[str, RunEntry] | None = None,
  ) -> RunLog:
    log = cls()
    prior_entries = prior_entries or {}
    for path, info in videos:
      fp = _safe_fingerprint(path)
      for lang in languages:
        key = result_key(str(path), lang)
        prior = previous.get(key)
        if (not resync and prior is not None and prior.status == SUCCESS
            and _unchanged(prior, fp)):
          # Carry the past success forward; do not reprocess.
          carried = prior
          carried.status = SUCCESS
          log.entries[key] = RunEntry(carried, tried_sources=set())
          continue
        resumed = prior_entries.get(key)
        if (not resync and resumed is not None and resumed.row.status != SUCCESS
            and _unchanged(resumed.row, fp)):
          # Resume in-progress work (e.g. wait-listed on a quota). Keep the prior
          # row, tried-sources and diagnostics; re-open it for this run.
          row = resumed.row
          row.status = PENDING
          log.entries[key] = RunEntry(
            row, set(resumed.tried_sources), dict(resumed.diagnostics))
          continue
        log.entries[key] = RunEntry(_new_row(path, info, lang, fp))
    return log

  # Function Summary:
  #    Entries that still need work this run (not yet successful).
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    entries [list[RunEntry]]:  unfinished entries
  #
  # Example:
  #    run_log.pending()  ->  [RunEntry(...), ...]
  def pending(self) -> list[RunEntry]:
    return [e for e in self.entries.values() if not e.is_success()]

  # Function Summary:
  #    Entries that need the given source tried (unfinished and not yet checked
  #    against that source) -- the pool to process for one source / quota window.
  #
  #  Input (parameters):
  #    source [str]:  a source identifier
  #
  #  Output:
  #    entries [list[RunEntry]]:  entries needing that source
  #
  # Example:
  #    run_log.pool_for_source("opensubtitles_com")  ->  [RunEntry(...), ...]
  def pool_for_source(self, source: str) -> list[RunEntry]:
    return [e for e in self.entries.values() if e.needs_source(source)]

  # Function Summary:
  #    Build per-source pools for several sources at once (e.g. to schedule API
  #    work when quotas reset).
  #
  #  Input (parameters):
  #    sources [Iterable[str]]:  source identifiers
  #
  #  Output:
  #    pools [dict[str, list[RunEntry]]]:  source -> entries still needing it
  #
  # Example:
  #    run_log.pools_for_sources(["opensubtitles_com", "subsource"])  ->  {...}
  def pools_for_sources(self, sources: Iterable[str]) -> dict[str, list[RunEntry]]:
    return {s: self.pool_for_source(s) for s in sources}

  # Function Summary:
  #    Record that a source was checked for an entry, optionally adding its
  #    candidate count to the row totals. Pass mark_tried=False to only count
  #    candidates without marking the source done -- used after a provider search
  #    but before its downloads, so a download that then hits a quota leaves the
  #    source retriable (not falsely recorded as exhausted-with-no-sub).
  #
  #  Input (parameters):
  #    key [str]:         the entry key (result_key)
  #    source [str]:      the source that was checked
  #    candidates [int]:  how many candidates that source returned (for row counts)
  #    mark_tried [bool]: whether to add the source to tried_sources
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    run_log.mark_source_tried(key, "subsource", candidates=3)
  def mark_source_tried(
    self, key: str, source: str, candidates: int = 0, mark_tried: bool = True,
  ) -> None:
    entry = self.entries[key]
    if mark_tried:
      entry.tried_sources.add(source)
    if candidates:
      if source in (SOURCE_EMBEDDED,):
        entry.row.extracted_from_video += candidates
      elif source in (SOURCE_EXISTING,):
        entry.row.existing_subs += candidates
      elif source == "milahu":
        entry.row.db_candidates += candidates
      else:
        entry.row.api_candidates += candidates

  # Function Summary:
  #    Get (creating if needed) the diagnostics record for one (entry, source).
  #
  #  Input (parameters):
  #    key [str]:     the entry key
  #    source [str]:  the source identifier
  #
  #  Output:
  #    diag [SourceDiag]:  the (possibly new) per-source diagnostics record
  #
  # Example:
  #    run_log._diag(key, "subsource").tried = True
  def _diag(self, key: str, source: str) -> SourceDiag:
    diags = self.entries[key].diagnostics
    diag = diags.get(source)
    if diag is None:
      diag = SourceDiag()
      diags[source] = diag
    return diag

  # Function Summary:
  #    Record per-source diagnostics for an entry: that the source was tried, how
  #    many candidates it offered, how many matched the language, and the final
  #    per-source outcome. Only the provided fields are updated.
  #
  #  Input (parameters):
  #    key [str]:               the entry key
  #    source [str]:            the source identifier
  #    candidates [int|None]:   total candidates the source offered
  #    lang_match [int|None]:   how many matched the wanted language
  #    outcome [str|None]:      a DIAG_* outcome value
  #    tried [bool]:            whether the source was actually attempted
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    run_log.record_diag(key, "subsource", candidates=3, lang_match=1, outcome=DIAG_REJECTED)
  def record_diag(
    self,
    key: str,
    source: str,
    candidates: int | None = None,
    lang_match: int | None = None,
    outcome: str | None = None,
    tried: bool = True,
  ) -> None:
    diag = self._diag(key, source)
    if tried:
      diag.tried = True
    if candidates is not None:
      diag.candidates = candidates
    if lang_match is not None:
      diag.lang_match = lang_match
    if outcome is not None:
      diag.outcome = outcome

  # Function Summary:
  #    Append a measured sync offset to a source's diagnostics (one per sync-test
  #    attempt), so the diagnostics TSV can show why candidates were accepted or
  #    rejected against the thresholds.
  #
  #  Input (parameters):
  #    key [str]:            the entry key
  #    source [str]:         the source identifier
  #    offset [float|None]:  the measured offset in seconds (ignored if None)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    run_log.add_diag_offset(key, "subsource", 3.21)
  def add_diag_offset(self, key: str, source: str, offset: float | None) -> None:
    if offset is None:
      return
    self._diag(key, source).offsets.append(offset)

  # Function Summary:
  #    Record a good synced subtitle for an entry: mark it SUCCESS, store the
  #    source, offset and output file.
  #
  #  Input (parameters):
  #    key [str]:            the entry key
  #    source [str]:         the source the winning sub came from
  #    subtitle_file [str]:  path to the final synced .srt
  #    offset [float|None]:  the synced offset in seconds
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    run_log.record_success(key, "milahu", "/m/A.en.srt", 0.3)
  def record_success(self, key: str, source: str, subtitle_file: str, offset: float | None) -> None:
    entry = self.entries[key]
    entry.tried_sources.add(source)
    self._diag(key, source).outcome = DIAG_GOOD
    row = entry.row
    row.status = SUCCESS
    row.result = source
    row.good_subtitle = True
    row.sync_offset = offset
    row.subtitle_file = subtitle_file
    row.updated_at = _now()

  # Function Summary:
  #    Mark an entry's terminal state when no source produced a good sub. Use
  #    status=WAITLIST when only blocked on quota, else FAILED.
  #
  #  Input (parameters):
  #    key [str]:     the entry key
  #    status [str]:  FAILED or WAITLIST
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    run_log.mark_unresolved(key, "WAITLIST")
  def mark_unresolved(self, key: str, status: str = FAILED) -> None:
    row = self.entries[key].row
    row.status = status
    row.result = row.result or status
    row.updated_at = _now()

  # Function Summary:
  #    All entries as result rows, for writing the final TSV.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    rows [list[ResultRow]]:  rows sorted by (video_path, lang)
  #
  # Example:
  #    write_results_tsv(path, run_log.to_rows())
  def to_rows(self) -> list[ResultRow]:
    return [e.row for e in sorted(
      self.entries.values(), key=lambda e: (e.row.video_path, e.row.lang))]

  # Function Summary:
  #    Persist the run log (rows + tried_sources) to a JSON sidecar for resume.
  #
  #  Input (parameters):
  #    path [Path]:  destination .json file
  #
  #  Output:
  #    written [Path]:  the path written
  #
  # Example:
  #    run_log.save(Path("run_log.json"))  ->  PosixPath("run_log.json")
  def save(self, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
      {
        "row": asdict(e.row),
        "tried_sources": sorted(e.tried_sources),
        "diagnostics": {s: asdict(d) for s, d in e.diagnostics.items()},
      }
      for e in self.entries.values()
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path

  # Function Summary:
  #    Load a run log previously saved with save().
  #
  #  Input (parameters):
  #    path [Path]:  the .json sidecar to read
  #
  #  Output:
  #    log [RunLog]:  the restored run log (empty if the file is missing)
  #
  # Example:
  #    RunLog.load(Path("run_log.json"))  ->  <RunLog>
  @classmethod
  def load(cls, path: Path) -> RunLog:
    log = cls()
    if not path.exists():
      return log
    for item in json.loads(path.read_text(encoding="utf-8")):
      row = ResultRow(**item["row"])
      key = result_key(row.video_path, row.lang)
      diagnostics = {
        s: SourceDiag(**d) for s, d in item.get("diagnostics", {}).items()
      }
      log.entries[key] = RunEntry(
        row, set(item.get("tried_sources", [])), diagnostics)
    return log


# Function Summary:
#    Fingerprint a video, tolerating a missing/unreadable file (size/mtime 0).
#
#  Input (parameters):
#    path [Path]:  the video file
#
#  Output:
#    fp [Fingerprint]:  the file fingerprint (zeros if unreadable)
#
# Example:
#    _safe_fingerprint(Path("A.mkv"))  ->  Fingerprint(size=..., mtime_ns=...)
def _safe_fingerprint(path: Path) -> Fingerprint:
  try:
    return fingerprint(path)
  except OSError:
    return Fingerprint(size=0, mtime_ns=0)


# Function Summary:
#    Whether a previous row's recorded size/mtime match a current fingerprint
#    (i.e. the video has not changed since the last run).
#
#  Input (parameters):
#    prior [ResultRow]:    the previous row
#    fp [Fingerprint]:     the current file fingerprint
#
#  Output:
#    same [bool]:  True if size and mtime_ns match
#
# Example:
#    _unchanged(prior, fp)  ->  True
def _unchanged(prior: ResultRow, fp: Fingerprint) -> bool:
  return prior.video_size == fp.size and prior.video_mtime_ns == fp.mtime_ns


# Function Summary:
#    Construct a fresh PENDING result row for a (video, language) pair.
#
#  Input (parameters):
#    path [Path]:         the video file
#    info [MediaInfo]:    its identification
#    lang [str]:          the language code
#    fp [Fingerprint]:    the video fingerprint
#
#  Output:
#    row [ResultRow]:  a new PENDING row
#
# Example:
#    _new_row(Path("A.mkv"), info, "en", fp).status  ->  "PENDING"
def _new_row(path: Path, info: MediaInfo, lang: str, fp: Fingerprint) -> ResultRow:
  return ResultRow(
    video_path=str(path),
    video_size=fp.size,
    video_mtime_ns=fp.mtime_ns,
    updated_at=_now(),
    type=info.media_type,
    title_or_show=info.title_or_show,
    year=info.year,
    season=info.season,
    episode=info.episode,
    video_filename=path.name,
    lang=lang,
    status=PENDING,
  )


# Function Summary:
#    Current UTC timestamp in ISO-8601 form for the updated_at column.
#
#  Input (parameters):
#    (none)
#
#  Output:
#    ts [str]:  ISO-8601 UTC timestamp (seconds precision)
#
# Example:
#    _now()  ->  "2026-06-14T19:23:05+00:00"
def _now() -> str:
  return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
