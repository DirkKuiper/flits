# Exports and Snapshots

FLITS supports both export bundles and full session snapshots.

## Export bundles

Use the export workflow when you want shareable outputs such as:

- structured JSON results
- plots
- analysis windows
- catalog-style artifacts
- array-based products

The export preview helps you see what is currently available from the session
state before downloading anything.

## Session snapshots

Session snapshots capture the analysis state itself rather than just the final
outputs. They are useful for:

- pausing and resuming work
- sharing a reproducible interactive state
- archiving the exact crop/mask/selection choices used in an analysis

Use **Save Session** to write the current session next to the source data in a
`snapshots/` folder. If the session was opened from an existing saved snapshot,
**Save Session** updates that JSON file. Use **Save Copy** when you want a
timestamped copy instead. The **Saved Sessions** browser can search stored
snapshots by source, file name, note text, preset, and DM, then reopen a
matching session directly.

Snapshots do not embed the raw filterbank data. To share a snapshot with someone
else, use **Download JSON** or send the saved JSON file and make sure they start
FLITS with `--data-dir` or `FLITS_DATA_DIR` pointing at a directory that
contains the same source data file. FLITS records the source file name,
data-directory-relative path when available, file size, SHA-256 content hash,
and scientific metadata so the import can find moved/copied data and reject the
wrong file.

## Recommended practice

For reproducible work, it is often useful to keep both:

- a snapshot for the full interactive state
- an export bundle for the derived products you actually cite or inspect

That combination gives you both replayability and clean final artifacts.
