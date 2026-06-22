# subhound.tui.app
#
# The Textual TUI: three tabs -- Setup (edit settings + credentials, no file
# editing), Run (pick a directory, start, watch a live per-(video,lang) table and
# summary stats) and Logs (live log stream). The pipeline runs in a background
# thread (the orchestrator is synchronous); updates are marshalled back onto the
# Textual event loop via call_from_thread (see ROADMAP "Concurrency").

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
  Button,
  DataTable,
  Footer,
  Header,
  Input,
  Label,
  RichLog,
  Static,
  Switch,
  TabbedContent,
  TabPane,
)

from .. import scheduling
from ..config.secrets import Credentials, load_credentials, save_credentials
from ..config.settings import Settings
from ..config.store import config_dir, load_settings, save_settings
from ..pipeline.orchestrator import Orchestrator, PipelineEvents, RunStats
from ..pipeline.results import ResultRow


class SubhoundApp(App):
  TITLE = "subhound"
  SUB_TITLE = "parallel multi-source subtitle fetcher"
  CSS = """
  #stats { padding: 1 2; height: auto; background: $panel; }
  #setup_form { padding: 1 2; }
  #setup_form Label { margin-top: 1; }
  .row { height: auto; }
  #run_controls { height: auto; padding: 1 2; }
  #run_controls Input { width: 1fr; }
  RichLog { background: $surface; }
  """
  BINDINGS = [("q", "quit", "Quit"), ("ctrl+s", "save_setup", "Save setup")]

  # Function Summary:
  #    Build the app, loading current settings + credentials.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    SubhoundApp().run()
  def __init__(self) -> None:
    super().__init__()
    self.settings: Settings = load_settings()
    self.credentials: Credentials = load_credentials(config_dir())
    self._seen_rows: set[str] = set()
    self._pipeline_running = False

  # Function Summary:
  #    Compose the widget tree: Setup / Run / Logs tabs.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    result [ComposeResult]:  the composed widgets
  #
  # Example:
  #    (called by Textual)
  def compose(self) -> ComposeResult:
    yield Header()
    with TabbedContent(initial="run"):
      with TabPane("Setup", id="setup"):
        with VerticalScroll(id="setup_form"):
          yield Label("Languages (comma-separated)")
          yield Input(",".join(self.settings.languages), id="languages")
          yield Label("OpenSubtitles API key")
          yield Input(self.credentials.api_key, id="api_key", password=True)
          yield Label("OpenSubtitles username")
          yield Input(self.credentials.username, id="username")
          yield Label("OpenSubtitles password")
          yield Input(self.credentials.password, id="password", password=True)
          yield Label("Accept offset threshold (s)")
          yield Input(str(self.settings.accept_offset_threshold), id="accept")
          yield Label("Reject offset threshold (s)")
          yield Input(str(self.settings.reject_offset_threshold), id="reject")
          yield Button("Save setup", id="save", variant="primary")
          yield Static("", id="setup_status")
      with TabPane("Run", id="run"):
        with Horizontal(id="run_controls", classes="row"):
          yield Input(placeholder="media directory…", id="dir")
          yield Label("Resync")
          yield Switch(value=False, id="resync")
          yield Label("Keep running")
          yield Switch(value=True, id="wait_quota")
          yield Button("Start", id="start", variant="success")
        yield Static("No run yet.", id="stats")
        table: DataTable = DataTable(id="results", zebra_stripes=True)
        yield table
        yield Static(
          "Scheduled runs: when only a rate-limited provider (e.g. OpenSubtitles' "
          "few downloads/day) is left, you can exit and let the OS re-run subhound "
          "periodically instead of keeping it open. Each run resumes where it left off.",
          id="schedule_help")
        with Horizontal(id="schedule_controls", classes="row"):
          yield Label("Repeat every (min)")
          yield Input("60", id="schedule_interval")
          yield Button("Show schedule", id="show_schedule")
          yield Button("Install schedule", id="install_schedule", variant="warning")
        yield Static("", id="schedule_status")
      with TabPane("Logs", id="logs"):
        yield RichLog(id="logview", highlight=True, markup=False, wrap=True)
    yield Footer()

  # Function Summary:
  #    Initialise the results table columns once the DOM is ready.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    (called by Textual on mount)
  def on_mount(self) -> None:
    table = self.query_one("#results", DataTable)
    table.add_columns("Video", "Lang", "Status", "Source", "Offset(s)")

  # Function Summary:
  #    Route button presses to Save or Start.
  #
  #  Input (parameters):
  #    event [Button.Pressed]:  the button event
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    (called by Textual)
  def on_button_pressed(self, event: Button.Pressed) -> None:
    if event.button.id == "save":
      self.action_save_setup()
    elif event.button.id == "start":
      self._start_run()
    elif event.button.id == "show_schedule":
      self._show_schedule(install=False)
    elif event.button.id == "install_schedule":
      self._show_schedule(install=True)

  # Function Summary:
  #    Persist the Setup tab into settings.toml and the encrypted secrets file.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    app.action_save_setup()
  def action_save_setup(self) -> None:
    try:
      self.settings.languages = [
        c.strip() for c in self.query_one("#languages", Input).value.split(",") if c.strip()
      ] or ["en"]
      self.settings.accept_offset_threshold = float(self.query_one("#accept", Input).value or 0.05)
      self.settings.reject_offset_threshold = float(self.query_one("#reject", Input).value or 2.5)
      self.credentials.api_key = self.query_one("#api_key", Input).value.strip()
      self.credentials.username = self.query_one("#username", Input).value.strip()
      self.credentials.password = self.query_one("#password", Input).value
      save_settings(self.settings)
      save_credentials(self.credentials, config_dir())
      self.query_one("#setup_status", Static).update("[green]Saved.[/]")
    except (ValueError, OSError) as exc:
      self.query_one("#setup_status", Static).update(f"[red]Error: {exc}[/]")

  # Function Summary:
  #    Show (and optionally install) the OS scheduler entry that re-runs subhound
  #    periodically for the chosen directory, so the user can exit instead of
  #    keeping the app open while only a rate-limited provider remains.
  #
  #  Input (parameters):
  #    install [bool]:  True = install the schedule; False = just preview it
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._show_schedule(install=False)
  def _show_schedule(self, install: bool) -> None:
    status = self.query_one("#schedule_status", Static)
    target = self.query_one("#dir", Input).value.strip()
    if not target or not Path(target).exists():
      status.update("[red]Enter an existing directory above first.[/]")
      return
    try:
      interval = max(1, int(self.query_one("#schedule_interval", Input).value or 60))
    except ValueError:
      status.update("[red]Interval must be a whole number of minutes.[/]")
      return
    if install:
      ok, message = scheduling.install_schedule(Path(target), self.settings.languages, interval)
      colour = "green" if ok else "red"
      status.update(f"[{colour}]{message}[/]")
    else:
      preview = scheduling.schedule_preview(Path(target), self.settings.languages, interval)
      status.update(f"Would schedule:\n{preview}")

  # Function Summary:
  #    Start a pipeline run in a background thread, wiring its events back to the UI.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    app._start_run()
  def _start_run(self) -> None:
    if self._pipeline_running:
      return
    target = self.query_one("#dir", Input).value.strip()
    if not target or not Path(target).exists():
      self.query_one("#stats", Static).update("[red]Enter an existing directory.[/]")
      return
    resync = self.query_one("#resync", Switch).value
    wait_quota = self.query_one("#wait_quota", Switch).value
    self._pipeline_running = True
    self._seen_rows.clear()
    self.query_one("#results", DataTable).clear()
    self.query_one("#stats", Static).update("Running…")
    # A background thread runs the synchronous pipeline; the self._pipeline_running guard
    # above prevents overlapping runs (so no exclusive group is needed).
    self.run_worker(
      lambda: self._run_pipeline(Path(target), resync, wait_quota),
      thread=True, name="pipeline")

  # Function Summary:
  #    The worker body: build and run the orchestrator with UI callbacks. Runs in
  #    a background thread.
  #
  #  Input (parameters):
  #    target [Path]:       the media directory
  #    resync [bool]:       reprocess previous successes
  #    wait_quota [bool]:   wait for exhausted sources to reset and retry
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    (run via run_worker)
  def _run_pipeline(self, target: Path, resync: bool, wait_quota: bool = False) -> None:
    events = PipelineEvents(
      on_entry=lambda row: self.call_from_thread(self._add_row, row),
      on_stats=lambda stats: self.call_from_thread(self._update_stats, stats),
    )
    orch = Orchestrator(self.settings, self.credentials, events=events)
    # Stream the run's logs into the Logs tab.
    from ..logging_setup import configure_logging
    configure_logging(
      target / ".subhound" / "logs",
      callback=lambda lvl, msg: self.call_from_thread(self._write_log, msg))
    try:
      orch.run(target, resync=resync, wait_for_quota=wait_quota)
    finally:
      self.call_from_thread(self._run_finished)

  # Function Summary:
  #    Append/refresh a results-table row for a finished (video, lang) entry.
  #
  #  Input (parameters):
  #    row [ResultRow]:  the finished entry's row
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._add_row(row)
  def _add_row(self, row: ResultRow) -> None:
    table = self.query_one("#results", DataTable)
    key = f"{row.video_path}\t{row.lang}"
    offset = "" if row.sync_offset is None else f"{row.sync_offset:.3f}"
    cells = (row.video_filename, row.lang, row.status, row.result, offset)
    if key in self._seen_rows:
      for col, value in zip(table.columns, cells):
        table.update_cell(key, col, value)
    else:
      self._seen_rows.add(key)
      table.add_row(*cells, key=key)

  # Function Summary:
  #    Update the stats panel from a RunStats snapshot.
  #
  #  Input (parameters):
  #    stats [RunStats]:  the current run statistics
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._update_stats(stats)
  def _update_stats(self, stats: RunStats) -> None:
    by_source = ", ".join(f"{s}={n}" for s, n in sorted(stats.by_source.items())) or "—"
    self.query_one("#stats", Static).update(
      f"total {stats.total_pairs}  ·  skipped {stats.skipped}  ·  processed {stats.processed}  ·  "
      f"[green]success {stats.succeeded}[/]  ·  [red]failed {stats.failed}[/]  ·  "
      f"[yellow]waitlist {stats.waitlisted}[/]  ·  undetermined {stats.undetermined}\n"
      f"by source: {by_source}")

  # Function Summary:
  #    Write a log line into the Logs tab.
  #
  #  Input (parameters):
  #    message [str]:  the formatted log line
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._write_log("... Processing 3 pairs")
  def _write_log(self, message: str) -> None:
    self.query_one("#logview", RichLog).write(message)

  # Function Summary:
  #    Mark the run as finished, re-enabling Start.
  #
  #  Input (parameters):
  #    (none)
  #
  #  Output:
  #    (none)
  #
  # Example:
  #    self._run_finished()
  def _run_finished(self) -> None:
    self._pipeline_running = False
