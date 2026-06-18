# subracer — Roadmap & Progress

Parallel, multi-source subtitle detection / extraction / download / sync with a
cross-platform TUI. This file tracks the approved implementation plan and what's
done. The full design lives in the approved plan; this is the working checklist.

Run the whole suite (verifies the test dataset checksums first, then runs tests):

```bash
uv run pytest
```

---

## Phase roadmap

- **Phase 1 — MVP (end-to-end):** config + store + hardware-keyed secrets · scan +
  media autodetect · identify · extract · sync · results TSV + skip · OpenSubtitles.com
  provider + quota · local OSDB **metadata** build/lookup · async orchestrator ·
  Textual TUI (Setup / Run / Logs).
- **Phase 2 — Providers:** ✅ SubSource (v1 JSON), Gestdown (TV-only Addic7ed proxy), YIFY
  (movies, yts-subs.com scrape), Podnapisi (movies+TV JSON), TVsubtitles.net (TV-only scrape);
  full ordered fallback chain + per-media-type filtering. (A separate Addic7ed provider was
  dropped — Gestdown already serves Addic7ed without its harsh rate limits.)
- **Phase 6 — Provider follow-ups:** send the SubSource **API key** (v1 API is rate-limited
  per key: 60/min · 1,800/hr · 7,200/day) and surface its rate-limit headers in the quota
  tracker; **opt-in live integration tests** (skipped by default, enabled via env var + real
  keys) to validate SubSource/YIFY/Podnapisi/TVsubtitles parsing against current markup.
- **Phase 3 — Quota orchestration:** ✅ quota detection, per-source wait-list, reset
  scheduling, and automatic retry of wait-listed videos once a source's quota resets
  (`run(wait_for_quota=True)` / `--wait-for-quota`).
- **Phase 4 — Full mirror:** ✅ opt-in milahu torrent shards + update shards + language split.
  **Implementation notes (from research):**
  - milahu distributes via **magnet links only** — no direct HTTP downloads for SQLite files; libtorrent remains required.
  - Shards are organized by **subtitle ID range** (e.g. `104xxxxx`, `103xxxxx`), not by language. Per-file language filtering at download time is not possible; download the full shard, then run `language_split()` afterward.
  - The torrent source is an **RSS feed** in the repo at `release/opensubtitles.org.dump.torrent.rss` — milahu does not use GitHub releases. The current `fetch_latest_release()` implementation targets the GitHub releases API (which returns empty) and needs to be replaced with an RSS parser.
  - There are **12 shards** covering ~100k subtitle IDs each; a complete database requires all of them. The TUI should let the user choose latest-only vs. all shards before downloading.
- **Phase 5 — Polish:** TUI beautification (theme/palette, styled stats dashboard + progress,
  per-row status colors, directory picker), offset-verification screen, unwanted/forced track
  cleanup, packaging. (Phase 1 TUI is functional but intentionally unstyled.)

---

## Progress checklist

| # | Task | Status |
|---|------|:------:|
| 1 | Scaffold project (pyproject, package dirs, uv venv, deps) | ✅ |
| 2 | `config/`: settings model, TOML store, encrypted hardware-keyed secrets, portable export/import | ✅ |
| 3 | `core/`: scan, identify (PTT + directory-aware), hashing, subtitle_lang | ✅ |
| 4 | `core/`: tools discovery, extract (ffmpeg embedded + existing subs), sync (ffsubsync + thresholds) | ✅ |
| 5 | `pipeline/results.py`: TSV read/write + run log + skip logic + tried-sources sidecar + quota pools | ✅ |
| 6 | `providers/`: base ABC + Candidate/QuotaState, opensubtitles_com, registry | ✅ |
| 7 | `osdb/`: metadata builder + index (hash-first) + local_osdb provider | ✅ |
| 8 | `pipeline/`: quota tracker + orchestrator (thread-pool) + logging_setup | ✅ |
| 9 | `tui/`: Textual app (Setup / Run / Logs) + `__main__` headless entry | ✅ |
| 10 | Tests (75) + headless end-to-end run | ✅ |
| 11 | `osdb/torrent_client.py`: libtorrent wrapper, per-file priority selection, blocking download loop | ✅ |
| 12 | `osdb/mirror.py`: `MirrorManager` — GitHub release fetch, file-filtered torrent download, `language_split`, `mirror_state.json` persistence, update detection | ✅ |
| 13 | `config/settings.py`: `osdb_mirror_repo` field (override for power users) | ✅ |
| 14 | `providers/registry.py`: MIRROR mode wires `MirrorManager` paths into `LocalOsdbProvider` | ✅ |
| 15 | `tui/app.py`: "Local Mirror" section in Setup tab (status, progress bar, Download/Update button) | ✅ |
| 16 | `tests/test_mirror.py`: state roundtrip, GitHub parsing, update detection, download flow, missing-dep guard | ✅ |

