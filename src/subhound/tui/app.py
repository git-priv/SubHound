# subhound.tui.app
#
# Textual interface for setup, pipeline execution, scheduling, and live logs.

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
  Button,
  DataTable,
  DirectoryTree,
  Footer,
  Header,
  Input,
  Label,
  ProgressBar,
  RadioButton,
  RadioSet,
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
from ..pipeline.results import FAILED, PENDING, SKIPPED, SUCCESS, WAITLIST, ResultRow


class DirectoryPicker(ModalScreen[tuple[Path, bool] | None]):
  """Modal directory browser; returns (selected_path, is_series) or None."""

  BINDINGS = [("escape", "cancel", "Cancel")]

  def __init__(self, initial: Path, is_series: bool = False) -> None:
    super().__init__()
    self.selected_path: Path = initial if initial.is_dir() else Path("/")
    self._is_series = is_series

  def compose(self) -> ComposeResult:
    with Vertical(id="directory_dialog"):
      yield Static("Choose a media directory", id="picker_title")
      yield Static(str(self.selected_path), id="picker_path")
      yield DirectoryTree(Path("/"), id="directory_tree")
      with Horizontal(id="picker_actions"):
        with RadioSet(id="media_type_picker"):
          yield RadioButton("Movies", value=not self._is_series, id="rb_movies")
          yield RadioButton("Series", value=self._is_series, id="rb_series")
        yield Button("Use directory", id="choose_directory", variant="primary")
        yield Button("Cancel", id="cancel_directory")

  def on_mount(self) -> None:
    if self.selected_path != Path("/"):
      self.run_worker(self._expand_to_selected, thread=False)

  async def _expand_to_selected(self) -> None:
    tree = self.query_one("#directory_tree", DirectoryTree)
    node = tree.root
    node.expand()
    for part in self.selected_path.parts[1:]:
      for _ in range(30):
        await asyncio.sleep(0.05)
        if node.children:
          break
      matched = next(
        (c for c in node.children
         if c.data and hasattr(c.data, "path") and c.data.path.name == part),
        None,
      )
      if matched is None:
        break
      node = matched
      node.expand()
      tree.scroll_to_node(node)

  def on_directory_tree_directory_selected(
    self, event: DirectoryTree.DirectorySelected,
  ) -> None:
    self.selected_path = event.path.resolve()
    self.query_one("#picker_path", Static).update(str(self.selected_path))

  def on_button_pressed(self, event: Button.Pressed) -> None:
    if event.button.id == "choose_directory":
      is_series = self.query_one("#rb_series", RadioButton).value
      self.dismiss((self.selected_path, is_series))
    elif event.button.id == "cancel_directory":
      self.dismiss(None)

  def action_cancel(self) -> None:
    self.dismiss(None)


