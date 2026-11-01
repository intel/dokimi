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
  writes an `eval_coverage.v1` artifact. Exit 1 under `--min-coverage` / `--min-rare-critical`,
  exit 2 on an unreadable input or a `needs` id the graph lacks. Building the graph, the user
  interview and drafting labels follow in #331.