**Phase 1 MVP complete** — full pipeline runs end-to-end (scan → identify → run log/skip →
embedded → existing → local OSDB → OpenSubtitles → results TSV), via TUI or `--headless`.

Legend: ✅ done · �doing in progress · ⬜ not started

---

## Done so far — detail

### 1. Scaffolding
- `src/` layout, `pyproject.toml`, `uv` venv. Deps: textual, httpx, platformdirs,
  pycountry, langdetect, tomli-w, ffsubsync, parsett, cryptography; dev: pytest,
  pytest-asyncio, openpyxl. External tools present: ffmpeg, mkvtoolnix (ffsubsync via pip).

### 2. config/
- [`settings.py`](src/subracer/config/settings.py) — typed `Settings` model mirroring
  Subservient options + new fields (source order/enabled, OSDB mode, parallelism caps);
  `sources_for(media_type)` filters by movie/tv/unknown.
- [`store.py`](src/subracer/config/store.py) — TOML load/save in platformdirs config dir.
- [`secrets.py`](src/subracer/config/secrets.py) — credentials in an encrypted file
  (`secrets.enc`, 0600) using authenticated AES (Fernet); key derived at runtime from
  hardware/OS ids (Linux machine-id / macOS IOPlatformUUID / Windows MachineGuid /
  MAC+hostname fallback). No key stored or hard-coded; no master password. Decrypts only
  on the same machine.
- [`portable.py`](src/subracer/config/portable.py) — plaintext export bundle +
  `install_bundle` (re-encrypts with the local machine key) + `delete_file`.

### 3. core/
- [`scan.py`](src/subracer/core/scan.py) — recursive video discovery + skip_dirs.
- [`identify.py`](src/subracer/core/identify.py) — PTT-based parsing with a directory-aware
  layer (show/SXX/episode), permissive season/episode formats, hyphen-title fix, year/title
  disambiguation, "unknown" with explanatory note when unresolvable.
- [`hashing.py`](src/subracer/core/hashing.py) — OpenSubtitles moviehash + (size, mtime_ns)
  fingerprint.
- [`subtitle_lang.py`](src/subracer/core/subtitle_lang.py) — detect a subtitle file's
  language from its text (langdetect + pycountry).

### 4. core/ (extract + sync)
- [`tools.py`](src/subracer/core/tools.py) — **self-contained tooling**: prefers a system
  ffmpeg/ffprobe, else falls back to bundled binaries via `static-ffmpeg` (fetched/cached on
  first use); `ensure_tools_on_path()` exposes them so ffsubsync finds ffmpeg; `ffsubsync` runs
  as `<python> -m ffsubsync`. `check_tools()` reports availability and whether bundled binaries
  are in use. (mkvextract dropped — ffmpeg handles extraction.)
- [`extract.py`](src/subracer/core/extract.py) — probe subtitle streams (ffprobe), extract
  text tracks to SRT (ffmpeg), and discover existing external subs next to the video. Image
  tracks (PGS/VobSub) are skipped (need OCR).
