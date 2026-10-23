<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Architecture overview

`dokimi` ships two parallel evaluation tracks, an optional
executable-task suite, and two tracks that change the *question* rather than the
pipeline: content novelty (what does an artifact add to a model?) and
repositories as evaluation subjects (can a repo supply its own tasks?). They
share configuration (`dokimi/config/products.yaml`, `products.yaml`, `intents.yaml`)
and a single CLI entry point (`cli.py`) but otherwise operate independently.

```text
                       ┌──────────────┐
                       │   cli.py     │   single argparse front-end
                       └──┬────────┬──┘
                          │        │
       ┌──────────────────┘        └────────────────────┐
       ▼                                                ▼
┌────────────────┐                          ┌──────────────────────────┐
│ Static track   │                          │ LLM evaluation track     │
│                │                          │                          │
│ ingest/        │ Markdown files           │ personas/                │
│   └─ chunking  │                          │ questions/               │
│ metrics/       │ coverage, freshness,     │ mcp/      (context fetch) │
│   └─ scoring   │ readability, examples    │ eval/     (judges, RAGAS)│
│ gate/          │ soft/hard/critical gates │ report/   (analysis)     │
│ runner/        │ orchestration + compare  │ dashboard/(aggregation)  │
│ report/        │ JSON + Markdown out      │                          │
└──────┬─────────┘                          └──────────┬───────────────┘
       │                                               │
       └───────────────► baselines/  reports/  ◄───────┘
                            (JSON / Markdown artifacts)

                       ┌─────────────────────────────────┐
                       │ external tasks root             │
                       │  (--tasks-root <checkout>)      │
                       │  Docker + oracle + pytest       │
                       │  exercised by GitHub Actions    │
                       └─────────────────────────────────┘

┌───────────────────────────────┐   ┌───────────────────────────────┐
│ Content novelty               │   │ Repository as subject         │
│                               │   │                               │
│ novelty/   atoms → paired     │   │ repos/     repo-eval.yaml +   │
│            probes → 2×2       │   │            task.json from the │
│ gate/      novelty gates      │   │            scanned repo       │
│                               │   │ profiles/  one YAML per agent │
│ asks: what does this artifact │   │ asks: does the repo's own     │
│ add to *this* model?          │   │ guidance reach the agent?     │
└───────────────────────────────┘   └───────────────────────────────┘
```

## Static documentation track

Lives entirely under `dokimi/{ingest,metrics,gate,runner,report}`.

1. `ingest/` walks the configured root, loads Markdown files, and produces
   per-file text.
2. `metrics/` scores each file. Each metric is a small pure module:
   `coverage`, `freshness_lite`, `readability`, `example_pass_rate`. See
   [contributing-metric.md](contributing-metric.md) for how to add one.
3. `runner/run.py` reads `benchmarks/spec.v1.yaml`, runs the enabled
   metrics, normalizes weights, and writes a snapshot JSON.
4. `gate/` applies soft gates, hard gates, critical bands, and regression
   thresholds (`runner/compare.py` powers `cli.py compare`).
5. `report/` turns snapshots and comparisons into Markdown.

Entry points: `python cli.py run`, `python cli.py compare`, `python cli.py
report`.

## LLM evaluation track

Lives under `dokimi/{personas,questions,mcp,eval,report,dashboard,orchestrator}`.

1. `personas/` discovers and validates target user personas from GitHub
   activity.
2. `questions/` generates, dedupes, and validates persona-driven questions,
   plus document-grounded questions ("hybrid generation").
3. `mcp/` retrieves documentation chunks for each question. The default
   client is Context7 over HTTP (`mcp/context7.py`); `mcp/factory.py`
   dispatches `--doc-source` to alternative clients (`local:`, `url:`, or a
   custom registered client — see
   [adding-doc-source.md](adding-doc-source.md)).
4. `eval/` runs answer generation (`llm.py`), single-judge scoring, the
   multi-judge panel, and RAGAS meta-evaluation. Every consumer of an
   `answers.v1`/`eval.v1`/`arms.v1` record (judge, panel, RAGAS, grounding,
   reports, gates) reads it through `dokimi/records.py`, the one module that
   knows the binary (`with_docs`/`without_docs`) and N-arm (`arms`/`scores`)
   shapes.
5. `report/eval_report.py` produces the per-product Markdown analysis.
6. `dashboard/` aggregates per-library results into a cross-library view
   (`DASHBOARD.md`, `dashboard.json`).
7. `orchestrator/` wires steps 1–5 together for the `evaluate` one-command
   pipeline.

