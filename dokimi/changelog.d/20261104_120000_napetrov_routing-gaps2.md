<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`novelty routing --prefer-path <dir>`**: keeps the one copy of a same-name catalog skill under `<dir>` (a directory under `--catalog`) when the copies there agree. `--all-vs-all --estimate-only`: prices every call the run would make, as the single-artifact `--estimate-only` does, with no client built and no query sent.

### Fixed

- **Same-name catalog copies with different content**: `novelty routing` now names the conflicting skills, the directory of each copy and the `--catalog <dir>` or `--prefer-path <dir>` to route with, instead of an empty field.
- **`--all-vs-all --queries-by-skill` with queries for a skill the field leaves out**: those queries are dropped with a WARNING naming the flag that keeps the skill (`--include-disabled`, `--include-invalid`, `--prefer-path`); the run is refused only when no query is left. A name no rule explains is still refused, as a likely typo.
- **`--all-vs-all` missing flags**: a missing `--queries-by-skill <file>` and `--out <file>` are reported in one error.
