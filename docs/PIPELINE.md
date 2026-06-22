# subhound — Per-run pipeline (authoritative spec)

The whole point: **find a good (well-synced, correct-language) subtitle for each
video and drop out of the pipeline for that video as early as possible.** Cheap,
local, network-free sources are tried first; paid/rate-limited network sources
last. We never re-check a source we've already checked for a given video.

## Order of operations

1. **Discover** every video file under the target directory (recursive).
2. **Build a run log** (same shape as the results file) containing all videos from step 1.
3. **Compare** the run log against the most recent results TSV.
4. **Skip completed:** unless the user enabled **resync**, drop from the run log any
   video whose last run status is `SUCCESS` (it already has a good synced sub). Skipped
   count is reported in the TUI summary.
5. **Embedded subtitles** — for every video still in the run log, extract embedded tracks:
   - 5a. If embedded subs are found, **sync-test** them.
   - 5b. If a synced embedded sub meets the user's thresholds (good), write it as a properly
     named `.srt` next to the video.
   - 5c. Mark that (video, lang) `SUCCESS` in the run log so later stages skip it.
6. **Existing external subs** — scan each video's directory for plain-text subtitle files
   (not only `.srt`):
   - 6a. Sync-test each, only for languages the user configured.
   - 6b. If a good sync is found, convert to `.srt` and save with the proper name → `SUCCESS`.
7. **External sources** — for videos still without a good sub, query sources **in order**,
   stopping the instant a candidate syncs well:
   - 7a. **Local OpenSubtitles DB** first (if built): pull all subs it has for that video +
     language.
   - 7b. Sync-test each; on the first good sync, update the run log, drop the named `.srt`
     in the directory, and **move to the next video**.

   Repeat 7 / 7a / 7b for the remaining videos against the following sources, in order:

   | Scope | Sources (in order) |
   |---|---|
   | All media | OpenSubtitles API · SubSource · Gestdown |
   | Movies only | YIFY Subtitles (find the working domain) |
   | TV only | Addic7ed |

8. **Tried-source tracking:** the run log records which sources have already been checked for
   each (video, lang) so the same source is never queried twice for the same video.
9. **Quota pools:** when an API's daily limit is exhausted, use the run log's tried-source data
   to build a **per-API pool** of videos not yet checked against that API. When the limit
   refreshes, process the pool — only videos that still need that specific source.

## Key invariants
- Per **(video, language)** granularity throughout (a video may need several languages).
- **Short-circuit**: as soon as a good synced sub is found for a (video, lang), that pair is
  `SUCCESS` and no further sources are tried for it.
- **Idempotent / resumable**: the run log persists progress (including tried sources) so an
  interrupted run resumes without repeating work, and a re-run skips `SUCCESS` rows unless
  resync is on.
- **Good** = synced offset within the user's accept threshold (see `core/sync.classify_offset`)
  and matching a configured language.

## Results TSV columns (one file for movies and TV; type-specific cells blank when N/A)
```
video_path  video_size  video_mtime_ns  updated_at  type  title_or_show  year  season
episode  video_filename  lang  extracted_from_video  existing_subs  db_candidates
api_candidates  sync_offset  good_subtitle  result  status  subtitle_file
```
`tried_sources` is **not** a TSV column — it lives in the run-log sidecar so the mandated
schema stays fixed.
