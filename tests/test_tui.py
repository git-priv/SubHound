# Smoke test for the Textual TUI: it composes, the key widgets exist across the
# three tabs, and the results table is initialised. Uses Textual's headless
# run_test harness (no real terminal).

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from subracer.tui.app import SubracerApp


@pytest.mark.asyncio
async def test_tui_composes_and_has_widgets():
  from textual.widgets import DataTable, Input, RichLog

  app = SubracerApp()
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

  app = SubracerApp()
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
