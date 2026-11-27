<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **The changed-lines mutation check posts its surviving mutants as one sticky PR comment.** The
  comment is updated on every push and marked clean once no mutant survives. A survivor that cannot
  change behaviour takes `# mutation-ok: <reason>` at the end of its line or alone on the line above
  and is listed as waived; a `mutation-ok:` with no reason waives nothing. With the repository
  variable `MUTATION_GATE` set to `true` an unwaived survivor fails the job; unset, the check only
  warns. The mutator also drops one clause of an `and`/`or`, cuts a conditional expression to one
  branch, drops a comprehension filter or one element of a literal collection, moves a comparison
  over its edge, and replaces a call, update, raise or `continue` by `pass`; the job runs up to 80
  mutants. A run whose candidate tests failed or did not run is posted as incomplete, not as clean. Developer tooling only (`scripts/mutate_changed.py`, `--comment-json`).