- [`sync.py`](src/subracer/core/sync.py) — `synchronize()` via ffsubsync, offset estimation,
  `classify_offset()` (ACCEPT/VERIFY/REJECT vs thresholds), and `apply_offset_ms()` for manual
  correction. This is the sync-test reused at every pipeline stage.

### 5. pipeline/results.py
- The mandated results TSV (`ResultRow` + read/write) and the in-run **RunLog**: builds per
  (video, language) entries, skips unchanged previous successes (unless resync), tracks which
  sources were tried per entry, builds per-source quota pools, records success/waitlist/failed,
  and persists to a JSON sidecar for resume. Authoritative flow in [docs/PIPELINE.md](docs/PIPELINE.md).

### 6. providers/
- [`base.py`](src/subracer/providers/base.py) — `Provider` ABC (`search`/`download`/`quota`/
  `supports`), `Candidate`, `QuotaState`, and `QuotaExceeded`.
- [`opensubtitles_com.py`](src/subracer/providers/opensubtitles_com.py) — OpenSubtitles.com REST
  provider: API-key + JWT login, hash/title/year or show+SxxExx search, download with quota
  tracking (remaining/reset), raises `QuotaExceeded` on 406/429 or zero remaining.
- [`registry.py`](src/subracer/providers/registry.py) — builds enabled+implemented providers in
  the configured order; `providers_for(media_type)` applies per-type filtering. (SubSource /
  Gestdown / YIFY / Addic7ed land in Phase 2.)

### Phase 2 providers
- [`gestdown.py`](src/subracer/providers/gestdown.py) — Addic7ed proxy JSON API, **TV-only**
  (needs season+episode). [`subsource.py`](src/subracer/providers/subsource.py) — SubSource
  **v1** REST (`/api/v1/movies/search`, `/api/v1/subtitles`, `/subtitles/{id}/download`).
  [`yify.py`](src/subracer/providers/yify.py) — yts-subs.com HTML scrape (movies; bs4).
  [`podnapisi.py`](src/subracer/providers/podnapisi.py) — podnapisi.net JSON search (movies+TV;
  lowers TLS seclevel). [`tvsubtitles.py`](src/subracer/providers/tvsubtitles.py) —
  tvsubtitles.net multi-step HTML scrape (TV only; bs4).
  [`_util.py`](src/subracer/providers/_util.py) — zip→srt extraction + language-name mapping.
- Per-media-type ordering: movies → opensubtitles_com, subsource, **yify**, **podnapisi**; TV →
  opensubtitles_com, subsource, **gestdown**, **podnapisi**, **tvsubtitles** (local_osdb first
  when enabled).

### 7. osdb/
- [`index.py`](src/subracer/osdb/index.py) — query layer over the milahu `subz_metadata` schema;
  **hash-first** lookup when a `MovieHash` column is present (PRAGMA-detected), then title/year/
  season-episode fill.
- [`builder.py`](src/subracer/osdb/builder.py) — create schema, ingest records, and
  `language_split()` to shrink the store to wanted languages.
- [`local_osdb.py`](src/subracer/osdb/local_osdb.py) — `Provider` over the index; resolves actual
  SRT bytes from milahu-style data DBs (`subtitles.srt_zstd`, zstd). Network-free; first external
  source. Registered in the registry and gated on `osdb_mode` + DB presence.

### Validation / tests
- `uv run pytest` gates on dataset SHA-256 integrity (`tests/conftest.py`) before running.
- Identify validated against the labeled `tests/data/portable_media_test_set` (333 scored
  video files): type 97% · year 100% · season 100% · episode 97% · title 98.8%. The only
  "unknown" results are the 10 positional `Fallen_Angel.mkv` cases (asserted).
- Reports written each run: `tests/reports/identify_report.{html,xlsx}` (integrity banner +
  per-file color-coded results). Accuracy table: `uv run python tests/eval_identify.py`.

---

## Deviations from the original plan (intentional)
- **Secrets:** switched from OS keyring to a single **hardware-key-encrypted file** as the
  only mechanism (predictable cross-platform; no Secret Service dependency). Added a
  password-manager-style **plaintext export/import** to move config between machines.
