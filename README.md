# subracer

Parallel, multi-source subtitle detection, extraction, downloading and syncing
with a cross-platform TUI. Point it at a media directory and it finds a
well-synced, correct-language subtitle for every video — trying cheap local
sources first and rate-limited network sources last, stopping as soon as a good
subtitle is found.

- Pipeline details: [docs/PIPELINE.md](docs/PIPELINE.md)
- Status & roadmap: [ROADMAP.md](ROADMAP.md)

## Quick start

```bash
uv sync
uv run subracer                       # launch the TUI
uv run subracer --headless --dir /media --languages en   # run once, headless
```

## Subtitle sources

Sources are tried in the order below, per (video, language), stopping at the
first subtitle that synchronizes within your thresholds. Movie-only and TV-only
sources are filtered automatically by media type.

| Order | Source | Media | Auth |
|---|---|---|---|
| 1 | Local OpenSubtitles DB | movies + TV | none (local files) |
| 2 | OpenSubtitles.com | movies + TV | optional (API key + account) |
| 3 | SubSource | movies + TV | API key |
| 4 | Gestdown (Addic7ed proxy) | TV only | none |
| 5 | YIFY (yts-subs.com) | movies only | none |
| 6 | Podnapisi | movies + TV | none |
| 7 | TVsubtitles.net | TV only | none |

## Provider API limits

These are **download** limits per source. subracer tracks remaining quota where a
provider reports it, and when a provider is exhausted it moves the affected
videos to a wait-list and retries that source after its quota resets (see
[docs/PIPELINE.md](docs/PIPELINE.md)).

| Provider | Unauthenticated | Authenticated (free account) | Membership / paid tier |
|---|---|---|---|
| **Local OpenSubtitles DB** | Unlimited (offline) | — | — |
| **OpenSubtitles.com** | 5 downloads / IP / 24 h | 20 downloads / day (rises with user rank) | **VIP:** up to 1000 downloads / day |
| **SubSource** | API key required for the v1 API | Per **API key**: 60 req/min · 1,800 req/hour · 7,200 req/day (rate-limit headers returned) | No paid membership |
| **Gestdown** (Addic7ed proxy) | No published per-day cap; caching proxy, fair-use expected | n/a (no accounts) | n/a |
| **YIFY** (yts-subs.com) | No published cap (HTML site, no API) | n/a (no accounts) | n/a |
| **Podnapisi** | No published per-day cap; fair-use expected | n/a (no accounts) | n/a |
| **TVsubtitles.net** | No published cap (HTML site, no API) | n/a (no accounts) | n/a |

Notes:

- **OpenSubtitles.com** also enforces a short-term request rate limit (HTTP 429
  on bursts) in addition to the daily download quota; the API reports
  `remaining` downloads and a reset countdown, which subracer uses to drive its
  wait-list. An app **API key** is required for API access even when running
  unauthenticated; a username/password adds the per-account quota.
- **SubSource** v1 API limits are **per API key** (60/min, 1,800/hour,
  7,200/day) and responses include rate-limit headers. (Sending the SubSource
  API key from subracer is a planned follow-up — see [ROADMAP.md](ROADMAP.md).)
- **Gestdown**, **YIFY**, **Podnapisi** and **TVsubtitles.net** publish no hard
  daily limits, but they are community/free services — use them politely.
  subracer's per-source "already tried" tracking avoids re-querying a source for
  the same video, and concurrency is bounded by the parallelism settings.
- Figures reflect the providers' published policies at the time of writing and
  can change; treat them as guidance, not guarantees.

## Tooling

ffmpeg/ffprobe are bundled via `static-ffmpeg` (a system install is used when
present); ffsubsync is a Python dependency. No manual external installs are
required.
