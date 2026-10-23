# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Dokimi: measure whether context improves a coding agent's answers and task outcomes.

The context under test is documentation, a skill, an MCP doc source or an agent
persona prompt.

The command line, `dokimi` (`python cli.py` in a source checkout), has its own
CLI reference; this reference covers the modules behind it. They group by
layer, as `docs/architecture.md` lays out and
`tests/test_import_architecture.py` enforces: a module imports only from its
own layer and the ones below. The front end is `dokimi.cli` and
`dokimi.commands`, the argparse commands, documented in the CLI reference.

Tracks and the tools beside them:

- Static documentation track: [`runner`][dokimi.runner] runs a benchmark spec
  and compares snapshots, on top of the loaders and scoring listed below.
- Reports and pipelines: [`report`][dokimi.report] renders the static, LLM,
  arm, task, subject and exploration results,
  [`dashboard`][dokimi.dashboard] aggregates results across libraries and
  [`orchestrator`][dokimi.orchestrator] runs the one-command pipeline.
- Executable tasks: [`harnesses`][dokimi.harnesses] runs coding agents on task
  directories.
- [`context`][dokimi.context] reads what a run's context window held.
- Content novelty: [`novelty`][dokimi.novelty]. Repositories as subjects:
  [`repos`][dokimi.repos]. Subject scorecards, one per run:
  [`subjects`][dokimi.subjects].
- Exploration quality: [`explore`][dokimi.explore]. Tool choice:
  [`tool_preference`][dokimi.tool_preference]. External catalogs:
  [`external`][dokimi.external]. Dataset export:
  [`datasets_export`][dokimi.datasets_export].

LLM evaluation engine:

- [`eval`][dokimi.eval] (answering and judging),
  [`personas`][dokimi.personas] and [`questions`][dokimi.questions].
- Treatment arms: [`treatments`][dokimi.treatments], with runtime modifiers in
  [`plugins`][dokimi.plugins].

Content loaders and pure scoring:

- [`ingest`][dokimi.ingest] loads Markdown (and, optionally, other document
  types), [`metrics`][dokimi.metrics] scores it, [`gate`][dokimi.gate] applies
  the static, novelty and trust gates.
- [`mcp`][dokimi.mcp] holds the doc-source clients;
  [`skills`][dokimi.skills] and [`agent_profiles`][dokimi.agent_profiles] hold
  the content the treatment arms inject.

Shared plumbing sits at the bottom, in the top-level modules:
[`artifacts`][dokimi.artifacts] (versioned artifact schemas),
[`records`][dokimi.records] (reading answer and eval records),
[`llm`][dokimi.llm] (model calls), [`caching`][dokimi.caching],
[`process`][dokimi.process] (starting child processes),
[`config`][dokimi.config], [`registry`][dokimi.registry] and
[`intents`][dokimi.intents].

The installed version is `dokimi.__version__`.
"""

from dokimi._version import __version__

__all__ = ["__version__"]
