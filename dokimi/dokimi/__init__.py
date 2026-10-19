# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Dokimi: measure whether documentation, a skill, an MCP doc source or an agent
persona prompt improves a coding agent's answers and task outcomes.

The command line (`python cli.py`, documented in the CLI reference) is the
supported entry point; this reference covers the modules behind it. They group
by evaluation track, as `docs/architecture.md` lays out:

- Static documentation track: [`ingest`][dokimi.ingest] loads Markdown,
  [`metrics`][dokimi.metrics] scores it, [`runner`][dokimi.runner] runs a
  benchmark spec and compares snapshots, [`gate`][dokimi.gate] applies the
  gates, [`report`][dokimi.report] writes the result.
- LLM evaluation track: [`personas`][dokimi.personas],
  [`questions`][dokimi.questions], [`mcp`][dokimi.mcp] (doc-source clients),
  [`eval`][dokimi.eval] (answering and judging),
  [`dashboard`][dokimi.dashboard] and [`orchestrator`][dokimi.orchestrator].
- Treatment arms: [`treatments`][dokimi.treatments], with the content they
  inject from [`skills`][dokimi.skills] and
  [`agent_profiles`][dokimi.agent_profiles].
- Executable tasks: [`harnesses`][dokimi.harnesses] runs coding agents on task
  directories; [`context`][dokimi.context] reads what their context window
  held.
- Content novelty: [`novelty`][dokimi.novelty]. Repositories as subjects:
  [`repos`][dokimi.repos]. Scorecards over time: [`subjects`][dokimi.subjects].
  Exploration quality: [`explore`][dokimi.explore]. External catalogs:
  [`external`][dokimi.external].

Shared plumbing sits in the top-level modules: [`artifacts`][dokimi.artifacts]
(versioned artifact schemas), [`llm`][dokimi.llm] (model calls),
[`config`][dokimi.config] and [`registry`][dokimi.registry].

The installed version is `dokimi.__version__`.
"""

from dokimi._version import __version__

__all__ = ["__version__"]
