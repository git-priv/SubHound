# subhound — Implementation Roadmap

Parallel, multi-source subtitle detection, extraction, download, normalization,
and synchronization with a cross-platform Textual interface.

This document separates the implemented baseline from remaining work. The
authoritative per-run behavior is defined in [docs/PIPELINE.md](docs/PIPELINE.md).

Run the complete verification suite with:

```bash
uv run pytest
```

## Status at a glance

| Phase | Scope | Status |
|---|---|:---:|
| 1 | End-to-end MVP | Complete |
| 2 | Provider expansion | Complete |
| 3 | Quota orchestration, persistence, and scheduling | Complete |
| 4 | Milahu provider and removal of the local OSDB experiment | Complete |
| 5 | Product polish and packaging | In progress |
| 6 | Provider authentication and live integration hardening | Planned |

## Implemented baseline

### Configuration and credentials

- Typed TOML-backed settings with configurable languages, thresholds, source
  ordering, source enablement, discovery filters, and concurrency limits.
- Machine-bound encrypted credential storage for OpenSubtitles credentials.
- Plaintext export/import for deliberately moving credentials to another
  machine, with re-encryption during import.
- Tolerant loading of older settings, including retired source identifiers.

### Discovery, identification, and local subtitle stages

- Recursive video discovery with configurable excluded directories.
- Directory-aware movie and episode identification using `parsett`.
- Embedded text-subtitle extraction through ffmpeg.
- Discovery of existing external subtitles beside each video.
- Language detection and conversion of supported text formats to UTF-8 SRT.
- Synchronization through ffsubsync with configurable accept/reject thresholds.

### External providers

Providers are filtered by media type and tried in configured order:

| Scope | Provider order |
|---|---|
| Movies | Milahu → OpenSubtitles.com → SubSource → YIFY → Podnapisi |
| TV | Milahu → OpenSubtitles.com → SubSource → Gestdown → Podnapisi → TVsubtitles.net |
| Unknown | Milahu → OpenSubtitles.com → SubSource → Podnapisi |

Milahu performs a combined search/download request. The other providers use the
common `Provider` search/download interface. Downloaded payloads are validated,
normalized, sync-tested, and accepted only when they pass the configured
thresholds.

### Orchestration and persistence

- One pipeline entry per `(video, language)` pair.
- Short-circuiting after the first acceptable subtitle.
- Bounded parallel entry processing with separate extraction, search, and sync
  concurrency limits.
- Stable results TSV, wide diagnostics TSV, and a JSON run-log sidecar.
- Resume without repeating completed sources; prior successful results are
  skipped unless resync is requested.
- Per-source quota wait-lists and retries after known reset times.
- Default long-running mode plus `--once` for scheduler-friendly single passes.
- Per-directory advisory lock preventing concurrent runs from corrupting state.
- Cron and Windows Task Scheduler preview/installation support.

### Interfaces and validation

- Textual Setup, Run, and Logs tabs with live per-entry results and summary
  statistics.
- Headless CLI, scheduling preview, configurable quota wait ceiling, and verbose
  logging.
- Self-contained ffmpeg/ffprobe fallback through `static-ffmpeg`.
- 131 tests covering configuration, secrets, discovery, identification,
  extraction, normalization, synchronization, providers, quota behavior,
  persistence, scheduling, CLI behavior, reports, and TUI composition.
- The portable media fixture is checksum-verified before the test suite runs.

## Remaining implementation plan

### Phase 5 — Product polish and packaging

#### 5.1 TUI workflow and visual polish — Complete

Deliverables:

- Directory picker (`DirectoryPicker` modal with `DirectoryTree`) instead of requiring a typed path. Keyboard binding `Ctrl+O` also opens it.
- Consistent dark theme and teal-accent palette across Setup, Run, and Logs.
- Structured progress and statistics dashboard: `ProgressBar` plus a 3×2 grid of stat cards (Total, Processed, Success, Failed, Wait-list, Skipped).
- Per-row colors for success, failure, wait-list, and skipped states applied to all cells via `ROW_STYLES`.
- `_set_running()` disables all mutable controls (run tab and setup tab) while the pipeline is active; Start button label changes to "Running…".