- **Parsing:** delegate filename parsing to **PTT (parsett)** instead of bespoke regexes,
  with a thin directory-aware wrapper.
- **Self-contained tooling:** ffmpeg/ffprobe bundled via **static-ffmpeg** (system install used
  when available); ffsubsync is a Python dep; mkvtoolnix/mkvextract dropped (unused). No manual
  external installs required.
- **Concurrency:** the orchestrator uses a **thread pool + semaphores** (not asyncio) since the
  building blocks are synchronous subprocess/HTTP calls; cleaner fit and easy to bound per stage.

### 8. pipeline/ (orchestrator + quota + logging)
- [`orchestrator.py`](src/subracer/pipeline/orchestrator.py) — runs docs/PIPELINE.md per
  (video, lang): embedded → existing → providers in order, short-circuit on first good sync,
  places the named `.srt`, records to the RunLog; parallel across entries (thread pool) with
  per-stage semaphores; emits `RunStats` (incl. skipped + undetermined) and per-entry events.
- [`quota.py`](src/subracer/pipeline/quota.py) — thread-safe `QuotaTracker`: exhausted sources,
  reset timers, **FIFO** per-source wait-lists (earliest-blocked videos retried first);
  `resettable()` lists sources with a known reset countdown for the drain scheduler.

### Phase 3 — quota orchestration
- Main pass: a source that raises `QuotaExceeded` is marked exhausted (with its reset countdown)
  and the blocked (video, lang) entries are wait-listed (`status=WAITLIST`), not failed.
- `Orchestrator._drain_quota_pools()` runs after the main pass when `wait_for_quota=True`: it
  takes the soonest-resetting source (OpenSubtitles preferred on ties) within `max_quota_wait`,
  waits out the reset (injectable `self._sleep` for tests), clears the source, and reprocesses
  only the entries that still need it. Each source is drained at most once per run; resets beyond
  the ceiling or with no known reset are left for a later run.
- Stats are now **derived** from the run log (`_recompute_stats`) instead of incremented, so a
  wait-listed entry that later succeeds on retry is counted once (WAITLIST→SUCCESS, no
  double-count). Local stages (embedded/existing) are skipped on retry via `needs_source`.
- Exposed as `run(wait_for_quota=, max_quota_wait_seconds=)`, the `--wait-for-quota` /
  `--max-quota-wait` headless flags, and a "Wait for quota" switch on the TUI Run tab.

### Multi-day persistence + scheduled runs
- **Keep running is the default.** `run(wait_for_quota=True)` is the default: a run does the main
  pass then stays up, draining quota wait-lists across *successive* resets (day after day) until
  everything resolves. `_drain_quota_pools` loops over reset cycles; a source whose whole cycle
  resolves nothing is "stalled" and left wait-listed (no spinning). Pass `wait_for_quota=False`
  (headless `--once`) for a single pass that exits — used by scheduled/cron runs.
- **Resume is always on** (crash/restart recovery, and what the cron option relies on). `run()` loads
  the prior run-log sidecar and `RunLog.build()` restores in-progress entries (wait-listed on a quota,
  etc.) with their tried-sources + diagnostics, re-opening them as PENDING. So a restart picks up where
  it left off: exhausted-as-miss sources aren't re-queried and embedded subs aren't re-extracted, while
  a quota-blocked source (never marked "tried") is retried. Fixed a related bug: a provider is only
  marked "tried" after its downloads finish without a quota block, so a mid-download quota leaves it
  retriable. Progress is persisted after the main pass and after every drain cycle.
- **Single run per directory** ([pipeline/lock.py](src/subracer/pipeline/lock.py)): an OS-level
  advisory lock (`fcntl`/`msvcrt`) on `.subracer/lock` stops two subracer processes from working the
  same directory at once and corrupting the shared TSVs / sidecar. A second run raises `RunLockError`
  (headless exits 3); the kernel frees the lock automatically if a process crashes, so it never goes
  stale.
