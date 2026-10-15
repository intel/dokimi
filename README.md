<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Dokimi — agent measurement framework

[![codecov](https://codecov.io/gh/intel/dokimi/graph/badge.svg?branch=main)](https://codecov.io/gh/intel/dokimi)

> **dokimi** — Greek *δοκιμή*, "test, proof". The name is the method: an
> artifact does not get trusted because it was written or because it is popular,
> it gets trusted because it passed a test that could have failed.

Documentation, skills, MCP doc sources and agent persona prompts are cheap to
write and easy to over-claim. This repository is the framework that measures
whether any of them actually helps a coding agent: change one thing, hold
everything else fixed, run the same question or task set, and report the delta
together with its p-value.

The measurement is the deliverable. An artifact ships with the scorecard that
earned it — a deliberate contrast to popularity-ranked marketplaces, which
structurally bury niche HPC and numerical-accuracy tooling.

The framework is vendor-neutral: any model reachable through LiteLLM, any
docs, skills or tasks.

## What it measures

### 1. Does it help? Enabled vs disabled arms

An *arm* is one context condition. Every arm runs the same question or task
set against the same model, and the report gives the per-arm delta against the
baseline with paired statistics. Arms are given as specs (`dokimi arms run
--arms …`):

| Arm | What the agent gets |
|---|---|
| `baseline` | nothing extra |
| `docs:<source>` | product documentation (local Markdown, URLs, Context7) |
| `mcp:<server>` | an MCP documentation server it can call |
| `skill:<path>` | one skill, its instructions in context |
| `skill-agent:<path>` | one skill offered by name and description; the model must decide to load it |
| `skill-catalog:<root>` | a whole skill catalog, to measure whether the right skill gets selected |
| `profile:<path>` | an agent persona / system prompt |
| `guidance:none\|raw\|bridge` | a repository with its `AGENTS.md`/`CLAUDE.md` removed, kept, or bridged (repository mode, below) |

The same arms drive question answering (LLM-as-judge with judge-validity
checks), executable Docker coding tasks through Codex, Claude Code, Harbor or
BenchFlow harnesses (`dokimi tasks run`), and file/line localization
(`dokimi explore run`). Results roll up per subject (`dokimi subjects run`):
a skill, MCP server, doc source, profile, bundle or repository, each with
a scorecard.

### 2. What does it add? Novel vs known content

An arm delta says whether an artifact helps; it does not say which parts.
`dokimi novelty analyze --artifact skill:<path>|text:<path>` splits the
artifact into knowledge atoms and probes each one against the model with and
without the artifact. Each atom comes out **added** (new to the model),
**redundant** (the model already knew it), **degraded** (the artifact broke
something the model knew) or **still missing** (stated but not picked up).
`text:` takes any file, so the same analysis runs on a documentation page or
a repository's `AGENTS.md`. The output is per (artifact, model) pair, with
shortening candidates (`novelty shorten`) and a check that the artifact can be
selected among competitors at all (`novelty routing`). See
[`skill-novelty.md`](dokimi/docs/skill-novelty.md).

### 3. Repositories: scan the guidance, then measure agents working in them

A repository is a subject in its own right, described by a `repo-eval.yaml`
manifest it owns:

- `dokimi repo scan` (L0, no LLM): do the links, paths, identifiers, commands
  and build labels the guidance names exist in the tree?
- `dokimi repo reach` (L1): which guidance files does a given harness actually
  load?
- `dokimi repo run`: run an agent per (task, arm, model, repetition) inside the
  repository's image, grade each run with the repository's own tests, and
  `dokimi repo report` the per (task, arm, model) table. With the `guidance:*`
  arms this measures what the repository's agent guidance does to the agent's
  work; `dokimi repo compare` puts each arm against a baseline arm, paired by
  task.

See the [repository evaluation contract](dokimi/docs/repo-evaluation-contract.md).
LLM-free static checks on the documentation itself (`dokimi run`) round this out.

## Repository layout

```text
README.md                          This overview
Makefile                           Delegates targets into dokimi/
dokimi/                            The framework (self-contained Python project)
LICENSE  NOTICE                    Apache-2.0 + attribution (repo-wide)
.github/workflows/                 CI (runs inside dokimi/)
```

All code, tests and CI live in [`dokimi/`](dokimi/). The
evaluation content that gets measured — skills, executable tasks, golden
question sets — lives in its own repository and is passed in by path, so the
framework can measure artifacts it does not own.

## Getting started

```bash
make benchmark-run    # delegates into dokimi/ (see its README)
make test             # runs the dokimi test suite
```

For the framework's quickstart, CLI surface, and architecture, see
[`dokimi/README.md`](dokimi/README.md).

## Where to start reading

The documentation is published at <https://intel.github.io/dokimi/>, which adds a
CLI and API reference generated from the code. The hand-written pages can also be
read on GitHub under [`dokimi/docs/`](dokimi/docs/README.md).

| You want to understand… | Read |
|---|---|
| Why measure context at all, and what an arm is | [`dokimi/docs/README.md`](dokimi/docs/README.md) |
| How to point the framework at your own questions, tasks and skills | [`dokimi/docs/external-content.md`](dokimi/docs/external-content.md) |
| How the measurement engine works | [`dokimi/docs/architecture.md`](dokimi/docs/architecture.md) |
| How to run an evaluation end to end | [`dokimi/docs/quickstart.md`](dokimi/docs/quickstart.md) |
| How to compare treatment arms (docs / skills / profiles) | [`dokimi/docs/evaluating-treatments.md`](dokimi/docs/evaluating-treatments.md) |
| How coding agents are run against executable Docker tasks | [`dokimi/docs/coding-harnesses.md`](dokimi/docs/coding-harnesses.md) |
| What a skill actually adds to a model, and what can be cut | [`dokimi/docs/skill-novelty.md`](dokimi/docs/skill-novelty.md) |
| How a whole repository is evaluated as a subject | [`dokimi/docs/repo-evaluation-contract.md`](dokimi/docs/repo-evaluation-contract.md) |
| What products are registered | [`dokimi/dokimi/products.yaml`](dokimi/dokimi/products.yaml) |
| What user problems map to which product | [`dokimi/dokimi/intents.yaml`](dokimi/dokimi/intents.yaml) |
| What changed in each release | [`dokimi/CHANGELOG.md`](dokimi/CHANGELOG.md) |

## License

Licensed under the [Apache License, Version 2.0](LICENSE); see [NOTICE](NOTICE)
for attribution.
