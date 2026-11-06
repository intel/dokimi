<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`novelty routing --keep-copies-under <dir>`**: keeps only the copies under `<dir>` (a directory relative to `--catalog`) of a same-name catalog skill whose copies differ, when the copies under it agree. A value that is empty, absolute, leaves `--catalog`, or is not a directory is refused with exit 2. `--all-vs-all --estimate-only`: prices every call the run would make, as the single-artifact `--estimate-only` does, with no client built and no query sent.
- **`novelty routing --all-vs-all` saved field**: records `keep_copies_under`, and lists each skill whose queries were dropped in `field.dropped_queries` (one record per skill: `skill`, `reasons`, `keep_flags`, `queries`).

### Fixed

- **Same-name catalog copies with different content**: `novelty routing` now names the conflicting skills, the directory of each copy and the `--catalog <dir>` or `--keep-copies-under <dir>` to route with, instead of an empty field. A copy at the catalog root is named as such, not as `<catalog>/.`.
- **`--all-vs-all --queries-by-skill` with queries for a skill the field leaves out**: those queries are dropped with a WARNING naming the flag that keeps the skill (`--include-disabled`, `--include-invalid`, `--keep-copies-under`); the run is refused only when no query is left. A name no rule explains is still refused, as a likely typo, and a catalog skill that failed to load is refused with the load error.
- **`--all-vs-all` "leaves out" list**: names only skills the field does not have, so a skill with one copy still in the field is not reported as left out.
- **`--all-vs-all` missing flags**: a missing `--queries-by-skill <file>` and `--out <file>` are reported in one error.