class SubhoundApp(App):
  TITLE = "SubHound"
  SUB_TITLE = "relentless, parallel multi-source subtitle fetcher"
  BINDINGS = [
    ("ctrl+q", "quit", "Quit"),
    ("ctrl+o", "browse_directory", "Choose directory"),
    ("ctrl+s", "save_setup", "Save setup"),
  ]

  CSS_PATH = "app.tcss"

  ROW_STYLES = {
    SUCCESS: "bold #A3BE8C",   # Nord Aurora green
    FAILED: "bold #BF616A",    # Nord Aurora red
    WAITLIST: "bold #EBCB8B",  # Nord Aurora yellow
    SKIPPED: "dim #81A1C1",    # Nord Frost medium blue
    PENDING: "#D8DEE9",        # Nord Snow Storm muted
  }

  def __init__(self) -> None:
    super().__init__()
    self.settings: Settings = load_settings()
    self.credentials: Credentials = load_credentials(config_dir())
    self._seen_rows: set[str] = set()
    self._pipeline_running = False

  def compose(self) -> ComposeResult:
    yield Header()
    with TabbedContent(initial="setup"):
      with TabPane("Setup", id="setup"):
        with VerticalScroll(id="setup_form"):
          yield Label("Subtitle Languages (ISO 639-1 two-letter language codes, comma-separated)")
          yield Input(",".join(self.settings.languages), id="languages")
          yield Static(
            "Examples: en, fr, de, es, it, pt, nl, sv, pl, ja, zh, ko, ar, ru\n"
            "https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes",
            id="language_help",
          )
          yield Label("OpenSubtitles.com API key")
          yield Input(self.credentials.api_key, id="api_key", password=True)
          yield Label("OpenSubtitles.com username")
          yield Input(self.credentials.username, id="username")
          yield Label("OpenSubtitles.com password")
          yield Input(self.credentials.password, id="password", password=True)
          yield Label("Accept offset threshold (s)").with_tooltip(
            "Sync offset: how many seconds a subtitle leads or lags the audio.\n"
            "Lead/Lag ≤ this → accepted automatically. \n"
            "Between this and the reject threshold → kept as-is.\n"
            "Default: 0.05 s."
          )
          yield Input(str(self.settings.accept_offset_threshold), id="accept").with_tooltip(
            "Sync offset: how many seconds a subtitle leads or lags the audio.\n"
            "Lead/Lag ≤ this → accepted automatically. \n"
            "Between this and the reject threshold → kept as-is.\n"
            "Default: 0.05 s."
          )
          yield Label("Reject offset threshold (s)").with_tooltip(
            "Sync offset: how many seconds a subtitle leads or lags the audio.\n"
            "Lead/Lag ≥ this → discarded as too far out of sync.\n"
            "Between the accept threshold and this → kept as-is \n"
            "Default: 2.5 s."
          )
          yield Input(str(self.settings.reject_offset_threshold), id="reject").with_tooltip(
            "Sync offset: how many seconds a subtitle leads or lags the audio.\n"
            "Lead/Lag ≥ this → discarded as too far out of sync.\n"
            "Between the accept threshold and this → kept as-is \n"
            "Default: 2.5 s."
          )
          yield Button("Save setup", id="save", variant="primary")
          yield Static("", id="setup_status")
      with TabPane("Run", id="run"):
        with VerticalScroll(id="run_view"):
          with Horizontal(id="run_controls"):
            yield Input(placeholder="Media directory", id="dir")
            yield Button("Browse", id="browse_directory")
            yield Label("Resync", classes="switch-label")
            yield Switch(value=False, id="resync")
            yield Label("Keep running", classes="switch-label")
            yield Switch(value=True, id="wait_quota")
            yield Button("Start", id="start", variant="success")
          yield ProgressBar(
            total=None, show_percentage=True, show_eta=False, id="progress")
          with Grid(id="stat_grid"):
            yield Static(self._card("Total", 0), id="stat_total", classes="stat-card")
            yield Static(self._card("Processed", 0), id="stat_processed", classes="stat-card")
            yield Static(self._card("Success", 0), id="stat_success", classes="stat-card success")
            yield Static(self._card("Failed", 0), id="stat_failed", classes="stat-card failed")
            yield Static(self._card("Wait-list", 0), id="stat_waitlist", classes="stat-card waitlist")
            yield Static(self._card("Skipped", 0), id="stat_skipped", classes="stat-card")
          yield Static("Ready. Choose a directory to begin.", id="run_detail")
          table = DataTable(id="results", zebra_stripes=True)
          table.cursor_type = "row"
          yield table
          with Vertical(id="schedule_panel"):
            yield Static(
              "Scheduled runs resume persisted quota wait-lists without keeping "
              "the application open.", id="schedule_help")
            with Horizontal(id="schedule_controls"):
              yield Label("Repeat every (min)", classes="switch-label")
              yield Input("60", id="schedule_interval")
              yield Button("Show schedule", id="show_schedule")
              yield Button("Install schedule", id="install_schedule", variant="warning")
            yield Static("", id="schedule_status")
      with TabPane("Logs", id="logs"):
        yield RichLog(id="logview", highlight=True, markup=False, wrap=True)
    yield Footer()

  @staticmethod
  def _card(label: str, value: int) -> str:
    return f"[dim]{label.upper()}[/]\n[bold]{value}[/]"

  def on_mount(self) -> None:
    self.query_one("#results", DataTable).add_columns(
      "Video", "Lang", "Status", "Source", "Offset(s)")
    if self.settings.last_directory:
      self.query_one("#dir", Input).value = self.settings.last_directory

  def on_button_pressed(self, event: Button.Pressed) -> None:
    actions = {
      "save": self.action_save_setup,
      "browse_directory": self.action_browse_directory,
      "start": self._start_run,
    }
    if event.button.id in actions:
      actions[event.button.id]()
    elif event.button.id == "show_schedule":
      self._show_schedule(install=False)
    elif event.button.id == "install_schedule":
      self._show_schedule(install=True)

  def action_browse_directory(self) -> None:
    value = self.query_one("#dir", Input).value.strip()
    initial = Path(value) if value and Path(value).is_dir() else Path(
      self.settings.last_directory or "/")
    is_series = bool(self.settings.series_mode)
    self.push_screen(DirectoryPicker(initial, is_series), self._directory_picked)

  def _directory_picked(self, result: tuple[Path, bool] | None) -> None:
    if result is None:
      return
    path, is_series = result
    self.query_one("#dir", Input).value = str(path)
    self.settings.last_directory = str(path)
    self.settings.series_mode = is_series
    save_settings(self.settings)
    self.query_one("#dir", Input).focus()

  def action_save_setup(self) -> None:
    try:
      self.settings.languages = [
        code.strip()
        for code in self.query_one("#languages", Input).value.split(",")
        if code.strip()
      ] or ["en"]
      self.settings.accept_offset_threshold = float(
        self.query_one("#accept", Input).value or 0.05)
      self.settings.reject_offset_threshold = float(
        self.query_one("#reject", Input).value or 2.5)
      self.credentials.api_key = self.query_one("#api_key", Input).value.strip()
      self.credentials.username = self.query_one("#username", Input).value.strip()
      self.credentials.password = self.query_one("#password", Input).value
      save_settings(self.settings)
      save_credentials(self.credentials, config_dir())
      self.query_one("#setup_status", Static).update("[green]Saved.[/]")
    except (ValueError, OSError) as exc:
      self.query_one("#setup_status", Static).update(f"[red]Error: {exc}[/]")

  def _show_schedule(self, install: bool) -> None:
    status = self.query_one("#schedule_status", Static)
    target = self.query_one("#dir", Input).value.strip()
    if not target or not Path(target).exists():
      status.update("[red]Choose an existing directory first.[/]")
      return
    try:
      interval = max(1, int(
        self.query_one("#schedule_interval", Input).value or 60))
    except ValueError:
      status.update("[red]Interval must be a whole number of minutes.[/]")
      return
    if install:
      ok, message = scheduling.install_schedule(
        Path(target), self.settings.languages, interval)
      colour = "green" if ok else "red"
      status.update(f"[{colour}]{message}[/]")
    else:
      preview = scheduling.schedule_preview(
        Path(target), self.settings.languages, interval)
      status.update(f"Would schedule:\n{preview}")

  def _set_running(self, running: bool) -> None:
    self._pipeline_running = running
    for selector in (
      "#dir", "#browse_directory", "#resync", "#wait_quota", "#start",
      "#schedule_interval", "#show_schedule", "#install_schedule",
      "#languages", "#api_key", "#username", "#password", "#accept",
      "#reject", "#save",
    ):
      self.query_one(selector).disabled = running
    start = self.query_one("#start", Button)
    start.label = "Running…" if running else "Start"

  def _reset_dashboard(self) -> None:
    self.query_one("#progress", ProgressBar).update(total=None, progress=0)
    for widget_id, label in (
      ("#stat_total", "Total"),
      ("#stat_processed", "Processed"),
      ("#stat_success", "Success"),
      ("#stat_failed", "Failed"),
      ("#stat_waitlist", "Wait-list"),
      ("#stat_skipped", "Skipped"),
    ):
      self.query_one(widget_id, Static).update(self._card(label, 0))
    self.query_one("#run_detail", Static).update("Scanning and building the run log…")

  def _start_run(self) -> None:
    if self._pipeline_running:
      return
    target = self.query_one("#dir", Input).value.strip()
    if not target or not Path(target).exists():
      self.query_one("#run_detail", Static).update(
        "[red]Choose an existing directory.[/]")
      return
    resync = self.query_one("#resync", Switch).value
    wait_quota = self.query_one("#wait_quota", Switch).value
    self._seen_rows.clear()
    self.query_one("#results", DataTable).clear()
    self._reset_dashboard()
    self._set_running(True)
    self.run_worker(
      lambda: self._run_pipeline(Path(target), resync, wait_quota),
      thread=True,
      name="pipeline",
    )

  def _run_pipeline(
    self, target: Path, resync: bool, wait_quota: bool = False,
  ) -> None:
    events = PipelineEvents(
      on_entry=lambda row: self.call_from_thread(self._add_row, row),
      on_stats=lambda stats: self.call_from_thread(self._update_stats, stats),
    )
    orchestrator = Orchestrator(self.settings, self.credentials, events=events)
    from ..logging_setup import configure_logging
    configure_logging(
      target / ".subhound" / "logs",
      callback=lambda level, message: self.call_from_thread(
        self._write_log, message),
    )
    try:
      orchestrator.run(
        target, resync=resync, wait_for_quota=wait_quota)
    finally:
      self.call_from_thread(self._run_finished)

  def _styled_cells(self, row: ResultRow) -> tuple[Text, ...]:
    style = self.ROW_STYLES.get(row.status, self.ROW_STYLES[PENDING])
    offset = "" if row.sync_offset is None else f"{row.sync_offset:.3f}"
    values = (
      row.video_filename,
      row.lang,
      row.status,
      row.result or "—",
      offset,
    )
    return tuple(Text(str(value), style=style) for value in values)

  def _add_row(self, row: ResultRow) -> None:
    table = self.query_one("#results", DataTable)
    key = f"{row.video_path}\t{row.lang}"
    cells = self._styled_cells(row)
    if key in self._seen_rows:
      for column, value in zip(table.columns, cells):
        table.update_cell(key, column, value)
    else:
      self._seen_rows.add(key)
      table.add_row(*cells, key=key)

  def _update_stats(self, stats: RunStats) -> None:
    finished = stats.skipped + stats.succeeded + stats.failed
    total = stats.total_pairs if stats.total_pairs > 0 else None
    self.query_one("#progress", ProgressBar).update(
      total=total, progress=finished)
    values = (
      ("#stat_total", "Total", stats.total_pairs),
      ("#stat_processed", "Processed", stats.processed),
      ("#stat_success", "Success", stats.succeeded),
      ("#stat_failed", "Failed", stats.failed),
      ("#stat_waitlist", "Wait-list", stats.waitlisted),
      ("#stat_skipped", "Skipped", stats.skipped),
    )
    for widget_id, label, value in values:
      self.query_one(widget_id, Static).update(self._card(label, value))
    by_source = ", ".join(
      f"{source}={count}" for source, count in sorted(stats.by_source.items())
    ) or "none yet"
    self.query_one("#run_detail", Static).update(
      f"Undetermined media: {stats.undetermined}  •  Success by source: {by_source}")

  def _write_log(self, message: str) -> None:
    self.query_one("#logview", RichLog).write(message)

  def _run_finished(self) -> None:
    self._set_running(False)
    self.query_one("#run_detail", Static).update(
      "[bold #88C0D0]Run complete.[/]")