- **Cron / Task Scheduler option** ([scheduling.py](src/subracer/scheduling.py)): instead of keeping
  the app open just to wait on a slow drip, the user can exit and let the OS re-run subracer
  periodically; each run resumes and grabs newly-freed quota. `schedule_preview()` shows the exact
  crontab line (Linux/macOS) or `schtasks` command (Windows); `install_schedule()` installs it
  (crontab rewrite preserving other entries / `schtasks /Create`). Surfaced as a "Scheduled runs"
  section on the TUI Run tab (interval + Show/Install) and the headless `--print-schedule`
  [`--schedule-interval N`] flag.

### Wide diagnostics TSV
- A second, opt-in **`parallel_pipeline_diagnostics.tsv`** is written next to the mandated results
  TSV (which stays byte-for-byte unchanged). It is **wide**: every mandated column, then five
  columns per source (`<source>_tried`, `_candidates`, `_lang_match`, `_offsets`, `_outcome`) for
  all nine sources (embedded, existing, local_osdb, opensubtitles_com, subsource, gestdown, yify,
  podnapisi, tvsubtitles). Per-source `SourceDiag` records (candidates found, language matches,
  every measured sync offset, and a `DIAG_*` outcome — good/rejected/none/quota/download_failed/
  error) are captured during processing in [results.py](src/subracer/pipeline/results.py) and
  persisted in the run-log JSON sidecar. For data nerds / troubleshooting, not the main results file.

### Subtitle normalization + download integrity
- [`core/subtitle_convert.py`](src/subracer/core/subtitle_convert.py) — `normalize_to_srt()`
  converts **every** subtitle (downloaded, on-disk, or extracted) to clean UTF-8 **SubRip**
  before syncing/placement. SRT is the most broadly supported sidecar across Plex/Emby/Jellyfin/VLC
  (delivered as text, no transcode). Time-based formats (SRT/ASS/SSA/VTT) convert with timing
  preserved (styling dropped); **MicroDVD** (frame-based) is timed using the video's real frame
  rate via `extract.video_frame_rate()` (ffprobe), falling back to 23.976. Format is detected from
  *content*, not extension, so mislabeled files still convert. Encoding is decoded tolerantly
  (UTF-8 → confident chardet → cp1252). Wired into the orchestrator as an injectable
  `normalize_fn` choke point; image/garbage payloads fail normalization and the candidate is skipped.
  (Future: an opt-in "preserve original styled format" flag for anime/"signs & songs" ASS tracks.)
- [`providers/_util.py`](src/subracer/providers/_util.py) `write_subtitle_bytes()` now **integrity-
  checks** downloads: ZIP archives are CRC-verified (`testzip()`) and any malformed/corrupt/truncated
  archive is discarded; the extracted payload is sanity-checked (decodes as text, isn't an HTML
  error page / JSON envelope, meets a minimum size) before being written; the written file's
  SHA-256 is logged for traceability. (Providers don't supply content hashes and HTTPS already
  guards in-flight corruption, so we validate structurally.)
- [`logging_setup.py`](src/subracer/logging_setup.py) — per-run log file + optional callback
  handler for the TUI live log.

### 9. tui/ + entry point
- [`tui/app.py`](src/subracer/tui/app.py) — Textual app with Setup (edit settings + credentials,
  no file editing), Run (pick dir, resync switch, Start, live per-(video,lang) table + summary
  stats) and Logs (live stream) tabs. The orchestrator runs in a background thread; updates are
  marshalled to the UI via `call_from_thread`.
- [`__main__.py`](src/subracer/__main__.py) — launches the TUI, or `--headless --dir <path>
  [--languages ..] [--resync]` runs once and prints a summary. Installed as the `subracer` command.

### 10. Tests
- 75 tests, gated on dataset checksums. Coverage: identify (dataset eval + HTML/XLSX reports),
  config/secrets/portable, results/run-log, providers (mock transport), osdb, orchestrator
  (injected fakes), CLI (headless), and a TUI compose smoke test (`run_test`).
- **Testing:** added a labeled dataset harness with HTML/Excel reports and checksum gating.
