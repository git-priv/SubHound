# subhound — Per-run pipeline (authoritative specification)

The pipeline resolves one subtitle for every requested `(video, language)` pair.
It tries local candidates before external providers and stops as soon as a
candidate is acceptable under the configured synchronization thresholds.

## Run initialization

1. Create the target `.subhound/` state directory and acquire its advisory lock.
   A second process targeting the same directory fails instead of sharing state.
2. Recursively discover videos, excluding configured skip directories.
3. Identify each video as a movie, episode, or unknown item.
4. Build one run-log entry per video and requested language.
5. Load the prior results TSV and `.subhound/run_log.json` sidecar:
   - Previous successes become skipped entries unless resync is enabled.
   - In-progress entries recover their tried-source and diagnostic state.
   - A changed size or modification time causes the video to be processed again.

Pending entries are processed through a thread pool. Separate semaphores bound
extraction, provider search, and synchronization work.

## Per-entry order of operations

### 1. Embedded subtitles

- Probe text subtitle streams with ffprobe and extract matching tracks with
  ffmpeg.
- Normalize each usable track to UTF-8 SRT and sync-test it.
- Place the first acceptable result beside the video and mark the entry
  `SUCCESS`.

Image-based streams such as PGS and VobSub are skipped because OCR is outside the
current scope.

### 2. Existing external subtitles

- Discover supported text subtitle files beside the video.
- Filter them to the requested language.
- Normalize and sync-test each candidate.
- Place the first acceptable result and stop processing the entry.

### 3. External providers

Unresolved entries query the applicable providers in order:

| Media type | Provider order |
|---|---|
| Movie | Milahu → OpenSubtitles.com → SubSource → YIFY → Podnapisi |
| TV | Milahu → OpenSubtitles.com → SubSource → Gestdown → Podnapisi → TVsubtitles.net |
| Unknown | Milahu → OpenSubtitles.com → SubSource → Podnapisi |

For each provider:

1. Skip it if the run log says it was already completed for this entry.
2. Search for candidates under the shared search-concurrency limit.
3. Download up to `top_downloads` candidates.
4. Validate and normalize each payload to UTF-8 SRT.
5. Synchronize it and classify the measured offset.
6. Place the first non-rejected result and mark the entry `SUCCESS`.

The sync layer currently accepts both accept-range and verify-range results. A
manual review workflow for verify-range results is planned in
[ROADMAP.md](../ROADMAP.md).

If every applicable source completes without an acceptable result, the entry is
`FAILED` unless at least one source is blocked by quota.

## Quota handling

A provider can report exhaustion by raising `QuotaExceeded` with an optional
reset countdown.

- The source is marked exhausted and the entry becomes `WAITLIST`.
- Other applicable providers may still resolve the entry during the same pass.
- In the default keep-running mode, the orchestrator waits for eligible known
  reset times and retries only entries that still need that source.
- `--max-quota-wait` limits how distant a reset can be before it is left for a
  later run.
- `--once` disables waiting after the main pass; scheduled runs use persisted
  state to retry later.
- A reset cycle that resolves nothing is treated as stalled to prevent an
  endless loop.

A provider is marked tried only after its downloads finish without a quota
block. This preserves retryability when quota is exhausted during a download.

## Persistence and outputs

Progress is persisted after the main pass, after each quota-drain cycle, and at
run completion.

| Path | Purpose |
|---|---|
| `parallel_pipeline_results.tsv` | Stable result row for each `(video, language)` pair |
| `parallel_pipeline_diagnostics.tsv` | Result fields plus per-source counts, offsets, and outcomes |
| `.subhound/run_log.json` | Resume state, tried sources, wait-list state, and diagnostics |
| `.subhound/logs/` | Per-run logs |
| `.subhound/work/` | Temporary normalized and synchronized files |

The stable result TSV columns are:

```text
video_path  video_size  video_mtime_ns  updated_at  type  title_or_show  year
season  episode  video_filename  lang  extracted_from_video  existing_subs
db_candidates  api_candidates  sync_offset  good_subtitle  result  status
subtitle_file
```

`db_candidates` is retained for schema compatibility and records Milahu
candidates. `api_candidates` records candidates from the other external
providers. Tried-source state remains in the JSON sidecar rather than changing
the stable TSV schema.

The diagnostics TSV appends five fields for each local stage and provider:
`tried`, `candidates`, `lang_match`, `offsets`, and `outcome`.

## Invariants

- Processing granularity is always `(video, language)`.
- A successful entry never queries later sources.
- A completed source is not queried twice for the same entry.
- Resume does not repeat successful local or provider stages.
- Downloaded and local text subtitles pass through the same normalization and
  synchronization path before placement.
- One failed entry does not terminate other entry workers.
- Results, diagnostics, and resume state describe the same final entry state.