Entry points: `python cli.py {personas,questions,answers,eval,report,
dashboard,evaluate}`.

The full step-by-step recipe is in [quickstart.md](quickstart.md).

### Treatment-arm comparison

The two-arm `with_docs`/`without_docs` answerer is generalized by
`dokimi/treatments/` into an N-way comparison of
context-augmentation treatments. A `Treatment` produces an `AgentConfig`
(system prompt + injected context) per question; arms cover documentation
injection (`docs`/`mcp:`), agent persona prompts (`profile:`, loaded from
`agent_profiles/`), skills (`skill:`, loaded from `skills/`), and agentic use
where the model decides to call a doc-search or skill tool (`agent:`,
`skill-agent:`, `skill-catalog:`) via the tool-calling loop in `eval/agent_runner.py`.
The spec grammar — what an `--arms` entry means, where `+` splits a combined
spec, and what each arm is *named* — is `treatments/specs.py`.
`eval/arm_runner.py` generates and judges answers for every arm and reports
per-arm deltas vs a baseline. Entry point: `python cli.py arms run`. Details in
[evaluating-treatments.md](evaluating-treatments.md).

## Content-novelty track

An arm comparison produces one number for a whole artifact: it helps, or it does
not. That is enough to ship or cut a skill and not enough to *edit* one, because
a skill is not homogeneous — part of it teaches the model something, part of it
is the model's own prior knowledge written down, and part of it can make the
model worse. `dokimi/novelty/` splits that single delta per sentence.

The mechanism is a paired probe. The artifact is cut into spans, checkable
knowledge atoms are extracted from those spans, and every atom is asked of the
same model twice — once with no artifact in context, once with it. A *different*
model grades both answers, so the four outcomes (`added`, `redundant`,
`degraded`, `still_missing`) are exhaustive and the comparison survives a judge
whose absolute calibration is poor.

1. `dokimi/novelty/spans.py` segments the artifact;
   `dokimi/novelty/atoms.py` extracts atoms and verifies each one
   against the span cited for it.
2. `dokimi/novelty/needs.py` builds a need graph from the artifact's
   `name` and `description` *only* — never from its body — so relevance has a
   scope the artifact cannot define for itself.
3. `dokimi/novelty/probes.py` builds the paired probes;
   `dokimi/novelty/measure.py` runs them over the configured arms
   (`no_artifact`, `full`, and the optional `placebo` and `shuffled` controls)
   and raises rather than let a model grade itself.
4. `dokimi/novelty/classify.py` judges content categories;
   `dokimi/novelty/attribute.py` maps outcomes back to spans, topics,
   and terms.
5. `dokimi/novelty/shorten.py` scores shortening candidates on five
   separate axes (tokens, knowledge, value, protection, routing), with a
   cache-backed retention oracle in `dokimi/novelty/retention.py`.
6. `dokimi/novelty/report.py` writes the scorecard block and an
   annotated HTML view of the artifact; `dokimi/gate/novelty_gate.py`
   turns the numbers into soft gates.

Entry points: `python cli.py novelty {analyze,atoms,probe,shorten,view}`, and
`python cli.py subjects run` to carry the result on a subject scorecard over
time.

Novelty is a property of the (artifact, model) pair, not of the file — the same
skill is worth very different amounts to a frontier and to a small model, so the
model under measurement is always recorded with the result. `--model` defaults to
`gpt-6.1-sol` (the default answer model); pass it explicitly, or the run measures that model instead of the
one you meant.
[skill-novelty.md](skill-novelty.md) is the authoring guide;
[skill-novelty-evidence.md](skill-novelty-evidence.md) is the study the default
thresholds come from, with the caveat attached to each number.

## Executable task track

