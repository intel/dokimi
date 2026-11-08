### Changed

- **The static docs checks move under `docs` (#94 M3)**: `docs check` replaces `run` (the benchmark
  spec run and snapshot, same flags and default paths) and `docs compare` replaces `compare` (the
  snapshot diff, same flags). Both old names still answer, but they print `moved: dokimi docs check
  ...` or `moved: dokimi docs compare ...` to stderr and exit 2 without running anything. Move the
  scripts, CI steps and Makefile targets to the new paths before the next release. The names `run` and
  `compare` stay free for the loop verbs in the release after this one.

  | Before | After |
  |---|---|
  | `run --spec S --out-json J --out-md M` | `docs check --spec S --out-json J --out-md M` |
  | `compare --base A --candidate B` | `docs compare --base A --candidate B` |

  `novelty routing` is not moved yet.
