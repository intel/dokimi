<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Dokimi — A/B testing for agent context

[![docs](https://github.com/intel/dokimi/actions/workflows/docs.yml/badge.svg?branch=main)](https://intel.github.io/dokimi/)
[![codecov](https://codecov.io/gh/intel/dokimi/graph/badge.svg?branch=main)](https://codecov.io/gh/intel/dokimi)

**Documentation: <https://intel.github.io/dokimi/>** — guides, plus a
[CLI reference](https://intel.github.io/dokimi/reference/cli/) and an
[API reference](https://intel.github.io/dokimi/reference/api/dokimi/) generated from the code.

> **dokimi** — Greek *δοκιμή*, "test, proof". The name is the method: an
> artifact does not get trusted because it was written or because it is popular,
> it gets trusted because it passed a test that could have failed.

Skills, instructions, docs, MCP servers and repository guidance are cheap to
write and easy to over-claim. Dokimi tests each one like an experiment: change
one thing, keep everything else fixed, run the same question or task set, and
report the delta together with its p-value. An artifact ships with the
scorecard that earned it.

What measuring shows ([evidence](dokimi/docs/skill-novelty-evidence.md)):

- **Most of a skill repeats what the model already knows.** For
  `claude-opus-5`, 70% of what a skill says is something the model already
  produces unprompted; 21% is new, and 2% breaks something it knew.
- **Models believe their context, even when it is wrong.** A plausible wrong
  value planted in a skill is repeated 94% of the time.
- **Value depends on the model.** What a small model needs, a frontier model
  may already know, so every verdict is per model.
- **Popularity is not evidence.** Popularity-ranked marketplaces structurally
  bury niche HPC and numerical-accuracy tooling.

The framework is vendor-neutral: any model reachable through LiteLLM, any
docs, skills or tasks. Why it exists and where it is going:
[`VISION.md`](VISION.md).

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
VISION.md                          Why the project exists and where it is going
Makefile                           Delegates targets into dokimi/
dokimi/                            The framework (self-contained Python project)
LICENSE  NOTICE                    Apache-2.0 + attribution (repo-wide)
.github/workflows/                 CI (runs inside dokimi/)
```

All code, tests and CI live in [`dokimi/`](dokimi/). The
evaluation content that gets measured — skills, executable tasks, golden
question sets — lives in its own repository and is passed in by path, so the
framework can measure artifacts it does not own.

## Reviewing a skill or a repository

The usual request is "use dokimi to review this skill" or "review this
repository's agent setup". This section is for whoever runs that request, a
person or a coding agent. Run the steps in order: each one costs more than
the step before it, and the first ones are free.

Setup, from [`dokimi/`](dokimi/):

```bash
cd dokimi
pip install -e .                 # installs the `dokimi` command
```

The `novelty` and `arms` steps take `--model` and `--provider`; `arms run` also
needs `--judge-provider` (it does not fall back to `--provider` for the judge);
`tasks run` and `repo` take `--model` only. The provider is one of `openai`,
`anthropic`, `amazon-bedrock`, `google-vertex` or `openrouter`.
Credentials are read from the environment: `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `AWS_REGION` with
`AWS_BEARER_TOKEN_BEDROCK` for Bedrock, and Google application-default
credentials (`GOOGLE_APPLICATION_CREDENTIALS`, with `VERTEXAI_PROJECT` and
`VERTEXAI_LOCATION`) for Vertex. The judge must be a different model from the
one under measurement, because a model that grades its own answers inflates
the scores. `novelty` refuses a self-judge; `arms run` does not check, so name
`--judge --judge-model <other> --judge-provider <judge-provider>` yourself.

### A skill

| Step | Command | Cost |
|---|---|---|
| 1. Does it load? | `dokimi catalog scan --root <catalog> --kind skills` | free |
| 2. Is it ever picked? | `dokimi novelty routing --artifact skill:<dir> --catalog <catalog> --queries queries.json [--model …]` | free without `--model` |
| 3. What will step 4 cost? | `dokimi novelty analyze --artifact skill:<dir> --tier triage --include-resources --estimate-only` | free |
| 4. What does it add? | the same command without `--estimate-only`, plus `--model`, `--provider`, `--judge-model` and `--max-usd` | ~100 calls |
| 5a. Do answers improve? | `dokimi arms run --product <p> --questions <set.json> --arms baseline,skill:<dir> --model <model> --provider <provider> --judge --judge-model <judge-model> --judge-provider <judge-provider>` | one answer and one grade per question and arm |
| 5b. Does code improve? | `dokimi tasks run --tasks-root <checkout>/evaluation/harbor/tasks --all --harnesses docker-claude,docker-claude-skill:<dir> --baseline-harness docker-claude` ([coding-harnesses.md](dokimi/docs/coding-harnesses.md)) | one agent run per task and harness; needs Docker and Claude credentials (`AWS_BEARER_TOKEN_BEDROCK`, `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`), not the provider keys above; run with `--dry-run` first |

- **Competitors** are the other skills an agent sees next to this one when it
  picks a skill to load. For a catalog skill they are its siblings in the
  same catalog: `--catalog <dir>` takes every other skill under it, and
  `--competitors skill:<path> …` names them one by one. Routing asks whether
  this skill wins the user requests meant for it. If you only have the one
  skill, skip step 2 and say so in the review: a number measured against
  invented competitors measures nothing.
- **Queries** are 5–10 requests written the way a user would phrase them.
  Write them without looking at the skill's own headings.
- **Stop after triage (step 4)** and report. The `standard` and `study`
  tiers cost 5–20 times more, so run them only when whoever asked agrees to
  the bill. Pass `--max-usd` to `novelty` steps: the run stops once it has
  spent that much (calls in flight can overshoot), writes no artifact when the
  cap trips, and stops at the first call to a model with no known price. Set
  the cap above the expected dollar spend: the triage example below was about
  $1.80. `arms run` and `tasks run` have no cap, so size them first: questions
  times arms, or tasks times harnesses. One measured example: triage on a
  19k-token skill with a mid-size model was 103 calls, about $1.80; `study` on
  the same skill was 1,900 calls, about $14.50.
- **At triage** with `--include-resources`, the 25-atom cap takes atoms in
  document order, and the entry point comes first: the flag reaches
  `references/` only if `SKILL.md` has fewer than 25 atoms. For such skills,
  use `--tier standard` (60 atoms), or `--all-atoms` for a full sweep, which costs more.
- Step 5 needs evaluation content: a golden question set for 5a, executable
  tasks for 5b. `arms run` takes no tasks. That content lives outside this repository
  ([external-content.md](dokimi/docs/external-content.md)). Without it, the
  review covers selection and content, not outcomes, and should say so.

The full path with reading notes: [skill-novelty-walkthrough.md](dokimi/docs/skill-novelty-walkthrough.md).

### A repository's agent setup

| Step | Command | Cost |
|---|---|---|
| 1. Do the paths, links and commands named in the guidance exist? | `dokimi repo scan <repo>` | free |
| 2. Which guidance files does the harness actually load? | `dokimi repo reach <repo> --model <id> --dry-run`, then a few `--only <scenario>` with `--budget` | capped by `--budget` (default $0.10) per scenario; needs the `claude` binary and Anthropic or Bedrock credentials |
| 3. What does the root guidance add? | `dokimi novelty analyze --artifact text:<repo>/AGENTS.md --tier triage …` | ~100 calls |
| 4. Does the guidance change task outcomes? | `dokimi repo run <repo> --manifest-file … --model <id> --batch <name> --arm guidance:none,guidance:raw --agent-network host --budget <usd> --max-batch-usd <usd>` | needs the repository's own tasks, Docker, `claude` binary on PATH (or `--bin`); `--budget` defaults to $2.00 per agent run with no batch cap unless you pass `--max-batch-usd` |

With no `repo-eval.yaml` in the repository, steps 1 and 2 check the default
guidance globs (`<repo>/**/AGENTS.md`, `<repo>/**/CLAUDE.md`,
`<repo>/.github/copilot-instructions.md`, `<repo>/.github/instructions/*.md`)
and say so.
To choose the files for `scan`, write a guidance-only manifest outside the tree and pass
it with `--manifest-file`:

```yaml
schema: repo_manifest.v1
id: my-repo
guidance:
  globs: ["**/AGENTS.md", "**/CLAUDE.md", ".github/copilot-instructions.md"]
  also_scan: ["CONTRIBUTING.md"]
```

The `id` must be lowercase: start with a letter or digit, then use only letters, digits, `.`, `_` and `-`.
`reach` always adds the harness's own guidance file names (for example `CLAUDE.md`, `CLAUDE.local.md` or `AGENTS.md`).

A guidance-only manifest has no tasks, so step 4 refuses it.

How to read the results:

- **Scan:** `fail` counts against the guidance and each one still needs a
  look. Harness tool names (`run_in_background`) and placeholders
  (`<name>`, `caseNN_*`) can show up as `fail` too. `unresolved` also needs
  a look: it can be a path the scan could not check, or one whose directory
  is gone.
- **Reach:** a guidance file nobody loads cannot help anyone. Run `reach`
  before you judge the guidance's content. `loaded` comes from the harness
  itself, not from the harness's documentation, so a result that contradicts
  the docs is a finding.
- **Step 4:** without tasks there is no outcome measurement. The review
  should say so rather than infer one from steps 1–3.

The contract a repository codes against: [repo-evaluation-contract.md](dokimi/docs/repo-evaluation-contract.md).

## Developing dokimi

```bash
make benchmark-run    # delegates into dokimi/ (see its README)
make test             # runs the dokimi test suite
```

For the framework's quickstart, CLI surface, and architecture, see
[`dokimi/README.md`](dokimi/README.md).

## Where to start reading

The **Documentation** links at the top go to the published site and its CLI and API
reference. The hand-written pages can also be read on GitHub under
[`dokimi/docs/`](dokimi/docs/README.md).

| You want to understand… | Read |
|---|---|
| Why the project exists and where it is going | [`VISION.md`](VISION.md) |
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