Tasks are [Terminal-Bench / Harbor](https://harborframework.com)-format
directories — Docker environment, `instruction.md`, oracle solution, and pytest
verifier — or BenchFlow `task.md` directories (SkillsBench), supplied from an
external content checkout named with `--tasks-root` (format spec:
[contributing-terminal-bench-task.md](contributing-terminal-bench-task.md);
layouts and which harnesses run each:
[coding-harnesses.md](coding-harnesses.md#task-layouts)). CI
builds a container from a pinned external catalog and runs its oracle and the
isolation probe, to make sure the harness, the task format, the verifier path
and the agent's sandbox all still work.

`dokimi/harnesses/` turns those task directories into measured
`task_runs.v1` artifacts. Built-in harness aliases include `codex`,
`claude-code`, and `terminal-bench:<agent>`; each runs a Harbor-compatible agent,
records operation telemetry (`harness`, `loop`, `tool`, `subagent`, ...), and
summarizes pass-rate deltas against a baseline harness. Entry point:
`python cli.py tasks run`. These modules own the solver's launch:

- `harnesses/container_agent.py` runs the agent in the task container, on an
  internal network.
- `harnesses/egress_proxy.py` is the allowlisting proxy that container's only
  way out goes through.
- `harnesses/agent_sandbox.py` is the host launch for the `-host` variants,
  and holds the `docker-probe` isolation probe that measures both launches.
- `harnesses/skill_treatment.py` installs the `docker-claude-skill:` arm's
  skill as a plugin (the `-skill-discover:` arm's too, without the notice) and
  reads skill reach from the agent's stream;
  `harnesses/solver_stream.py` runs the solver and records that stream.
- `harnesses/benchflow.py` hands a cell to BenchFlow's `bench eval run` (the
  `benchflow*:<agent>` harnesses, the only ones that run `task.md` tasks) and
  folds the rollout's `result.json` into the row under the cell rule in
  [coding-harnesses.md](coding-harnesses.md#benchflow-cells).

See [contributing-terminal-bench-task.md](contributing-terminal-bench-task.md)
to add a task and [coding-harnesses.md](coding-harnesses.md) to run harness
comparisons.

Skills and tasks don't have to live in this repo: `dokimi/external/`
reads another checkout's own manifests (`skills.yaml`,
`evaluation/harbor/suites.json` — the uxlfoundation/skills convention) and
reports what loads via `python cli.py catalog scan --root <path>`; loaded
entries then plug into the same `--tasks-root` / `docker-claude-skill:<path>`
flags as local ones. [external-content.md](external-content.md) is the
end-to-end walkthrough of that; the "External Sources" section of
[coding-harnesses.md](coding-harnesses.md) covers the harness-specific part.

## Repositories as evaluation subjects

The tracks above measure an artifact the framework is handed. This one turns the
question around: a *repository* declares what evaluating it means, and the
framework runs that. The subject is no longer a skill but the repo's own agent
guidance — `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`,
contributor docs — and the question is whether that guidance reaches an agent
and changes what it produces.

The split is deliberate: tasks, graders, and the manifest live **in the scanned
repository**, not here (`dokimi/repos/` only reads them). A repo
evolves with its own tasks; a copy of them in this tree would be stale by the
next release.

- `dokimi/repos/manifest.py` — loads and validates `repo-eval.yaml`
  (`repo_manifest.v1`) in two passes: the manifest's own shape, then the
  manifest against one snapshot (declared paths exist, guidance globs match,
  every task directory carries a contract-shaped `task.json`).
- `dokimi/repos/source.py` — resolves `<dir>`, `<dir>@<rev>` or
  `<url>@<rev>` to a pinned snapshot, cached per URL as a blob-less clone, so a
  run names an exact commit.
- `dokimi/repos/reach.py` + `dokimi/repos/harness_profile.py`
  — the reach probe. It does not trust a harness's documented loading rules: it
  builds a skeleton workspace with the repo's guidance files at their real
  paths, plants a head and a tail token in each, and asks the agent which tokens
  it can see. A file comes back `loaded`, `truncated` (one token — the file was
  cut), `fetched` (seen only after the agent read it), `not_loaded`, or `error`
  (no usable answer). Harness behaviour is data, not code:
  `dokimi/repos/profiles/claude-code.yaml` is a `harness_profile.v1`
  file and any other harness is another YAML.
- `dokimi/repos/budget.py` — `repo budget`: what the tree puts into an
  agent's context when it starts at the root, in characters, with no LLM call.
  Root CLAUDE.md files and their `@` imports, the root AGENTS.md files and their `@` imports, and
  the skill listing (without skills marked `disable-model-invocation: true`) load at start; nested
  guidance and what nested CLAUDE.md and AGENTS.md files import load lazily;
  skill bodies on demand; with a CLAUDE.md, `.claude/CLAUDE.md` or `CLAUDE.local.md` at the root, an
  AGENTS.md no CLAUDE.md imports is reported off. A skill with no description is listed by its first
  non-empty body line, as Claude Code does. It warns on skills with no description or body text,
  skills with near-duplicate descriptions (identical descriptions, or Jaccard index >= 0.5
  after stop-word removal when both have 3+ words left), on AGENTS.md files turned off, on
  unreadable files (non-UTF-8, dangling or out-of-tree link), and on imports past the harness's
  hop limit, each of which also trips
  `--fail-on-warning`. `start` is not an upper bound: it is too low by what it does not count
  (`.claude/rules/*.md` files; only `@path.md` imports are followed, so `@notes.txt` adds nothing;
  skills are read only from real `.claude/skills/*/SKILL.md` directories at the root, so a skill
  behind a linked directory is not seen, and a nested `<subdir>/.claude/skills/` skill, which Claude
  Code loads on the first read in that subdirectory, is not counted; and imports past the profile's `guidance.import_max_depth`,
  four hops for Claude Code, which warn naming what was skipped). It is too high by whatever the
  harness cuts from the skill listing: the listing is charged in full, and the harness cuts it past
  its listing budget. `--out` and `--json` write the inventory as a `repo_budget.v1` artifact.
- `dokimi/repos/static.py` — the `repo_static.v1` schema: what the
  repository's own `static` entry point writes, so its checks can be merged
  into L0.
- `dokimi/repos/bases.py` — checks that need every task base: each
  `base` must resolve, and the guidance globs are evaluated at each base.
- `dokimi/repos/events.py` — converts a harness trace to the
  `events.jsonl` run trace.
- `dokimi/repos/l0/` — `repo scan` (L0): checks the links, paths,
  identifiers, commands and build labels the guidance names against the same
  tree (`repo_readiness.v1`), with no LLM call.
- `dokimi/repos/environment/` — `repo inspect`: instruction files and their
  scope, check surfaces per project root, launch risks (hooks, MCP, risky
  scripts) and the file inventory (`repo_environment.v1`), read from the tree
  alone.
- `dokimi/repos/workspace.py` — loads `task.json` files, exports the
  eval dir and the image build context (tasks removed), and prepares a cell's
  workspace: snapshot at `base`, setup patches, the guidance arm, and no
  history or unreachable objects left behind.
- `dokimi/repos/container.py` — how every container starts (fixed
  paths, bind mounts only, the caller's uid, env by name, no new privileges)
  and the docker CLI runner; tests swap in a fake runner.
- `dokimi/repos/grading.py` — runs the `grade` and `oracle` entry
  points in their own container, reads `grade.json` (`repo_grade.v1`) without
  following links, and turns it into the framework's verdict and gate.
- `dokimi/repos/runner.py` + `dokimi/repos/report.py` —
  `repo run` (one agent per task × arm × model × rep cell, then `grade`, one
  `result.json` per cell) and `repo report` (gated success with a Wilson
  interval, grader failures, missing cells, cost).
- `dokimi/repos/compare.py` — `repo compare`: treatment arms against a
  baseline arm over one or more batches, one value per task and arm (gate
  share, score, cost, turns, chosen `grade.json` metrics), with an interval
  over tasks (bootstrap for gate and score, t for the rest), a sign-flip
  permutation p and Holm's correction.
- `dokimi/repos/selfcheck.py` — `repo tasks check`: on each task
  `grade` must fail the untouched workspace and pass it after `oracle`, and an
  isolation probe must not see the tasks or the source from the agent's
  container.

Entry points: `python cli.py repo {validate,scan,fetch,reach,budget,tasks check,run,report}`
(`python cli.py repo --help` is the current list). The interface a repository
codes against is [repo-evaluation-contract.md](repo-evaluation-contract.md),
still marked PROPOSED: the per-host egress proxy (§2) is not implemented, so
`repo run` needs `--agent-network` for an agent that must reach its model.

## Context window

`dokimi/context/trace.py` reads a Claude Code `stream-json` trace into the
prompt size of every model call (`input + cache_creation + cache_read`
tokens, one call per message id), the compaction events, tool-result sizes and
failures per tool, and repeated tool calls. `python cli.py context trace`
prints one or more traces as a comparison table; it calls no model. Guide:
[context-window.md](context-window.md).

## Shared building blocks

- `dokimi/process.py` — the one place in the package that spawns a process
  (git, `gh`, doc snippets, harness profiles, the `command:`, Harbor, BenchFlow
  and `docker-*` harnesses with their `docker` and `claude` calls, the explorer and
  the repository-mode docker runner). A timeout,
  an interrupt or the command's exit kills its whole process group / job
  object (with `term_grace`, as for `bench` and `docker run`, a timeout or an
  interrupt sends the group SIGTERM first and kills it after the grace); so does a SIGTERM/SIGHUP to the `dokimi` CLI, which installs
  `install_signal_handlers()` at start-up (importing the module installs
  nothing; a library caller opts in). A timeout is a
  result (`returncode` 124, partial output kept) not an exception, output is
  read as UTF-8, a captured stream keeps at most 1 MiB in memory (its first and
  last half, `truncated` set; `stdout_to`/`stderr_to` write a whole stream to a
  file; git output is kept whole), and every call states its environment policy (`Env.inherit` or
  an `Env.only` allow-list). `FakeRunner` is its test adapter: code that spawns
  takes a `runner` argument.
- `dokimi/llm.py` — the entry point for LLM calls. `LLM` (one model at one provider:
  `complete`/`ask`/`chat`, retry, the response cache, usage) sits on one
  adapter seam: `LiteLLMAdapter` in production, `FakeAdapter` in tests (the
  `fake_llm` fixture). `ask_parsed` is the redraw-until-it-parses loop.
  `llm_call`, `chat_completion` and `utils.get_llm` delegate to `LLM`.
- `dokimi/registry.py` — product registry loaded from
  `products.yaml`; powers `cli.py library` (alias `product`) and
  `cli.py benchmark`.
- `dokimi/intents.py` — intent registry loaded from `intents.yaml`:
  the problem/intent space mapping domains (data science, debugging,
  optimization, …) to the products that serve them. Consistency with
  `products.yaml` is enforced by `python -m dokimi.config_check`.
- `dokimi/config/products.yaml` — per-product config: GitHub repo, Context7 ID,
  retrieval defaults, judge model, persona count.
- `benchmarks/spec.v1.yaml` + `dokimi/runner/spec.schema.json` — declarative
  static-benchmark configuration.

## CI

These GitHub Actions workflows run on PRs (see `.github/workflows/`):
`ci.yml`, `agent-quality.yml` and `docs.yml`, described below, and
`contracts.yml`, which runs only when a PR touches `dokimi/schemas/`:
`python -m dokimi.schema_compat` fails an in-place edit that narrows
an existing schema version, unless a reviewer applies the `schema-break-ok`
label. `conda-forge.yml` runs only when a PR or a push to `main` changes
`pyproject.toml`, one of the two scripts it runs or the workflow file (also weekly, and by
hand, `workflow_dispatch`): it solves the runtime dependencies from conda-forge
alone (`scripts/conda_forge_specs.py` turns them into match specs) on Python
3.11 and 3.14, installs the wheel into that environment with `--no-deps`, and
runs `pip check` and the installed-package smoke test,
`scripts/smoke_installed.py`.

**`ci.yml`** — fast code checks:

1. **static** — the checks that need no lock install, as steps of one job:
   `ruff check .`, zizmor on the workflows, the license headers, the pre-commit
   hooks and bandit (pip-audit is its own workflow, `pip-audit.yml`). Schema checks: `schema_check` validates
   `benchmarks/spec.v1.yaml`; `config_check` checks `products.yaml`/`intents.yaml`/
   `dokimi/config/products.yaml` drift. The `guidance self-scan (L0)` step runs
   `repo scan` as a gate (`--fail-on-fail`).
2. **mypy** — type check of the whole package (modules listed in the
   `pyproject.toml` override are exempt until fixed).
3. **test** — pytest from `requirements.lock.txt`, offline, in parallel
   (pytest-xdist), on Python 3.11–3.14; coverage is measured on the 3.14 leg,
   and on a PR 80% of the changed lines must be covered.
4. **package-smoke** — builds/installs the package with a fresh resolve of its
   dependency ranges (Python 3.11 and 3.13) and exercises the console and
   module entry points.

   **windows-smoke** (a PR check on `windows-latest`) builds the wheel, installs
   it, runs the installed-package smoke test and a few fast tests.

**`agent-quality.yml`** — task and benchmark checks:

5. **external-catalog-smoke** — clones a pinned external content catalog, runs
   `catalog scan` over it, then drives one of its real tasks through the
   `docker-oracle` harness (build + solve + verify, with the task's own
   `network_mode`, bridge for this public task) and the `docker-probe`
   isolation probe. It asserts both pass
   rates, so a launch change that lets an in-container agent reach the grader,
   the skills or the network fails CI. Task definitions are not in this
   repository, so this job proves the harness and the task format rather than any
   particular task.
6. **benchmark** — runs the static agent-quality benchmark with `--strict`, so
   a gate or band failure fails the job, and uploads `current.json` /
   `current.md` artifacts.

**`docs.yml`** — the documentation site:

7. **build** — `mkdocs build --strict` of `mkdocs.yml`: `docs/` plus the CLI
   and API reference generated from the code. A link or anchor between pages
   that does not resolve, a link to a source file that does not exist, or a
   list that nests differently on the site than on GitHub fails it. On a push
   to `main`, **deploy** publishes the build to GitHub Pages.
