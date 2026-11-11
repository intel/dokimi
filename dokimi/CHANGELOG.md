<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

Notable changes to dokimi, by release: what to know when choosing or upgrading
a version. The full history is in the Git log and the pull requests.

> **Pre-1.0:** commands, flags, defaults and output fields can change between
> minor releases (0.1 → 0.2). Pin the version you evaluate with, and read the
> release's `Changed` and `Removed` entries before upgrading: a changed default
> or metric can move scores for the same inputs.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html), as set
out in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

Pending changes are fragments in [`changelog.d/`](changelog.d/), collected
here when a version is released.

<!-- scriv-insert-here -->

<a id='changelog-0.2.0'></a>
## [0.2.0] — 2026-11-11

Full list of changes: [v0.1.0...v0.2.0](https://github.com/intel/dokimi/compare/v0.1.0...v0.2.0).

### Breaking changes

- Commands moved into groups. An old name prints the new one (`moved: dokimi docs check`) and exits 2:

  | 0.1.0 | 0.2.0 |
  |---|---|
  | `run` | `docs check` |
  | `compare` | `docs compare` |
  | `repo tasks check` | `eval check` |
  | `personas discover` | `eval personas discover` |
  | `personas approve` | `eval personas validate` |
  | `questions generate` | `eval questions generate` |
  | `questions analyze` | `eval questions analyze` |
  | `questions refine` | `eval questions refine` |
  | `questions panel-review` | `eval questions review` |
  | `tasks list` | `eval tasks list` |
  | `catalog trigger-set` | `eval triggers generate` |
  | `eval score` | `eval score pointwise` |
  | `eval pairwise` | `eval score pairwise` |
  | `eval panel-score` | `eval score panel` |
  | `eval checklist` | `eval score checklist` |
  | `eval grounding` | `eval score grounding` |
  | `eval ragas` | `eval score ragas` |
  | `eval judge-validity` | `eval judge validate` |
  | `eval judge-wording` | `eval judge wording` |
  | `eval cross-judge` | `eval judge agreement` |
  | `novelty analyze` | `skill novelty analyze` |
  | `novelty atoms` | `skill novelty atoms` |
  | `novelty probe` | `skill novelty probe` |
  | `novelty items` | `skill novelty items` |
  | `novelty shorten` | `skill shorten` |
  | `catalog scan` | `catalog check` |
  | `library intents` | `catalog intents` |
  | `library list` | `catalog list --kind product` |
  | `library show` | `catalog show --kind product` |
  | `product list` | `catalog list --kind product` |
  | `product show` | `catalog show --kind product` |
  | `subjects list` | `catalog list --kind subject` |
  | `subjects show` | `catalog show --kind subject` |
  | `context trace` | `trace inspect` |
  | `dataset export` | `data export` |
  | `ingest docling` | `data import docling` |

  The bare groups `personas`, `questions`, `context`, `library`, `product`, `dataset` and `ingest` point to `eval personas`, `eval questions`, `trace`, `catalog`, `catalog`, `data export` and `data import`.
- Exit codes: 0 success; 1 a failed check, gate or run; 2 bad input or usage, with one `Error: <message>` line on stderr; 3 a budget stopped `tools preference` (partial output kept; other commands still exit 1 on a budget stop); 5 a crash in `backends`, `runs` or `trace` with `--json`. Most bad-input refusals move from 1 to 2. The table, and the commands that still exit 1 on some bad input (`repo run`, `eval check`), are in [docs/quickstart.md](docs/quickstart.md).
- These now exit 1 where they exited 2: `eval score pairwise` when every comparison failed, `eval judge agreement --judges` when a judge scored nothing, `tools preference` when every call failed.
- Nothing measured is no longer a pass: `tasks run`, `eval check`, `repo run`, `docs check --strict` and `eval judge wording|agreement --strict` exit 1 when a planned cell errored, went unscored or is missing.
- One same-model rule covers `baseline compare`, the self-judge warning and `repo compare`: the provider is not part of a model's identity and a dated id matches its alias, which `baseline compare --strict` reports as not verified.
- `baseline compare --strict` fails on a model or provider that is missing or "Unknown"; `docs check --strict` fails a NaN band.
- `tools preference` exits 3 when its budget stops it; `novelty routing` exits 1 when it scores no query.
- `catalog check` exits 1 on a root with no `SKILL.md`, and `report generate` exits 1 on an eval with no evaluations, `--format json` included (both exited 0); `benchmark preflight` exits 2 on a `file:` key that is missing, unreadable or empty (it printed "Preflight OK").
- `repo scan` checks the Python versions a guidance file names against the matching workflow matrix, so a scan can fail where it passed.
- Scores and verdicts are not comparable with 0.1.0; re-run baselines before comparing. A failed call, empty reply or judge failure is an error, not a score or a pass; Harbor-backed cells are graded from Harbor's `result.json`; `p` and `repo compare` intervals come from a sign-flip test on per-question deltas; reports use unrounded values over complete pairs; the no-retrieval placeholder is no longer judged unless `--judge-placeholder`; docker `python` examples and ragas 0.3 now produce scores; `repo run` token counts include subagents and helper models; skill discovery, novelty and routing measures changed.
- Output fields changed: `arms.json` drops `p_ttest`, `p_wilcoxon` and `n_wilcoxon` and adds `p`, `n_changed` and `ci_method` (older files still validate); `report model-compare` replaces its t and Wilcoxon columns and `t_stat` with a sign-flip p column and a changed-pairs column; `repo compare` rows gain `delta.method`, with `delta.lo`/`delta.hi` `null` at 5 or fewer tasks; `delta_vs_baseline` is always present (`null` when no question has both arms scored); an `eval judge validate` PASS covers only the context it was measured in (`--context stored` added).
- Judge, persona, question-generator and validator replies cached on Bedrock, Vertex or OpenRouter are not reused (the cache key now includes the streaming flag), so those calls are paid once more.
- On `google`/`google-vertex`, a model named `gemini/...` goes to Google AI Studio instead of Vertex; the `vertex_ai` provider reads `GEMINI_API_KEY` (the answerer read `OPENAI_API_KEY`); on Bedrock an explicit `api_key` wins over `AWS_BEARER_TOKEN_BEDROCK`.
- An `mcp:` source's `;id=` wins over a registered product's `context7_id` in `answers`, `questions`, `arms run` and `benchmark run` (the registry library was queried before).
- OpenRouter output is no longer capped at 2000 tokens.
- Novelty calls request 16384 output tokens when `--max-tokens` is not given (litellm sent 4096 on Bedrock), and a reply cut at the output ceiling is an error instead of a bad parse.
- The novelty default judge skips any model of the answerer's family; with `--judge-provider anthropic` and an Anthropic answerer the run fails until `--judge-model` is given.
- `novelty analyze` cost totals leave out calls replayed from the response cache (counted in a new `replayed_calls`), and `usd` is `null` when a call returned no usage.
- Under `--provider openai`, a model litellm does not know as OpenAI goes to the configured endpoint as `openai/<model>`. A bare Bedrock judge name, or a `--judge2` equal to the first judge, exits 2.
- `--provider openai-codex` is removed from `answers generate`, `eval questions generate`, `eval personas discover`, `evaluate` and `benchmark run|preflight|batch`, which could not run it.
- `repo validate` refuses a task directory name with whitespace or a comma, and a task whose grader is not a `dokimi:` grader without `entry_points.grade`.
- A task with a `network_mode` other than `none`, `allowlist` or `public`, or with `allowed_hosts` but no `allowlist`, errors instead of running offline.
- Host subprocess examples with limits are not run where the limits cannot be applied (any limit on Windows, a memory limit on macOS), and `docs check` exits 2: use the `docker` backend, or `mem_mb: null` on macOS. The `docker` backend refuses a missing or non-positive `mem_mb`.
- `repo run` refuses a batch whose cells came from another execution (other `--tools`, `--delegation`, profile field or harness version) or an earlier dokimi: pass `--force` or a new `--batch`.
- `backends --json` prints a `cli_output.v1` object with rows under `data.rows` instead of a list; `runs` and `trace` `--json` use the same document.
- Python API removed: `dokimi.utils.get_llm`, `dokimi.utils.bedrock_litellm_model`, `dokimi.utils.normalize_model_ref` (use `dokimi.utils.same_model`), `dokimi.llm.ChatOpenAI` and `dokimi.llm.ChatAnthropic` (use `dokimi.llm.Invoker(dokimi.llm.LLM(model, provider, key))`), `dokimi.cli_flags.CANONICAL` and `BANNED`, `dokimi.eval.stats.MIN_PAIRS_FOR_TEST`, `HAS_SCIPY` in `dokimi.report.model_compare` and `dokimi.report.eval_report`, and the interaction re-exports of `dokimi.eval.stats`.
- Python API moved: `sign_flip_p`, `min_sign_flip_p`, `ShiftTest`, `sign_flip_interval`, `within` and `ALPHA` from `dokimi.repos.compare*` to `dokimi.eval.sign_flip`; `diagnoser.retrieval_hit` to `dokimi.records.Record.retrieval_hit`.
- Python API changed: `diagnose(context_answer, delta)` takes the context `Answer` and the delta; `paired_test`, `arm_significance`, `interaction_test` and `interaction_significance` raise `ValueError` for a `confidence` other than 0.95; `mean_ci`, `flatten`, `load_artifact` and `extract_json_object` raise `ValueError` on bad input; `run_solver` returns `process.Completed`; judge and panel retry warnings log under `dokimi.llm`.

### Added

- `repo inspect`, `repo budget` and `repo skill-reach`, with reach scenarios and receipts.
- Codex CLI and Copilot CLI harness profiles, and `docker-claude[-host]-skill-discover:<skill>` arms (the skill installed without the treatment notice).
- Task `host` requirements in `task.json` (`min_cpus`, `cpu_vendors`, `cpu_flags`): a host that misses them runs none of the task's cells; `eval check --allow-unsupported` exits 0 for that case.
- Reusable `.github/workflows/repo-eval.yml` workflow (validate, scan and tasks check on a caller's checkout).
- `repo run` arms `guidance:rev:`, `patch:`, `guidance:overlay`, skill arms and `--delegation none`.
- Built-in graders `dokimi:review_replay`, `dokimi:review_seeds`, `dokimi:typecheck` and `dokimi:hidden_tests`.
- `repo compare` judged recall, pass^k, headroom flags and a ledger.
- `eval coverage`, `eval headroom`, `eval redteam`, `eval leak`, `eval import`, `eval native-import`, `eval inspect` and `eval score reference`.
- `eval tasks generate --from graph|replay|prs`.
- `catalog check` spec, bundle, content and token-free command checks, corpus mode and `--ignore`; `catalog trigger-probe`; `skill check`; skill package formats.
- Novelty `provenance` block, second judge (`--judge2`), reversal resistance, and `novelty routing --all-vs-all`, `--repeats` and `--estimate-only`.
- `subjects evidence` and `catalog eval --tier static`.
- `trace consistency` and `trace prefilter` (rank guidance rules as ablation candidates from session logs).
- `baseline compare` opt-in gates `--max-context-drop`, `--max-lift-drop` and `--min-matched`.
- `runs list`, `runs show`, `backends` and `data schema list|show`; a run envelope (`run_id`, `dokimi_version`) on run artifacts, except personas, question reviews and the report's Q&A file.
- `claude-cli` provider and multi-turn sessions in `arms run`.
- `tasks run` runs terminal-bench v1 tasks natively and SWE-bench rows (`--swe-bench`), and `--network PHASE=MODE[:hosts]` sets the network per phase of `docker-*` runs.
- Signed releases with build provenance.

### Fixed

- Most commands refuse a missing, unreadable or wrong-shape input, a device or directory given as a file, or a mistyped source with a message instead of a traceback.
- A NaN or infinite score no longer passes a gate or crashes an aggregate, and `paired_test` returns a result for finite deltas up to about 1e300.
- A guidance file whose path holds a carriage return is found, and on Linux and macOS a path that is not valid UTF-8 keeps its bytes in the `guidance:overlay`, `guidance:none` and `patch:` arms and in `schema_compat`, and `guidance:as-skill` packages such a file (its name shown with U+FFFD).
- `repo scan` reads a root symlink to a directory as that directory.
- `data import docling` no longer writes through a symlink.
- Skill discovery skips `.git`, `node_modules`, `site-packages` and virtualenvs, so a dependency's copy of a skill is not loaded as a catalog skill.
- `mcp:http=` doc sources work with mcp 2.x.
- A `novelty routing --max-usd` run stopped by its budget saves its partial result (`.partial.json`, `status: "partial"`), also under a long `--out` name; it wrote nothing before.

### Security

- Rotate your OpenRouter key if you ran `eval questions generate --validate --provider google-vertex` with `OPENROUTER_API_KEY` set: the validator sent it as the Google key.
- Every `docker-*` step has a budget, task containers are hardened like `repos` containers, and the `docker` example backend drops every capability.
- The `dev` extra requires pytest 9.0.3 or later; the locks pin fixed certifi, setuptools and filelock, and drop pillow, gitpython, ragas, the langchain packages and diskcache.

<a id='changelog-0.1.0'></a>
## [0.1.0] — 2026-10-19

First release on PyPI. Requires Python 3.11 to 3.14.

### Added

- `dokimi --version`; runs and scorecards record `dokimi_version`.
- `tools preference`: how much one change to a tool moves a model's choice between two equal tools; `--max-cost-usd` caps its spend.
- `repo compare`: treatment arms against a baseline arm, paired by task.
- `context trace`: prompt size of every model call in a Claude Code trace.
- `novelty --max-usd` spend cap; `novelty routing --catalog` tests against the real sibling skills.
- Guidance-only repo manifests: `repo scan`, `reach` and `validate` without tasks.
- `repo run --build-timeout` and `repo tasks check --build-timeout` (default 3 h).
- Arms artifacts store the exact text the judge read; `eval cross-judge --judges` re-scores on it.
- Novelty `bundle` records name what was measured (`artifact_name`, `artifact_ref`, `measured_digest`).
- Documentation site at https://intel.github.io/dokimi/.

### Changed

- Default models: answers and generation `openai/gpt-6.1-sol`, judge `anthropic/claude-sonnet-5-5`, on every command. Scores differ from earlier defaults.
- `--judge-provider` alone picks that provider's default judge model; `google`, `google-vertex` and `openrouter` require `--judge-model`; `openai-codex` is no longer offered.
- Question sets are written as `questions.v2` (`question` instead of `text`); v1 sets still load.
- A golden set under `--questions-root` is answered as-is: no question generation, no persona discovery.
- Every judge path refuses a record with no question text before the first judge call (exit 2).
- `eval panel-score` checks numeric claims the way the single judge does. Panel scores differ from earlier runs.
- `harmful_atoms` reports `measured: false` when the conflict check did not cover every atom pair.
- `command:`, Harbor, BenchFlow and explorer timeouts kill the whole process tree.
- Bundled registries and the spec schema ship inside the package.
- RAGAS is an optional extra: `eval ragas` and `answers generate --ragas` need `pip install 'dokimi[ragas]'`, and exit 1 before any model call without it.
- `beautifulsoup4` and `anthropic` are no longer direct dependencies.

### Removed

- Python 3.10.
- The top-level `cli` module is no longer installed; use `dokimi`, `python -m dokimi` or `dokimi.cli`.

### Fixed

- `eval panel-score` judged every answer with an empty question and no context. Panel scores differ from earlier runs.
- Judge and panel replies with non-finite or out-of-range scores are rejected and not cached.
- The trust gate's retrieval hit rate read a field nothing wrote, so it reported 100% for runs with no documents.
- `tasks run` could score a failed cell as passed (relative `--output-dir`, or a stale run dir that could not be cleared).
- Question id `0` was read as missing.
- The agentic arm ran a tool on `{}` when its arguments did not parse; failed calls are now recorded.
- Novelty no longer counts truncated or failed replies, non-atom rows or non-boolean verdicts as results.
