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

## Recommended practice

For reproducible work, it is often useful to keep both:

- a snapshot for the full interactive state
- an export bundle for the derived products you actually cite or inspect

That combination gives you both replayability and clean final artifacts.
