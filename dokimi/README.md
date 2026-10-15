# Dokimi

> **This is the whole project.** It evaluates whether a given skill / MCP doc
> source / agent profile actually improves agent answers, and produces the
> evidence (a scorecard) that decides whether the artifact is worth shipping.
> See the [repository overview](../README.md) for the short version.

`dokimi` measures whether a skill, MCP doc source, documentation set or
agent profile actually improves a model's or coding agent's answers and task
outcomes. It runs the same question or task set with and without one controlled
context layer and reports the delta together with its significance.

Context layers it can measure: local Markdown/URL documentation, Context7 and
real MCP servers, skills (`SKILL.md` and bundles), agent profiles (system
prompts), plugins, and whole code repositories with their own tasks and graders
(manifest validation and pinned sources today; the task-run contract is proposed).

Evidence tracks:

| Track | Question it answers | Guide |
|---|---|---|
| Context benchmark | Do answers to a fixed question set improve with the artifact? | [user-flow.md](docs/user-flow.md), [quickstart.md](docs/quickstart.md) |
| Treatment arms | Which of several docs / MCP / skill / profile arms helps, and by how much? | [evaluating-treatments.md](docs/evaluating-treatments.md) |
| Executable tasks | Does the agent produce code that builds and passes tests in Docker? | [coding-harnesses.md](docs/coding-harnesses.md) |
| Exploration (ExploreBench) | Can the agent locate the right files and line ranges? | [external-content.md](docs/external-content.md) |
| Content novelty | What does a skill *add* to a given model, and what can be cut? | [skill-novelty.md](docs/skill-novelty.md) |
| Repository subjects | How well does an agent work inside a given repository? | [repo-evaluation-contract.md](docs/repo-evaluation-contract.md) |
| Context window | What did the agent's context hold: start-up load, growth, compactions, tool-output weight? | [context-window.md](docs/context-window.md) |
| Static docs checks | Are the docs well-structured, fresh, readable, with runnable examples? | [Static benchmark metrics](#static-benchmark-metrics) |

### Three questions you can ask

The arm comparison is the default, and two tracks ask a different question about
the same material. Pick by what you are about to do with the answer:

| Question | Use | Command |
|---|---|---|
| Does this artifact help, and is the delta real? | treatment arms | `cli.py arms run` |
| *Which parts* of it help, and which parts does the model already know? | content novelty | `cli.py novelty analyze` |
| Does a repository's own guidance reach the agent working in it? | repository as subject | `cli.py repo reach` |

The first ships or cuts an artifact; the second edits one; the third measures a
codebase instead of a document.
[docs/README.md](docs/README.md) explains the three in full and
[docs/architecture.md](docs/architecture.md) maps them to modules.

The framework is content-agnostic: question sets, tasks, skills and products are
passed in by path or registered in `products.yaml`. The bundled registry uses
oneTBB, oneDAL and oneMKL as worked examples.

The project is pre-1.0; interfaces and artifact schemas may still change.

## Requirements and setup

- Python 3.11–3.14.
- API keys for the models you run, read from the environment:
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or any other
  [LiteLLM](https://docs.litellm.ai/docs/providers) provider (for example
  `AWS_REGION` + `AWS_BEARER_TOKEN_BEDROCK` for Bedrock). `GITHUB_TOKEN` is
  optional and raises the persona-discovery rate limit.
- Docker, only for the executable-task, harness and repository tracks.
- Optional extras: `ocr` (Docling ingestion) and `mcp` (real MCP servers).

```bash
pip install -e ".[dev]"     # installs the `dokimi` command plus test tooling
# or, without installing the package:
pip install -r requirements.txt -r requirements-test.txt
```

The examples below use `python cli.py …`; after `pip install -e .` the
`dokimi …` command is equivalent. For what a run costs in API calls,
see the [cost estimate](docs/quickstart.md#cost-estimate).

## Quick start

Run the canonical context benchmark:

```bash
python cli.py benchmark preflight --target onetbb
python cli.py benchmark run --target onetbb
```

Outputs go to `results/onetbb_final/` by default. The default comparison is one fixed semantic retrieval config (`top_k=3`) with `without_docs` vs `with_docs` arms.

Optional static documentation gate for Markdown/code example quality:

```bash
python cli.py run \
  --root . \
  --spec benchmarks/spec.v1.yaml \
  --out-json baselines/current.json \
  --out-md reports/current.md
```

Run the test suite:

```bash
python -m pytest -q
```

For the LLM evaluation pipeline (answers, judging, dashboard), see [docs/quickstart.md](docs/quickstart.md). For benchmark invocation and comparison recipes, see [docs/benchmarking-and-comparison.md](docs/benchmarking-and-comparison.md). A high-level architecture map is in [docs/architecture.md](docs/architecture.md), and [docs/README.md](docs/README.md) is the documentation index.

## Main CLI areas

The top-level CLI is `python cli.py` (or `dokimi` once installed):

```text
Context benchmark
  benchmark           Preflight and run the benchmark for registered targets
  evaluate            Run the full pipeline in one command
  personas            Discover and approve target user personas
  questions           Generate, analyze, refine, and panel-review questions
  answers             Generate context-arm and baseline answers
  eval                Score answers, run RAGAS, or use a multi-judge panel
  arms                Compare treatment arms (docs / MCP / skills / profiles)
  subjects            Run a subject descriptor (artifact + suite) into a scorecard

Executable and agentic tracks
  tasks               Run Docker coding tasks through Codex, Claude Code or Harbor harnesses
  explore             Run file/line localization tasks (ExploreBench)
  repo                Validate a repository's repo-eval.yaml, fetch pinned sources, probe guidance reach
  catalog             Check which skills/tasks in an external checkout load cleanly
  context             Read a run's context window from its trace (no LLM call)

Content analysis
  novelty             Measure what an artifact's content adds to a model
  run                 Run the static documentation benchmark
  ingest              Convert PDF/Office/HTML to Markdown via Docling

Results and registries
  report              Render reports (incl. model-compare)
  compare             Compare static benchmark snapshots
  baseline            Save and compare evaluation baselines
  dashboard           Generate dashboard Markdown/JSON
  dataset             Export artifacts to JSONL/Parquet/Hugging Face
  library             List/show registered products and intent domains (alias: product)
```

That is the curated set; `python cli.py --help` lists every group.

Use `--help` on any command for detailed options, for example:

```bash
python cli.py questions --help
python cli.py eval --help
python cli.py dashboard generate --help
```

For end-to-end benchmark and comparison commands, including static snapshots,
LLM context-arm vs baseline runs, fair multi-model comparisons, treatment arms,
dashboards, and baselines, see
[docs/benchmarking-and-comparison.md](docs/benchmarking-and-comparison.md).

## Product and intent registries

Two top-level YAML registries describe what the benchmark can evaluate:

- **`products.yaml`** — *what products exist*: identity, description, repo, and
  doc sources for each product (oneTBB, oneMKL, VTune, dpnp, …). Used by
  `python cli.py library …` (alias: `product`) and `python cli.py benchmark …`.
- **`intents.yaml`** — *what user problems exist*: broad scenario domains
  (data science, machine learning, performance optimization, debugging, …),
  each mapped to the products that serve it, with example user intents. A
  domain may span several products, so a domain is the unit you pick when you
  want to know which product an agent should reach for.

The domain→product mapping lives only in `intents.yaml`;
`python -m dokimi.config_check` (run in CI) fails if it references an
unregistered product, or if `config/products.yaml` drifts from `products.yaml`.
Explore the mapping with:

```bash
python cli.py library intents                       # list domains + products
python cli.py library list --domain data-science    # products for a domain
python cli.py library show vtune                    # includes the product's domains
```

## Static benchmark metrics

The default static benchmark is configured in `benchmarks/spec.v1.yaml` and currently uses four active metrics:

| Metric | Purpose |
| --- | --- |
| `coverage` | Checks Markdown structure: headings, code blocks, and body content. |
| `freshness_lite` | Scores files by modification age. |
| `readability` | Estimates text readability with a Flesch-Kincaid-style score. |
| `example_pass_rate` | Executes supported fenced code examples in isolation. |

Weights are normalized across enabled metrics. The benchmark also supports soft gates, hard gates, critical bands, and regression thresholds. Hard gates and critical bands are enforced only when `--strict` is passed.

## LLM and treatment evaluation workflow

The LLM workflow is designed to answer a practical question: does documentation, a skill, an MCP source, or an agent profile improve model answers and task outcomes?

A typical one-command benchmark flow is:

```bash
python cli.py benchmark preflight --target onetbb
python cli.py benchmark run --target onetbb --multi-run 3
```

The pipeline discovers or loads personas, generates or reuses questions, answers each question in context-arm and baseline conditions, scores both answers with an independent judge, and writes model metadata, token usage, `question_set_hash`, reports, and trust-gate signals. For compatibility, persisted answer/evaluation JSON still uses the legacy `with_docs` and `without_docs` keys for those two conditions.

For fair multi-model comparisons, generate one question set and reuse it:

```bash
python cli.py benchmark run --target onedal --output-dir results/onedal_seed
python cli.py benchmark run --target onedal --questions-from results/onedal_seed --model gpt-5.1
python cli.py benchmark run --target onedal --questions-from results/onedal_seed --model claude-sonnet-4-5-20250929 --provider anthropic
```

Generated artifacts — answer runs, judge scores and benchmark snapshots — go under `results/`, `reports/`, `answers/`, `eval/` or `baselines/`; those paths are git-ignored and no captured run is committed. Curated, version-controlled *inputs* live under `data/` (`data/skills/fastcontext/`, `data/agent_profiles/`, `data/explore_tasks/`) — see [`data/README.md`](data/README.md). Product skills, question sets and personas are evaluation content: they come from a separate content repository and are passed in by path (`skill:<path>`, `--questions <path>`, `--questions-root <dir>`).

See [docs/methodology.md](docs/methodology.md) for the detailed model roles, question types, difficulty levels, task workflow, and trust checks.

## Executable tasks

Terminal-bench-style tasks are the executable side of the benchmark: they validate not just text answers but working code and measurable behavior, so they can test whether docs, skills, or an agent profile actually improve an agent's ability to produce correct and performant code.

The task definitions are evaluation content and live in a separate content repository. Run them from any checkout by naming its task root:

```bash
python cli.py tasks run --tasks-root <checkout>/evaluation/harbor/tasks \
  --tasks <task-name> --harnesses docker-oracle --out-json results/runs.json
```

The directory layout and `task.toml` fields a task must provide are specified in [docs/contributing-terminal-bench-task.md](docs/contributing-terminal-bench-task.md). CI runs a real external catalog through the same harness (see [CI](#ci) below) with network disabled at solve and verify time.

## Repository layout

```text
cli.py                   Single entry point for all CLI commands
benchmarks/              Static benchmark spec and schema
config/                  LLM/runtime configuration for the eval track
products.yaml            Product registry: identity + doc sources per product
intents.yaml             Intent registry: problem/task domains mapped to products
dokimi/                  Main Python package
  agent_profiles/        Agent persona prompt loader
  commands/              CLI subcommand definitions
  dashboard/             Dashboard aggregation/rendering
  context/               Context window of a run, read from its harness trace
  eval/                  Answer generation, judges, panels, RAGAS, arm runner
  explore/               ExploreBench localization track
  external/              External skill/task catalog scanning
  gate/                  Soft/hard gates and regression classification
  harnesses/             Coding-agent harness adapters (Codex, Claude Code, Harbor)
  ingest/                Markdown loading and chunking
  mcp/                   Documentation source clients (incl. MCP protocol)
  metrics/               Static documentation metrics
  novelty/               Content-novelty engine (atoms, probes, shortening)
  orchestrator/          Pipeline orchestration
  personas/              Persona generation/analysis (synthetic users)
  questions/             Question generation, validation, refinement
  repos/                 Repository subjects: manifest, sources, validation, harness profiles, reach probe
  report/                JSON/Markdown report generation
  runner/                Benchmark orchestration
  schemas/               JSON Schemas for every persisted artifact
  skills/                SKILL.md loader
  subjects/              Subject descriptors and scorecards
  treatments/            Treatment-arm abstraction (docs/MCP/skills/profiles)
data/                    Curated, version-controlled benchmark inputs (see data/README.md)
  skills/fastcontext/    The SKILL.md fixture the loader tests and examples use
  agent_profiles/        Agent persona (system) prompt fixtures
  explore_tasks/         Localization tasks with known-good references
docs/                    Documentation (full index: docs/README.md)
  README.md              Concept overview and documentation index
  quickstart.md          End-to-end LLM evaluation pipeline
  external-content.md    Pointing the framework at your own questions, tasks, skills
  evaluating-treatments.md
                         Comparing docs / MCP / skill / profile arms
  coding-harnesses.md    Executable tasks through coding-agent harnesses
  context-window.md      What a run's context window held, from its trace
  skill-novelty.md       What a skill adds to a model
  repo-evaluation-contract.md
                         Evaluating a whole repository as a subject
  architecture.md        Module map and data flow
  adding-doc-source.md   Local/URL/custom doc sources
  contributing-metric.md How to add a static documentation metric
  contributing-terminal-bench-task.md
                         Executable task format (tasks live in the content repo)
  methodology.md         Model roles, question types, judging, trust checks
  skill-novelty.md       What an artifact adds to a model, and how to shorten it
  skill-novelty-evidence.md
                         The study the novelty defaults come from
  repo-evaluation-contract.md
                         What a repository supplies to be evaluated as a subject
tests/                   Pytest suite
```

## CI

Among the checks GitHub Actions runs on pull requests (more in
[docs/architecture.md](docs/architecture.md#ci)):

1. `test` — installs `requirements.lock.txt` and runs pytest offline with coverage; on a PR, 80% of the changed lines must be covered.
2. `benchmark` — runs the static agent-quality benchmark in strict mode (a gate or band failure fails the job) and uploads artifacts.
3. `External catalog smoke test` — clones a pinned public content catalog, scans it, and drives one of its real tasks through the `docker-oracle` harness and the `docker-probe` isolation probe, asserting both pass rates.
4. `Build docs (strict)` — builds the documentation site with `mkdocs build --strict`, so a broken link or a list that renders differently than on GitHub fails it; a push to `main` publishes the site to GitHub Pages.

## Repository hygiene

Do not commit generated logs, coverage databases, tarballs, local caches, or one-off experiment outputs. Keep generated benchmark artifacts either ignored locally or explicitly curated as fixtures. Use pull requests for all changes to `main`.

Status reports, phase plans, and other development-history notes do not belong in the repository — work-in-progress should live in PR descriptions and issues.

## Contributing

- Run `python -m pytest -q` before opening a PR.
- See [docs/contributing-metric.md](docs/contributing-metric.md) and
  [docs/contributing-terminal-bench-task.md](docs/contributing-terminal-bench-task.md)
  for the two main extension flows.
- Keep generated artifacts under `reports/` or `results/` (both ignored).
  Only commit curated fixtures that are part of a reproducible benchmark.

## License

Licensed under the [Apache License, Version 2.0](../LICENSE). See [NOTICE](../NOTICE)
for attribution. Contributions are accepted under the same license — see
[CONTRIBUTING.md](CONTRIBUTING.md). To report a security issue, see
[SECURITY.md](SECURITY.md).
