# Smoke test for the Textual TUI: it composes, the key widgets exist across the
# three tabs, and the results table is initialised. Uses Textual's headless
# run_test harness (no real terminal).

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from subhound.tui.app import DirectoryPicker, SubhoundApp


@pytest.mark.asyncio
async def test_tui_composes_and_has_widgets():
  from textual.widgets import DataTable, Input, RichLog

  app = SubhoundApp()
  async with app.run_test() as pilot:
    # Setup tab widgets.
    assert app.query_one("#languages", Input) is not None
    assert app.query_one("#api_key", Input).password is True
    # Run tab: results table initialised with its columns.
    table = app.query_one("#results", DataTable)
    assert len(table.columns) == 5
    # Logs tab.
    assert app.query_one("#logview", RichLog) is not None
    await pilot.pause()


@pytest.mark.asyncio
async def test_tui_start_button_runs_pipeline():
  # Regression: the pipeline-running guard must not collide with Textual's own
  # internal state, and clicking Start must actually run the pipeline and add a
  # results row. Runs fully offline (no enabled sources -> no network).
  from textual.widgets import DataTable, Input, TabbedContent

  d = Path(tempfile.mkdtemp())
  (d / "Inception (2010)").mkdir()
  (d / "Inception (2010)" / "Inception (2010).mkv").write_bytes(b"x" * 150000)

  app = SubhoundApp()
  app.settings.enabled_sources = []  # offline; embedded/existing find nothing -> FAILED
  async with app.run_test(size=(120, 36)) as pilot:
    app.query_one(TabbedContent).active = "run"
    for _ in range(4):
      await pilot.pause()
    app.query_one("#dir", Input).value = str(d)
    for _ in range(4):
      await pilot.pause()
    await pilot.click("#start")
    for _ in range(100):
      await pilot.pause(0.1)
      if not app._pipeline_running:
        break
    assert app._pipeline_running is False
    assert app.query_one("#results", DataTable).row_count == 1
    assert (d / "parallel_pipeline_results.tsv").exists()


@pytest.mark.asyncio
async def test_directory_picker_sets_media_directory():
  from textual.widgets import Input, RadioButton, TabbedContent

  directory = Path(tempfile.mkdtemp()).resolve()
  app = SubhoundApp()
  async with app.run_test(size=(120, 36)) as pilot:
    app.query_one(TabbedContent).active = "run"
    app.query_one("#dir", Input).value = str(directory)
    await pilot.pause()
    await pilot.click("#browse_directory")
    for _ in range(4):
      await pilot.pause()
    assert isinstance(app.screen, DirectoryPicker)
    assert app.screen.selected_path == directory
    # Confirm the picker returns path + is_series, updates dir and saves settings.
    await pilot.click("#choose_directory")
    for _ in range(4):
      await pilot.pause()
    assert app.query_one("#dir", Input).value == str(directory)
    assert app.settings.last_directory == str(directory)
    assert app.settings.series_mode is False  # Movies was default


@pytest.mark.asyncio
async def test_running_state_disables_mutable_controls():
  from textual.widgets import Button

  app = SubhoundApp()
  async with app.run_test(size=(80, 24)) as pilot:
    app._set_running(True)
    await pilot.pause()
    for selector in (
      "#dir", "#browse_directory", "#resync", "#wait_quota", "#start",
      "#schedule_interval", "#show_schedule", "#install_schedule", "#save",
    ):
      assert app.query_one(selector).disabled is True
    assert str(app.query_one("#start", Button).label) == "Running…"

    app._set_running(False)
    await pilot.pause()
    assert app.query_one("#start", Button).disabled is False
    assert str(app.query_one("#start", Button).label) == "Start"


@pytest.mark.asyncio
async def test_dashboard_progress_and_success_row_styling():
  from rich.text import Text
  from textual.widgets import DataTable, ProgressBar

  from subhound.pipeline.orchestrator import RunStats
  from subhound.pipeline.results import ResultRow, SUCCESS

  app = SubhoundApp()
  async with app.run_test(size=(100, 30)) as pilot:
    stats = RunStats(
      total_pairs=5,
      processed=4,
      succeeded=2,
      failed=1,
      waitlisted=1,
      skipped=1,
      by_source={"milahu": 2},
    )
    app._update_stats(stats)
    await pilot.pause()
    progress = app.query_one("#progress", ProgressBar)
    assert progress.total == 5
    assert progress.progress == 4

    result = ResultRow(
      video_path="/media/Movie.mkv",
      video_size=1,
      video_mtime_ns=1,
      updated_at="",
      type="movie",
      title_or_show="Movie",
      year=2024,
      season=None,
      episode=None,
      video_filename="Movie.mkv",
      lang="en",
      sync_offset=0.125,
      good_subtitle=True,
      result="milahu",
      status=SUCCESS,
      subtitle_file="/media/Movie.en.srt",
    )
    app._add_row(result)
    await pilot.pause()
    cells = app.query_one("#results", DataTable).get_row(
      "/media/Movie.mkv\ten")
    assert all(isinstance(cell, Text) for cell in cells)
    assert all("A3BE8C" in str(cell.style).upper() for cell in cells)
    assert str(cells[2]) == SUCCESS
