<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`eval coverage`: which needs of a subject an eval set grades, without a model call.**
  `python cli.py eval coverage <evals.json | skill dir | tasks root> --graph need_graph.json`
  reads a frozen need graph (new `need_graph.v1` schema; a novelty need graph is valid as
  written) and the `needs` list on each `evals.json` row or in a task's `metadata.needs`. It
  prints the weighted coverage, the coverage of `core` + `rare_critical` and of `rare_critical`
  alone, the uncovered `core` / `rare_critical` nodes, and each eval's relevance
  (`relevant_core`, `relevant_peripheral`, `out_of_scope`, `orphan`, `unlabelled`). `--out`
  writes an `eval_coverage.v1` artifact. Exit 1 under a `--min [METRIC=]X` floor, exit 2 on an
  unreadable input, a graph with duplicate node ids, or a `needs` label that is not a list of
  ids the graph has. A row or task with no `needs` key is `unlabelled`; an explicit
  `"needs": null` is malformed and exits 2. `--out` is refused when it would overwrite the
  graph or any input file. A `--min` floor is judged on the unrounded share, not the rounded
  figure shown in the report. Novelty's need graph now keeps `confirmed_by` and `provenance`.
  Building the graph, the user interview and drafting labels follow in #331.