Acceptance criteria met:

- All workflows remain keyboard accessible (`Ctrl+O` for picker, `Ctrl+S` for save, `Q` to quit).
- Layout remains usable at common terminal sizes.
- 5 TUI tests cover composition, pipeline run, picker, running-state disable, and progress/row styling.

#### 5.2 Offset verification workflow

The sync layer already distinguishes accept, verify, and reject ranges. The
pipeline currently accepts non-rejected results automatically.

Deliverables:

- Review screen for candidates in the verify range.
- Candidate metadata, measured offset, source, and destination shown before the
  user accepts, rejects, or applies a manual correction.
- Deterministic headless policy for verify-range results.

Acceptance criteria:

- A reviewed decision is persisted and survives resume.
- Manual corrections are tested without invoking real media tools.

#### 5.3 Settings contract and media cleanup

The settings model contains preferences that are not yet wired into runtime
behavior. These must not remain as misleading no-op options.

Deliverables:

- Audit `audio_track_languages`, `smart_sync`, `delete_extra_videos`,
  `extras_folder_name`, `preserve_forced_subtitles`,
  `preserve_unwanted_subtitles`, `download_retry_503`, and `pause_seconds`.
- Implement each supported setting end to end or remove it with a tolerant
  configuration migration.
- Implement unwanted/forced subtitle cleanup for retained cleanup settings.
- Preview destructive cleanup actions before execution.

Acceptance criteria:

- Every persisted setting has tested runtime behavior or is explicitly retired.
- Cleanup never removes the selected successful subtitle.
- Destructive behavior is opt-in and covered by filesystem tests.

#### 5.4 Packaging and release readiness

Deliverables:

- Build and install wheel/sdist artifacts in clean environments.
- Verify the `subhound` console entry point on Linux, macOS, and Windows.
- Document configuration/data locations, upgrades, and uninstall behavior.
- Define versioning and release-check procedures.

Acceptance criteria:

- Clean-install smoke tests pass without importing from the source checkout.
- Release artifacts contain no test fixtures, caches, credentials, or local
  development metadata.

### Phase 6 — Provider hardening

#### 6.1 SubSource API-key and quota integration

Deliverables:

- Add a distinct SubSource API-key credential with a migration-safe secrets
  schema update.
- Send the credential through `SubSourceProvider` without conflating it with the
  OpenSubtitles API key.
- Parse SubSource rate-limit headers into `QuotaState` and the existing quota
  tracker.
- Surface missing/invalid credentials clearly in Setup and logs.

Acceptance criteria:

- Mocked tests cover authenticated requests, missing credentials, invalid keys,
  remaining quota, and reset behavior.
- Secrets never appear in logs, diagnostics, exported run state, or exceptions.

#### 6.2 Opt-in live provider tests

Deliverables:

- Environment-gated live tests for SubSource, YIFY, Podnapisi, and
  TVsubtitles.net.
- Separate markers so the default suite remains deterministic and offline.
- Sanitized failure output that captures response shape without credentials or
  user-identifying request data.

Acceptance criteria:

- Default `uv run pytest` never makes live provider calls.
- A documented command runs the live suite when explicitly enabled.
- Parser regressions produce actionable diagnostics without leaking secrets.

## Intentional exclusions and retired designs

- The local OpenSubtitles database/mirror experiment was removed. Milahu is the
  first external provider; there is no `osdb` package or local database setup.
- A separate Addic7ed provider is not planned while Gestdown provides that TV
  source through a caching proxy.
- Image-based subtitle tracks such as PGS and VobSub require OCR and remain out
  of scope.
- Styled ASS preservation is deferred; the current compatibility target is
  normalized UTF-8 SRT.

## Completion rule

A roadmap item is complete only when its implementation, automated tests, and
user-facing documentation are present and the full offline suite passes.
