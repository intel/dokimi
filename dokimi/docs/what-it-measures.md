<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# What Dokimi measures

## 1. Does it help? Enabled vs disabled arms

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
| `guidance:rev:<rev>` | the same repository with the guidance files of another revision, such as a PR's `AGENTS.md` (repository mode) |
| `patch:<file>` | the repository with a unified diff applied on top of its own guidance: a new script, a Makefile target (repository mode) |

The same arms drive question answering (LLM-as-judge with judge-validity
checks), executable Docker coding tasks through Codex, Claude Code, Harbor or
BenchFlow harnesses (`dokimi tasks run`), and file/line localization
(`dokimi explore run`). Results roll up per subject (`dokimi subjects run`):
a skill, MCP server, doc source, profile, bundle or repository, each with
a scorecard.

## 2. What does it add? Novel vs known content

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
[`skill-novelty.md`](skill-novelty.md).

## 3. Repositories: scan the guidance, then measure agents working in them

A repository is a subject in its own right, described by a `repo-eval.yaml`
manifest it owns:

- `dokimi repo scan` (L0, no LLM): do the links, paths, identifiers, commands
  and build labels the guidance names exist in the tree?
- `dokimi repo budget` (no LLM): how many characters of guidance, `.claude/rules` and skill
  listing every model call pays for at start-up, what loads lazily or on
  demand, and which AGENTS.md files a CLAUDE.md turns off.
- `dokimi repo reach` (L1): which guidance files does a given harness actually
  load?
- `dokimi repo inspect` (static, no LLM): which instruction files, checks,
  capability surfaces and risky hooks or scripts the repository offers an
  agent, with findings per stage.
- `dokimi repo run`: run an agent per (task, arm, model, repetition) inside the
  repository's image, grade each run with the repository's own tests, and
  `dokimi repo report` the per (task, arm, model) table. With the `guidance:*`
  arms this measures what the repository's agent guidance does to the agent's
  work; `dokimi repo compare` puts each arm against a baseline arm, paired by
  task.

See the [repository evaluation contract](repo-evaluation-contract.md).
LLM-free static checks on the documentation itself (`dokimi run`) round this out.
