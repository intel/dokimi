<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`novelty routing --estimate-only`** prints, as JSON, the calls, input and output tokens and the list-price `usd` that `--model` would make, from the same selection prompt the run sends, and makes no model call. Without `--model` the bill is zero. `--all-vs-all` has no estimate yet; its stderr line still says how many calls the model arm makes.
- **`novelty routing --all-vs-all --repeats N`** asks each query N times, as a single-artifact run does, and prints and writes the `noise` block (agreement per query, the rate of each repeat, `half_range`). The model arm's counts are then calls. `--repeats` still needs `--model`.
- **`novelty routing --all-vs-all --model`** writes the same `cost` block as the single-artifact run, on stdout and in the result file.

### Fixed

- **A query that names a skill left out for `disable-model-invocation` says so.** `--all-vs-all` refused it as "not in the field" with no hint of why; the message now names each such skill and points to `--include-disabled`.
- **`--queries-from-headings` no longer gives up on a skill with no usable heading.** Only `##` and `###` headings of two or more content words were offered, so a skill whose headings were one word each, or a `#` title alone, had no queries (24 of 144 real skills). Its description's sentences are now the fallback, still stamped as self-derived triage, and the result records `queries_source`. A skill with neither still exits non-zero with the reason.
- **`--seed` help says what it fixes.** It seeds the card order only; it does not make the model's answers repeatable, which is what `--repeats` measures. The help no longer calls the run reproducible.
