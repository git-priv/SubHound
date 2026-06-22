# subhound

Parallel, multi-source subtitle detection, extraction, download, normalization,
and synchronization with a cross-platform TUI. Point it at a media directory
and it finds a correct-language subtitle for each video, sync-tests candidates,
and stops as soon as an acceptable result is found.

- Pipeline behavior: [docs/PIPELINE.md](docs/PIPELINE.md)
- Implementation status and remaining work: [ROADMAP.md](ROADMAP.md)

## Quick start

```bash
uv sync
uv run subhound
uv run subhound --headless --dir /media --languages en
uv run subhound --headless --dir /media --languages en --once
```

The default headless mode remains running when an exhausted provider has a known
quota reset within `--max-quota-wait`. Use `--once` for a single pass or for
periodic scheduler runs that resume from persisted state.

## Subtitle sources

For each `(video, language)` pair, subhound first checks embedded tracks and
existing subtitle files beside the video. If neither yields an acceptable
result, it tries the applicable external providers in order:

| Media | Provider order |
|---|---|
| Movies | Milahu → OpenSubtitles.com → SubSource → YIFY → Podnapisi |
| TV | Milahu → OpenSubtitles.com → SubSource → Gestdown → Podnapisi → TVsubtitles.net |
| Unknown | Milahu → OpenSubtitles.com → SubSource → Podnapisi |

Movie-only and TV-only providers are filtered automatically. The source order
and enabled set are configurable.

## Provider API limits

These figures are operational guidance and can change. subhound tracks quota
only when a provider exposes enough information to do so.

| Provider | Current integration | Published/fair-use limits |
|---|---|---|
| **Milahu** | No account or key | No documented quota |
| **OpenSubtitles.com** | App API key; optional account login | Unauthenticated and account download quotas; remaining/reset data drives the wait-list |
| **SubSource** | API-key wiring planned | API key limits documented as 60/min, 1,800/hour, and 7,200/day |
| **Gestdown** | No account | No published daily cap; fair use expected |
| **YIFY** | HTML site | No published daily cap; fair use expected |
| **Podnapisi** | Public JSON endpoint | No published daily cap; fair use expected |
| **TVsubtitles.net** | HTML site | No published daily cap; fair use expected |

Notes:

- OpenSubtitles can return quota responses during search or download. Entries
  blocked by quota are persisted and retried after a known reset.
- SubSource credential and rate-limit-header integration is planned in
  [ROADMAP.md](ROADMAP.md). It will use a provider-specific credential rather
  than reusing the OpenSubtitles API key.
- Free/community providers should be used conservatively. Searches are bounded
  by concurrency settings and completed sources are not queried twice for the
  same entry.

## Runtime state

Each target directory receives a `.subhound/` state directory containing the
run-log sidecar, temporary work files, logs, and a single-run lock. The stable
results and diagnostics TSV files are written at the target root. See
[docs/PIPELINE.md](docs/PIPELINE.md) for exact filenames and invariants.

## Tooling

ffmpeg and ffprobe are provided through `static-ffmpeg` when no system install is
available. ffsubsync is a Python dependency. No manual media-tool installation
is required for the normal workflow.
