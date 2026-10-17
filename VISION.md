<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Vision

## Why

Agents now read skills, docs, MCP servers, persona prompts and repository
guidance before they act. All of this is cheap to write and easy to
over-claim. Today nobody can see whether a given skill or repository actually
makes an agent better. Skill authors write blind, repo maintainers guess what
their `AGENTS.md` does, and marketplaces rank by popularity.

Dokimi exists to replace belief with measured, tested data.

## The point

**Trust what passed a test that could have failed.** For any skill, doc
source, MCP server, profile or repository, dokimi should answer three
questions with evidence:

1. **Does it help?** The same tasks, the same model, with and without the
   artifact, and the delta with its statistics. This A/B comparison is the
   core; everything else builds on it.
2. **What exactly helps, and what doesn't?** The parts that add something,
   the parts the model already knew, the parts that hurt, and what is still
   missing.
3. **What to do next?** A concrete path to improve the skill, the repository
   or the MCP server, so that the next run measures better.

## Where we are going (next 3–6 months)

Point dokimi at any skill or repository and get the full picture: exact
numbers, where the artifact helps and where it breaks, and a ranked list of
changes to make. First use case: HPC and numerical libraries, where
popularity signals are weakest and correctness matters most.

## How we build it

- **The A/B comparison is the core.** Novelty, routing, repository scans,
  tool preference and whatever comes next are ways to get sharper or cheaper
  answers to the same question.
- **Cheap first, costly only when needed.** Static and LLM-free checks run
  first. Expensive runs (agents, Docker, panels) are only for what the cheap
  checks cannot settle. How much rigor and how much spend is up to the use
  case and should be configurable.
- **Combine signals.** Tests, oracles, LLM judges and static checks each catch
  different things. The goal is to combine them into a result better than any
  one of them gives alone, not to pick a winner.
- **Reuse, don't rebuild.** Sit on top of existing harnesses, benchmarks and
  eval tools, and integrate them in pieces or whole. We write code only where
  nothing else does the job.
- **Vendor-neutral and reproducible.** Any model, any harness, any content.
  Content lives in its own repositories and the framework measures it.
  Results are paired, carry statistics, and can be re-run.
- **Agents are first-class users.** Coding agents run dokimi, read its
  reports and act on them. Contributors and adopters come next. The CLI and
  the library are the interface.

## Not settled yet

We are still exploring, so nothing is ruled out. A shared scorecard format may
come later, but only if it earns its place.
