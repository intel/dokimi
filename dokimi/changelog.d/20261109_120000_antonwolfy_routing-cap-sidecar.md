### Fixed

- **A `novelty routing` run stopped by `--max-usd` saves past a sidecar it cannot use** — when
  `--out` already holds a finished result, the partial one goes to the first free
  `<out stem>.partial.json`, `<out stem>.partial2.json`, ... or one that is already partial. A
  FIFO there blocked the run while it read it, a read-only partial file stopped it with a
  permission error, so the paid calls were not saved, and a dangling symlink was taken for a free
  name and written through, creating its target. A sidecar that is not a regular file (a FIFO, a
  symlink) is now skipped without being opened or followed, and a partial one this cannot write
  is skipped too, in a single-artifact run and in `--all-vs-all`. A sidecar name too long for the
  file system stops the save with that error rather than trying ever longer names.
