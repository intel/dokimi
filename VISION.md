<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Vision

**A/B testing for agent context.**

## Why

Agents now read skills, docs, MCP servers, persona prompts and repository
guidance before they act. Today nobody can see whether any of it actually
makes an agent better: skill authors write blind, repo maintainers guess what
their `AGENTS.md` does, and marketplaces rank by installs.

- **Context is cheap to write and easy to over-claim.** A skill can read well
  and change nothing, or make answers worse.
- **Value depends on the model.** What a small model needs, a frontier model
  may already know. For `claude-opus-5` under lenient grading, 70% of what a
  skill says the model already produces unprompted; under strict grading the
  skill's real job shows up in exact values and qualifiers
  ([evidence](dokimi/docs/skill-novelty-evidence.md)).
- **Models believe their context, even when it is wrong.** A plausible wrong
  value planted in a skill is repeated 85–94% of the time in a small study
  ([evidence](dokimi/docs/skill-novelty-evidence.md)).
- **Popularity is not evidence.** Stars and installs bury niche expert
  tooling, such as HPC and numerical libraries.

Dokimi exists to replace belief with measured, tested data.

## The point

**An artifact is trusted because it passed a test that could have failed.**

Change one thing (a skill, instructions, docs, an MCP server, `AGENTS.md`).
Keep everything else fixed. Get the difference, with its p-value.

Every answer leads to an action:

1. **Does it help?** Ship it, or don't, per model. This A/B comparison is the
   core; everything else builds on it.
2. **What exactly helps, and what doesn't?** Edit it: the parts that add
   something, the parts the model already knew, the parts that hurt, and what
   is still missing.
3. **What to do next?** Fix it: a concrete path to improve the skill, the
   repository or the MCP server, so that the next run measures better.

## Where we are going

Point dokimi at any skill or repository and get the full picture: exact
numbers, where the artifact helps and where it breaks, and a ranked list of
changes to make. First use case: HPC and numerical libraries, where
popularity signals are weakest and correctness matters most.

## How we build it

- **The A/B comparison is the core.** Novelty, routing, repository scans,
  tool preference and whatever comes next are ways to get sharper or cheaper
  answers to the same question.
- **Beat a placebo, not just nothing.** Off-topic text of the same length
  made two of five measured models much worse (a third somewhat worse), so a gain
  should beat a control of comparable length. Today that control is opt-in
  (`dokimi novelty analyze --artifact FILE --arms no_artifact,full,placebo --placebo-text FILE2`).
- **"Not measured" is never shown as "pass".**
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
