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

### Added

- **`eval judge validate --context stored`** — judges each base with the arms
  artifact's verified `judge_materials`, the same material the arms were scored
  with. Bases without verified material are dropped before `--bases` /
  `--max-bases` sampling; the summary reports `n_without_context` and per-arm
  kept/dropped counts in `by_arm` (with `scored` bases in `--sequential` mode). The run exits 2 with a typed scorer or
  `--mode pairwise`, on a file with no non-empty verified material, if an arm
  loses all its bases, has only `padding` pairs or gets none sampled, if a `--sequential` run would pass
  with none of an arm's bases scored, or if no pair is usable.

- **Repository budget command** — `dokimi repo budget <source>` lists what the tree puts into an agent's context when the agent starts at the repository root, and when each part loads. The command reads only the tree and calls no model, sizing guidance files and skill listings and bodies, and listing `.mcp.json` server names (not sized). Output distinguishes four load times: `start` (root CLAUDE.md files, a root AGENTS.md when no root CLAUDE.md, `.claude/CLAUDE.md` or `CLAUDE.local.md` displaces it, and the `@` imports of those files), `lazy` (nested guidance), `on demand` (skill bodies), and `off` (guidance never loaded). Supports `--json` for structured output, `--harness` to pick the profile whose guidance file names are read, and `--fail-on-warning` to exit 1 on any warning: skills with no description and no body text, near-duplicate skill descriptions, `AGENTS.md` files turned off by a root `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md`, and unreadable files. `@` imports stop at the harness profile's `guidance.import_max_depth` (an optional harness_profile.v1 field; four for `claude-code`, unlimited when absent), and each import left unfollowed is a warning.

- **`dokimi repo inspect`** — a static look at what a repository offers a
  coding agent, with no manifest, LLM call or repository command: instruction
  files per harness, build/test/lint/typecheck surfaces per project root,
  MCP/hook/plugin capabilities, risky `package.json` scripts, automatic hooks
  that run repository code, and generated, test and large files. Writes the new
  `repo_environment.v1` artifact; `--fail-on-error` makes it a CI gate.

- **Task `host` requirements** — a `task.json` can declare
  `"host": {"min_cpus": 4, "cpu_vendors": ["GenuineIntel"], "cpu_flags": ["avx512f"]}`
  (every key optional) for a hidden test that can only show its bug on some hardware, such
  as a thread race. `eval check` and `repo run` probe once, in a container of the
  evaluation's image, what such a container sees: CPUs are its affinity mask capped by its
  cgroup CPU quota (`docker --cpus`), vendor and flags come from `/proc/cpuinfo`. A task the
  host cannot show is `unsupported` with the reason in `tasks check` (which exits 1, as for
  an egress task, unless `--allow-unsupported` is given, for CI on hosts that cannot show
  some bugs; broken tasks, egress tasks and tasks whose host probe failed still exit 1), and in `repo run` its cells are not run: each holds an `unsupported.json`,
  and `repo report` lists them apart, neither failed nor missing; if the probe itself
  failed, `repo run` exits 1. A later run on a host that
  meets the requirements runs them. The probed facts are recorded in `check.json` `host`, in
  the batch's `_eval/host.json` and in every cell's `meta.json` `host` (or its `unsupported.json` `host` when the cell is not run). A `host` value of the
  wrong shape is refused before any container starts.
  `repo validate` reports it too, and refuses a `cpu_vendors` name `/proc/cpuinfo` does not
  use (`GenuineIntel`, `AuthenticAMD`, `HygonGenuine`, `CentaurHauls`, `Shanghai`); vendors
  and flags match in any case. A `/proc/cpuinfo` the probe could not read is a probe failure,
  not a hardware miss. A failed probe writes no `unsupported.json`, so `repo report` counts the
  cells missing. On a host miss `tasks check` still prepares the workspace and probes isolation
  (and runs the oracle when only CPUs are short); it skips the null check.

- **`repo compare` flags gate and score comparisons with no headroom** — each comparison gains `headroom`: `"ceiling"` when both arms are at the top (gate 1.0, the task's `score.max`) on every paired task, `"floor"` when both are at 0 on every one, `"near_ceiling"` when the baseline is at the top on all but at most one of 2+ paired tasks and the treatment is nowhere below the baseline, else `null`; arms whose tops differ, and a grader failure or an agent outage (error result with no turn count or at most one turn, or a non-zero exit with no turn count that is not a timeout) in either arm, get `null`; a batch without a recorded top is never `ceiling`. A pair is at the top only when both arms reach it and tie: a score past `score.max` is an effect, not a top. The text report prints a note under the comparison. A zero delta on such a task set is no headroom, not no effect. p values and Holm's correction are unchanged. `result.json` now records the task's `score_max`; score rows of batches run before this have no known top and are never flagged `ceiling`.

- **`model_resolved` in `repo run` cells** — `meta.json` and `result.json` record the model that answered the cell, read from a `claude-stream-json` trace (the main thread's `message.model`, else the `init` event's `model`): one name, a sorted list when several answered, `null` for a `text` trace. `repo report` and `repo compare` print a warning when a cell was answered by another model than its `--model` (a Bedrock-style provider prefix such as `us.anthropic.` is not a difference), and `repo compare` also when the two arms of a comparison were answered by different models. A harness alias that pins an older model now shows up. `--json` output gains `model_resolved` per report row and per compare batch stamp, and `model_mismatch` in the report.

- **Harbor token and cost metrics** — a Harbor cell records `prompt_tokens`,
  `completion_tokens`, `cache_read_tokens`, `total_tokens` and `cost_usd` from
  the trial's `agent_result` (summed over the steps of a multi-step task), so
  `summary.per_harness.<h>.cost` covers Harbor arms, plus `metrics.n_trials`,
  `metrics.result_json`, and for a trial that raised `metrics.harbor_exception`
  and any `metrics.reward_after_exception`.
  `command:` templates, including ones that wrap `harbor`, are graded as before.

- **`repo run --arm guidance:rev:<rev>` and `--arm patch:<file>`** — two arms beyond guidance on/off. `guidance:rev:<rev>` runs a task on its base with the guidance files (the manifest's `guidance.globs`) of another revision of the source: "does this PR's `AGENTS.md` help?" without generated task twins. The revision is resolved once per batch and the arm is named by its full SHA (`guidance:rev:<sha>`, also in the cell name), so a branch that moves later starts another arm instead of adding cells to the old one; `meta.json` keeps the revision as given in `arm_rev`. `patch:<file>` applies a unified diff from the host with `git apply` on top of `guidance:raw`, for tooling or layout treatments; the arm is named `patch:<file name>@<sha256 prefix>` (no `/`, so `repo compare` `BATCH/ARM` selectors work) and `meta.json` records the file's path and sha256 in `arm_patch`. A patch that does not apply fails that cell's preparation. Both arms write files only: no object of the source's other revisions enters the workspace's git.

- **Skill discovery arm (`docker-claude-skill-discover:<skill>`,
  `docker-claude-host-skill-discover:<skill>`)** — installs the skill exactly as
  `docker-claude-skill:<skill>` does (same plugin, same `Skill` tool, same launch), but the
  prompt does not open with the treatment notice, so the agent finds the skill by its
  description or not at all. Run it beside `docker-claude` and `docker-claude-skill:<skill>`
  to tell "the skill helps once loaded" from "the agent picks it up by itself". The default
  skill arm is unchanged.
- **Skill rows say which arm they are** — `docker-claude*-skill*` rows record
  `metrics.skill_notice` (`true` on the skill arm, `false` on the discovery arm) and
  `metrics.skill_listing` (`description_chars`, the listing's per-skill cap and whether the
  description was `truncated` in Claude Code's skill listing). A discovery cell whose
  description the listing cuts gets a warning in `run.log`.
- **Discovery gap in `tasks run` summaries and reports** — when a discovery arm runs next to
  its with-skill arm (same launch and skill directory, however the path is spelled), `summary.discovery_gaps` (`task_runs.v1`) holds both pass rates, the
  gap (with-skill − discovery, withheld when the arms ran or lost different cells, with
  `attempted_cells_match` and `errored_cells_match` saying which) and both skill-invocation
  rates, and the Markdown report gains a *Skill discovery* table.

- **`repo run --delegation none` for a single-agent baseline.** Under
  `--tools all` Claude Code could start subagents through its `Agent` tool, so
  a `repo run` cell was not a single-agent run. `--delegation none` runs the
  harness profile's new `delegation.none` tool arguments instead (for
  `claude-code`: `Agent`, `Task` and `Workflow` added to
  `--disallowedTools`); a profile without them refuses the flag, and so does a tool mode that
  `delegation.none` does not list (an allow-list, no fallback to the native
  arguments; `claude-code` lists `none`, `read` and `all`). A cell records the
  delegation whose arguments ran: `native` for a tool mode whose arguments are
  the same either way. The default,
  `--delegation native`, keeps today's behaviour.

- **More L0 Bazel and CMake claims** (`repo scan`, ported from oneDAL's own
  checker, #41 step D6): a BUILD snippet fenced as `python` is checked as
  Starlark when it loads a label or calls a Bazel-only rule (`cc_library`,
  `http_archive`); the files of a top-level `exports_files([...])` count as
  target names (`exports_files(glob(...))` makes a missing name `unresolved`); each `load()` symbol must be a public top-level name of
  the in-tree `.bzl` it names (`starlark_load`); a label of an external
  repository is checked against that repository's BUILD file when the tree
  holds one (`<repo>.BUILD`, `BUILD.<repo>`, `<repo>.tpl.BUILD`, named by a
  `.bzl`, `MODULE.bazel` or `WORKSPACE`, and not a `bazel_dep`),
  and `@<module>//` alone is `ok`;
  `cmake -S <dir>` must name a directory with a `CMakeLists.txt`
  (`cmake_src`; `fail` only after a literal `cd`).

- **Built-in review graders, `task.json` `grader: "dokimi:review_seeds"` and `"dokimi:review_replay"`** — a review task is now data only: `manifest.json` (seeds and decoys) or `gold.json` (human review threads) next to `task.json`, graded by framework code instead of the repository's `grade` and `oracle` entry points. `eval check` and `repo run` use them with no other change. They run in the grading container on the image's `python3` (standard library only, 3.8 or newer), from a read-only mount at `/eval/graders` the agent's container never gets; an image without `python3` is a grader failure that says so. The matching rules are oneDAL's `review` and `review_replay` graders', with the same metrics for the same answers; `grade.json` records `details.grader_version`. An answer with no findings JSON is a failed run with `recall` 0 and `parse_error`, not a grader failure. Any other grader name goes to the repository's entry points as before, and the `dokimi:` prefix is reserved (contract §4.5).

- **Codex CLI and Copilot CLI harness profiles for `repo run` and `repo reach`** — `--harness codex`, `codex-review`, `copilot` and `copilot-review` run oneDAL-style repository tasks under `codex exec`, Codex's native review mode (`codex exec review --commit HEAD`), `copilot -p` and `copilot -p` with a `/review`-prefixed prompt, so guidance arms can be compared per harness, not only under Claude Code. Their JSON traces convert to `events.jsonl` (`trace: codex-json`, `copilot-json`). A profile can now run the harness's own prompt (`native_prompt`) and convert its answer before grading (`answer_format: codex-review` appends a findings block parsed from Codex's review text: the final message decides when it holds a finding or a clean verdict, and the earlier messages are read only when it has neither; with no finding and no clean verdict, such as a refusal, no block is appended); `repo reach` refuses a `native_prompt` profile. `bin_companions` mounts the helper files an executable needs beside it (Codex's `codex-code-mode-host`).

- **`repo run` skill arms** — `--arm guidance:none+skill:<path>` installs one
  skill under `.claude/skills/<name>/`, and `skill-catalog:<dir>` installs
  every skill in a directory. `result.json` records `skills`
  (`skills_used`, `skill_invoked`, `skill_selected_correct`).
  `repo report --json` gives, per row, the mean of the token fields `result.json` already has
  (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`).
  `eval check --arm` rejects a skill
  path that does not exist, and a skill whose `SKILL.md` `name` differs from its
  directory name. The arm is named by the directory (`skill:lint`), so
  `repo compare` can select it. Resuming a batch with other skill content
  under the same arm name is refused.
- **`args_if_present` in harness profiles** — arguments appended only when a
  file exists in the workspace and the tool mode is `all`.

- **repo-eval workflow: `allow-unsupported` input** — passes `--allow-unsupported` to `eval check`, so a task whose task.json `host` requirements the runner misses (a race that needs more CPUs, another vendor) passes its job instead of failing it. Broken tasks and failed host probes still fail. Off by default.

- **Usage and cost for `novelty routing --model`** — a model run now writes a `cost` block into `routing.json` (calls, input and output tokens, cache read and write tokens, and `usd`, which is `null` when any call this run paid for had no known price) and prints the same totals on stdout, so a routing measurement states what it cost. Calls replayed from the response cache are counted in `replayed_calls` and left out of the token and `usd` totals (`llm_calls` still counts them), and `usd` is also `null` when a call returned no usage.

- **Run plan and result artifacts** — two new artifact kinds, `plan.v1` and `result.v1`. A plan lists every cell a run will measure (arm × model × task × rep, each with a stable `cell_id`) and records the subjects, suites, models, harnesses, grader digest, budget (whose `cells` must equal the number of planned cells) and the one `varied_variable` (a list for a 2×2 design). A result gives each cell one status: `pass`, `fail`, `inconclusive`, `invalid`, `infra_error` or `not_run`, and refers to its plan by `plan_digest`. `dokimi.runner.plan.completeness` computes planned minus completed cells. A run is `complete` only when every planned cell is `pass`, `fail` or `inconclusive`. A result for a different plan, or one with unplanned or duplicated cells, is `invalid`, as is a plan whose cell ids do not match their coordinates; an `invalid` result reports no statuses. When a plan crosses harnesses, the harness is part of the arm.

- **`dokimi-design-evals` skill (draft)** — `data/skills/dokimi-design-evals/`
  guides an agent through designing evals for a skill or repository: sources,
  a per-scenario template, outcome/process/cost layers, verifier self-test
  with the existing `repo validate`, `eval check` and `docker-oracle` /
  `docker-probe` commands, manual mutation controls, coverage report and
  `draft → reviewed → holdout` statuses. Bundled skills are now covered by
  the docs command check and a new frontmatter/link test.

- **Multi-turn session measurement for `arms run`** — `--session-turns N` asks each question as its own text followed by the first N-1 entries of its `follow_ups` list, keeps the conversation history across the turns, and records each turn's input, output, cache read and write tokens and cost under `session.turns` of the arm. The arm's `token_usage` and `metrics` are the sums over turns, so cost rollups read them as before. `--turn-reminder TEXT` also runs each arm as `<arm>+reminder`, with the text appended to every user turn in a `<system-reminder>` block, to test whether a per-turn reminder keeps an instruction in force in later turns. Measurement only: `--judge` is refused with `--session-turns` and every `eval` judge command refuses a session artifact, a question with too few `follow_ups` fails the run before any answer is paid for, and agentic arms (`mcp:`, `skill-agent:`, `skill-catalog:`) are refused per arm. Without the flags, `arms run` behaves as before.

- **`repo fetch --json`** prints `{"sha", "git_dir"}` as JSON, so a script reads the
  pinned commit without splitting the tab-separated line.
- **`repo validate --out FILE`** and **`repo budget --out FILE`** write the full
  report as JSON, as `repo scan` and `repo inspect` already do. The summary (or
  `--json`) still prints.
- **`repo validate --fail-on-warning`** exits 1 when the report has any warning,
  as `repo budget --fail-on-warning` does, so a CI job can gate on a guidance
  glob that matches nothing or a task base SHA that is not 40 characters.

The default output and exit codes of all three commands are unchanged.

- **`novelty routing --all-vs-all`** routes every skill that has queries against the whole
  field, in turn, and reports a confusion matrix plus an attractor table: the skills picked
  for other skills' queries, with how many of those queries they took and from whom. Takes
  `--queries-by-skill <json>` and `--out`, and no `--artifact` or `--repeats`. The lexical arm always runs;
  `--model` adds the model arm.
- `--all-vs-all` rates are over the scored queries (`n_scored`, `n_unscored`; `null` own rate
  when none was scored; a query with no content word is unscored in both arms and not sent
  to the model); a lexical tie among other cards is `(tie)`; a failed model call
  exits non-zero; a repeated skill key in the queries file is refused; routing results record
  the field rule under `field`.
- `--all-vs-all` names skills with the same description as one tied group (`tied_groups`), not
  as each other's attractor: a pick between them is in the confusion matrix only.

- **Process metrics in `result.json`** — every `repo run` cell's `result.json` has a
  `process` object (`null` when the agent left no readable trace) counted from `events.jsonl`: `tool_calls`, `commands`,
  `failed_commands` (not denied, `rc` neither 0 nor null), `denied_commands`, and `guidance_read` (the
  workspace paths read with the Read tool, not denied, that the manifest's guidance globs match). A manifest's new `process_metrics:` maps
  a name to a regex, searched in each command's text, or its `output` (`match: output|any`),
  and `process.counters` holds each count. `repo validate` refuses a regex that does not
  compile, and a name that is not a string (a bare YAML `404:` or `on:`).

- **`catalog scan` reviews each skill against the Agent Skills spec** — a loaded skill
  whose `name` is not its directory name or not kebab-case (at most 64 characters), or
  whose `description` is over 1024 characters, is an `error`; a `name` missing from the
  frontmatter, or a `SKILL.md` byte-identical to another skill's (a mirror, named with
  the copy nearest the root, the one the `skill-catalog:` arm offers), is a `warn`. `disable-model-invocation: true` is printed as a
  note, since the model is never offered that skill. Each finding is printed under its
  skill.
- **`catalog scan --strict`** — exits 1 on a skill spec error as well as on a skill that
  does not load. Without it a spec error is printed and the exit code stays 0.
- **`catalog scan --format json`** — one object with a row per skill (`state` is
  `ok`, `warn` or `error`, `findings`, `content_sha256`, `duplicate_of`) and per task
  (`state` is `ok`, `skipped` or `error`), for a CI gate or a script.

- **Mutation pilot over the measurement core** — `.github/workflows/mutation.yml`
  runs mutmut 3.8.0 (hash-pinned in its own `requirements-mutation.lock.txt`, installed by that workflow alone) weekly and on demand over
  `eval/stats.py`, `eval/binary_stats.py`, the score, regression, critical-band and
  trust gates and `caching.py`, and writes the score per module and the surviving
  mutants to the job summary. It never fails a build. New tests kill the survivors
  that were real gaps (inclusive thresholds, the Wilcoxon veto, per-condition metric
  keys, the bootstrap tail index, the cache key's role and sampling parameters), which
  takes the pilot from 75.3% to 92.0% of mutants killed. Configuration:
  `[tool.mutmut]` in `pyproject.toml`; usage: CONTRIBUTING.md.

- **`repo report` and `repo compare` show `result.json` `process` counts** —
  `repo report` prints, under each (task, arm, model) row, the mean of each
  `process` count over the cells that have one (`tool_calls`, `commands`,
  `failed_commands`, `denied_commands`, `guidance_read` as a number of paths,
  and `counter:<name>` for each `process_metrics` counter); the JSON row has
  them under `process`. `repo compare` adds each count a compared cell
  reports as a descriptive measure (`process:<key>`, `counter:<name>`): a
  paired delta with a t interval and a raw p, outside Holm's family. Cells
  whose `process` is absent or `null` are left out, so batches written before
  `process` existed compare as before; a count only one arm reports for a
  (treatment, model) is not compared, and a warning names it.

- **`novelty analyze` states the measured share on stdout** — the headline now prints
  `coverage: N% of the artifact's readable words were measured`, with `(entry point only)`
  for a run without `--include-resources`. The novelty.v1 bundle record stamps the same figure
  as `bundle.measured_share`, so a script can read it without recomputing it from
  `measurable_outside_share`.

- **`novelty routing --repeats N` and the raw reply of a declined pick** — `--repeats N` (with `--model`) asks the model N times per query, with the same card order each time, so the spread between repeats is the model's own sampling noise. The `model` block gains `noise`: each query's agreement across its repeats (`per_query`), the selection rate of each repeat, and `half_range`, half the gap between the highest and lowest of those rates, a crude noise floor.  The rates cover only the queries with no failed call in any repeat (`n_queries_compared`). Each repeat is a fresh sample, so with `DOKIMI_RESPONSE_CACHE` set a repeat is no longer a replay of the first reply. Every abstention or unparseable pick now keeps the model's raw reply under `per_query[].raw_reply` (first 2000 characters). `LLMClient.text` takes a `draw` index for the same reason.

- **repo run: uptake of what a `patch:` arm added** — `meta.json` `arm_patch`
  lists the files the patch `added` (guidance files and `.mcp.json` left out) and the
  `mcp_servers` it added to a `.mcp.json`, and `result.json` gets an `uptake`
  object: the agent's commands naming an added file, reads of one, and calls of
  those servers' tools, leaving out calls the harness denied.
  `repo report` shows the cells that used the arm's
  additions per row; `repo compare` lists uptake per treatment and warns when a
  treatment's cells used none of it, since its deltas then measure the patch's
  text, not the tool (#41 D4).

- **Run consistency** — `dokimi context consistency` reads repeated Claude Code `stream-json` traces of one task and reports three things apart: result stability (`success_rate`, `pass@k`, `pass^k`), path stability (`mix_jsd`, `order_distance`, `action_distance`, spread of calls, turns and tokens, grouped into strategies), and conformance to an optional contract of named actions and rules (`requires`, `forbids`, `before`, `after_last`, `last_ok`). A `Bash` call is one step per command it chains, a shell write carries its file, and a blocked Stop hook is a failed step. A failed call fails every command it chains, and a check piped without `pipefail` does not pass `last_ok`. It calls no model. See `docs/run-consistency.md`.
- **pass^k and path stability in `repo compare`** — `--pass-k K` adds `pass^K` per task (the chance K of its cells all pass the gate), and `--path-stability` adds `path_distance` (how much the tool paths of a task's cells differ, read from `events.jsonl`). Both are descriptive and outside Holm's correction.

- **`tools preference` names the groups a factor is skipped for** — a group whose twin has
  no `vendor_name`, `detail` or `exclusion` silently dropped that twin's items for the
  factor, so the effect rested on fewer groups, or on one twin of a group. The command now
  prints one line per affected factor, with the group ids (and the twin, when only one
  lacks the field), before any call is made (visible in `--dry-run`). Item counts and
  artifacts are unchanged.

- **`eval inspect <path> [--json] [--out FILE]`** — detects the eval-task
  format of a file, a task directory or a whole tree and reports its
  structure: kind (executable, answer-only, trigger, localization), item
  count, present and missing parts, lint problems and which dokimi backends
  consume it today. Recognises Harbor, BenchFlow `task.md`, terminal-bench v1,
  SWE-bench instances, Inspect AI samples (JSONL and CSV) and `@task` files
  (scanned as text, never imported), OpenAI evals JSONL and registry specs,
  skill-creator `evals.json` and trigger sets, skill-reach query sets,
  promptfoo configs and test files, dokimi question sets and ExploreBench
  tasks; anything else is reported as `unknown` with its files. A dataset
  file a config names (an Inspect `FieldSpec` remap, an OpenAI evals
  model-graded spec, promptfoo `tests: file://`) is read as that config says,
  and a git-LFS pointer stub is reported as one. `--json` and `--out` write
  the `task_inspect.v1` artifact. Detection only: no new format runs yet.

- **repo budget: `repo_budget.v1` schema** — `repo budget --json` and `--out` now
  carry a `schema_version` and validate against `repo_budget.v1`, as `repo scan`,
  `repo inspect` and `repo reach` do, so a CI gate on the JSON can detect a
  changed shape. `--out` writes through the artifact writer, as `repo scan
  --out` does: it creates missing parent directories and validates the payload
  against the schema before writing.

- **`tasks run --swe-bench <rows.jsonl>`** runs SWE-bench rows (Lite,
  Verified) with the `docker-*` harnesses: each row runs in its prebuilt
  `swebench/sweb.eval.*` image, `docker-oracle` applies the gold patch, and the
  verifier runs SWE-bench's eval script and grades the log with the repo's
  SWE-bench parser (reward 1 only when every `FAIL_TO_PASS` and `PASS_TO_PASS`
  test passes). `eval inspect` now names `docker-*` as the consumer of a
  SWE-bench file whose repos and versions all have a test spec.
  The solve runs offline and the verifier on a PyPI allowlist by default;
  `--all` skips the 17 rows in `swebench.KNOWN_BAD` known to fail with the
  gold patch in the prebuilt image (live-network tests, Harbor's known
  issues). Every row of a file is checked before anything is staged.

- **terminal-bench v1 tasks run natively** — a `task.yaml` task
  (laude-institute/terminal-bench `original-tasks/`) loads as a `TaskSpec` and
  runs under `docker-oracle` / `docker-claude` as terminal-bench runs it:
  `solution.sh` at `/oracle`, `run-tests.sh` and `tests/` at `/tests`, the
  image's user, `TEST_DIR` and the compose environment, and the pytest summary
  as the verdict. A task one `docker run` cannot reproduce (extra services,
  volumes, tmpfs, ports, a service command, build args or target, a host
  environment value; `solution.yaml`; `run_tests_in_same_shell`) is refused
  with the reason. Harbor-backed harnesses and `benchflow*` run a Harbor copy
  written lazily into a content-keyed cache; its `test.sh` scores the same
  pytest summary. `eval inspect` lists `docker-*`, `harbor` and `benchflow` as
  consumers (only `docker-*` for a `solution.yaml` task, which has no Harbor export). A v1 task gets 8 GB of memory
  (its compose `deploy.resources.limits.memory` when set; the default is
  `DOKIMI_TBENCH_V1_MEMORY_MB`, else 8192), where other formats default to 2 GB.

- **`eval import <path>`**: imports Inspect AI samples and `@task` files
  (read with `ast`, never imported), OpenAI evals JSONL (graded per its
  registry YAML), skill-creator `evals.json` and promptfoo configs as
  questions.v2 sets. Each question has a `grading` block of format-neutral
  checks (`match`, `exact`, `fuzzy`, `regex`, `choice`, `json`, `judge`,
  `unsupported`) and `prompt_style: verbatim`. The import report lists what
  did not carry over. A row whose `ideal` or `target` is blank or missing is
  `unsupported`, not graded; a promptfoo `threshold` that is not a finite number
  (a boolean included) stops the import with an error.
- **`eval score reference`**: scores `answers generate` or `arms run` output against
  those checks. Deterministic checks need no model. `judge` checks go to the
  checklist judge in both polarities. It writes eval.v1 or an arms artifact.
  A `regex` check runs through the `regex` package (now a dependency) with a
  1 s limit; one that runs past it is unscored, not failed. The summary's
  `check_unscored` counts the passes that left such a check out.
  `eval score reference` refuses a blank `values` entry or a negative `weight` before
  the run starts.
- **`eval native-import <log>`**: reads an `inspect eval --log-format json`
  log or a `promptfoo eval -o` output as answers.v1 plus eval.v1, so native
  verdicts can be compared with dokimi's on the same completions.
- skill-creator imports set `expected_skill` to the skill the `evals.json`
  belongs to, so the same set also feeds the `skill-catalog:` trigger eval.
- `answers generate --doc-source none` answers the baseline only.
- `eval inspect` lists `eval import` as a consumer of the answer-only formats.

- **Per-phase network policy for `docker-*` runs** — build, solve and verify
  each get `none`, `allowlist` (egress sidecar) or `public`. Defaults: build
  public, solve and verify none. Set from task.toml (`[environment]`,
  `[agent]`, `[verifier]` `network_mode` / `allowed_hosts`, Harbor's
  `allow_internet`), or `dokimi tasks run --network PHASE=MODE[:hosts]`.
  The effective plan and each phase's source land in `metrics.network`; a
  skill's `metadata.network` is recorded as `metrics.network.skill`, not
  granted.

- **`catalog trigger-probe`** — runs each trigger-set query in a fresh, unannounced
  `claude -p` session with the catalog committed as project skills, a scratch `HOME`
  and config, and the query sent verbatim; scores the `Skill` calls with the same
  selection summary as the `skill-catalog:` arm, one column per trial.
- **`dokimi.harnesses.payload_audit`** — `audit_payload` / `new_leaks` report dokimi
  artifacts (arm names, label keys, question ids, grading words, task paths) in a
  captured request, argv or env, with operator content blanked and the stock harness's
  own text subtracted.

- **Read trigger sets back** — `dokimi eval triggers generate --trigger-set PATH[=TARGET]` reads a google/skill-reach QuerySet, an anthropics skill-creator / agentskills.io `[{query, should_trigger}]` set (TARGET names its skill), or a questions set with `expected_skill`, relints it against `--catalog`, and writes any of the three formats, so an imported set runs on the `skill-catalog:` arm. `arms run` given a skill-reach or skill-creator file names the conversion command; `eval inspect` lists `eval triggers generate` as the consumer of both formats.
- **Skill package formats** — `dokimi catalog formats --root DIR [--json]` lists Agent Skills `SKILL.md` packages (with the host read from the path, the agentskills.io name/description limits linted, and Codex `agents/openai.yaml` noted), Cursor `.mdc` rules, Copilot `*.instructions.md`, Gemini CLI `gemini-extension.json` and Claude Code `.claude-plugin/plugin.json` bundles, with how each is selected (description, globs, always, install).
- **Skill selection precision** — `skill_selection_summary` and the arms report's *Skill selection* table carry `precision`: hits over the questions where the arm loaded any skill, so misroutes and false triggers count against it.

- **`execution.mem_mb: null`**: trusted host examples can omit the memory cap and keep the
  CPU cap and timeout. On macOS this is required for `backend: subprocess`, where the default
  `mem_mb: 512` cannot be applied.

- **`--provider claude-cli` / `--judge-provider claude-cli`**: answers, judging,
  question generation, personas and novelty can run through a logged-in Claude
  Code CLI (`claude -p`) instead of an API key. Each call runs with no tools, no
  user or project settings and no MCP servers, in one fixed empty directory, with
  the prompt on stdin. A requested `temperature` or `seed` is refused, since
  the CLI cannot honour them. `cost_usd` is `None`, because the CLI's figure is
  list price. Rate limits, overload and timeouts are retried; an unknown model or
  a missing login is not. See `docs/subscription-backends.md`.
- **`backends`**: lists the CLI backends with install, version and login status;
  calls no model. `benchmark preflight` checks a CLI backend's login instead
  of an API key, and exits 2 when it is not installed or not signed in, as for a
  missing key. The login is read with no settings files, as a call runs, so a
  Bedrock route set only in `~/.claude/settings.json` is not signed in.

- Opt-in baseline context-score and paired-lift regression thresholds, requiring complete matched scoring and known matching model identities. A regression and an identity mismatch exit 1; partial or insufficient evidence exits 2. `--min-matched` without a threshold exits 2. Existing `--strict` remains an identity check.

- **`repo scan` checks the Python versions a guidance line gives for a workflow against its
  matrices** — a table row such as `` | conda tests, py3.12-3.14t | `conda-package.yml` | `` went
  stale when the workflow's matrix moved on, and every path and name on it still resolved. On a
  line that names a workflow under `.github/workflows/`, a dashed range (`py3.12-3.14t`) or a list
  of two or more versions (`Python 3.12, 3.13 and 3.14`) is now a `python_versions` claim, `fail`
  when the workflow, and the local workflows it calls, run other versions: the values of matrix
  keys that name Python, less what `exclude` takes out of every combination, and the
  `python-version` a setup step pins, or that a caller passes to a called workflow's input. A
  `3.14t` value or a `cp314t` tag is a free-threaded build, and a pre-release (`3.14.0-rc.1`) runs
  its minor version. A computed matrix, `include`, `exclude` or step version, a value such as
  `3.x` or `>=3.12`, a `python-version-file` with no `python-version`, a call to another
  repository's workflow, or no Python version make the claim `unresolved`, and so does a range
  that holds for the matrices and not with a setup step's pin, or the other way round (a lint job
  on another Python).

- **`dokimi repo tasks replay` builds `dokimi:review_replay` tasks from past
  reviews** — given a threads file `gh api graphql` wrote (the query is in
  contract §4.6), it writes one task per merged pull request: `change.patch`
  (the change at the revision most threads were written on, from its merge
  base with the base branch), `task.json`, and `gold.json` from the threads a
  reviewer opened on a line of the new file. `addressed` is true when the PR's
  own later commits changed the file within `--window` lines of the thread, so
  neither a merge of the base branch into the PR nor a bot's commit counts. Files the guidance
  globs match and files under `eval_dir` are left out of the patch and the
  gold, so the task applies under every guidance arm. Every thread, review body
  and pull request left out is printed with the reason; a task directory that
  exists is kept unless `--force`. It makes no GitHub call. It exits 1 when no
  pull request gave a task, and on an error that is not one pull request's own
  (the threads file, the source, the manifest, the prompt file, writing a
  task); a git failure while one pull request is built skips that one.

- **CLI route table** — the top-level `dokimi --help` lists the commands by
  section (The loop, Subjects, Evals, Analysis and data), and every command's
  help line ends in `[free]` or `[paid]`: `paid` when the command can make a
  model call in some flag combination. The table lives in `dokimi/cli_routes.py`;
  `dokimi data schema show cli_routes` prints it as JSON, and
  `schemas/cli_routes.v1.json` pins its shape.
- **`dokimi data schema list|show KIND`** — the new `data` command group; `show`
  prints a schema kind as JSON. `cli_routes` is the only kind for now.
- **Tombstones** — the route table can name an old argv prefix that moved; running
  it prints `moved: dokimi <new command>` and exits 2 without running anything.
  No command has moved yet, so the table is empty.

- **`eval headroom` flags arms items with no headroom** — reads one or more `arms.v1` files of the same question set and flags an item as a `ceiling` when the no-skill arm's mean is at least `--ceiling` (default 0.9, on 0-1), or as a `floor` when the no-skill arm was scored, every arm that a run scored has a mean at most `--floor` (default 0), and at least one arm other than the no-skill arm scored. An item with fewer than `--min-reps` runs (default 3) scoring its no-skill arm is `provisional`. The human table goes to stdout; `--out` writes a `headroom.v1` artifact with every item. The flags change no score. Cached replays are not detected: with `DOKIMI_RESPONSE_CACHE` set, a repeated run counts as a fresh rep (tracked in #370). Inputs that are not repetitions of one configuration (model, judge, run config other than the seed, harness, arms or library differ) exit 2. Pooling also refuses a file scored by `eval score reference` next to a judged one, an arm whose profile prompt digest changed, and a question judged grounded in one file and ungrounded in another.

- **`benchmark run --judge-temperature`** — a new flag: it sets the judge's sampling
  temperature and is recorded as `judge_temperature` in the eval output. Before this change the
  flag did not exist, and `benchmark run` rejected it as an unknown argument.

- **`eval redteam` checks that grading blocks reject wrong answers** — grades a planted wrong, an empty and a hedged answer against each question's `grading` block, and a canary right answer built from its one literal check. A wrong answer the grader accepts, or a canary it rejects, is a violation: the command prints pass rates, exits 1 and, with `--out FILE`, writes a `redteam.v1` artifact (it records the judge model and provider). A run in which a graded question was not measured (judge checks without `--judge`, a block of only negated checks) is `inconclusive` and also exits 1. Judge checks are unscored unless `--judge` is given, so the default run makes no model call.

- **Errors are counted apart.** An answer whose call failed is tallied as `errors` in `eval score reference` summaries and per arm in arms summaries. The arms summary shows an `Errors` column, and the console summary shows the count per arm.

- **Replay hits you can check** — a `dokimi:review_replay` grade now records `details.matches`: per credited thread, the thread (id, anchor, gold comment) and the finding that hit it (its index, location, severity, message) with their distance in lines, so a hit can be read from `grade.json` alone. The metrics are unchanged. `dokimi repo report --hits` lists those pairs by task, arm, model and rep, as the cell's `result.json` names them even when a match carries a key of the same name (`--json` for records), and counts the cells graded before `matches` existed.
- **Parse errors in `repo report`** — a row notes how many of its cells had an agent answer whose findings JSON did not parse (`grade.json` `parse_error`): scored 0 on a score-only row, counted as failed on a gate row, and not counted for a cell already counted as a grader failure. `--json` gives `parse_errors` per row, so a lower recall from answers that did not parse can be told from one from worse reviews.

- **`repo run` records each cell's start context, and `repo compare` compares it** — `result.json`
  `agent.first_prompt_tokens` is what the first main-thread model call was sent (input plus cache
  creation plus cache read tokens, one call per message id, as `context trace` reads it): start-up and
  the task. It is `null` for a trace without that call and for the `text` format. `repo compare` adds a
  descriptive `first_prompt_tokens` row; a batch written before the field has no such row.
- **`repo compare --task ID`** — repeatable: compares only those task ids. An id no finished cell of the
  batches has exits 1 and names it; the tasks left out are not listed as unpaired, nor their unfinished
  cells as missing (a cell's task is the one its `meta.json` records), and the `--json`
  summary (`task_filter`) and the text header (`tasks: N of M (--task)`, M the tasks with a finished
  cell) record the selection. Without `--arm`, an arm with no finished cell among the selection is
  not compared, and both name it (`task_filter.dropped_arms`, `not compared, no finished cell among
  the --task selection: <arms>`).
- **Reading notes under a `repo compare` comparison** — a gate or score row of 2+ tasks whose interval
  includes 0 says it is inconclusive and gives `mde`, an approximate, uncorrected t-based estimate of
  the effect a 5% one-sample t test detects with 80% power at that spread, a planning figure and not
  the power of the sign-flip test or of the Holm-corrected decision (`mde` is in every `--json` row). With 5 or fewer changed tasks the sign-flip p cannot go
  below 0.05 (its floor is `2 / 2^k` for k changed tasks), so `mde` is `null` and the note says how
  low p can go instead. The same holds where Holm's correction keeps a gate or score row's
  `p_holm_floor` (its `p_holm` with its p at that floor, the other rows of the family as observed) at
  0.05 or above, as with two such rows of six changed tasks each (`p_holm` 0.0625 at best); the note
  then says `with Holm over N rows, p_holm cannot go below ...`. A row whose interval and p disagree says so and why
  (`interval_p_disagree`); for gate and score it compares the interval with Holm's `p_holm`, quotes
  it and says it is the one to read, and a descriptive row uses its raw p; descriptive rows with
  p below 0.05 are named as uncorrected and outside Holm's family. No delta, interval or p changes.

- **`catalog trigger-probe` known-positive guard** — before the labelled queries, one
  explicit run (`Use the <skill> skill.`, for `--target` or the alphabetically first skill name of the catalog)
  must load that skill. If it does not, the probe writes `"status": "invalid: skill not
  loaded"` (Claude Code listed it) or `"invalid: skill not visible"` (it did not), with
  `known_positive` holding the guard run, the probe's `max_turns_hit`/`not_listed`/`skill_selection_summary` keys
  at 0, `[]` and `{}`, no answers, no stale `<out>.partial`, prints the same status on stdout, and
  exits 1. A skill call whose result is an error, or another plugin's `plugin:skill`, does
  not count as a load. A pass shows only that the skill can be loaded; listing is diagnosed by `invalid: skill not listed`. A guard run
  that ended in an error writes `"status": "error: known-positive run failed"` instead. An
  empty `--catalog` is refused (exit 2) before the guard runs, unless `--no-known-positive` is passed. Pass
  `--no-known-positive` to skip the check; the output then says `"status": "unchecked"` (or `invalid: skill not listed` when a
  catalog skill is never listed).

- **`dokimi runs list` lists the artifacts under the runs root** — the root is
  `results/` in the working directory, or `DOKIMI_RUNS_DIR`. One row per artifact:
  its `schema_version`, subject, model, recorded time and path, newest first (and,
  since the run envelope, one run's artifacts together);
  `--kind` (repeatable) and `--subject` (an exact match) filter the rows, and
  `--json` prints them with the fields `schema_version`, `kind`, `run_id` (`null`
  in a file saved before the run envelope), `subject`, `model`, `generated_at`, `path`
  and `size_bytes`. A file is an artifact when its first 256 bytes open with
  the `schema_version` of a registered kind, as `save_artifact` writes it; every other file is counted by why it
  was skipped, and a file that cannot be read is listed with its reason. A
  missing `results/` prints "no runs" and exits 0; a `DOKIMI_RUNS_DIR` that does
  not exist or is not a directory exits 2. The command only reads.
- **`dokimi runs show PATH` shows one artifact** — `PATH` as `runs list` prints
  it (relative to the root), or an absolute or working-directory path inside the
  root once symlinks are resolved (neither command reads a file outside the
  root). It prints the artifact's kind, its fields, the length of the lists it
  holds (answers, evaluations, cells, ...) and the files next to it, and checks
  it against its schema: an artifact that fails the check is shown with
  "schema: invalid, N errors" and every error, and the command still exits 0.
  A path outside the root, a missing file, a directory, or a file that is not a
  readable artifact of a registered kind exits 2. `--json` prints the same
  fields as a `runs list` row plus `schema`, `schema_valid`, `schema_errors`,
  `contents` and the neighbours.

- **`catalog scan` checks each skill's bundle and description, and detects drifted mirrors** —
  a relative Markdown link in `SKILL.md` to a file that does not exist is a `warn`
  (`link-missing`), and says so when the file exists from the catalog root instead (a wrong
  base). A link or `../` code span that reaches a file outside the skill directory is a `warn`
  (`link-escapes-skill`, `path-escapes-skill`): it breaks when the skill is installed alone. A
  target is classed by where it resolves too, so a symlink cannot reveal whether a file
  outside the checkout exists.
  A description with no "when" clause is a `warn` (`description-no-when`), as is one
  whose words overlap another skill's by at least half (`description-near-duplicate`). Two
  skills in different paths with the same directory name and description but different
  `SKILL.md` text are a `warn` on both (`mirror-drift`), naming the other copies; same-named
  skills with different descriptions are separate skills. Links inside code fences, URLs,
  anchors, and code spans that name no file are not checked. Skills with
  `disable-model-invocation: true` are exempt from the description checks. Warnings never
  change the exit code; a spec `error` fails only with `--strict`, while a load error always fails.

- **`repo budget` counts `.claude/rules`** — a `.claude/rules/**/*.md` file is a `rule` entry.
  A rule in the root `.claude/rules/` whose `paths` frontmatter names no pattern is `start`; one
  whose `paths` does, and every rule of a subdirectory's `.claude/rules/`, is `lazy`, unless a root `CLAUDE.md` or `AGENTS.md` imports it: then it is `start` whatever its `paths`. The
  frontmatter is not counted, since Claude Code removes it before loading the rule, and an `@`
  path in a rule is not followed, unless a CLAUDE.md or AGENTS.md imports the rule: then its `@` imports are followed, transitively and within the hop limit. A root import loads the rule at start; a nested CLAUDE.md import loads it lazily, when that nested file is read. A link to a directory under `.claude/rules` is a
  warning: the rules behind it are not counted. A link under `.claude/rules` that does not resolve in
  the tree is named too, whatever its suffix, since the tree cannot tell a dangling file from a directory, and so is a root `.claude`
  link out of the tree or to a directory with `rules/`, and a git submodule at `.claude` or at or below
  `.claude/rules`, whose files neither tree lists; a
  `CLAUDE.md` under `.claude/rules` is a rule, not guidance. Measured on Claude Code 2.1.304: a root rule
  raised the first prompt by its size, `paths: []` included; a scoped or nested one loaded only
  once the agent read a file it covers; a rule's `@extra.md` was not expanded. `start` was too
  low by unscoped root rules before; `repo_budget.v1` widens `kind` with `rule`. Rules are counted
  for a harness profile with the new optional `guidance.claude_rules: true` (`claude-code`
  only); the Codex and Copilot profiles leave it unset. An `@` import of a file keeps its frontmatter
  (it is counted in full), and a profile that does not read rules strips none from an imported rule file.
- **Receipt**: `repo reach --format receipt` names start-up rules in its not-probed notice. A file a rule links to is
  expected in a subtree only when it is a nested CLAUDE.md; a linked nested AGENTS.md is `unknown` there.

- **`dokimi_config.v1`, the repository config schema and loader (library only, no CLI yet)** — a
  repository's `.github/dokimi/config.yaml` (or `.dokimi/config.yaml`) declares its subjects (skills, and
  the repository itself with `kind: repo`), where each subject's evals are (beside the skill by default,
  or a central glob such as `evaluation/harbor/tasks/onetbb-*`), the plan defaults, `shared` paths for
  the smoke set, and the budget and baseline store. The loader `dokimi.repo_config` finds the file in
  the order explicit path, `.github/dokimi/config.yaml`, `.dokimi/config.yaml` under the repository root,
  validates it against the schema, and reports each subject's `skill_sha` (the tree digest of the skill
  directory without its evals and without any nested skill directory, which is its own subject) and
  `suite_hash`. In a git work tree both hash each file's git blob id, so ignored paths, a nested `.git` and a CRLF
  checkout (`core.autocrlf`) leave them as they are, and a committed line-ending change moves them; outside a
  work tree they hash the bytes on disk. A skill's suite claims whole directories of its skill (its evals, or a directory such as
  `tests/**`), and those directories leave `skill_sha`. A suite may pick individual files under its skill's
  `evals` directory; those files leave `skill_sha` and move `suite_hash`. A suite that picks an individual file
  elsewhere in its skill (`SKILL.md`, a script, a reference) is refused at load, naming the subject and the file. A link in a suite that resolves into its skill is refused too. An explicit subject whose id is taken by a default skill at another directory is refused instead of silently replacing it. `baseline.store: false`
  reads as `off`, a `branch:` store name with whitespace, a leading `-` or a final newline is refused, and a budget number that YAML reads as text (`1e3`) is refused with the plain
  decimal or the `1.0e+3` spelling, and a `.nan` or `.inf` budget is refused. YAML anchors and aliases are refused. A suite directory is
  hashed file by file. In v1 a link is refused in a suite and on the way to one (a skill may hold only its own
  directory as a link, when that links inside the repository; a repository's manifest may be reached through links),
  so its retargeting is never attributed to the suite. Nothing reads the file yet: the commands that use it come later,
  and existing `.agent-eval/` lookups are unchanged.

- **`guidance:overlay` arm for per-agent AGENTS.md overlays** — `dokimi repo run --arm guidance:overlay` gives a cell the base `AGENTS.md` and the `AGENTS.<agent>.md` of the harness under test, and removes the overlays of other agents (agentsmd/agents.md#185). A harness profile names its agent with the new `overlay` key (`claude` for Claude Code, `codex`, `copilot`). The arm refuses to run without that key, or when the base has no overlay for the agent. `dokimi repo reach` now probes every `AGENTS.<name>.md`, and reports a foreign overlay that the harness loaded as a `finding:` line and in the `overlays` block of `guidance_reach.v1`. Its `own_loaded` field says whether the harness loads its own overlay at all.

- **SCIP code index reader (internal)** — `dokimi.repos.code_index.load()` reads a SCIP `index.scip` written by another tool and answers definitions, references, enclosing scopes, base classes and per-file occurrences. It is a library API; `repo inspect --index` reads it too. The protobuf wire format is decoded with the standard library only, so no dependency is added. A malformed file raises `CodeIndexError`; a file cut at a document boundary reads with fewer documents. Part of #41.

- **`subject.distribution_digest` in the subject scorecard** — the `sha256:` digest a `.well-known/agent-skills/index.json` entry (agentskills PR #254) would carry for the subject: the bytes of `SKILL.md` when its directory holds nothing else (a directory ref or a `SKILL.md` file ref alike). A skill with resources is served as an archive, whose bytes a checkout cannot predict, so it gets `null` under either ref, and so does a ref that is not a local path. A client can match a published result to the skill it installed by this field. `ref_digest` is unchanged: a content digest of the tree, which no index carries.

- **`repo run` arms `guidance:format=xml` and `guidance:as-skill`** — the guidance files of the base, rendered as XML tags (each Markdown heading becomes a `<section>` element, checked to restore to the original file exactly), or moved into one skill directory (`.claude/skills/<repo>-guidance/SKILL.md`, the repo id made into an Agent Skills name). Both compare the same guidance content in another shape under the same model; `guidance:format=md` is refused as a duplicate of `guidance:raw`, and POML is not offered yet. A `guidance:as-skill` catalog arm refuses an `expected_skill` that names the generated skill, before any cell.

- **Time to first useful result, per repo-eval run** — a `task.json` can declare
  `"useful": {"command": "<regex>"}` for a task graded by its target tests, or
  `"useful": {"edit": "<glob>"}` (or a list of globs) for one graded by an edit to a target
  file. The first non-denied event with `rc` 0 that hits it (a command whose text matches, or an
  edit whose path matches, not a deletion) is recorded in `result.json` `first_useful` with its `issued_s` (the
  event's issue time) and `steps` (events up to and including it). A run with no hit says
  why (`never`, `unobserved`, or `timeout`: a `command` pattern, searched with the `regex` module, ran past
  2 s on one event, so the run has no verdict); a task with no declaration is `not_declared`. A hit in a trace
  without timestamps is `untimed`: it keeps its `steps` and counts as observed, with no seconds.
  `repo report` shows a row's observed count, medians and reasons under the row, and
  `repo compare` pairs the per-task medians as `first_useful_s` and `first_useful_steps`, and
  the share of the cells with a verdict that observed a hit as `first_useful_rate` (`unobserved` and `timeout`
  cells are unmeasured: neither a hit nor a miss); its header counts observed of measured cells per arm and
  how many are unmeasured.
  A malformed `useful` is refused by `repo validate`, `eval check` and `repo run`.
  Batches run before this change have no `first_useful` and no such rows.
- A Codex `file_change` record whose `changes` has a malformed entry (not an object, or no path), or is a
  non-list value other than `null`, is one dropped line, as the other malformed records are. The whole record is dropped: its valid
  entries are not read either, so no edit from it counts.

- **`repo compare --ledger FILE`** — gives each guidance rule of a `repo_ledger.v1` file a verdict: its arm against its baseline on the rule's linked cases (task ids), with the paired statistics of `repo compare`, Holm correction across the rules, and `keep`, `review` or `insufficient-evidence` with the reason. A rule whose `reviewed` date is older than `review_within_days` (default 180) gets a warning; its verdict is computed as usual. The ledger only computes verdicts; cutting a rule is left to a person. The design, the ledger format and the incident-to-case path are in `docs/rule-ledger.md`. `--json` adds a `ledger` key to the comparison.

- **`dokimi:typecheck` built-in grader and `repo scan --typecheck` (Python fences).** The grader type-checks the
  task's listed files with the image's pinned mypy and passes when none has an error (`tool`, `version`, `paths`
  in `task.json`; a mismatched version is a grader failure). `repo scan --typecheck` (with `--typecheck-tool mypy|basedpyright`) checks
  each Python fence of the guidance against `--python-env`'s site-packages: a fence with an error is a `fail`
  stale-API candidate, a fence that does not parse or has no interpreter is `unresolved`. The report gains a
  `typecheck` block, `null` without the flag. See `docs/repo-evaluation-contract.md` §4.5 and §8.2.
- A `dokimi:typecheck` task needs no `reference/` directory: with one, its files are the repair the grade
  reads; without one, the listed files are graded as they are and the self-check runs the null step only,
  reporting a listed path that is missing or a suppression the workspace already has (no run could pass).
- A tool report without its diagnostic fields (for example `{}`) is a tool failure, not a clean run.
- A fence that imports a module the environment lacks, or has without types (no `py.typed`), is `unresolved`, not `fail`,
  unless the module is a standard-library one (`import imp` on 3.13) or a missing submodule of a package the repository interpreter finds (`json.nosuch`);
  the grader reads such an installed module as `Any`, not as an error. A fence whose only errors are
  a statement that needs a function or loop around it (`break`, `continue`, `yield from`, `nonlocal`, `from ..x import y`) is unresolved too.
- The grader refuses a workspace module named as one the image's site-packages provides, as it does a standard-library name,
  except the repository's own package when the image installed it from the workspace directory (`pip install .` there).
- A branch mypy never checks because of `TYPE_CHECKING` (`if not TYPE_CHECKING:`, the `else` of `if TYPE_CHECKING:`,
  also inside `not`, `and` or `or`: `if not TYPE_CHECKING and x:`)
  in a listed file is a suppression. Version and platform guards and unlisted modules are not guarded (§4.5).
- The grader does not read a dot directory (`.venv`, `.git`) that holds no listed file: its `.pyi` files and links
  outside the workspace no longer fail or refuse a clean grade.

- **`novelty routing --include-invalid`** keeps a catalog skill with a spec error in the `--catalog`
  field, and the saved result records the flag under `field`. A name that conflicts with another
  catalog skill still leaves the field.

- **`novelty routing --estimate-only`** prints, as JSON, the calls, input and output tokens and the list-price `expected_usd` that `--model` would make, from the same selection prompt the run sends, and makes no model call. Without `--model` the bill is zero. `--all-vs-all` has no estimate yet; its stderr line still says how many calls the model arm makes.
- **`novelty routing --all-vs-all --repeats N`** asks each query N times, as a single-artifact run does, and prints and writes the `noise` block (agreement per query, the rate of each repeat, `half_range`). The model arm's counts are then calls. `--repeats` still needs `--model`.
- **`novelty routing --all-vs-all --model`** writes the same `cost` block as the single-artifact run, on stdout and in the result file.

- **`context prefilter`** — reads Claude Code session logs (`~/.claude/projects/<project>/*.jsonl`) and reports, per rule of an AGENTS.md or CLAUDE.md, how often the situation the rule describes came up, whether the agent ran the command or touched the path it names, and how often the user pushed back on it. The result is a ranked list of candidates for ablation, not a verdict: a rule that is rare or carries a protection tag (a negation, version gate, ordering or threshold) is listed but not ranked. Deterministic, no model call, no transcript text printed; `--json` gives the full report. A subagent's transcript (`<session>/subagents/*.jsonl`) is read as part of its session, and a resumed log that repeats records (same `uuid`) is one session with them. Goose session stores (`sessions.db`) are read too. See [session-prefilter.md](docs/session-prefilter.md).

- **`subjects evidence SCORECARD`: a subject scorecard as a `skill_evidence.v1` document** — the file a publisher puts next to a skill so a client can read what was measured: one result per (matrix cell, non-baseline arm) with model, provider, harness, effort, and the paired test of the arm against the cell's recorded baseline arm on `avg_aggregate` (`n` pairs, delta, confidence interval, corrected p-value) and a verdict (`improved` or `regressed` only on a delta that is significant after correction, `inconclusive` otherwise). It is keyed by `subject.digest`, the scorecard's `distribution_digest`, which is what a `.well-known/agent-skills` index entry carries for the skill. Free: it reads the saved scorecard and makes no model call. Every value comes from the scorecard; one it does not hold, such as `effort` before runs record it, is `null`. Writes `skill-evidence.json` next to the scorecard unless `--out` names a file; exits 2 when the scorecard has nothing to publish or `--out` is the scorecard itself. The schema is experimental until the agentskills evidence proposal settles.
- **Matrix rollup and subject scorecard cells record `baseline_arm`**, so a reader no longer has to guess the baseline from the arm that has no delta (an arm whose every answer failed has none either).

- **An effective-instructions receipt for a reach probe** — `repo reach --format receipt` prints, and writes to `effective_instructions.json` in `--out`, the receipt of `effective-instructions/v0` (agentsmd/agents.md#213) for each probed scenario: each guidance source's path, sha256, scope, expected state (from `repo budget`'s start-up rules, Claude Code only), observed state (`loaded`, `fetched`, `not_loaded` or `unknown`), `observation_method` (`unknown`) with dokimi's own `dokimi_method` (`marker_self_report`: the model reports the marker tokens it sees; the prompt is not captured), the harness's name and version, the displacements seen in the run, and the unknowns. `repo reach --from <reach.json> --format receipt` builds the same receipt from a saved probe without a harness run, after checking the file's repository, harness and commit against the source; with `--from`, both must be at a commit (a working tree is a usage error, exit 2), and the receipt may not be written over the saved file. `followed` is always `unknown`: the probe does not show what the model did with a loaded source. The receipt makes no LLM call and is validated against `schemas/effective_instructions.v1.json`. The mapping and its limits are in `docs/repo-evaluation-contract.md` §7.1. The default output of `repo reach` is unchanged. With `--from`, every probe flag is refused when given, including at its default value, and an empty `--from` name is a usage error (exit 2); the receipt's unknowns name a truncated record and an error record separately.

- **A reach conformance suite and a nightly agent × version matrix** — `data/reach-conformance/` holds eight fixture repositories (root, nested, `CLAUDE.md` with `AGENTS.md`, a Claude Code `@` import, an `AGENTS.override.md`, a subdirectory shadow, a file over the size cap, a symbolic link), and `expectations.yaml` (`reach_conformance.v1`) gives what each harness's vendor documentation says each file loads in each scenario, or `unknown`. `scripts/reach_conformance.py` checks the expectations against the fixtures, runs `repo reach --format receipt` over them for each harness with a model, credential and CLI present (the rest are recorded as skipped), and merges the per-harness `reach_matrix.v1` JSON into one matrix and one markdown page. `.github/workflows/reach-matrix.yml` runs it nightly and on dispatch, not as a pull-request check, and uploads the matrix as an artifact. The design, the agreement rules and the limits are in `docs/reach-matrix.md`.

- **`eval coverage`: which needs of a subject an eval set grades, without a model call.**
  `python cli.py eval coverage <evals.json | skill dir | tasks root> --graph need_graph.json`
  reads a frozen need graph (new `need_graph.v1` schema; a novelty need graph is valid as
  written) and the `needs` list on each `evals.json` row or in a task's `metadata.needs`. It
  prints the weighted coverage, the coverage of `core` + `rare_critical` and of `rare_critical`
  alone, the uncovered `core` / `rare_critical` nodes, and each eval's relevance
  (`relevant_core`, `relevant_peripheral`, `out_of_scope`, `orphan`, `unlabelled`). `--out`
  writes an `eval_coverage.v1` artifact. Exit 1 under a `--min [METRIC=]X` floor, exit 2 on an
  unreadable input, a graph with duplicate node ids, or a `needs` label that is not a list of
  ids the graph has. A row or task with no `needs` key is `unlabelled`; an explicit
  `"needs": null` is malformed and exits 2. `--out` is refused when it would overwrite the
  graph or any input file. A `--min` floor is judged on the unrounded share, not the rounded
  figure shown in the report; a floor on a class the graph has no node of (n/a) also exits 1.
  Known gap: a terminal-bench v1 task (`task.yaml`) has its `metadata.needs` ignored, so it counts
  as `unlabelled`; `task.toml` and `task.md` tasks are read. Novelty's need graph now keeps
  `confirmed_by` and `provenance`.
  Building the graph, the user interview and drafting labels follow in #331.

- **Unrounded statistics next to the rounded ones** — every paired significance block (`summary.per_arm[arm].significance` in `arms.v1`, and the cells that copy it) gains `exact: {delta_mean, ci_lo, ci_hi, p_adjusted}`: the values before rounding. `delta_mean` (2 decimals), `ci_lo`/`ci_hi` (4) and `p_adjusted` (4) are unchanged, so existing readers see the same numbers; a consumer that publishes the effect or re-derives a verdict reads `exact`.

- **Judged recall for replay tasks** — `dokimi repo hits judge <batch> --judge PROVIDER/MODEL` asks a judge model whether each credited (thread, finding) pair of the batch's `dokimi:review_replay` cells makes the same point (`same point`, `related` or `unrelated`), reading the thread's full comment from the task's gold file and the finding's full text from `answer.txt` rather than the 200-character `matches`. Each fully judged cell gets `hits_judged.json` (`repo_hits_judged.v1`) with the labels, their counts and `recall_judged`, which is at most `recall` since only credited pairs are rescored; the grade is not changed. A judge that runs any answer model of the batch (a CLI alias counting as its family's full id) and a `--labels` entry naming no credited pair, labelling a pair whose thread comment or finding text is no longer the one the sheet holds (compared exactly), or giving one pair two different labels, are refused with exit 2 before any call; `--dry-run` prices the run without a call, and `--max-usd` starts no new call once spent (a cell left gets no file, exit 1), and after a judge reply with no known price starts no further live call, since the cap cannot be enforced on it (that reply still labels its pair, cached pairs still finish, and the run exits 1 only when pairs are left). `dokimi repo hits sheet <batch> --out FILE` samples the pairs for a person to label, `--per-arm` per arm and answer model with a fixed `--seed`, and refuses to replace a sheet that has labels, or an existing file it cannot read or parse as JSON; with that file, `--labels` reports agreement and Cohen's kappa per arm and answer model, and without it the output says `recall_judged` is uncalibrated (`--json`: `uncalibrated_rows`, the rows no labelled pair covers). `repo report --hits --json` also gives `grader_failures` and `cells`, the replay cells whose `matches` was read and those whose answer had no findings JSON (a parse error, counted with recall 0).

- **Replay tasks and `guidance:rev:` arms record what a leak check needs** —
  `repo tasks replay` writes `public_at` into each `task.json`: the earliest
  `createdAt` of a gold thread's first comment, left out (with a note) when
  a gold thread has no time with an offset. A `guidance:rev:<rev>` cell's
  `meta.json` `arm_rev` adds `guidance_latest` (the last commit to touch each
  guidance file at `<rev>`, or a path a guidance link resolves through),
  `guidance_commits` (the commits that touch a guidance path outside
  `eval_dir` or a path a link
  resolves through, a merge when its own result changes one, newest first by committer
  date, at most 200) and `guidance_commits_truncated`, each commit with its
  author and committer date (a UTC date as `Z`), so a reader can see whether
  a guidance commit came after a task's review was public. A `guidance:rev:`
  cell whose source is a shallow clone fails its preparation: the history
  needs the full one (`git fetch --unshallow`).

- **`dokimi repo inspect --index FILE`** — reads a SCIP code index of the tree
  and adds a `structure` section to `repo_environment.v1`: modules with their
  reference fan-in, cross-tree bridges, name ambiguity and whether the
  guidance names them, plus `info` findings for the hotspots. Tree files of
  the indexed kinds that the index lacks are counted, so an index cut short
  does not read as complete.

- **`dokimi repo skill-reach`: which skills a harness actually lists** — places token-seeded `SKILL.md` fixtures from a catalogue (`repos/skill_fixtures.yaml`, or `--catalogue`) in a fresh workspace, HOME and config dir, asks the agent to report the skills it can see, and classifies each as `listed`, `fetched`, `not_listed` or `error`, with a verdict (`one`, `all`, `some`, `none`) per same-name collision group. Covers project and user client dirs, `.agents/skills`, subdirectories, nesting, symlinks and description formats. Harness profiles gain a `skills` block (`project_dirs`, `user_dirs`) and a `skills` tool mode; claude-code and codex declare both. Writes `skill-discovery.json` (`skill_discovery.v1`).

- **Key-free static catalog checks** — `catalog eval ROOT --tier static --out-dir DIR` plans deterministic catalog checks,
  maps actual tasks by their owning skill, preserves checks-only imports and missing
  runtime coverage, and writes schema-validated catalog, plan and result receipts.
  `--changed-from` selects affected skills with conservative handling of unknown changes.
  No model, skill script, Docker task or verifier is executed by this command.

- **`process.unknown_reads` in `result.json`** — the relative shell reads a trace made at a directory the converter could not follow (after `source .venv/bin/activate`, `cd -`, or a `cd` in an earlier Claude Code Bash call), which give no `read` event, for Claude Code, Codex and Copilot alike; `0` when there are none. A `guidance_read` of `[]` with `unknown_reads` above 0 means not known, not none read. `repo report` and `repo compare` show it as `process:unknown_reads`, after `process:guidance_read`. `docs/repo-evaluation-contract.md` §4.1 says so.

- **`repo reach --scenarios shadow` measures what a `CLAUDE.local.md` turns off** — the probe could only show the
  tree as it is, so on a tree with `AGENTS.md` files and no `CLAUDE*` file it could not show that a `CLAUDE*` file
  turns them off. The new `shadow` kind writes the profile's new `guidance.shadow_file` (`CLAUDE.local.md` in the
  built-in `claude-code` profile, and in no other) for one scenario at a time: `shadow=.` at the root, started there
  with tools off, and `shadow=<dir>` in each guidance directory holding a file not named `CLAUDE*`, started at the
  root and told to read a file in `<dir>`. The file carries its own head and tail tokens, is not committed, and is
  removed when its scenario ends, after an error too; a directory where the tree already has a file or a directory
  (an empty one included) at that path, or a file above it, gets no shadow scenario, and a shadow path that would
  replace or go under one of the probe's own files stops the run with an error before any scenario runs. When a
  shadow scenario is planned, the shadow file's path, as given, joins the paths the probe refuses to find above the
  workspace: with a profile's `shadow_file: MEMORY.md`, a `MEMORY.md` there stops a run and a dry run alike, and with
  `shadow_file: .claude/MEMORY.md`, a `.claude/MEMORY.md` there does and a plain `MEMORY.md` does not. Its result is a `shadow` record on the scenario row of `reach.json`
  (`guidance_reach.v1`, widened in place) and one line under the matrix, so the matrix keeps one row per tree file.
  `--format receipt` leaves the shadow scenarios out: the receipt describes the tree as committed. `shadow` is not in the default (`root,cwd,read`, which the `--scenarios` help now names instead of "all"), and a
  profile without `guidance.shadow_file` refuses it as a usage error (exit 2). `--dry-run` lists the shadow
  scenarios with the file each writes.

- **A `bash=<dir>` reach scenario** — `repo reach --scenarios bash` runs, for each guidance directory, a scenario started at the root with the profile's new `bash` tool mode (`claude-code`: `--tools Bash`) that tells the agent to `cat <dir>/REACH_PROBE.txt` (the path shell-quoted, so a space or `;` in a directory name stays one operand) and then list tokens: it shows whether a shell read of a nested file loads that directory's guidance, as `read=<dir>` shows for the Read tool. The default stays `root,cwd,read`, so a probe costs no more unless asked; `--dry-run` lists the bash scenarios and `--only bash=<dir>` picks one. A profile without a `bash` tool mode (`codex`, `copilot`) asked for `--scenarios bash` is a usage error (exit 2) naming the profile. `harness_profile.v1` accepts an optional `bash` key under `tools` (not under `delegation.none`, so `repo run --tools bash --delegation none` is refused); the receipt treats a `bash=<dir>` run as it does `read=<dir>`. The scenario runs a shell on the host, with the harness's credentials and network, so `claude-code`'s `bash` mode adds a PreToolUse hook that blocks every Bash call but the exact `cat` that `repo reach` names in `DOKIMI_REACH_COMMAND`, and blocks every call when the hook itself fails: a `CLAUDE.md` in the probed repository cannot make the probe run anything else.

- **Judged recall in `repo compare`** — `dokimi repo compare <batch> --baseline ARM --metric judge:recall_judged` compares judged recall from `repo hits judge`'s `hits_judged.json` across arms, as the row `metric:judge:recall_judged`, next to `--metric recall` from `grade.json`. A cell counts only when its file is `repo_hits_judged.v1`, names the cell's own cell, task, arm and model, and holds the `recall`, `n_threads` and credited (thread, finding) pairs its `grade.json` has now, with the comment and message cuts each pair records (`repo hits judge` now writes the match's `comment` and `message` with each pair, so a regrade that rewords a thread or finding within the 200 characters the grade keeps makes the file stale; a later change is not seen); stale (regraded since judging), unjudged (`recall_judged` null), a cell with no gold thread, a cell `repo hits judge` never judges (a grader failure, a grade without `matches`, another grader: `not_judged`), misplaced and unreadable files are left out of the task's mean and counted per arm (`--json`: `judged_cells`, with each arm's judges; the text prints one line per arm with a cell left out, and a warning when the cells read name more than one judge). Any other `judge:` name, and a `judge:recall_judged` no selected cell reports, are refused with exit 2, the latter saying to run `repo hits judge` first unless every cell has no gold thread or is `not_judged` (a rerun writes the same null, or no file; the per-arm line leaves its hint off then too); the text notes that compare cannot tell whether the judge was calibrated.

- **`repo compare` flags the replay tasks a `guidance:rev:` arm may have seen** — for each compared
  arm whose cells record `arm_rev`, a replay task (its `task.json` has `public_at` or `source_pr`) is
  flagged by date when a guidance file that applies to it has its `guidance_latest` commit committed
  after the task's `public_at`, and by name when a `guidance_commits` subject names its PR (`#<n>`,
  `PR#<n>`, `PR <n>`, `GH-<n>`, `<owner>/<repo>#<n>` of its own repository, or the PR's URL; `#31030` and `other/repo#3103` do not
  name `#3103`).
  A nested `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` or `GEMINI.md` applies to the tasks that patch
  a path under its directory; any other guidance file, and every file for a task with no
  `patch_files`, applies to every task. A task is unknown, with the reason, when it has no
  `public_at` (by date), the arm is a `patch:` arm, the batch recorded no guidance history, a cell
  file has the wrong shape, none of its cells has a `task.json` or one without it ran an `arm_rev` no
  checked cell has (pooled batches), or `guidance_commits_truncated` cut commits that may be later than its
  `public_at` (by name). The `--json` summary's `leak` lists each checked arm's `checked`,
  `not_checked`, `by_date`, `by_name`, `unknown`, `clean` and `names_truncated`; the text prints the
  counts, the tasks named with their commits and, as `--task` options, the clean tasks with the ones
  the check does not read (a hand-written task stays in the comparison). No delta, interval, p or
  exit code changes.
- **`repo tasks replay` writes `patch_files`** — each `task.json` lists the paths `change.patch`
  changes, a rename as both its paths, for `repo compare` to scope a nested guidance file by.

- **`dokimi eval tasks generate --from graph`** — writes enumeration tasks
  from a SCIP code index of a pinned revision, with no LLM or docker: which
  files (or functions) reference a function (C++ overloads as one), and which classes derive directly
  from a class. About half the tasks name a symbol whose name others share.
  The tasks are graded by the new built-in grader `dokimi:graph_enum`
  (score-only F1 against the index's gold). Contract §4.7.

- **`novelty routing --keep-copies-under <dir>`**: keeps only the copies under `<dir>` (a directory relative to `--catalog`) of a same-name catalog skill whose copies differ, when the copies under it agree; a copy of that name outside `<dir>` that fails to load is left out too. A value that is empty, absolute, leaves `--catalog`, or is not a directory is refused with exit 2. `--all-vs-all --estimate-only`: prices every call the run would make, as the single-artifact `--estimate-only` does, with no client built and no query sent.
- **`novelty routing --all-vs-all` saved field**: records `keep_copies_under`, and lists each skill whose queries were dropped in `field.dropped_queries` (one record per skill: `skill`, `reasons`, `keep_flags`, `queries`).

- **A fidelity golden for docker-claude arms** — `dokimi/harnesses/fidelity.py` records the request the pinned Claude Code (2.1.318) sends for a fixed task, normalized, and `tests/test_fidelity_golden.py` checks each arm's request against it and against the payload audit. A new CI job (`fidelity`) runs it. Refresh the golden with `scripts/refresh_fidelity_golden.py`; see `tests/fixtures/fidelity/README.md`.

- **Run envelope on run artifacts**: every artifact of a registered run kind now has `run_id`
  and `dokimi_version` right after its `schema_version`. The files one process writes share the
  `run_id` (`20261104T153012Z-1a2b3c4d`), so a catalog run's report, plan and result carry one id.
  Set `DOKIMI_RUN_ID` to give several commands one id, for example the steps of a CI job. A
  malformed value (anything but 1-128 letters, digits, `.`, `_` or `-`, starting with a letter or
  a digit) exits 2 before a command runs (`--help` and `--version` still work). `runs list
  --json`, `runs show` and the rows of `dataset export` carry the `run_id`. Input kinds
  (`questions`, `dokimi_config`, `repo_ledger`, `need_graph`, `cli_routes`) get no envelope, so a
  question set saved again keeps its bytes and its `question_set_hash`. `plan_digest` and the
  `eval headroom` duplicate check leave the envelope out. The `--json` print of `repo scan`,
  `repo inspect`, `repo budget` and `eval inspect` now leads with `schema_version` and the
  envelope, as the file `--out` writes does. Files saved before the envelope have neither key and
  stay valid, and the 26 run-kind schemas declare both as optional, `run_id` with the same
  pattern. A reader that compares a whole saved run artifact with a committed copy now sees two
  more keys.

- **Task import reads four more formats** — `dokimi eval import` now judges a custom Inspect `@scorer` on its docstring's first paragraph (a bare one stays unsupported), loads `hf_dataset(...)` rows through the `datasets` package (a builder such as `json` reads `data_files=` and `data_dir=` from the task's directory, and one that names neither is refused; a URL `data_files=` and a hub dataset's own paths are left as they are; Inspect's own options such as `shuffle=` and `auto_id=` are not passed to the loader; a local dataset folder or file is refused; `trust_remote_code=` and `trust=True` are refused), reads a promptfoo config written as code (`promptfooconfig.js`/`.mjs`/`.cjs`/`.ts`, which needs `node` on PATH), and grades a model-graded OpenAI spec with no ideal by its registry prompt when that prompt needs no `{ideal}` or `{criteria}`. A Harbor `evaluation/harbor/suites.json` (only at that path) is detected as `harbor-suites` and its tasks are counted by `dokimi eval inspect`.

- **`catalog check` corpus mode for large catalogs** — `--per-skill` reviews each skill alone, so the
  cross-catalog checks (`duplicate`, `name-conflict`, `mirror-drift`, `description-near-duplicate`) do
  not fire between unrelated skills. A `SKILL.md` nested in another skill's directory is marked with
  `nested_in` in JSON and with a `note` in text; `--skip-nested` leaves it out. With `--per-skill`,
  `--jsonl PATH` streams one row per skill, `--jobs N` reviews in parallel, and `--resume` skips the
  skills already in PATH. Each row carries `skill_sha256`, the digest of the skill's directory, and
  `--resume` keeps a row only while that is unchanged. `--jsonl`, `--jobs` and `--resume` need `--per-skill` (`--resume` needs
  `--jsonl`); other misuse exits 2. From Python, `dokimi.external.review_skill_dir(path)` returns the
  `SkillReview` of one skill directory.

- **Fuzzing** — atheris targets for the parsers of model replies and Markdown
  frontmatter (`fuzz/`), run by the `fuzz` workflow on pull requests that touch
  them and weekly; the seed corpus replays in every test run.

- **`eval leak` checks a candidate skill for held-out answers and locks the dev/val/test split** — it
  reads a skill (a directory or its SKILL.md) and a questions set, flags an item when at least
  `--min-fraction` (default 0.10) of the 8-word runs of its expected answers and checks also occur in
  the skill's text, or when any run of `--verbatim-words` words (default 12) of them occurs in the skill
  whatever the share, and exits 1 when an item is flagged. A skill file that is not UTF-8 text or is a
  symbolic link is not read and is listed as a warning. `--allow TERM` and `--allow-file FILE` list terms
  that may appear in both (API names, paths, identifiers). `--split FILE` checks a dev/val/test assignment
  against the lock written at first use (`--lock`, default the split's name with `.lock.json`). The lock holds
  each item's split and a digest of its grading block, so a changed answer or check counts as a change: a changed
  split exits 1 and names the items that moved, and the lock is never rewritten. `--relock` overwrites the lock
  after a deliberate change, and only when the run passes; a blocked run leaves the old lock and reports the
  digest it would have replaced. The lock is an accidental-edit check, not tamper-proof. `--proposer FILE` (a JSON
  list of question ids) exits 1 when it gives the proposer a test item. Writes a `leak.v1` artifact to
  `--out`. No model is called.

- **Novelty review controls.** These are the checks a reviewer asked of the novelty 2x2.
  - `paired` now reports `added_over_placebo` when the `placebo` arm ran: right with the artifact, wrong without it, and wrong with the placebo.
  - A new `no_artifact_repeat` arm asks the baseline again as a new cache draw. With it, `paired` reports `noise_floor`, `added_net_of_noise` and `degraded_within_noise`.
  - `novelty analyze` and `novelty probe` take `--context-framing reference|neutral`, which controls for placebo abstention caused by the prompt. A probe record names its framing (`context_framing`). `skill novelty items` refuses probe records of different framings; a record without the field counts as `reference`.
  - `added_over_placebo.rate` is `null`, not `0.0`, when no probe was answered in all three arms.
  - `added_net_of_noise` and `degraded_within_noise` are computed over the probes all three arms answered, and are omitted when there are none.

- **Signed releases with build provenance** — the release workflow signs the
  sdist and wheel with Sigstore (keyless, the workflow's identity) and attaches
  `<file>.sigstore.json` bundles plus a SLSA build-provenance attestation
  (`dokimi-X.Y.Z.intoto.jsonl`, also in `gh attestation verify`) to the GitHub
  Release. `docs/releasing.md` shows how to verify a download.

- **`eval tasks generate --from prs` builds authoring tasks from merged pull requests** — review replay measures
  guidance for reviewing; these tasks measure it for writing code. From the same kind of file
  `gh api graphql` writes (with `body` and `createdAt`), one `authoring_pr<number>/` task per merged PR:
  `base` is the merge base of its head with `mergeCommit^1`, the prompt is its title and description
  without fenced blocks that quote the change, links to its commits or diff, and lines that name a
  hidden test (counted in `prompt_dropped`; more than 5 skip the PR), its added or changed test files
  go to `hidden/`, and the rest of its diff, without guidance files and `eval_dir`, is the oracle in
  `reference/`. A PR with no test file, only test changes, more than `--max-lines` (default 400) lines
  outside its tests, or a fast-forward merge is listed with the reason. `public_at` is the PR's
  `createdAt`, so `repo compare` flags these tasks as it does replay tasks. No LLM, no GitHub call;
  exits 1 when no PR gave a task or the input cannot be used (contract §4.8). `repo tasks authoring`, its
  name on `main` before this release, prints `moved: dokimi eval tasks generate --from prs ...` to stderr
  and exits 2 without running anything.
- **Built-in grader `dokimi:hidden_tests` and the manifest's `entry_points.test`** — the framework copies
  a task's hidden files into the workspace with their file mode, runs the repository's test command on them (`{tests}`; it
  writes a JUnit report to `{out}`), and grades `pass` when every hidden test ran and passed (a skipped one
  fails it, so a task whose oracle run skips one is broken), with `pass_share` beside it for
  `repo compare --metric pass_share`. `eval check` runs the null and the oracle steps of such a task
  twice each and reports a task whose two runs differ as `flaky`, which exits 1 like a broken one
  (contract §2, §4.3, §4.5).

- **`catalog check` granularity hints** — after the skill rows, `catalog check` prints a
  free, static `# granularity` section: skill pairs that look like merge candidates
  (overlapping descriptions and trigger clauses, duplicated sections or code blocks) and
  skills that look like split candidates (two of: a long body, disjoint `when` clauses,
  unrelated section topics). It calls no model, is hints only, and never
  changes the exit code. The `--format json` report gains a `granularity` block with
  `hints_only: true` and no verdict. The paid follow-up command its `next:` line names is
  planned and not yet available.

- **`catalog check --ignore CODE`** (repeatable) leaves the findings with that code out
  of the scan: they are not printed and do not set the row's state or the `--strict`
  exit. A noisy check, such as `name-mismatch` on a directory with a synthetic name,
  no longer fails a scan. An unknown code is a usage error that lists the codes. With
  `--jsonl --resume`, rows written under other `--ignore` codes are reviewed again.

- **`backends --json` prints a `cli_output.v1` document** (#94 F2): `status`, `verdict` (null),
  `summary`, `cost`, `next`, and the rows under `data.rows`. A crash or an input error in `backends --json`
  prints the same envelope with `error.code` `infra` (exit 5) or `input` (exit 2).
  `dokimi data schema show cli_output.v1` prints the schema. The other `--json` leaves still print their
  own payload until their follow-up PRs.

- **Paired, cluster-bootstrap difference between two arms** — the library function
  `dokimi.eval.paired_cluster.paired_cluster_difference` takes paired outcomes with a cluster id
  (a prompt or task id) and returns the mean difference over clusters, a seeded cluster-bootstrap
  interval (`seed`, `confidence`) and, with a `margin`, a non-inferiority verdict. Repeats of one
  prompt count as one cluster (each arm averaged per cluster), so the interval is not too narrow as a
  cell-level one would be. At least 10 clusters are needed by default (`min_clusters` overrides it).
  A zero-width interval never reads `noninferior`: it reads `inferior` below `-margin`, else
  `inconclusive`. Near the floor the interval is still anti-conservative (documented). No
  command reads it yet; it is for trigger-probe and routing results. Tracks intel/dokimi#424.

- **Novelty `provenance` block** — every `novelty analyze` and `novelty probe` JSON records
  `dokimi_version`, `git_commit` (`null` outside a checkout), `artifact_digest`,
  `prompts_sha256`, the answerer and judge models, `context_framing`, and the vendors read
  off the model ids. A judge of the answerer's vendor is warned about on stderr (and in
  `warnings` for `analyze`); it is not refused.
- **`--judge2 MODEL --judge2-frac F`** on `novelty analyze` and `novelty probe` — a second
  judge regrades a seeded share (default 0.15) of the graded runs; `judge2` reports lenient
  agreement, Cohen's kappa on lenient and exact verdict agreement. A second judge that is the
  answering model exits 2 unless `--allow-self-judge`; so does a fraction outside (0, 1].
- **Reversal resistance for both arms** — `reversal_resisted_by_arm` and `reversal_paired`
  (resisted only with the artifact, only without it) next to `reversal_resisted`.

- **Token-free command checks in `catalog check`** — the skill's `SKILL.md` and its bundled text files are
  scanned, with regular expressions only. `pipe-to-shell` warns on `curl` or `wget` piped into a shell or an
  interpreter; `permission-bypass-flag` warns on `--dangerously-skip-permissions`, `--yolo` and
  `bypassPermissions`; `install-unpinned` warns once per skill with the count of `pip`, `npm` and `npx`
  installs that name no version. Installs are read from code (fenced blocks and code spans of prose files, the whole of
  scripts), so prose is not counted; requirements files, local paths, URLs and `git+` sources are skipped. All three
  are warnings and do not change the exit code.

- **`catalog check` reports three more spec and size warnings on loaded skills** — a frontmatter key
  that neither the Agent Skills spec nor Claude Code names is a `warn` (`frontmatter-unknown-key`),
  and so is an `allowed-tools` entry that is not a Claude Code tool (`allowed-tools-unknown`; `mcp__*`
  tools are not checked). A body over 500 lines or about 5,000 tokens (characters / 4) is a `warn`
  (`body-too-large`). A `SKILL.md` with no frontmatter now fails to load with "no frontmatter", and one
  with an unclosed `---` block with "unclosed frontmatter", where both said "missing a `description`".
  The `name-format` error for a name over 64 characters now says the length. Warnings do not change the
  exit code; no skill has to change to keep passing `catalog check`.

- **Novelty warns when the extractor and the judge are one model or one vendor family.** The
  extractor writes the answer key and the judge grades against it, so one model doing both
  is flagged on stderr by `novelty analyze`, and by `novelty probe` for the model that wrote the
  saved answer key. `novelty analyze` records the relation in `run_config.extractor_judge`
  (`extractor`, `judge`, `same_model`, `same_vendor`). The run is
  not refused and no default changes: with no `--extract-model` the extractor is still the judge,
  so a default run now warns. `docs/skill-novelty.md` has a new section, "Choosing the extractor
  and the judge", on picking a cross-family pair.

- **`dokimi runs list` groups the artifacts by run**: a new first column, `RUN`, is the
  artifact's `run_id` (`-` when it has none), and the artifacts of one run are listed together.
  The runs are newest first, each in the place of its newest artifact; an artifact with no
  `run_id` stands on its own, in its place by time. The summary has a new line that counts the
  runs and the artifacts with no `run_id`. `--json` lists `artifacts` in the same order and adds
  `runs`: each run's `run_id`, its `kinds` and the `paths` of its artifacts. `--kind` and
  `--subject` filter the rows before they are grouped.
- **`dokimi runs show RUN_ID` shows one run**: the artifacts that carry that `run_id`, as
  `runs list` rows, and the entries under the root that could not be read, since one may belong
  to the run. An artifact file of that name still wins (under the root, or from the working
  directory and inside the root, looked up as for any path, under the root first); a directory
  of that name, such as the run's own, does not. A
  run id that no artifact has exits 2, as a missing file did; so a missing file whose name could
  be a run id (`missing.json`) now says "no file missing.json under the runs root results/, and
  no artifact there has that run_id". A root that cannot be listed exits 2 and says so. A
  name under a directory that cannot be searched now says "cannot read: Permission denied" on
  Python 3.14 too, for `runs show` and for the runs root of both commands, where 3.14 said the
  file was missing.

- **Content checks in `catalog check`** — each loaded skill's `SKILL.md` and the text files beside it are checked, with no key and no model call. `legacy-model-id` (warn) names a model ID its vendor has deprecated or retired, with the file, line, status, date and the vendor's replacement; the list is `dokimi/external/legacy_models.toml`, as of 2026-11-07. `hidden-unicode` (warn) names a bidi control, a Unicode tag character or a stray zero-width character, with its line and codepoint. No action is needed; a skill that uses a listed ID or a hidden character now shows a `warn` row until it is fixed.

### Changed

- **`eval judge validate` exits 2 when no pair is usable, in every mode** — a
  file with no answer longer than the minimum (or where every degradation is a
  no-op) now prints an `Error:` line and exits 2 instead of a `ValueError`
  traceback (exit 1), with or without `--context`.

- **`eval judge agreement --validity` matches a PASS by judge and context** — a passing
  report validates a measure only when both the judge label and the context
  (`stored` or `none`) agree, so a no-context PASS no longer validates a judge
  that read the stored material. A FAIL still silences every measure of that
  judge in any context, and a PASS never lifts it, whatever the report
  order. Reports without a `context` key (written before this
  change) count as `none`, and a stored measure whose scores fell back to
  snippets is `none` too; one mixing stored and snippet scores is `mixed`,
  which no PASS validates. `eval judge agreement --judges` re-scores with the
  stored material.

- **One rule for a record without a question** — every judge path (`eval score pointwise`,
  `eval score panel`, the RAGAS meta-evaluation of `eval score ragas` and
  `answers generate --ragas`, `arms` judging, `eval score pairwise`, `eval score checklist`,
  `eval judge agreement`, and the degradation pairs of `eval judge validate` /
  `eval judge wording`) now checks the records it would send to a judge before
  the first judge call, and before the `--judge-temperature` sampling probe, and
  refuses the run when one has a missing or blank question text, naming the
  record. A record the path would skip anyway is not refused: an answer-error
  record with no answer, a record with only empty answers, one without expected
  points (`eval score checklist`), one without a baseline answer or with only arms left
  out by `--arms` (`eval score pairwise`), and one whose answers are all under the
  degradation length floor (a record without a question id is a `RecordError`,
  see the records entry). `arms run --judge` checks every question before any answer is paid
  for. Before, `eval score pointwise` recorded a per-question error only when the key was
  missing, the degradation pairs skipped the record silently, RAGAS reported a
  warning, and the other paths sent an empty question to the judge. The CLI
  prints `Error: …` and exits 2, the code these commands use for other input
  errors (1 stays the verdict of `eval judge validate` and the `--strict` gates).

- **The novelty gate's `artifact_coverage` judges readable words** — its unmeasured share
  now leaves role decisions (such as a skill's own `evals/`) out of the denominator, the
  same basis as the `novelty analyze` warning and the report, so the gate and the warning
  quote one figure (56% rather than 26% for 400 entry-point words, a 500-word reference
  and 1,000 words of evals; that entry-point run now gets a WARN). Freshly measured
  entry-point runs of skills with their own evals score higher: on 71 public skills, 10
  moved up by +0.040 to +0.134, none crossed the 0.5 ceiling, and no bundle run moved.
  New `bundle` records (`novelty.v1`, `atoms.json`, `probes.json`) carry
  `coverage_basis: "readable"`, and the gate check in the scorecard carries it as `basis`.
  A record without the stamp, which is every artifact stored before this release, is still
  gated on all bundled words, so re-gating it gives the value it had; a stamp this version
  does not know is reported as not measured by `evaluate_novelty`, and rejected by schema
  validation when the record is loaded. Do not compare `artifact_coverage` values
  across the two bases.

- **A `repo run` cell is reused only by the same execution.** `meta.json` and
  `result.json` gain `execution` (`id`, `tools`, `delegation`); the `id`
  hashes the profile's name, command template, tool arguments, environment
  (`env_set`, `env_allow`), config (`config_dir_env`, `config_files`), prompt
  suffix, `egress`, `bridge`, the SHA-256 of the harness executable and harness
  version (the executable's content, not its path). Rerunning a batch with
  another `--tools`, `--delegation`, an edited profile field listed above, harness binary, or harness
  upgrade no longer counts cells from
  the old one as done: the run is refused before any agent starts, and `--force` or a
  new `--batch` runs them. A batch dir that already holds cells of another
  execution is refused before any agent starts (unless `--force`), and so is a
  result from earlier versions, which carries no `execution`: it is not
  reused.
- `--force` is refused when the batch dir holds finished cells outside the run's plan, or of a task the host cannot run (which `--force` keeps), whose execution id differs, so one report row never pools two executions. `--delegation none` does not stop a skill with `context: fork` from starting a subagent.

- **`docker-oracle` stops an oracle that overruns its budget** — `solve.sh` now
  gets the task's agent budget (`agent.timeout_sec`, else 600 s), like an agent.
  An oracle that runs out is a failed cell (reward 0, rc 124) that stays in the
  pass-rate denominator. BenchFlow's `oracle` errors the same overrun
  (`metrics.error`, no verdict), so the two oracles count it differently.
  Before, `docker-oracle` waited for it with no limit.
- **Every `docker-*` step has a budget** — the build, the solve and the verifier
  keep their own. Every other `docker` step (`cp`, `inspect`, the `exec`s that copy
  files in, the sidecar's `create`, which may pull its image) gets 600 s instead of
  none. A step that must succeed and runs out errors the cell. A cleanup
  or collection step whose timeout would let a verdict be scored on unsafe or missing
  state ends the cell with reward 0 and rc 124 instead, and the verifier does not
  run: the pre-verifier reap, the plugin and credential-file removals, the sidecar's
  removal (retried, best-effort, at close), the recompile, and the copy of
  `reward.txt`. The agent's tree, the solve's stream and its cost are still saved.
  A *completed* failing compile still only fills `recompile_failed`. These
  are scored reward 0 (they stay in pass-rate denominators), not recorded as errors.
  A killed `docker` step's partial output is now in `run.log`.
- **`docker-claude-host` is contained** — the host `claude` starts through the
  process runner. A timeout kills its whole process tree, so a tool process the
  agent started no longer outlives its cell. `claude`'s output is read as UTF-8,
  whatever the locale. The in-container agent, the in-container probe and the
  docker CLI go through the same runner, each with the environment it had before.
  `solver.jsonl` keeps the whole stream.
- **Library API** — `DockerSolverHarness` and `ContainerSandbox.open` take a
  `runner` (`create_harness(..., runner=)` now passes it to the `docker-*`
  harnesses too). `solver_stream.run_solver` takes keyword-only `timeout`, `env`,
  `cwd` and `runner`, and returns a `process.Completed`.
- **`metrics.egress` is tri-state** — it carries `status`: `violated` (the proxy
  denied a request), `ok`, or `unknown` when the sidecar's log timed out or failed
  at teardown. An `unknown` cell keeps its verdict, but it is not a clean egress
  record: `scripts/check_smoke_runs.py` fails on it.

- **A malformed answers, eval or arms record is an error that names it** —
  every command and report that reads these files now reads them through one
  module (`dokimi.records`), and a record it cannot read stops the command
  with a `RecordError` naming the file and the record (`answers.json
  answers[3]: missing question_id`); `dashboard` leaves such a file out with a
  warning. Before, a record with no `question_id` was skipped
  (`report model-compare`, `eval judge validate`, `eval score checklist`), judged
  under a made-up id (`eval score panel`) or crashed `eval score pointwise` part-way
  through a paid run; a condition or arm payload that is neither an object nor
  `null` (which the schemas do not allow), or an `expected_skill` that is not a
  name, a list of names or `null`, was skipped.
  A `question_id` that is present but neither a string nor an integer is
  reported as such (`question_id must be a string or an integer, got 1.5`);
  where a record carries both `question_id` and `id`, `question_id` is read.
  `eval score pointwise` now checks every record, including that it has a
  `question_text`, before the first judge call, and exits 2 naming the record;
  `arms run` refuses such an id in the questions file before the first call.
  `report eval` leaves a malformed answers record out of its Q&A sidecar with a
  warning, keeping the others. Files written by dokimi carry these fields; no
  score changes for them. Arms files written with the runner's `unknown_q`
  fallback id still read in `eval judge agreement`'s stored scores.
  `eval score pointwise`, `eval score panel`, `eval score ragas`, `eval score grounding`,
  `eval judge validate`, `eval score checklist`, `eval score pairwise`, `eval judge wording`,
  `eval judge agreement` and `baseline compare` exit 2 on a `RecordError`, naming
  the record on stderr, so exit 1 stays the verdict of the gates. `report
  generate`, `report eval`, `baseline save`, `report model-compare` and
  `eval questions review` exit 1 on one, as on their other input errors.
- **`eval judge agreement --judges` refuses a binary answers file** — like
  `eval score checklist` and `eval score pairwise`, which already exited 2 on a file with
  no `arms` (`answers generate` output), it now exits 2 naming the file
  before any judge call, where it printed an empty table. No command sends a
  binary file's `with_docs`/`without_docs` answers to a judge as two arms, and
  `eval judge agreement` still takes its stored scores from `evaluations` only,
  not from a legacy inline `arms[name].evaluation`.
- **A non-numeric aggregate is unscored** — an aggregate written as a string
  (`"80"`) or a bool counts as no score in every reader; `dashboard` used to
  parse `"80"`. No dokimi writer writes one.
- **`baseline compare` matches question ids as strings** — an int `1` in one
  file and a string `"1"` in the other are now the same question.
- **`diagnose(context_answer, delta)`** takes the context `Answer` and the
  delta instead of two dicts; `diagnoser.retrieval_hit` moved to
  `dokimi.records` (`Record.retrieval_hit`).

- **`repo validate` refuses task ids that contain whitespace or a comma** — a task directory whose name has a space, tab or comma is now a `task.json` error instead of passing validation. The repo-eval workflow's `tasks`, `smoke-tasks` and `exclude-tasks` inputs are split on whitespace and commas, so such a task could never be named there. Rename the task directory to a name without whitespace or commas.

- **Internal refactor, no behavior change** — functions over the new
  complexity cap were split into helpers. CI now fails a PR that makes a
  file longer or a function more complex than its entry in
  `scripts/growth_baseline.json`; `python scripts/check_growth.py --update`
  lowers the baseline when code shrinks.
  After rebasing onto main, run it to lower shrunk entries; `--seed`
  re-records every over-cap entry at its current value. Once main has a
  baseline, a raised or added entry needs a hand-written rationale.

- **`pip-audit` is its own workflow, `.github/workflows/pip-audit.yml`**, run on a PR or
  push that changes a lock, a requirements file or `pyproject.toml`, and daily on `main`.
  A daily run that finds a new advisory opens (or comments on) one issue. Before, it was a
  step of `ci.yml`'s `static` job, so an advisory published against an existing pin failed
  every open PR, whatever it changed.

- **`eval judge validate --context stored --sequential` exits 2 when an arm with
  verified material got no scored base** — the SPRT can decide on the other arms
  and stop first; the run now refuses, as fixed mode does, instead of passing
  without covering that arm. A FAIL on the bases that were scored is still
  reported as a FAIL (exit 1).

- **No `egress.jsonl` when the sidecar's log is unknown** — a container cell whose
  sidecar log could not be read at teardown no longer writes an empty
  `egress.jsonl`, which read like a clean run, and removes one an earlier run left
  in the same output directory. `metrics.egress.status` stays
  `unknown`; a missing file means unknown. `docs/coding-harnesses.md` also notes
  that `docker-probe` reads `egress.status: violated` by design.

- **A scan with unread build inputs is `incomplete`** — an unreadable `.bzl`, BUILD,
  `.bazelrc` (or a file it imports), `MODULE.bazel` or `WORKSPACE` file (binary, over 2 MB, dangling link) now adds a
  `build file not scanned: <path>: <why>` warning to the `repo_readiness.v1` payload and sets
  `incomplete: true`, as an unreadable guidance file already did. Before, only the claims that
  needed the file were `unresolved`, and the scan itself looked complete.

- **A batch is pinned to the built-in grader sources too.** When a task names a `dokimi:` grader, `repo run` hashes the built-in graders into `_eval/batch.json` (`graders`) next to `eval_dir` and `image`, and refuses to resume a batch after they change, as it does for an edit to `.agent-eval/grade.py`; `repo compare` warns when two batches ran from different graders. Before, a grader change moved scores (for example `replay_mul`) between cells of one batch with no warning. A batch stored before this change with such a task has no `graders` pin and is refused on resume: start a fresh `--batch` dir. A batch with no such task is not pinned to them and resumes as before.

- **`novelty analyze --estimate-only` projects the bill it will actually run.** The atom
  count is the larger of one per two spans and one per 23 artifact tokens, capped as
  before; the new `expected_calls` counts the verify, shorten, carrier and classify calls
  and half the retention-oracle pairs that escalate to a model, and `by_role` splits it
  into extractor, answerer and judge. `expected_usd` prices those calls at list prices,
  with the extractor's output per call set per tier, and is `null` when a model has no
  price. The constants were fitted on 13 of 25 measured runs; on the other 12 (held out)
  calls landed within 13%, atoms within 11% and dollars within 33%, all within the 35%
  tolerance. Dollars ran high on 5 runs and low on 7 (worst -33%), so `expected_usd` is not a
  ceiling. `total_calls` and `total_calls_if_every_pair_escalates` keep their formula but
  follow the new atom count, so they rise wherever the span rule left the count under the
  cap and the token rule gives more (a 588-token skill at `--tier standard`: 129 -> 229).
  The answerer's artifact term is priced per arm that carries the artifact, so
  `--arms no_artifact,full,shuffled` projects higher than the two-arm default; an artifact
  above the largest one measured (6,723 tokens) sets `usd_extrapolated: true`.

- **Novelty coverage record moved below the gate** — `Coverage`, the exclusion classes and the coverage bases now live in `dokimi.metrics.bundle_coverage`, and `load_artifact_text` in `dokimi.novelty.artifact`. The novelty gate no longer imports the novelty track, and `novelty.bundle`, `novelty.analyze`, `novelty.report` and `gate.novelty_gate` no longer form an import cycle. Every old import path (`dokimi.novelty.bundle.Coverage`, `dokimi.novelty.analyze.load_artifact_text`, ...) still works; behaviour is unchanged.

- **`novelty analyze` cost totals are what this run spent** — the per-role cost summary now leaves calls replayed from the response cache out of `input_tokens`, `output_tokens` and `usd` (they were summed from the stored records before) and counts them in a new `replayed_calls`; `llm_calls` still counts them. `usd` is now `null` when a call returned no usage, where it was the sum of the priced calls. The block also gains `cache_read_tokens` and `cache_write_tokens`, and the Markdown report's cost line names the replayed calls when there are any.

- **External-catalog smoke gate** — `scripts/check_smoke_runs.py` now declares its planned cells and checks them with `completeness`. A planned cell missing from `runs.json` fails the gate even when every count in the summary is clean.

- **`novelty routing --catalog <dir>` reads the catalog through its `skills.yaml`** — when the
  catalog root has a `skills.yaml`, that manifest decides which skills are competitors, as it
  does for `catalog scan`, so a `SKILL.md` it does not list is no longer a sibling. Without a
  manifest, every `SKILL.md` under the directory counts as before. A declared skill that cannot
  be loaded is skipped with a warning on stderr instead of making the whole field unreadable.
  Two catalog skills with the same name and different content leave the field with a warning
  naming every path, rather than the run being refused.

- **`novelty routing` skips a non-`SKILL.md` competitor with a warning** — a
  `skill:` competitor that names a file other than `SKILL.md`, or a directory without one,
  is left out of the field and reported on stderr. It used to abort the whole run, so a
  shell glob over a skills directory (which also matches helper scripts) could not be
  passed. The artifact under test is still refused.

- **`novelty routing` leaves `disable-model-invocation` skills out of the field** — no
  harness lists such a skill, so the model cannot select it, and a card for it scored a
  choice that does not exist (`ultracave` and `megacave` in the caveman repo). They are
  left out of a `--catalog` and of `--competitors`, each with a warning on stderr, unless
  `--include-disabled` is passed. A disabled artifact is still routed, with a warning that
  its rate is not a harness rate.

- **`entry_points.grade` is optional** when every task's grader is a built-in `dokimi:` one;
  `repo validate`, `repo run` and `eval check` name the tasks that need it otherwise,
  the last two before any agent runs.

- **`novelty routing` exits 1 when no query is scored** — a run whose queries are non-empty
  but all filtered out (no content word left) now exits non-zero with `"status": "no_queries"`
  and a `reason`, where it used to exit 0 with null rates. A script that read the null rates
  as a result must check the exit code or `status` instead.

- **`run --strict` reads the critical bands strictly** — a NaN metric now violates its band; it passed silently before. A band the gate cannot read (an unknown condition, or a `None` metric) exits 2 and names the spec, instead of ending in a traceback.

- **A run that read half or less of the readable words warns on stdout** — the `WARNING: only
  N% ...` line goes to stdout, because a headline piped elsewhere never shows stderr. The
  threshold of the stderr warning is unchanged, and an entry-point run warns on both channels
  as before. The stdout line is wider than the stderr one in two cases: a `--include-resources`
  run whose word budgets left half or more of the readable words unread now warns on stdout
  (its `warnings` list is unchanged), and `coverage: unknown` (a bundled file did not decode)
  keeps the `(entry point only)` scope and warns when the words it counted show the run read
  half or less of the readable words (the entry point, or a bundle run capped by the budgets). `bundle.measured_share` is `null` when the share is unknowable.

- **Pipeline summary reads through `dokimi.records`** — the per-condition
  averages and the delta average that `EvaluationPipeline.run` prints and returns
  are now read with `dokimi.records` instead of the `with_docs`/`without_docs`
  keys of the evaluation dicts. The numbers are unchanged. A test now holds every
  other reader of those keys to a list of exceptions that only shrinks.

- The answerer sends a question with `prompt_style: verbatim` as it is,
  without the expert-answer wrapper.
- task_inspect.v1 accepts the `eval import` consumer.
- questions.v2 declares `grading` and `prompt_style` (a widening; existing
  files are unchanged).

- **A task whose `network_mode` is not `none`, `allowlist` or `public`, or that
  lists `allowed_hosts` without `allowlist`, now errors the run** instead of
  running offline.

- **Metrics and stats** no longer score wrongly or crash on edge cases in readability, example extraction, coverage, citation parsing, localization, NaN/Infinity aggregates and `mean_ci` confidence levels. Scores for the same inputs can change: re-baseline stored scores that depend on readability, coverage, citations, localization or records. `mean_ci` now raises `ValueError` for a `confidence` outside (0, 1).

- **Host subprocess examples are not run when their limits cannot be applied.** On Windows
  any limit, and on macOS a memory limit, stops the run with an error naming the limit
  (`dokimi run` exits 2), as does a limit the kernel rejects when the snippet starts, so
  `example_pass_rate` is neither 0.0 nor 1.0 for snippets that never ran. On macOS set
  `mem_mb: null`; on Windows use the `docker` backend. A process limit above
  an inherited hard limit (for example `ulimit -v`) is lowered to it instead of failing the
  launch. The `docker` backend refuses the same way when `mem_mb` is missing or not positive.
- **The isolation probe reports `network: untested`, not `closed`**, when `bash` or `timeout`
  is missing, including inside task containers on images without them. Re-run the probe on
  such an image with the tools installed to get a `closed` result.
- **`ci_local.py` runs the gitleaks scan on Linux x64 only through the workflow download.**
  Other hosts use an installed `gitleaks` at the workflow's pinned version, with a notice, or
  skip the scan with the reason; `--offline` no longer skips the native scan.

- **`docker-*` task containers are hardened like `repos` containers** — the
  task container starts with `--init`, `--cap-drop ALL`, `--security-opt no-new-privileges`
  and `--pids-limit 32768`, and gets back only `CHOWN`, `DAC_OVERRIDE` and `FOWNER`
  (the harness's own root steps on the agent's tree) and `SETUID` and `SETGID`
  (task scripts that drop privileges, such as apt's `_apt` sandbox). A setuid binary
  in a task image can no longer raise its privileges. Every other capability Docker
  grants by default is dropped, so a solve or verifier step that needs one now fails:
  killing another user's process (`KILL`), raw sockets (`NET_RAW`), `mknod`
  (`MKNOD`), `chroot` (`SYS_CHROOT`). Setting the setgid bit on a file whose group
  root is not in (`FSETID`) exits 0 and leaves the bit off. A port below 1024 still
  binds. Before, the container ran with Docker's default capabilities and no process
  limit. `docker-*` results from before this change may not be comparable for a task
  whose step needs a dropped capability: re-run it to compare.

- **Some Holm and BH verdicts at the boundary change from not significant to
  significant** — a result whose exact adjusted p is in `[0.04995, 0.05)` is now
  `significant_adjusted: true`, where it was `false`. Example: three tests at
  p = 0.0166662 give a Holm p of 0.0499986, shown as `0.05`; the arms report now
  marks them significant, and so does the model-comparison report, whose Holm ran
  on p-values already rounded to 4 decimals (0.0501 here). Its Wilcoxon clause is
  read unrounded too, as in the arms report. No verdict moves the other way, and
  results outside that window keep their verdict. Reports written by earlier
  versions keep their old verdicts; re-run the comparison to get the new ones.
- **The model-comparison `p (adjusted)` column can move in its last decimal** —
  it is now the exact adjusted p rounded once, as in the arms report: three runs
  at a raw p of 0.024037 showed `0.072` (3 x 0.024) and now show `0.0721`.

- **`report generate` and the pipeline summary average each arm over the complete pairs, as `report eval` does** — the
  Overall table, the topic and source breakdowns, and the `stats` block of `--format json` used
  to average the context arm and the baseline arm over every question scored in each, so the two
  means could cover different questions and their difference was not the delta printed beside
  them. The `pipeline` run did the same for the summary it prints and stores in
  `steps.evaluation.summary`. All of them now cover only the questions scored in both arms, and
  the delta is the difference of those means. For an eval file with an unpaired question, the
  context and baseline numbers change; for a file the judge writes, the delta does not. The report
  says how many questions are unpaired (`paired_questions` and `unpaired_questions` in the JSON,
  and a note plus the Count cell in the Markdown). To see the new numbers, run `report generate`
  on an existing eval file again.
  The top and bottom performer tables of `report generate` rank the complete pairs too.

- The `--provider` choices of every command come from one tuple,
  `dokimi.llm.ANSWER_PROVIDERS`. `tools` keeps API providers only, since it needs
  tool calling.
- A CLI provider refuses an agentic arm (`mcp_agent`, `skill_agent`, `skill_catalog`) and
  `--session-turns` in `arms run` and `arms matrix-run`, before any answer is paid for: the CLI
  runs one prompt with no tools and no conversation.
- A subscription usage limit fails the call at once, not as a retryable rate limit: it resets
  in hours, so a retry does not help.
- A CLI reply records the model the CLI resolved its alias to: `resolved_model` on each usage
  row (answers, judge calls), `judge_resolved_models` in the judge record. The response-cache
  key takes the CLI version, so a new release that moves `sonnet` does not replay the old
  model's replies. The resolved id is known only after a call, so a move the CLI does not
  ship with is not caught.
- Independence checks treat an alias and its full id as one model (`opus` and
  `claude-opus-…`). The novelty default judge on `claude-cli` is another Claude family than the answerer.
- The CLI child process drops `ANTHROPIC_*`, `CLAUDE_*` and `CLAUDECODE` variables from the
  environment (except `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN` and
  `CLAUDE_CODE_GIT_BASH_PATH` (Windows)), so a billing or
  session setting in the caller does not reroute the call. Git is stopped at the work dir's parent. A Bedrock or Vertex route set
  in that environment is dropped too: the CLI then uses its own login, and `backends` reports
  that login, not the route.

- Task runs preserve artifacts and exit nonzero for any errored, unscored or missing planned cell, including partly errored runs that previously exited 0; supported dry runs remain plans and verifier failures remain scored outcomes.

- **Judge and panel retry warnings come from `dokimi.llm`** — the judge and
  panel redraw loops now run `llm.ask_parsed`. The warning text for an
  unparseable reply is unchanged, but its logger is `dokimi.llm`, not
  `dokimi.eval.judge` / `dokimi.eval.panel`, so a log filter keyed on those
  names must change. A single judge whose three draws all fail to parse now
  raises `llm.Unparseable` (a `ValueError` subclass) with the same message. It
  used to raise the parse error itself, which for a reply that parses to a JSON
  array was an `AttributeError`. Scores, redraws, cache keys and usage are
  unchanged.

- `dokimi run` and `dokimi compare` have a one-line help in the top-level list.
  Their parsers have no help text, so the route table supplies it.

- **Input and usage refusals exit 2, not 1** — the exit-code table (new in `docs/quickstart.md`, section Exit codes) gives 2 to a missing, unreadable, malformed or unusable input, a flag combination a command refuses, an output file that cannot be written, and a benchmark run or preflight with no provider credential. Exit 1 is left for a verdict or a gate that ran and failed, and for a runtime or model failure. The messages are unchanged. Scripts that branch on `$? -eq 1` for these cases must also accept 2. Affected: `eval personas` (validate, discover), `eval questions`, `eval` (score, judge), `orchestrate`, `report` (eval, generate, model-compare), `baseline`, `novelty`, `catalog` (and `catalog probe`), `answers`, `arms`, `explore`, `tasks`, `subjects`, `repo` (`validate`, `scan`, `inspect`, `budget`, `fetch`, `reach`, `report`, `compare`), `library`, `tools`, `dashboard`, `run`, and `benchmark` (run, preflight, batch). Only the validation step of a command exits 2; a failure while it runs keeps 1. Specifically: `benchmark preflight` exits 2 when a provider credential, the output dir, the question set or the doc source is invalid, even when a failed check is also reported, and 2 for a `local:` or `okf:` doc source path that does not exist (a `url:` or `mcp:` source is parsed, not reached), and 1 when a check alone fails (a doc source that fails to build); `benchmark batch` with nothing to run exits 2; `repo validate`, `scan`, `inspect`, `budget`, `fetch` and `reach` exit 2 for a malformed source string, an unpinned remote, a pinned local path that is not the repository root, a manifest path that leaves the tree, a manifest or profile error, or a bad scenario selection, and 1 for a clone, fetch or pin resolution that fails (including a pinned local source that is not a git repository, a pin absent locally, and a remote pin missing from origin) and for a harness run; `arms run` and `arms matrix-run` exit 2 for a bad spec or setup input, and 1 for a run failure or a failed sampling probe (the same for the `--judge-temperature` probe of `eval score pairwise`, `eval score checklist`, `eval judge wording`, `eval judge agreement` and `eval judge validate --mode pairwise`: 2 when the provider table or litellm's own parameter check refuses the setting, with no request sent, 1 when the probe call is rejected); a matrix probes every cell before its first answer, so a cell refused at the probe makes no answer call (probe calls are still model calls and can fail, exit 1); `eval triggers generate` exits 2 for a duplicate question id, a question without text or an `@field` rule that labels none of its source's questions (refused before any `--rewrite` call; a `--trigger-set` file with no labels is accepted with `--format questions` only, since reach and skill-creator would write it as an empty set), and 1 for a `--strict` refusal (a lint gate, as on main) and when a `--rewrite` returns nothing; a missing RAGAS extra stays 1. `repo run` and `eval check` are not converted: their input refusals still exit 1. `tools preference` exits 1 when every call failed and 3 when `--max-cost-usd` skipped calls. A `--static` file that cannot be read or is not JSON exits 2 in `repo scan`; a schema-invalid document exits 1.

- **Stack P scores move (#228 step 2)** — the context arm now asks the doc source for the
  `top_k` docs the command was given: `benchmark run` asks for 3 (`DEFAULT_RETRIEVAL_TOP_K`; it
  has no `--top-k`), `dokimi evaluate` and `answers generate` for their `--top-k` (default 3).
  Before, they left `max_results` at the client default of 5. The doc library id resolves
  through the registry's `context7_id` before the client's own resolver, in the `--registry`
  that was loaded. A question with no document (no hit, or the reranker raised) is no longer judged on
  its `[FALLBACK: ...]` placeholder by default, so it drops out of the paired statistics; the
  record and its `retrieval_hit` stay, its diagnosis stays `empty_retrieval`, and the run
  summary says how many questions it left out. Pass `--judge-placeholder` to judge the
  placeholder again (this restores only that part of the old method). The dashboard's arm
  means and delta leave such a question out too, as `report eval` does. Its doc score counts a
  retrieval miss as 0 only when the baseline has a score and no error touched the pair (neither
  arm has a judge error); a miss with an error leaves the doc score alone. `report eval` and `report generate` show `—` for the arm means and delta when
  no question is scored in both arms (the `report generate --format json` `stats` block carries
  `null` for `avg`, `min` and `max`), and the run summary prints no means then. `benchmark run`,
  `evaluate` and `eval score pointwise` take the same flag; `benchmark batch` has none and never judges
  the placeholder. Scores from before this change are not comparable with new ones.
- **Trust gate** — `retrieval_hit_rate` counts every evaluation's recorded retrieval outcome,
  not only the ones scored in both arms, so unjudged misses still count against the rate, and a
  judge-error row keeps the outcome of its answer. The rate is shown even when no question is
  scored in both arms. A judge-error row of an older eval file, which has no `retrieval_hit`,
  is left out of that count as before. The thresholds are unchanged.
- **Zero-score check** — a judge-error row on a retrieval miss now counts as unusable in
  `zero_score_fraction` (a judge error on a retrieval hit is still left out), so a run can fail
  that fail-severity check where intel/main passed it.
- **Stamped artifacts** — the answers and eval artifacts carry `method_version` (2). Artifacts
  from before this change have no stamp and represent the legacy method (version 1); readers do
  not yet normalize or enforce the version, so it does not yet stop a comparison of mixed methods.
  `eval score pointwise` stamps its eval with the version of the answers file it judges, so legacy answers
  re-judged today stay version 1. An `eval score pointwise` artifact also carries `judge_placeholder`, so two
  files of one version that differ in it are told apart. `eval score panel` (the JudgePanel path)
  writes neither stamp yet, so its artifact reads as version 1 whatever answers it judged. An absent
  or null stamp means version 1; any other value must be a positive integer, or `eval score pointwise` exits
  2 before any judge call.

- **Earlier figures are not comparable.** Pass rates, deltas and selection rates from runs before this change do not match new ones for the same inputs. Re-run `arms judge` or `eval score reference` to compare.
- **Errors leave the scores out.** An answer whose call failed leaves `n`, the pass rate and the mean out. Before, it was lumped with answers that no check could score.
- **Empty replies are errors, never scores.** An empty, whitespace-only or content-less model reply is an error on its cell in arms runs and in `eval score reference`: it is never judged or graded (a negated check no longer scores `""` as 1.0), and it leaves `n`, the pass rate and the mean out.
- **Judge failures with an empty message are counted.** A judge exception whose message is empty (for example `TimeoutError()`) is recorded under its class name, so it is counted as an error rather than dropped.
- **`delta_vs_baseline` is paired.** It is the mean of arm minus baseline over the questions both arms scored. Before, it was the difference of two unpaired means, so a question one arm errored on moved the delta.
- **A judge failure is an error, not a pass.** In `eval score reference`, a judge check whose call raised, or whose reply had no YES or NO, makes the answer an error: no score and no pass, even when its other checks passed. Before, the other checks decided the pass. The block carries `error`, as an arm cell does.
- **An empty reply stays an error when an output shaper adds text.** Emptiness is read on the model's reply before the shapers run, so a footer on an empty reply is not an answer.

- **Bad input exits 2 in the `novelty` and `report model-compare` commands** — a
  file that `novelty` cannot read or that is not the artifact it names (`--artifact`,
  `--atoms`, `--probes`, `--competitors`, `--queries-by-skill`), and the runs that
  `report model-compare` refuses (a missing, unreadable, non-JSON or non-object file;
  runs that disagree on the treatment, prompt or judge), and the skill fields that
  `novelty routing` refuses (a missing competitor, a skill listed twice, a skill compared
  with itself, an all-vs-all catalog that holds one skill twice with different content),
  exit 2 with `Error: <message>` on stderr. For a bad file the message names the file and
  the reason; for a refused field it names the skill or option at fault. The `novelty`
  file refusals keep the exit code 2 they had, `--artifact` and `--atoms` among them; the
  `model-compare`, `routing` and `view` refusals keep that code too, and `eval headroom`
  refuses an input it cannot read or parse the same way. Exit 1 stays the verdict of the gates
  (`--strict`, `eval judge-validity`) and the code of a model failure (an extractor or judge
  that errors), so a script that branches on the code must treat 2 as "fix the input".
  `subjects run --novelty-artifact` exits 2 with `Error: cannot load novelty artifact: <reason>`, as before. `novelty view`
  also checks the `bundle` of an atoms file, so a malformed bundle is refused with exit 2. Two `report model-compare` messages changed wording:
  `File not found: <file>` is now `cannot read <file>: No such file or directory`, and
  `Invalid JSON in <file>` is now `<file> is not valid JSON`. An invalid artifact named by
  `novelty view` says which part is wrong: `is not valid JSON`, `is not UTF-8 text`,
  `is not a novelty artifact: expected a JSON object`, or `is not a valid novelty artifact:
  <reason>`.

- **The evaluation commands live under `eval` (#94 M1)** — `personas`, `questions` and the
  evaluation leaves of `tasks`, `catalog` and `repo` are now under `dokimi eval`, and the
  `eval` leaves are grouped into `eval score`, `eval judge`, `eval questions`, `eval personas`
  and `eval triggers`. Every old path still answers, but it prints `moved: dokimi <new argv>`
  to stderr and exits 2 without running anything, so a script fails loudly and names the new
  command. `eval score` stays a live group: bare `eval score` exits 2 with a usage error, and
  `eval score --help` lists its leaves. Move the scripts to the new paths before the next release. The arguments are
  unchanged. `dokimi repo tasks replay`, `repo run`, `repo report` and `repo compare` stay where
  they are.

  | Before | After |
  |---|---|
  | `tasks inspect` | `eval inspect` |
  | `repo tasks check` | `eval check` |
  | `tasks list` | `eval tasks list` |
  | `personas discover` | `eval personas discover` |
  | `personas approve` | `eval personas validate` |
  | `questions generate` | `eval questions generate` |
  | `questions analyze` | `eval questions analyze` |
  | `questions refine` | `eval questions refine` |
  | `questions panel-review` | `eval questions review` |
  | `questions import` | `eval import` |
  | `catalog trigger-set` | `eval triggers generate` |
  | `eval score` | `eval score pointwise` |
  | `eval pairwise` | `eval score pairwise` |
  | `eval checklist` | `eval score checklist` |
  | `eval panel-score` | `eval score panel` |
  | `eval grounding` | `eval score grounding` |
  | `eval ragas` | `eval score ragas` |
  | `eval reference` | `eval score reference` |
  | `eval judge-validity` | `eval judge validate` |
  | `eval judge-wording` | `eval judge wording` |
  | `eval cross-judge` | `eval judge agreement` |
  | `personas` (bare, or its help) | `eval personas` |
  | `questions` (bare, or its help) | `eval questions` |

  The `[free]`/`[paid]` labels in `--help` follow the commands. The `consumers` labels that
  `tasks inspect` reports (and the `task_inspect.v1` schema enum) name the new paths. The
  `repo tasks check` step of `repo-eval.yml` runs `dokimi eval check`; its job name is unchanged.

- **An absolute link inside a build context digests by a relative target** — a pinned batch or `worktree-` image tag for such a context changes once.

- **`guidance:none` also removes the agents' overlays** — a `guidance:none` cell now deletes every `AGENTS.claude.md`, `AGENTS.codex.md` and `AGENTS.copilot.md` as well as the files the guidance globs match; a `guidance:as-skill` cell drops the same overlays and does not package them. The cell's execution id is unchanged, so on a repository with overlays a `guidance:none` or `guidance:as-skill` result is not comparable with one from an earlier version: start a new `--batch` (or re-run with `--force`) rather than resuming an old one. `repo tasks replay` now leaves overlays out of `change.patch` as it does guidance files; a replay task built earlier from a PR that edits an overlay fails to prepare under `guidance:none` and `guidance:as-skill`, and under `guidance:overlay` when the edited overlay is another agent's, until it is rebuilt with `--force`.

- **`novelty routing --catalog` leaves out a skill with a spec error by default** (for example a
  description over 1024 characters, or a name that is not kebab-case or not its directory name), with
  a warning on stderr naming the error. Before, such a skill was in the field. `--include-invalid`
  keeps it. A `--competitors skill:<path>` with a spec error is skipped the same way. A name with two
  different copies also changes the field, so a routing result saved before this change is not
  comparable with one saved now for the same `--catalog`: rerun it to compare. With
  `--include-disabled`, a copy that is `disable-model-invocation` counts as one of the copies.
- **`novelty routing --catalog` skips a name that two different skills share, with a warning, instead of
  refusing the run** (exit 2). Every copy of that name leaves the field, the rest of it stays, and stderr
  names every path. Under `--all-vs-all`, a query file that names such a skill, or one left out
  for a spec error, is still refused (exit 2), and the message names each such skill with its reason.
- **`catalog scan` reports a same-name skill with different content as a `name-conflict` warning**
  (among listed skills: no `disable-model-invocation`, no spec error), naming the other path and that
  `novelty routing --catalog` leaves the name out of the field. Before, each of the two skills was
  reported `ok`. It is a warning, not an error, so `--strict` does not fail on it.

- **A capped novelty run probes atoms spread over the whole input** — under
  `--max-atoms` or a tier's cap (25 at `triage`, 60 at `standard`, 120 at
  `study`), extraction no longer stops at the cap: it reads every span, and of
  the M atoms it extracts the run probes n at indices
  `floor((i + 0.5) * M / n)`, every (M/n)-th in document order by the earliest
  span each atom cites (on a `--include-resources` run, across the files in
  file order, so a file with few atoms may get no probe), so a capped rate
  estimates the whole input's per-atom rate instead of describing its opening.
  Like any fixed stride it can fall in step with repeated, same-shaped
  sections and miss or over-count what each one adds; for such an artifact, or
  a rate that decides something, pass `--all-atoms`. The selection takes no
  seed: the same extracted atom list gives the same sample. `novelty analyze`
  and `novelty atoms` (whose `atoms.json` `probe` and `shorten` read) probe the
  same atoms. A cap below 1 is refused before any call. New runs record
  `extraction.reached` `null`; when the cap leaves atoms out, `atoms.sampling`
  is `cap-spread:<n>` and `extraction.sampled` `{"atoms", "of", "rule"}`, and
  when it keeps every atom (M ≤ n), `full` and `null`.
  Only a cap that leaves atoms out sets `spans.capped_atom_set` and brings the
  cap's forfeits: an input whose atoms exactly meet the cap no longer counts as
  capped. The cap warning, the `novelty.md` header and the headline's
  `coverage:` line say how many of the input's atoms were probed and with what
  stride, or, when some spans went unread, that the sample is spread over the
  atoms of the spans that were read and how many were not. Artifacts written
  with `cap-per-artifact:<n>` and a `reached` record still validate and render
  as before. Probing still stops at the cap, but a run whose cap was reached
  before its last extraction batch now asks the remaining batches, and
  `--estimate-only` counts them, one call per batch of 25 spans; the tier profiles were measured with the old
  order (decision D017). The
  shortening forfeit now says candidates are scored against a spread sample of
  the cap's atoms, so cutting a span with no sampled atom looks free.

- **docker-claude arms no longer show dokimi's own names to the agent** — the host workspace is a plain `mktemp` directory instead of `dokimi-run-*`, the in-container skill plugin is mounted at `/opt/claude-plugins` instead of `/opt/abskill`, and the prompt is the task instruction with only the treatment notice in front of it, not the "Solve this coding task…" wrapper. Skills still load through `--plugin-dir`: a skill in `$CLAUDE_CONFIG_DIR/skills` is not offered under the launch's `--setting-sources ""`. The agent binary and its files live under `/opt/agent` instead of `/opt/dokimi-agent`. `tests/test_payload_audit.py` asserts the arms' full command (executable and argv, docker and host) and cwd carry no marker. Runs measured before this change are not comparable in their prompt text or command line.

- **A Claude Code `Bash` call that prints a file is a read, as a Codex or Copilot shell command is** — the `claude-stream-json` converter now gives a `read` event, before the `command` event, for each file a Bash command only prints (a bare `cat`, `head`, `tail`, `nl` or `sed -n`, after any `cd` in the same command: `cd benchmarks; cat AGENTS.md` reads `benchmarks/AGENTS.md`). So `result.json` `process.guidance_read` now counts guidance printed through Bash, where it counted only the Read tool before, and means the same for the three harnesses; `tool_calls` counts the new read events too, a `cat` of an installed skill's `SKILL.md` counts the skill as used, and a `cat` of a file a `patch:` arm added counts in `uptake.reads`. A command that is one read of one file and fails (`cat AGENTS.md` where there is none), alone, in a brace group (`{ cat AGENTS.md; }`) or after a `cd` (`cd sub && cat AGENTS.md`, `time cd`, `command cd`, `builtin cd` or `X=1 cd` too, which Codex and Copilot commands counted as a read before), gives no read, but one naming two files keeps its reads for all three, even when only one of them is known (`cd -; cat missing.md /ws/AGENTS.md`, whose read Codex and Copilot commands dropped before); a refused one gives denied reads; a pipe, `grep` or `rg` gives none, nor does a negated read (`! cat AGENTS.md`), whose exit says nothing of the file. Claude Code keeps the shell's directory between Bash calls, so after a call that ran a `cd`, `pushd`, or an `eval`, `source` or `.` (which may run a `cd`) in that shell (not in a subshell, pipeline or background job: `cd sub &` moves nothing, nor does a refused call), later relative reads are not recorded (absolute ones are), the parent's and every subagent's alike, since a subagent may share the parent's shell. For all three harnesses, a backgrounded or piped `cd`, `pushd`, `eval`, `source` or `.` no longer loses the directory for the rest of its command, a later `||` branch included (`source env.sh & cat AGENTS.md` and `source env.sh & false || cat AGENTS.md` read `AGENTS.md`), a `cd` inside `$( )`, backticks or `( ... )` no longer moves the shell (`(true; cd sub); cat AGENTS.md` reads `AGENTS.md`), a brace group is followed through the `$( )` or nested groups in it (`{ cd $(pwd); } | cat` moves nothing), and a directory builtin whose name is quoted or escaped is that builtin (`"source" "$ENV"` and `\cd "$D"` lose the directory, and `"cd" sub && cat AGENTS.md` that fails gives no read), and a command the parser does not fully follow whose command word, after `builtin`, `command` or `time` and their options (and assignments after `time`: `time X=1 cd sub` runs the `cd` in the shell), names a directory builtin (`X="a b" cd "$D"`, `time -p cd sub`, `command -p cd sub`, `builtin -- cd sub`; not `time git add .` or `command -v cd`) counts as moving the shell, as does one in a `case` arm, while a `time` after an assignment or quoted or escaped (`X=1 time cd sub`, `\time cd sub`) runs /usr/bin/time and moves nothing. In `repo reach`, a Bash read of a guidance file now makes it `fetched`. A read that was refused or failed, by any tool (the Read tool's too, before this change), no longer makes a file `fetched` in `repo reach` or its receipt's `states.accessed` true, nor counts a skill whose `SKILL.md` it named as used; nor does a refused or failed `Skill` tool call (a Claude Code `Skill` event now carries `rc`, `1` when the call failed), and a failed read of a file a `patch:` arm added no longer counts in `uptake.reads`; the event stays in `events.jsonl`; a refused relative Bash read after a move stays with no `path`, since which file it named is not known. `first_useful.steps` counts events, so a Claude Code run that prints a file through Bash before its first useful result shows one step more than before (`cat AGENTS.md`, then a passing `pytest`: 3, was 2), and `issued_s` is unchanged; `repo compare` pairs steps with batches run before this change on that basis. `docs/repo-evaluation-contract.md` §4.1, §7 and §7.1 say so.

- **The `-host` arms' plugin directory now comes from one helper** — `docker_solver` builds the
  skill plugin and passes `--plugin-dir` from the same `_host_plugin_dir`, so the leak-audit test
  checks the path the harness uses. No change in behaviour: it is still `plugin` under the
  workspace root, beside the agent's work tree.

- **Capped novelty runs: span and shortening figures are not comparable with
  older capped runs** — on a run whose cap left atoms out,
  `frac_carrying_nothing`, dead weight, sole carriers, the novelty gate's
  `dead_tokens` check and the candidate `novelty shorten --out` writes are
  now computed over every extracted atom instead of the cap's sample, so the
  same input gives different numbers than before. Re-run older capped
  `novelty analyze` and `novelty atoms` runs before comparing them with new
  ones.

- **Self-judging warning on more judge paths** — the evaluator-independence warning that
  `evaluate`, `benchmark` and `orchestrate` print now also fires on:
  - `arms run --judge` and `subjects run --judge` (and `--judge-panel`, whose roles all run on
    the judge model), when the judge model is the answer model, for example
    `--judge-provider openai`, which resolves to the default answer model `gpt-6.1-sol`;
  - `eval score pairwise` (escalation judge included), `eval score checklist`,
    `eval score reference`, `eval score ragas`, `eval judge validate` (escalation judge included),
    `eval judge wording` and `eval judge agreement --judges`, for each judge that is the
    model the answers file records in its top-level `model`/`provider`;
  - `eval questions analyze` and `eval questions review`, when the reviewing model is the one
    the questions file records as its generator (the default for both, since they share the
    generator defaults); the message names the generator and reviewer.

  Files that record no `model`/`provider` stay silent. Warning only: every run goes ahead
  and no defaults changed. The helper is `dokimi.utils.warn_judge_independence`, with
  `warn_recorded_self_judge` for saved files.

- **A skill whose only spec error is a description over 1024 characters stays in the routing
  field** — `dokimi novelty routing` (single-artifact and `--all-vs-all`) left such a skill out
  unless `--include-invalid` was passed, but harnesses differ: Claude Code lists it and truncates
  the description past about 1,536 characters, pi.dev rejects it. The skill now stays in the
  field, from `--catalog` or `--competitors skill:`, with its full description. The run warns
  with the skill name and its length, and the saved result lists it under `field.spec_warnings`
  (an empty list when none); an over-limit skill under test (`--artifact`) is warned about and
  listed there too.
  Any other spec error (name not kebab-case or not its directory name, broken frontmatter) still
  leaves a skill out unless `--include-invalid`. `dokimi catalog check` no longer treats such a
  copy as unlisted, so it reports a `name-conflict` with a different copy of the same name, as
  routing does.
  The routing field of a catalog with such a skill changes, so routing rates measured before
  this release on it differ: re-run a saved baseline before comparing with it.

- **`golden_manifest.max_docs` is optional** — the spec schema no longer
  requires it, and the shipped `benchmarks/spec.v1.yaml` drops it: an absolute
  doc-count cap had to be raised with every new doc and failed main when two
  PRs each added one. A spec that sets it is still enforced; `min_docs` stays
  required.

- **Input refusals print `Error: <message>` on stderr and exit 2 through one path** — more
  refusals in `eval score`, `eval judge`, `eval headroom`, `eval redteam`, `eval import`,
  `answers generate`, `data export`, `data import docling`, `run` and `baseline compare` now
  raise `InputError`, which `cli.main` turns into the same
  `Error: ...` line and exit 2, so a message that used to be printed and exited with
  `sys.exit(2)` reads the same and no traceback escapes. A cross-judge run with several
  stored-material problems prints one `Error:` line, with the problems below it.
  `baseline compare` on an eval record it cannot read now prints `Error: <message>` where it
  printed `❌ <message>`; the exit stays 2.

- **Run failures exit 1, not 2** — `eval score pairwise` in which every comparison failed
  (the errors are still written to `--output`), and a `--judges` judge that scored none of its
  answers in `eval judge agreement --judges`, now exit 1 like other run failures
  after writing their report. Scripts that treated 2 as "bad input" for these runs must check 1.

- **Command arms (`command:` and `--command-template`) send the task instruction as written** — the `Solve this coding task. Work only inside the task environment…` and `# Task: <name>` lines that dokimi put around `{prompt}` are gone, as a user's agent never sees them. `{prompt}` and `{prompt_quoted}` now expand to the bare `instruction.md`, and `{prompt_escaped}` to the same text with `{` and `}` doubled. Command-arm runs measured before this change are not comparable in their prompt text.

- **Skill, catalog, trace and data commands live under their groups (#94 M2)**: `skill novelty`
  (analyze, atoms, probe, items) and `skill shorten` are under `skill`. `catalog check` replaces
  `catalog scan`, and `catalog intents` replaces `library intents`. The trace commands are under
  `trace` (`inspect`, `consistency`, `prefilter`). `data export` replaces `dataset export`, and
  `data import docling` replaces `ingest docling`. Every old path still answers, but it prints
  `moved: dokimi <new argv>` to stderr and exits 2 without running anything. The arguments are
  unchanged. Move the scripts to the new paths before the next release.

  | Before | After |
  |---|---|
  | `novelty analyze` | `skill novelty analyze` |
  | `novelty atoms` | `skill novelty atoms` |
  | `novelty probe` | `skill novelty probe` |
  | `novelty items` | `skill novelty items` |
  | `novelty shorten` | `skill shorten` |
  | `catalog scan` | `catalog check` |
  | `library intents` | `catalog intents` |
  | `context trace` | `trace inspect` |
  | `context consistency` | `trace consistency` |
  | `context prefilter` | `trace prefilter` |
  | `dataset export` | `data export` |
  | `ingest docling` | `data import docling` |
  | `context`, `dataset`, `ingest` (bare, or their help) | `trace`, `data export`, `data import` |

  `novelty routing` and `novelty view`, `library list|show`, `subjects list|show` and
  `repo tasks replay` stay where they are for now. The `.github/workflows/agent-quality.yml`
  step runs `python cli.py catalog check`; its job name is unchanged.

- **`noise` is always an object in routing and `--all-vs-all` results** — with
  `--repeats 1` it is no longer `null`: `n_repeats` is 1, and the agreement, mean
  agreement, half-range and unstable-queries fields are `null`. The console `noise` line prints only
  for two or more repeats. Code that tested `noise` for `null` should test
  `noise["n_repeats"] > 1`.
- `novelty routing` and `--all-vs-all` refuse an `--out` that is not a regular file (a FIFO,
  a device such as `/dev/null` or `/dev/stdout`) or a symlink loop, before the first model call.

- **Every `repo compare` interval now inverts the sign-flip test** — a row's 95% interval was a
  percentile bootstrap (gate, score) or a t interval (the other measures) next to a sign-flip p, and near
  0.05 the two disagreed. It is now, for every measure, every shift the row's sign-flip test does not
  reject at the uncorrected 5% level (exact up to 16 changed tasks, Monte Carlo above with the draws of p,
  made once from the seed, so the ends are the same on every run), and it excludes 0 exactly when p is
  below 0.05. Under a shift tied tasks count too, so both ends are bounded from 6 tasks on and open at 5
  or fewer: `delta.lo` / `delta.hi` are then `null` in `--json` (before, only a single task had `null`
  ends) and print as `-inf` / `+inf`, in `repo compare` and in the `--ledger` text. There, a non-zero end
  that 3 decimals would print as `0.000` prints at 2 significant digits (`+0.0002`), so an interval that
  excludes 0 no longer reads `[+0.000, +0.000]`; in `repo compare --json`, `delta.mean` stays inside the
  ends (six deltas of `4e-5`: mean `0.0001`, not `0.0`). Each `--json` row, and each rule result of the ledger,
  names its method, `delta.method` `sign_flip_inversion` (`null` for a rule result with no pairs); the ledger's ends
  stay at 4 places, as before. `interval_p_disagree` now fires only where
  Holm's correction lifts a gate or score row's p across 0.05, and its note says the interval is
  uncorrected and `p_holm` decides the row. Printed p, `p_holm`, `mde` and `p_holm_floor` do not change.

- **`ci95_added` resamples atoms rather than probes** (`ci_cluster: "atom"`). Paraphrases of one atom are correlated, so the old interval was too narrow. It needs at least 5 atoms: a run of 4 atoms with 3 paraphrases each got an interval before and gets `null` now. The new intervals are wider, so `ci95_added` from older cells is not comparable with new ones; re-run those cells to compare. A subject scorecard cell now carries `ci_cluster` next to `ci95_added`, null for an older artifact.
- The `shuffle_text` docstring no longer cites the study's shuffled-arm win, because that arm injected more files than `full` did.
- `prompts_sha256` now also covers the two context preambles, so its value differs from earlier runs.

- **Input refusals in `catalog`, `catalog eval`, `catalog probe`, `tasks`, `explore`, `subjects`, `inputs`, `tools`, `novelty` and `baseline regression` print `Error: <message>`** — the ones that printed the bare message now go through the same path as every other refusal; the rest already printed the prefix. The exit code is still 2.

- **Catalog listings, the replay task builder and one-skill checks move under their groups (#94 M3)**:
  `catalog list|show --kind product` replaces `library list|show` (and the `product` alias), and
  `catalog list|show --kind subject` replaces `subjects list|show`. `eval tasks generate --from replay`
  replaces `repo tasks replay` (the same flags, `--threads` included). `skill check SKILL --root ROOT`
  is new: the `catalog check` findings for one skill. Every old path still answers, but it prints
  `moved: dokimi <new argv>` to stderr and exits 2 without running anything. Move the scripts to the
  new paths before the next release.

  | Before | After |
  |---|---|
  | `library list` | `catalog list --kind product` |
  | `library show NAME` | `catalog show --kind product NAME` |
  | `subjects list` | `catalog list --kind subject` |
  | `subjects show PATH` | `catalog show --kind subject PATH` |
  | `repo tasks replay` | `eval tasks generate --from replay` |
  | `product list`, `product show`, bare `library` | `catalog list --kind product`, `catalog show --kind product`, `catalog` |

  `novelty routing` and the `docs` group (`run`, `compare`) are not moved yet.

- **`backends --json` output is an object, not a list.** The rows moved from the top level to
  `data.rows`. Read `data.rows` instead of the array.

- **Novelty calls ask for 16384 output tokens when `--max-tokens` is not given** — the
  4096 default that litellm sends to Bedrock-hosted Claude cut long extraction and
  judge replies. An explicit `--max-tokens` is sent as given.
- **An answerer pair whose answer is cut at the ceiling is errored** — a reported
  `finish_reason` `length` marks the pair errored and drops it from the paired summary, rather
  than scoring the cut text. Rerun with a higher `--max-tokens` to keep those pairs.

- **Tool-preference confidence intervals come from the shared bootstrap.** `tool_preference` `ci95` now resamples with
  `dokimi.eval.stats.bootstrap_ci` (integer seed, the same stream as the McNemar and grounding intervals), where it used
  its own RNG stream. The resample count (2000) and the output shape are unchanged, but the bounds move: the RNG stream
  changes, by Monte Carlo noise (in the third decimal, about 0.005 at most in the cases checked). Artifacts written
  before this change keep their old bounds; a re-run gives the new ones.

- **`catalog check` finds near-duplicate descriptions in far less time on large catalogs** — the
  `description-near-duplicate` pass compares only the skill pairs that can reach its threshold
  (a prefix filter over the description words), instead of every pair that shares a word. Findings are
  unchanged. On a synthetic 5000-skill catalog with a realistic word tail the pass went from about 16 s to under 1 s; nothing to do on upgrade.

- **One `+` splitter for arm specs** — `--arms` treatment specs and `repo run --arm`
  specs now share one splitter (`dokimi.treatments.specs.split_parts`). Accepted
  specs are unchanged; the internal parsers that split on `+` are no longer duplicated.

- **Input refusals in `eval personas validate` and `eval personas discover`, `subjects`, `dashboard generate`, the `--python-env` and `--typecheck-tool` refusals of `repo scan`, and `repo skill-reach` print `Error: <message>`** — the ones that printed a `✗`, `⚠` or `Error loading ...` line now print the same `Error:` line as every other refusal, and `Error loading subject|novelty artifact|subject work: X` becomes `Error: cannot load subject|novelty artifact|subject work: X`. The exit code is still 2.

- **The docker CLI is spelled in one module** — every `docker` command the package runs is built by
  `dokimi/docker_cmd.py` (binary, step and build timeouts, mounts, `-e` values, task-container
  hardening). No flag, default, argv or exit code changes: `--build-timeout` and the network policy
  behave as before.

- **`novelty --judge2` is refused when it is the first judge's model** — a second judge that is the same
  model as `--judge-model` (by the same model-identity rule as the answerer check, so an equivalent API
  id and a Bedrock id count, and a CLI alias counts on a CLI route) now exits 2 before any call, unless
  `--allow-self-judge` is passed. Its agreement with the first judge is not an independent second opinion.

- **Arms significance and its interval come from one sign-flip test** — every paired
  arms result (`arms run` per arm and per interaction contrast, `report model-compare`
  and the with/without eval report) now takes `p` from one two-sided sign-flip test
  on the per-question deltas and its 95% interval from inverting that test, as
  `repo compare` rows do, so the interval excludes 0 exactly when `p < 0.05`. The
  interval is uncorrected; Holm or BH corrects `p` into `p_adjusted`.
  In `arms.json` the `significance` and `interactions` blocks drop `p_ttest`,
  `p_wilcoxon` and `n_wilcoxon` and add `p` and `n_changed` (the pairs whose delta
  is not 0); `ci_method` is `sign_flip_inversion`. `arms.v1.json` is widened in
  place, so an `arms.json` written before still validates, and the arms report
  still renders it (its p column falls back to `p_ttest`, marked `†`, with a note
  under the table that the record predates the sign-flip test). In the arms report a
  non-zero Δ, interaction or interaction-table arm delta that one decimal would print as
  0.0 prints at 2 significant digits in fixed point, as the interval ends do (`+0.04`,
  not `+0.0`; `+0.00001`, not `+1e-05`). In `arms.json` a mean under 0.005 keeps 2
  significant digits too, in `delta_mean` and in an interaction's `delta_a`,
  `delta_b`, `delta_combined` and `additive_expectation`, so an arm's Δ reads the
  same in both tables.
  Verdicts near 0.05 move, almost always toward significant: the old rule (t test
  *and* Wilcoxon below 0.05) was conservative, about 3% false positives at a
  nominal 5% on simulated judge deltas, against 5% now for deltas symmetric about 0 (skewed deltas of mean 0 reject 7 to 10% at 10 to 20 pairs). A constant shift now gets
  a p and a verdict (eight +20 deltas: p 0.0078) where it had none; Cohen's d_z
  stays undefined there. Fewer than 6 pairs now get a p too: `significant` is
  `None` where the floor `2 / 2**k` over the k changed pairs is not below 0.05,
  and both interval ends are open (`null`) at 5 or fewer pairs.
  `report model-compare`'s table has one sign-flip p column and a changed-pairs
  column instead of the t and Wilcoxon columns, and its `t_stat` is gone. Its delta and
  Cohen's d_z cells print a non-zero value that one decimal would print as 0.0 at 2
  significant digits in fixed point, so eight +0.04 pairs read `+0.04` beside `OK Yes`, not `0.0`.
  `sign_flip_p`, `min_sign_flip_p`, `ShiftTest`, `sign_flip_interval`, `within`
  and their constants (with one `ALPHA`) moved from `dokimi.repos.compare`,
  `dokimi.repos.compare_interval` and `dokimi.repos.compare_reach` to
  `dokimi.eval.sign_flip`: code that imported them from `dokimi.repos` imports
  them from there. `repo compare` output is unchanged.
  In the Python API, a `confidence` other than 0.95 now raises `ValueError` where
  it was accepted, since the interval inverts the test at 0.05: in `paired_test`
  and `arm_significance` of `dokimi.eval.stats` and in `interaction_test` and
  `interaction_significance` of `dokimi.eval.interactions`, for any row with 2 or
  more pairs. `dokimi.eval.stats.MIN_PAIRS_FOR_TEST` is gone, and so is the
  `HAS_SCIPY` of `dokimi.report.model_compare` and `dokimi.report.eval_report`
  (`dokimi.eval.stats.HAS_SCIPY` stays).

- **Input refusals exit 2 with the same `Error: <message>` line** — the refusals that still printed a `✗`, `Error building arms/plugins`, `Error loading matrix run inputs`, `Error computing plugin delta` or a bare message now go through one path: in `arms`, `baseline`, `benchmark`, `eval coverage`, `repo`, `report`, `run`, `tools` and the `eval` commands that read questions or judge sheets. A failed `--out` write in `repo validate`, `repo scan`, `repo inspect` and `repo budget` still prints a `✗` line and exits 2. The arms messages read `Error: cannot build arms/plugins: ...`, `cannot load matrix run inputs: ...` and `cannot compute plugin delta: ...`.
- **Runtime failures and failed checks exit 1, not 2** — `repo hits` on a batch another `repo run` holds, and `tools preference` when every call failed.
- **`tools preference` exits 3 when a budget stops the run** — `--max-cost-usd` reached, or a call with no price, keeps the partial artifact and exits 3 (`EXIT_INCONCLUSIVE`, the run-incomplete code in `docs/quickstart.md`) instead of 2.

- **`runs list --json`, `runs show --json`, `trace inspect --json`, `trace consistency --json` and
  `trace prefilter --json` print a `cli_output.v1` document** (#94 F2). The payload moved from the top
  level to `data`: read `data` where you read the object or list before. `status` is `partial` when a
  trace or session could not be read (exit 1), when a trace or session has skipped lines or records, or
  when `runs list|show` could not read entries under the root (exit 0), and `failed` when nothing was read; a `trace` leaf that refuses its
  input (no traces, a bad `--arm`, an unreadable contract or outcomes file) prints `Error: <message>` and
  exits 2, with `error.code` `input` under `--json`. `runs list|show` do the same
  when `DOKIMI_RUNS_DIR` names a root that does not exist (a missing default root is not an error for
  `runs list`, which exits 0; `runs show` exits 2). A crash exits 5 with an `infra` error
  document. The other `--json` leaves still print their own payload until their follow-up PRs.

- **The static docs checks move under `docs` (#94 M3)**: `docs check` replaces `run` (the benchmark
  spec run and snapshot, same flags and default paths) and `docs compare` replaces `compare` (the
  snapshot diff, same flags). Both old names still answer, but they print `moved: dokimi docs check
  ...` or `moved: dokimi docs compare ...` to stderr and exit 2 without running anything. Move the
  scripts, CI steps and Makefile targets to the new paths before the next release. The names `run` and
  `compare` stay free for the loop verbs in the release after this one.

  | Before | After |
  |---|---|
  | `run --spec S --out-json J --out-md M` | `docs check --spec S --out-json J --out-md M` |
  | `compare --base A --candidate B` | `docs compare --base A --candidate B` |

  `novelty routing` is not moved yet.

- **`example_pass_rate`, and with it the document and summary `score` and the soft
  gate, rise for documents with `python` examples in the `docker` backend** — those
  examples ran for the first time with the fix above, so a document whose python
  examples pass scores higher for the same inputs than before the upgrade, and a soft
  gate that failed on them can now pass. Compare such scores with runs made after it,
  not with older baselines.

### Removed

- **`dokimi.eval.stats` no longer re-exports the interaction tests** — `detect_factorial_cells`, `interaction_test` and `interaction_significance` are imported from `dokimi.eval.interactions`, where they are defined. The re-export made `stats` and `interactions` import each other. Results are unchanged. If your code imports these names from `dokimi.eval.stats`, import them from `dokimi.eval.interactions` instead.

- Dead code in the answering path: the unused `Answerer.llm` client and the unreachable
  "no documents" branch of `_generate_with_docs`.

- **`dokimi.cli_flags.CANONICAL` and `dokimi.cli_flags.BANNED`** — the two module-level
  lookup tables (spelling to concept name) are gone, with the `_index()` helper that built
  them. Nothing read them: the flag contract test builds its own index from `CONCEPTS`. No
  command, flag or output changes.

- **`dokimi.utils.get_llm`, `dokimi.utils.bedrock_litellm_model`, `dokimi.llm.ChatOpenAI` and
  `dokimi.llm.ChatAnthropic`.** The answerer, judge, persona and question generators and question
  validator call `dokimi.llm.LLM` for every provider; their requests, cached replies and usage
  records do not change (#229 P1-2). `ChatOpenAI(model=m, api_key=k)` becomes
  `dokimi.llm.Invoker(dokimi.llm.LLM(m, "openai", k))` (`"anthropic"` for `ChatAnthropic`), which
  sends the same request. A direct `get_llm(provider, m)` call becomes the same line, but the route
  and key then follow `dokimi.llm`'s table: an unknown bare Bedrock name is no longer replaced by
  `$ANTHROPIC_DEFAULT_SONNET_MODEL`, OpenRouter has no 2000-token cap, Bedrock uses the key it is
  given, an openai model not in litellm's openai table goes out as "openai/<model>", an unreadable
  "file:" key raises instead of being sent as text, usage records the model and provider instead of
  the route and `litellm`, and replies
  `get_llm` cached are not found. In place of `bedrock_litellm_model(m)`,
  `dokimi.llm.resolve_model(m, "amazon-bedrock")` gives the route under the same table.

### Fixed

- **Qualified and negated figure verdicts** — the wrong-figure cap in
  `dokimi.eval.rubric.is_wrong_figure()` now reads a verdict that ends the claim after a
  qualifier list or trailing clause, such as `x: misleading/wrong` or
  `x: reasonable, not wrong`. Such claims were unread before, so a figure the
  judge called wrong did not cap `correctness` at 40. `not right` and
  `not correct` now cap too, while a negated intensifier (`not entirely correct`,
  `not entirely wrong`) hedges and stays unread. A `/` or `,` is not a separator on its own
  (`x: see /wrong`, `x: see docs/wrong` and `64 bytes, wrong` stay unread;
  only slash-separated tokens right after the separator may qualify, as in
  `misleading/wrong`). After a qualifier list or comma, alternatives such as
  `right/wrong` stay unread, and adverbs other than intensifiers
  (`hardly wrong`, `possibly wrong`) leave the claim unread. Without a qualifier
  list, `_VERDICT` reads `x: right/wrong` and `x: hardly wrong` as on main.

- **A withheld task-run comparison keeps its exclusions** — when only one
  `(task, repeat)` cell is shared, McNemar still cannot run (`p` stays null),
  but the comparison record now carries `n_excluded`. Before, that case
  returned no record, so cells each harness attempted alone disappeared and
  the report looked like no comparison.

- **Harbor-backed cells are graded from Harbor's `result.json`** — `codex`,
  `claude-code` and `terminal-bench:<agent>` now read the trial's
  `<run-dir>/<job>/<trial>/result.json` instead of `harbor`'s exit code and
  stdout. Harbor exits 0 for a trial that raised, so an agent install or
  environment failure used to be recorded as a pass. Now a trial with
  `exception_info` (top-level or on a step of a multi-step task), a trial with
  no reward in [0, 1] in `verifier_result.rewards.reward`, or no trial file at
  all is an errored cell (`metrics.error`), out of `pass_rate` and the paired
  test. The exception is not an error when it is a `VerifierTimeoutError` and
  nothing else: that trial is a scored 0 (`metrics.verifier_timeout`) that stays
  in `pass_rate`, since a verifier that overran its timeout is a failure, not
  missing data. The reward otherwise comes from that file only, never from
  Harbor's printed `Reward` table. Pass rates of earlier Harbor-backed runs
  that had errored trials were too high; rerun to compare.

- **`repo run` tokens now cover the same agents as its cost.** `result.json`
  took `input_tokens`/`output_tokens` from Claude Code's parent-only `usage`
  while `cost_usd` covered subagents too; tokens are now summed over
  `modelUsage` when the harness reports it. The agent record gains
  `cache_read_tokens`, `cache_write_tokens`, `usage_scope` (`tree`, `main` or
  `null`) and `subagent_calls`, and an `events.jsonl` event a subagent made
  carries `agent`. Token totals of earlier batches where the agent delegated
  are lower than they should be; re-run those cells to compare them.

- **`<placeholder>` words in commands** — `bazel test //pkg/<algo>:tests`,
  `make <target>` and `pre-commit run <hook>` were split at `<` and `>` as
  redirections, so a cut label (`//pkg/`) was `unresolved` and `>` was looked
  up as a make target or hook id. A `<placeholder>` is now a word to fill in,
  like `$VAR`, and is not looked up.
- **Starlark and Python fences nested in a list item** kept the item's
  indentation, so their `load()`, macro and keyword-argument claims were
  skipped. Such a fence is now checked as it would be at the top level.

- **`repo reach` writes the profile's `config_files`** — only `repo run` wrote them, so a profile whose endpoint or hooks live in a config file (a Codex `config.toml` naming a provider) probed a different setup than it ran.
- **A profile's `trace` is part of the execution id** — a profile edited only in its trace converter kept its id, so `repo run` reused cells graded from another converter's answer and read events; a `claude-stream-json` trace adds no key, so Claude Code ids are unchanged.
- **A trace record holding U+2028, U+2029 or U+0085 is one record** — the Claude Code, Codex and Copilot converters split their JSON-lines stdout with `str.splitlines`, which also breaks at those characters, legal unescaped in a JSON string; such a record was dropped as not JSON, and an answer in it was lost.

- **An unscored arm stays out of `baseline save` and `report generate`** — a
  question whose context or baseline aggregate is `null` or non-numeric is left
  out of the baseline summary (as in the trust gate) and of the report's
  top/bottom delta tables, which raised `TypeError` on a bool aggregate.
- **A binary record with `scores: null` keeps its scores** — it was read as an
  arms record with no arms.
- **An explicit `null` score wins over a legacy inline score** — an arm whose
  entry under `scores` is `null` no longer takes the legacy
  `evaluation.aggregate_score` written inline for that arm. A failed arm stays
  in the arms list: the arm judge writes it as an unscored row, and
  `non_baseline_arms` still counts it as a treatment that produced nothing.
- **`baseline save` refuses a malformed eval file before writing** — it no
  longer leaves a baseline file on disk with no manifest entry.
- **Reports read the question of a panel eval file** — `eval score panel` writes
  each question as `question`; `report generate` and `report eval` read only
  `question_text`, so their tables (and the `report eval` Q&A sidecar) showed
  blank questions for a panel run.
- **`report generate` reads a panel run with a fully failed question** — when
  every panel vote of a question failed, the panel writes `aggregate: null` and
  the report raised `TypeError`; the question is now left out of that arm's
  figures, as the other reports already did.
- **`eval questions review` reads an answers file** — it read `question`/`text`,
  but answers records carry `question_text`, so it reviewed no questions.
- **`report model-compare` reads a legacy arms file with a failed arm** — an arm
  payload of `null` raised `AttributeError`; that question is now skipped, as
  one with a missing score already was.

- **A boolean task reward is not a score** — `summarize_task_results` no longer
  folds `reward: true` into `avg_reward` as 1.0, or `false` as 0.0. A bool is a
  flag, not a measurement, the same rule the cost total already uses. A numeric
  reward is unchanged.

- **The agent loop accepts tool-call arguments that are already parsed** — a
  provider that returns `function.arguments` as a dict no longer aborts the
  loop with a `TypeError`. A non-object value is recorded as `malformed_args`,
  and a call with a null `function` is recorded as an unknown tool. The
  assistant turn replayed to the model names that call `unknown`, not null.

- **claude-code starts a repo's MCP servers** — the stock profile passed
  `--strict-mcp-config` without `--mcp-config`, so a repo that ships
  `.mcp.json` ran with no MCP tools. It now passes `--mcp-config .mcp.json`
  when that file is present and `--tools all` is used (`--tools none`/`read` do not
  limit MCP tools, so they start no MCP server).
  This shifts baselines: a repo that ships `.mcp.json` now starts its MCP
  servers under `--tools all`, so its results differ from earlier runs of the
  same repo and arm. Compare only batches run after this change.
- **A malformed `.mcp.json` is caught up front** — `repo validate` warns (and still succeeds) about one that is not a JSON object (or whose `mcpServers` is not) at a task base (a file that is not UTF-8 text included), and `repo run --tools all` exits before any cell, instead of every cell failing as a task failure when the CLI rejects the file; a `patch:` arm that writes one is a cell error.
- `check_arms` refuses, before any cell, a base that tracks `.claude` or `.claude/skills` as a symlink.
- A `guidance:none` arm can install a skill that the base ships under the guidance globs, because the arm drops those files first.
- A `SKILL.md` `name` that YAML reads as an impossible date is not a crash.

- **A `---` look-alike on the first line no longer opens frontmatter** —
  `parse_frontmatter` now requires the opening fence to be exactly `---`, the
  same rule it applies to the closing fence. A document that starts with a
  thematic break such as `-----` and has a `---` line later keeps its leading
  text in the body. An unterminated block with a leading BOM is returned
  without the BOM, like every other branch.

- **repo-eval: a hung oracle leaves a verdict** — the Summary and Upload steps of
  `repo-eval.yml` run with `always()`, and a job cancelled by `timeout-minutes` before
  `check.json` exists records a `timeout` verdict for the task. The self-check
  (`repo-eval-self.yml`) now also runs on changes to `commands/repo*.py`,
  `schemas/repo_*.json`, `requirements.lock.txt` and `pyproject.toml`.

- **Nothing measured is not a pass** — gates that read an empty or all-errored result as green now exit 1 with an `incomplete` line and the counts. `eval check` and `repo run` refuse an export with no task (`repo run` crashed with an IndexError on one); `eval check` exits 1 when no task passed the self-check, even with `--allow-unsupported`; `repo run` exits 1 when no cell of the batch has a result, and its summary line now counts unsupported cells. `eval judge wording` reports a condition with no scored pair as NOT MEASURED (with errored-pair counts) instead of "No rewrite moves any variant", and `--strict` exits 1 on it. `eval judge agreement` labels an arm `NOT MEASURED` when no voting measure compared a single question, and `--strict` exits 1 on that or when no arm was compared. `run --strict` exits 1 when no document was measured. Partial errors change no threshold.

- **`PATH@REV` takes a revision with a slash** — `repo scan .@origin/main` (and `refs/heads/x`) read the whole spec as a path and failed with "not a directory"; a branch with `/` is now a revision for every `repo` command that takes a source. A suffix that is no revision (`a..b`, `x//y`, trailing `/`) still stays in the path, and an existing local directory with an `@` in its path (`ws/job@2/repo`) is read whole.
- **`eval check` and `repo run` progress is flushed per task** — under a pipe (CI) the per-task lines were block-buffered and all appeared at exit unless `PYTHONUNBUFFERED` was set.

- **The isolation probe of `eval check` writes its task list to a `mktemp` file** and removes
  it on exit, instead of a fixed file in `/tmp` that it left behind. Two probes on one machine
  no longer share the file, and a file another user owns no longer breaks the probe.

- **`novelty routing --catalog` with a `skills.yaml` counts a mirrored skill once** — a catalog
  that lists the same `SKILL.md` bytes under two paths (a plugin copy of the `skills/` tree)
  used to put the name in the field twice, and the run was refused as naming a competitor more
  than once. The copy nearest the catalog root stands for the card; the others are named on stderr.

- **`novelty routing --queries-from-headings`** no longer reads a `##` line inside a
  fenced code block as a request, so a shell comment does not become a query.
- **`novelty routing` with no usable query** says why and suggests `--queries`, and
  writes the result with `"status": "no_queries"` (the reason is in `reason`), where it
  used to exit with a bare "no usable queries". A successful result carries `"status": "ok"`.
- **Every `novelty routing` rate line** prints its `n_queries`, and a self-derived rate
  carries `triage: self-derived` on the same line. A run that scores fewer than five
  queries prints a warning, since one query moves a rate by 20 points.

- **`catalog scan` names broken YAML frontmatter** — a `SKILL.md` whose frontmatter is not
  valid YAML (a bare colon in `description:`, say) is reported as a `frontmatter-parse`
  error with the YAML message, and exits 1. The scan showed only "missing a description"
  before. `parse_frontmatter` is unchanged for other callers.
- **`catalog scan` without a `skills.yaml` no longer stops at the first broken skill** — each
  `SKILL.md` under the root is an entry, so a skill that does not load is an error row and
  the rest are still reviewed. A `SKILL.md` under `template/` or `templates/` is still left out.

- **Skill discovery skips templates and counts a mirror once** — `discover_skills` (behind the
  `skill-catalog:` arm, `eval triggers generate`, `catalog scan` without a `skills.yaml`, and
  `novelty routing --catalog`) used to load every `SKILL.md` under the directory. A `SKILL.md`
  under a `template/` or `templates/` directory is a scaffold, not a skill, and is now skipped.
  A `SKILL.md` byte-identical to another under the directory (a plugin's copy of the `skills/`
  tree) and loading under the same skill name is offered once: the copy nearest the root stands
  for it. Without a frontmatter `name`, identical text in two differently named directories is
  two skills. Same-named skills whose text differs are still both offered.
  `discover_skills(root, notes=[])` lists each one it leaves out; the `skill-catalog:` arm
  prints each of those lines to stderr, and the other callers do not print them.

- **A judge rerun no longer pays for an unparseable first reply** — with
  `DOKIMI_RESPONSE_CACHE` on, only a reply that parses is stored, so a rerun of an
  answer whose score came from a retry asked the first attempt live again on every
  run before the stored retry replayed (at pinned sampling the provider repeats the
  unparseable reply, so this never healed). The single judge and each panel judge
  now look up the stored entry of every attempt before any live call: a rerun of
  an already-scored answer makes 0 provider calls. Cache keys are unchanged, so
  existing caches stay valid and get the saving at once.

- **Nothing measured is not a pass (second batch)** — `tasks run` exits 1 as `incomplete` when every cell errored (the artifact and report are still written). The novelty gate's status is `NOT MEASURED`, not `PASS`, for a cell with no check measured. `run` refuses with one line and exit 1 when a selected document does not load, instead of a KeyError or OSError traceback. `baseline compare` says "Nothing to compare (0 scored)" when no question is scored on both sides, and its "No notable changes" line counts the questions compared; the snapshot `compare` report says "Nothing to compare (0 scored)" instead of "No regressions" when either snapshot has no document.

- `context consistency`: shell control syntax (`for`/`do`/`done`, `if`, subshells, comments) is no longer a step; a shell edit matches an `edit` action, and a command whose output is saved to a file (`pytest > test.log`) does not; an empty arm is "no runs"; malformed `--outcomes`/`--contract` files end in a clean error; an outcome key shared by two traces is refused; a trace given twice to one arm (a repeated `--arm`, overlapping globs) counts once.
- `repo compare --path-stability`: an empty trace is unmeasured, not a steady path, and a deeply nested line no longer aborts the comparison.
- `context consistency`: a Bash line is read with quotes, backslashes, `$(...)`, backticks, `&` and `case` kept right, and `timeout`, `nohup`, `nice`, `sudo`, `env` and `VAR=1` are not the program, so `find -exec ... \;` and `timeout 300 pytest` are one step each; a step's action and `command` text lose the `for`/`do`/`if` words; a shell edit behind a prefix (`sudo sed -i`) is an edit; `pytest; echo done` and `pytest || true` hide the check's status like a pipe; a run with tool calls and no `result` event counts as failed; `tool_calls_*` count calls, not steps; a scalar or nested rule argument or `tool` is a clean contract error.
- `repo compare --pass-k` / `--path-stability`: tasks the measure does not apply to (`pass^K` of a score task) are not listed as unpaired, and tasks with too few cells are one line with a count.
- `context consistency`: `&&` and `;` inside `[[ ... ]]` and `(( ... ))` no longer split a line; a `word)` is a case label only inside `case ... in`, so `(cd build && make) 2>&1 | tail -5` is a `make` step; `command -v pytest` is not a test run; a trailing `&` and a loop body hide the status for `last_ok`, as does a later `||` or `;` after `pytest && ...`, and `set -e` / `set -o pipefail` count only as a `set` command from where it runs (to the end of a `( ... )` subshell), not from a heredoc body, a comment or a later command; a list regex in a contract is an alternation and a non-regex value is refused; a token count that is not a finite number is skipped, traces without usage give no `output_tokens_cv`, and a one-run strategy has no inner distance and does not count as valid diversity.

- **`eval check --warm` judges task `host` requirements** — before, `--warm` skipped the
  host verdict, so on a host that misses a task's requirements the untouched-tree null run
  could pass and the task was reported `broken` instead of `unsupported`, and
  `--allow-unsupported` could not pass it. The probe is the same container view (affinity,
  cgroup CPU quota) with or without network; `repo run` already judged it.

- **A response-cache entry of the wrong shape is a miss, not a crash** —
  `load_response` already treated unparseable JSON as a miss, but an entry that
  parsed into a list, a number, a numeric text, a non-object `usage` or a
  `usage` value of the wrong type (a string token count, a null call count)
  raised `TypeError`/`AttributeError` (or replayed a non-string answer) and
  ended the run. A `null` text, which a reply without content is stored as, still
  replays. A wrong-shape entry now reads as a miss and the call is made again.

- **`eval judge agreement`, `eval score pairwise`, `eval score checklist` and `eval judge wording` exit 2
  on an unreadable input file** — a missing or malformed `--answers`, `--pairwise`, `--checklist`
  or `--validity` file, or one that is not a JSON object (or lacks the `verdicts`,
  `questions` or `summary` section `eval judge agreement` reads from it), now prints
  `Error: ...` naming the file and exits 2 (bad input, not a failed gate)
  instead of ending in a Python traceback.

- **`dataset export`** prints `Error: cannot export <input> to <out-dir>: <reason>` and
  exits 2 when the input file is missing, is not JSON, fails schema validation, lacks
  the record array for its `--kind` (with `--no-validate`), or the output directory
  cannot be written, instead of ending in a Python traceback. For `answers`, `eval`,
  `arms` and `task_runs` the input may also be any JSON value that is not an object.
  `dokimi.datasets_export.flatten` and
  `dokimi.artifacts.load_artifact` raise `ValueError` for these inputs, where
  `flatten` raised `KeyError` or `TypeError` before.

- **`report generate` exits 1 with a message on an unreadable eval file** — a
  missing path, a file that is not JSON or not UTF-8, JSON that is not an
  object, or an `evaluations` value that is not a list now prints
  `Error: ...` naming `--eval` instead of a Python traceback.

- **`baseline save`, `list` and `compare`** exit with a message naming the file, not a
  traceback, when the eval file, the saved baseline or `manifest.json` is not valid JSON,
  is not a JSON object, or cannot be read, and when `manifest.json` holds a `baselines`
  value that is not a list or holds an entry without a string `name` and `path` or with a
  `summary` that is not an object (`save` used to leave an orphan baseline file behind). A
  manifest with no `baselines` key is treated as empty. `save` and `list` exit 1; `compare`
  exits 2, since 1 is the `--strict` verdict; that includes a missing `--eval` file, which
  used to exit 1.
- **`baseline compare`** treats a `run_metadata` that is not an object, or a
  `panel_config.models` that is not a list of strings, as a model the artifact does not
  record (named on stderr as not compared) instead of raising a traceback.

- **`eval questions analyze`** and **`eval questions review`** exit 1 with
  `Error: cannot read questions from <file>: ...` when the questions file is not
  valid JSON, not UTF-8 or a directory, instead of a Python traceback.
  `eval questions analyze` did the same for a missing file.

- **`eval triggers generate`** prints `Error: --questions <path>: <reason>` and exits 2 (bad
  input, not a failed gate) when the `--questions` file is missing, a directory, not valid
  JSON, not UTF-8 or nested too deeply, instead of ending in a Python traceback. The read
  goes through a shared `read_json_input` helper whose `InputError` `cli.main` reports the
  same way for any command.

- **repo run: uptake of a root-level file** — a bare `lint` at the start of a
  command no longer counts as a run of the arm's `lint` file, since a bare name
  resolves through `PATH`; it counts as `./lint`, under the workspace path, or
  after an interpreter.

- **`repo inspect` lists a `.cursor/rules/*.mdc` below the root** — a rule
  file such as `web/.cursor/rules/style.mdc` was not listed in
  `instructions`. It is now a record with `directory` `web`, and with scope
  and activation `unknown`: Cursor's rules docs cover only the root
  `.cursor/rules` (and nested `AGENTS.md`), so whether Cursor reads it is not
  known from the docs. Its note says so.

- **The novelty report no longer shows an unchecked 0 as a clean one** — when
  the `harmful_atoms` gate check reads a count of 0 conflicting atoms as
  `not measured` (the conflict check compared fewer pairs than
  `min_conflict_coverage`, the atom set was capped, or `conflict_coverage` is
  malformed), the Markdown report `novelty analyze` writes now says why in
  place of the `Conflicts first` line, and its `harmful_conflicting` row reads
  `not measured (0 found)`. Before, it printed `| harmful_conflicting | 0 |`
  next to a gate row that said `not measured`.

- **Two `docker-*` cells of one task in one process no longer share a
  container name** — the name `dokimi-run-<task>-<pid>` gets a random suffix,
  so one cell's cleanup cannot remove the other's container.

- **Vendored skills no longer load** — skill discovery (the `skill-catalog:` arm and the `catalog` commands) does not walk `.git`, `node_modules`, `site-packages`, `.venv`/`venv`, `.tox`/`.nox`, `.hg`/`.svn` or `__pycache__` below the catalog root, so a dependency's copy of a skill is no longer offered as a skill of the catalog.

- **`eval`, `report`, `answers`, `benchmark` and `evaluate`** exit with a message on an unreadable or invalid input file or an unwritable `--output`, instead of a traceback.

- **`novelty` subcommands** exit with a message on an unreadable or invalid `--placebo-text`, `--probes`, `--atoms`, `--novelty`, `--queries` or `--artifact`, or an `--atoms` file whose `bundle` is not an object, instead of a traceback.

- **`eval questions`, `eval personas`, `library` and `subjects`** exit with a message on a missing, unreadable or unparseable input file, and those commands, `dashboard` and `ingest docling` on an unusable output path, instead of a traceback. `library` now exits 1 on a missing `--registry` or `--intents` file, where it used to log a warning and print an empty result.

- **`run`, `compare`, `arms`, `tasks`, `explore` and `repo`** exit with a message instead of a traceback on the unreadable, non-UTF-8 or corrupt spec, snapshot, result, plan, task and question files their tests cover, and on an unwritable output. This covers a deeply nested `--manifest-file`, `--static` or `--harness` file, an unreadable or non-UTF-8 `.agent-eval/repo-eval.yaml`, a `tasks run --tasks` name that matches no task, and an `explore` task file with an invalid YAML timestamp, a bad `lines` range or a `repo_root` that is not a string.
- `run` and `compare` exit 2 on a bad spec or snapshot (a non-numeric summary value, an empty or absolute `golden_manifest` include pattern, or fewer documents than its `min_docs`) and exit 1 when an output cannot be written, as `arms` does. An error inside a metric is no longer reported as bad input. `tasks` error messages use the `Error:` prefix.
- `repo report` and `repo compare` exit 1 on a result file without a usable `grade`, `agent`, `task`, `arm` or `model`, naming the file. A grader score that is NaN, infinite or beyond 1e150 is recorded as a grader failure, and a batch that already stores one is read the same way. A `process` counter or `grade.json` metric that is not finite or beyond 1e150 is left out of the means, as a non-number is, instead of ending in an overflow traceback.
- `repo run` records `agent.num_turns` as null when the harness reports anything but an integer, and notes it in `agent.trace_errors`; a finished run with such a value still counts as started.

- **`tools preference` and `catalog`** exit with a message on a non-UTF-8 `--groups` file, an unwritable `--out`, `--items-out` or `--out-json`, an unreadable or wrong-shaped manifest, a malformed `SKILL.md` under `--catalog`, or a task directory without `instruction.md`, instead of a traceback.

- **Ingest and discovery**: `chunk_text` no longer ends with a chunk that only repeats the overlap, `discover_markdown` skips vendored and VCS directories, intents with a null `products` or `name` are listed, and a directory named `SKILL.md` is not a skill.

- **`dokimi eval inspect` no longer stops on one malformed file** — a promptfoo config with
  `tests: 5`, a dokimi questions file with `"questions": null`, a promptfoo tests file with a
  non-string row key, or an explore task with `lines: [.inf]` raised and ended the scan, so no
  other file under the path was reported. Each file is now reported on its own and the scan
  goes on.

- **`novelty probe` and `novelty shorten`** exit with a message on an `--atoms` file whose spans, atoms, `extraction` or `bundle` have a missing or wrong-type field or whose JSON is nested too deeply to parse, and `novelty view` does the same for deep nesting, instead of a traceback.

- **`eval judge agreement` exits 2 on a `--pairwise` or `--checklist` entry of the wrong shape,
  before any judge call** — a verdict whose `score` is not a finite number (missing, null,
  a string, a bool, NaN, infinite or past a float's range), an arm that is not an object of
  verdicts, or a checklist question without a `question_id` (or with a null or bool one),
  without an `arms` object, or with a `coverage` that is neither a finite number nor null
  now prints `Error: <file>: <entry> ...` and exits 2 instead of ending in a Python
  traceback or passing a bad value on. The entries are read with the file, before
  `--judges` makes its first paid call; until now a wrong shape ended the run after those
  calls.

- **Windows `process.run` closes the child's stdout and stderr pipes at EOF** and after an
  interrupted launch, instead of leaving them for garbage collection (`ResourceWarning`).

- **`mcp:http=` doc sources work with mcp 2.x** — the MCP-protocol client
  imported `streamablehttp_client`, which mcp 2.x removed, so with the `[mcp]`
  extra resolving to mcp 2.x every `http` doc source failed with an
  `ImportError`. It now uses `streamable_http_client` (mcp 1.24+ and 2.x),
  falls back to the old name on older 1.x, and accepts both the 1.x
  three-stream and 2.x two-stream shapes.

- **A verifier sidecar whose removal times out no longer replaces the verdict** — on a
  `docker-*` run with an allowlisted verifier, a `docker rm` of the `-verify` sidecar that
  timed out after `test.sh` turned the computed reward into a TIMEOUT cell (exit 124, reward
  0), and the sandbox teardown did not know the sidecar, so it stayed on the run's network.
  The verifier's verdict and `metrics.network.verify_egress` now stand, the timeout goes to
  `run.log`, and the teardown retries the removal before it drops the network.
  The same holds for a `-verify` sidecar whose start failed and whose removal then timed
  out, and the next run with the same id removes a `-verify` sidecar a crashed run left,
  before it creates the network.

- **The next-step hints name the `dokimi` command, and the `arms run` example in a baseline-only report runs as written** — `dokimi eval personas discover`, `dokimi eval personas validate`, `dokimi baseline list` and `dokimi baseline compare` used to print `python cli.py …` as the command to run; they now print `dokimi …`, the console script, with the same arguments. The example `arms run` command in a model-comparison report that has only baseline arms now carries the `--product` and `--questions` that `dokimi arms run` requires, in place of a `[other parameters]` placeholder. No command, flag or output changes otherwise.

- **`dokimi eval questions generate --validate` no longer sends `OPENROUTER_API_KEY` to a provider other than OpenRouter** — with
  `--provider google-vertex`, the question validator passed the OpenRouter key as the Google key, so validation used the
  wrong key. `OPENROUTER_API_KEY` is now used only for `openrouter`, as the judge and the question generator already do.
  `google-vertex` uses `GEMINI_API_KEY`: set it, or Vertex credentials, or the validation call fails and each question is
  kept unscored. No command or flag changes. If you validated with `google-vertex` while `OPENROUTER_API_KEY` was set,
  rotate that OpenRouter key.

- **Significance after Holm or BH is decided on the unrounded adjusted p** — the
  verdict (`significant_adjusted`) of the arms report, the interaction contrasts
  and the model-comparison report was tested against alpha on the 4-decimal
  rounded `p_adjusted`, so an adjusted p just under 0.05 showed as 0.05 and read as
  not significant. It now uses the exact value, as the cross-judge Holm and the
  harness McNemar verdicts already did. `p_adjusted` still displays 4 decimals.

- **`report eval` computes the delta from unrounded means** — the Delta was the difference of the
  two arm means after each had been rounded to one decimal, so it could be 0.1 away from the
  difference of the unrounded means. It is now the unrounded difference rounded once for display,
  so it can move by 0.1 in the Summary, Breakdown and Difficulty tables. The arm means themselves
  print as before. Where questions are unpaired, the Count cell now shows them, and a note says the
  means are over the complete pairs.
  The win rate and the degradation rate in Key Observations divide by the complete pairs, not by
  every question, so they can rise for a file with an unpaired question.

- **`repo scan` no longer fails a path the repository's `.gitignore` ignores** — guidance
  that names generated or ignored files had each such path reported `fail` as a rename,
  because its parent directory exists and the file is never tracked. A missing path, directory
  (with or without its trailing slash) or glob that a tracked `.gitignore` ignores is now
  `unresolved`, and the reason names the `.gitignore` line. The rules follow gitignore(5), negation and nested files included;
  `.git/info/exclude` and `core.excludesFile` are not part of the snapshot and are not read.
  Some paths an earlier scan reported `fail` are now `unresolved`, so the fail count and the L0
  gate result can differ from a scan of the same repository made before this change.

- `baseline compare` now treats a blank, non-string or case-variant "Unknown" provider or model, and a panel member without a `provider/` prefix, as not recorded, for every run: the "not compared" warning appears and `--strict` exits 1 where it used to pass.
- A command harness with no reward file records its exit code as reward 1.0 or 0.0, so `tasks run` counts it as scored; exit 126, 127, signal exits, Windows crash statuses (NTSTATUS error codes) and a reward file with no reward in it are no verdict, even with a `reward: x` on the output: the cell is errored and stays out of `pass_rate` and the paired test. `avg_reward` of command harnesses now includes these exit-code cells, so it does not compare with older `task_runs` artifacts.
- `tasks run` exits 2 before running when a task or harness is listed twice, including one task under two spellings and two tasks with the same directory name.
- Undecided judge-validity checks visibly report INCONCLUSIVE, with the existing default and strict exit policies.

- **`--provider openai` reaches an OpenAI-compatible endpoint with a model litellm does not
  know as OpenAI's** — such a model (`glm-5.3` behind `OPENAI_API_BASE`, an `org/name` id
  such as `meta-llama/Llama-3.1-8B`, a model newer than the installed litellm) was sent bare,
  and litellm refused it ("LLM Provider NOT provided") before any request. Such names now go
  out as `openai/<model>`, on every path that takes the openai provider: answers, the judge
  and panel, `eval judge agreement --judges openai/<model>` (#136), whose arms read NOT MEASURED
  before, and question and persona generation. A name litellm takes for OpenAI's
  (`gpt-6.1-sol`, `o3`, a `ft:gpt-4o-mini:…` fine-tune), or one with a litellm provider prefix
  (`azure/…`, `ollama/…`), keeps its route. Every name keeps its response-cache entries: on
  provider openai the key holds the model name as given, not the route, so it does not move
  with this fix or with litellm's price map (the remote one or the installed backup, which
  can disagree on a new model). The double-prefix workaround (`openai/openai/<model>`) still
  works.
- **`--provider openai` no longer hands a bare name to another cloud with the OpenAI key** —
  a bare name that litellm assigns to another provider (about 450 in litellm 1.106: `claude-*`
  to Anthropic, `amazon.nova-pro-v1:0` and the other Bedrock ids, `gemini-*` to Vertex, Cohere
  and AI21 names) went to that provider with `OPENAI_API_KEY` as its key; Anthropic and
  Bedrock refuse it, so such a run worked only while `OPENAI_API_KEY` was unset. It now goes
  to the OpenAI endpoint like any other name litellm does not know as OpenAI's. To reach those
  clouds, use `--provider anthropic`, `amazon-bedrock` or `google-vertex`.

- **`repo scan` checks `pytest --pyargs` module names as modules** — `pytest --pyargs pkg.tests`
  names a package, not a path, so every such claim was `unresolved` ("tried pkg.tests, ...").
  With `--pyargs`, a dotted name that is no path in the tree is now a `pytest_module` claim:
  `ok` when `pkg/tests.py` or `pkg/tests/__init__.py` is below some directory of the tree
  (present in the tree, not checked to import), `fail` when the tree holds a package the
  name lies in but not the module (`pkg.tests.test_a` with `pkg/tests/` gone is a miss in
  `pkg`) and the package sits at the root or in `src/`, of the tree or of a directory above
  the guidance file, and `unresolved` when it holds no such package (an installed one) or
  holds it only deeper (`docs/examples/pkg/`). A `::test` node after a module is checked in
  the module's file, the copy at an import root when the tree holds more than one. Without `--pyargs` a dotted name stays a path.

- **`eval import` leaves a blank judge assert, an empty `contains*` list
  and a punctuation-only Inspect `exact` target out of the grading** — a
  promptfoo `llm-rubric`/`factuality` (or other judged) assert with a blank or
  missing value, or a `contains*`/`icontains*` assert with an empty list,
  imported as a check that `eval score reference` refuses, which aborted the run for
  every question of the file; it now imports as `unsupported`, listed and not
  counted, like a blank value already did. An Inspect `exact` target that
  SQuAD-normalises to nothing, such as `...` or `the`, imported as a value no
  answer can equal; it is now dropped, and a sample left with no target
  imports as `unsupported`.

- **`report generate` exits 1 with a message on an eval file that holds no
  evaluations** — `{}`, `{"evaluations": []}` or `[]` as `--eval` now prints
  `Error: --eval <path> holds no evaluations` and writes no report, instead of
  ending in a `ZeroDivisionError` traceback. This holds for `--format json`
  too, which used to exit 0 and write a report of zero stats. A pipeline run
  that judges no answers now shows `n/a` in the report's failure-analysis Rate
  column instead of failing the same way.

- **`catalog trigger-probe` no longer reports an empty probe as a run** — a
  `--trigger-set` with no labelled query (a questions set without `expected_skill`,
  or an empty set) ran nothing, wrote `"answers": []`, printed `"summary": {}` and
  exited 0. It now exits 2 with `Error: --trigger-set has no labelled queries`
  before any run, and writes no `--out` file. When runs happened but Claude Code
  listed no skills in any of them (an older Claude Code, or every run failing before
  its init event), `not_listed` stayed `[]` without a word; a warning on stderr now
  says `not_listed` was not checked. The output JSON and the exit code of that case
  are unchanged.

- **Stack P no longer ignores `top_k`** — `Answerer` passes `max_results=top_k` to the doc
  source; the context arm's retrieval request is now the one the arms stack makes.
  `answers generate --top-k` takes a positive integer, as `dokimi evaluate` does, and a config
  `retrieval.top_k` that is not one exits 2.
- **Library id from the registry** — `EvaluationPipeline` resolves the library id through
  `resolve_library_id_for_product` (explicit id, then an `mcp:` source's `;id=`, then `context7_id`,
  then the client), in the
  registry that `benchmark run` and `benchmark batch` loaded from `--registry`. A registered
  product without `context7_id` now fails for a client that needs a declared id.
  The question step's topics cache (`.cache/topics`) is keyed on the doc source as well, so
  two doc sets of one product (two `local:` dirs, say) no longer share their topics.

- **`eval judge agreement` says why a `--judges` judge left answers unscored** — a
  failed judge call used to drop the answer and its error, so a judge with no
  API key, an unknown model or an unreachable endpoint gave `NOT MEASURED` and
  exit 0 with no reason anywhere. Each measure with unscored answers now logs
  one warning, `<judge> (<kind>): N of M answers unscored, first error: ...`,
  and the report keeps the same counts under `unscored` (JSON) and as a note
  under the table (Markdown). A judge that scored none of its answers is a
  failed run: after the report is written, `eval judge agreement` prints an `Error:`
  line and exits 1, with or without `--strict`, as `eval score pairwise` does when
  every comparison fails. A judge that scored some answers keeps exit 0.

- **Empty replies and content-less replies are not cached.** Neither is stored in or replayed from the response cache, so a later run asks the model again.
- **`delta_vs_baseline` is always present.** It is `null` when no question has both arms scored, instead of missing.

- **"Same model" means one thing in `baseline compare`, the self-judge warning and `repo compare`** — they
  used two rules that disagreed. A dated id (`claude-sonnet-4-5-20250929`) and its undated alias, a Bedrock
  regional profile (`eu.anthropic.…`) and its bare id, and the same model behind a route
  (`anthropic/`, `openrouter/anthropic/`, `bedrock/`, `vertex_ai/`) or a `-v1:0` revision are now one model
  everywhere; two different dated snapshots are still two models, and case no longer matters. Effects: the
  self-judge warning fires when the answer and the judge are the same model on another provider too; `baseline
  compare` no longer reports or refuses (`--strict`) a judge or answer that only changed its route or region;
  `repo compare` pairs arms by the same rule. A dated snapshot against its undated alias is the exception in
  `baseline compare`: the alias does not record which snapshot answered, so it is named on stderr as not
  verified and `--strict` refuses it, as before. Scores are not recomputed, but a comparison
  that used to refuse or warn about such a pair may now pass.
  `dokimi run` stores `run_metadata.evaluator_independence_warning` by this rule too, as `dokimi eval` does (it
  was an exact provider-and-model match, so the printed warning and the stored flag disagreed).
  A route is a provider or gateway prefix; the organization of an `org/name` id is part of the id, so
  `meta-llama/X` and `myorg/X` are two models, but a known vendor written as a path segment, as OpenRouter does
  (`qwen/X`, `z-ai/X`), is the vendor of Bedrock's `qwen.X`, `zai.X` and is dropped like it. Only a known vendor before the first dot is dropped (`anthropic.`,
  `openai.`, as in Bedrock ids), so a custom name such as `alice.model` stays its own model, and an explicit
  `huggingface/` route keeps the organization after it (`huggingface/google/X` is not `X`). So a bare requested name answered by an org-prefixed id (`X` answered by `myorg/X`)
  now gets the `repo compare` and `repo report` model-mismatch warning, which it did not before. Only a
  Bedrock ARN (`arn:aws:bedrock:…:inference-profile/`) loses its path, so `huggingface/foundation-model/X` is not `X`.

- **A capped novelty run says how far the cap reached** — extraction stops at
  the atom cap in document order, so a capped run measures the input up to the span
  the cap stopped at; the input is the text that was segmented, which leaves out
  the bundled files a run did not select. The cap warning of `novelty analyze` now names the span it
  stopped at, the input's spans up to it and their share of the input's
  tokens, instead of calling the atoms a sample, and `novelty atoms` now
  prints it too; `extraction.reached` records the same in `novelty.json` and
  `atoms.json` (`null` when the cap did not bind); the `novelty.md` header
  states it; and on a capped run the headline's `coverage:` line says the
  readable words went into the input and the capped atoms come from the first
  N% of the input's tokens, instead of saying they were measured (as does the
  warning printed under it when half or less of the artifact's readable words
  went into the input). A cap met on the input's last span says instead that
  the atoms reach it and any atoms past the cap in the last reply were dropped, in the warning, the header and the
  `coverage:` line. The tier forfeit (`added` is a rate over the atoms the
  cap kept, not the whole input) and the `harmful_atoms` gate reason (0
  conflicts among the capped atoms) no longer call the capped atoms a sample
  either, and hold however far the cap reached.

- **The dashboard and `report model-compare` average each arm over the complete pairs, as
  `report generate` does** — the dashboard's per-library and per-source context and baseline
  means, the context and baseline columns of the `report model-compare` Overall Summary and
  its ranking, and the context column of its per-difficulty rows, used to average each arm over
  every question scored in it, so the means could cover different questions than the delta
  beside them. They now
  cover only the questions scored in both arms, and the dashboard's delta is the difference of
  those means rather than the mean of the stored per-question `delta`. For a run with a question
  scored in one arm only, the arm means change, and on the dashboard the status band that
  follows the context mean can change too. A run that scored one arm only still shows that
  arm's mean; a run, source group or difficulty row with both arms scored but never on the same
  question shows no means.

- **`repo scan` says why a scan is `incomplete`** — the text output used to print a missing
  `also_scan` file like any glob that matches nothing, and `--fail-on-fail` exited 1 with
  `fail 0` and no message. A scan that is incomplete now prints an `incomplete: ...` line after
  the warnings, with the warnings that make it so; `--fail-on-fail` prints them on stderr as
  `✗ scan incomplete: ...` when it exits 1 on such a scan; and `repo_readiness.v1` lists them in
  `incomplete_reasons` (`[]` when the scan is complete). A complete scan prints what it did
  before. The guidance-only manifest in `docs/reviewing.md` no longer lists
  `also_scan: ["CONTRIBUTING.md"]`, which made every scan of a repository without that file
  incomplete.

- **An unreadable `result.json` no longer ends `repo run`** — a rerun of a
  batch with a cell whose `result.json` was truncated or not UTF-8 stopped the
  whole batch with a bare JSON or decode error, and one holding a JSON list
  refused the batch as another execution (`unrecorded`). In a cell of a task
  this host cannot run, a truncated or non-UTF-8 one was instead counted as
  already done, even with `--force`. That cell is now logged as `✗ <cell>: <path>: ...` with
  how to recover (remove the file, or `--force` where this host can run the
  task), the other cells still run or are reused, and the command exits 1 as
  for any other crashed cell. The file is left as it is.

- **`repo scan` no longer fails or leaves unresolved a GitHub Action named without `@ref` that the
  workflows use** — guidance such as "Every `actions/checkout` sets `persist-credentials: false`"
  had `actions/checkout` checked as a path: `unresolved`, or `fail` in a `.github/AGENTS.md` once
  the repository holds local actions under `.github/actions/`, which failed `--fail-on-fail`. An
  `owner/name` (or `owner/repo/path`) that the tree does not hold, outside a submodule, is now
  `ok` when a job or step of a `.yml`/`.yaml` workflow directly in `.github/workflows/` (not a
  nested one), or of a local composite action under `.github/`, uses it as
  `uses: <name>@<ref>`, and the reason names that file (`used by .github/workflows/ci.yml`). A
  name whose directory the tree holds at the repository root (`tools/gone` beside `tools/`) is
  still judged as a path, so a stale path still fails. A name only in a `run:` script does not
  count, and an Action no step uses gets the verdict it got before. A workflow PyYAML cannot
  build (a date that does not exist, `!!bool maybe`, `!!timestamp foo`, deep nesting) is skipped
  here and leaves a Python range naming it `unresolved`, instead of aborting the scan.

- **`repo scan` resolves a bare glob like a bare file name** — a glob with no `/`, such as
  `type_dispatch*.hpp`, was tried only at the guidance file's directory and its ancestors, so it
  was `unresolved` ("matches nothing in the tree; a build may create it") while the bare name
  `type_utils.hpp` next to it was `ok_basename`. It is now `ok_basename` too when it matches the
  name of some file or directory in the tree, with the count in the reason ("bare glob; 3
  match(es) in 2 directories, e.g. inc/utils/type_dispatch.hpp"). A bare glob that matches no
  name stays `unresolved`, with the same reason and `.gitignore` check as before.

- **Pairwise judge reprompts an unparseable reply at pinned sampling** — with
  `temperature` or `seed` set, a redraw of the same prompt returned the same
  non-answer, so three identical replies lost the verdict. `PairwiseJudge` now
  takes `pinned=True` for that case and adds a reminder to re-draw in the
  required output format before each redraw (its own text, not the JSON reminder
  of the single and panel judges). The `eval` CLI does not set it yet, so its
  runs are unchanged. Unpinned judges are unchanged.
- **Pairwise and checklist reruns replay stored redraws** — a rerun of a
  pairwise or checklist judgement now replays the stored redraw instead of
  asking the first draw live again. Stored replies are not changed; the rerun
  makes fewer requests.

- **`explore list` and `explore run --all` refuse a tasks root with no task, exit 2** — with no
  `--tasks-root` and an empty bundled `data/explore_tasks`, or with a `--tasks-root` that is missing
  or holds no YAML, `explore list` printed nothing and exited 0, and `explore run --all` exited 1
  with a message that did not name the flag. Both now print to stderr what was searched, name
  `--tasks-root`, and exit 2. A missing bundled root (an installed package) exited 1 and now exits 2.
  `explore run --tasks <id>` for an id no root holds, and `run` with no selection, also exit 2
  (they exited 1). A malformed, unreadable or duplicate-id task file exits 2 for `list` and `run`
  too (it exited 1). A bundled root that has tasks still lists them with no flag.

- **A non-finite gate metric no longer passes its gate** — a repository task whose `gate` names a numeric metric (`{"metric": ..., "min": ...}`) counted a cell as a success when its `grade.json` held `NaN` or `Infinity` for that metric, since no comparison with NaN holds and an infinity clears any minimum. Such a cell is now a failure (`success: false`). `repo validate` now refuses a task whose `gate` minimum is `NaN` or infinite, and a `score.max` past `1e150` (the largest score `repo report` reads), which used to make `repo report` exit 1 on the whole batch. A `timeout_s`, `budget_usd` or `max_turns` written as an integer past the float range is reported as a problem instead of crashing the check, and so is a reference-check `weight` or `threshold` of that size. Cells recorded before this change keep the result they were recorded with; a re-run cell is graded again.

- **`catalog scan` on a root with no `SKILL.md` exits 1 with `no SKILL.md found under <root>`**
  instead of exiting 0 silently. With `--kind all`, a tasks-only root only warns, and `--strict`
  makes that a failure. A root with a `skills.yaml` that has no skill that can be scanned says
  `skills.yaml under <root> has no skill that can be scanned` instead.

- **A query that names a skill left out for `disable-model-invocation` names it.** `--all-vs-all` used to refuse it with only a generic hint about `--include-disabled`; the message now names each such skill, and each one left out for a spec error or a name conflict, with its reason.
- **`--queries-from-headings` names its source.** The result records `queries_source` (`headings`, `none` when no heading gives a query, or `null` for `--queries`). A skill with no usable heading still exits non-zero with the reason; its description is not used as a query.
- **`--estimate-only` without `--model` reports no query sent** (`n_queries_sent` 0), as it makes no call.
- **A failed model call's message says what it leaves out.** Failed calls are left out of the model arm's scoring; a query with a failed repeat is also left out of the repeat-noise rates. The partial result keeps the calls that succeeded.
- **`--seed` help says what it fixes.** It seeds the card order only; it does not make the model's answers repeatable, which is what `--repeats` measures. The help no longer calls the run reproducible.

- **`repo budget` reports a nested `AGENTS.md` as `off` when its own directory holds a
  `CLAUDE.md`, `CLAUDE.local.md` or `.claude/CLAUDE.md`** — Claude Code 2.1.316 does not read
  `sub/AGENTS.md` beside `sub/CLAUDE.md`, also when it reaches `sub/` lazily from the root, but
  `repo budget` counted it as `lazy`, along with its `@` imports. It is now `off`, its imports are
  not counted, and a warning names the `CLAUDE*` file that turns it off. It stays `lazy` when one
  of those files is a link to it or a loaded file `@`-imports it, or when the same file is also
  reached as an `AGENTS.md` in another directory, and when it does not resolve (a dangling or
  out-of-tree link keeps `lazy` and only the not-readable warning). A `CLAUDE*` file in a parent below the root or
  in a deeper directory does not turn it off; a root one still turns every `AGENTS.md` off. The rule applies only with a profile that sets the new optional
  `guidance.claude_displaces_agents` key of `harness_profile.v1`, which only `claude-code` does: a
  tree with a root `CLAUDE*` file, and the Codex and Copilot profiles (Copilot's guidance names
  list `CLAUDE.md`), get the same budget as before. The `claude-code` profile's `guidance.notes`
  state the measured rule.

- **A lead-in line no longer trips the reach prose warning** — `repo reach` no longer counts, in a scenario's `prose`, the answer's first non-empty line when it has at most 12 words, ends in `:` and a later line holds a token (`Now I'll list all verification tokens found in my context:` followed by the tokens counted 10 words and printed "answered with 10 words besides tokens" although every token was reported). Any other words still count: a sentence after the tokens, a lead-in with no token after it, a first line not ending in `:`, a first line of more than 12 words (an explanation or a refusal ending in `:`).

- **`repo reach --dry-run` plans the scenarios a run executes** — the dry run and the `--only` check planned from
  every probed path, a link the run cannot follow included, so `sub/AGENTS.md -> nowhere.md` listed `cwd=sub`,
  `read=sub` (and `shadow=sub`) though a run lists that link under `unresolved` and probes nothing in `sub/`. Both
  now plan from the run's own list, so the dry run leaves those scenarios out and `--only read=sub` is a usage
  error there (exit 2). The dry run's `files` still lists every probed path. A tree whose every guidance file is
  such a link (`AGENTS.md -> nowhere.md` alone) made the dry run list `cwd=.` and exit 0 while a run stopped with
  `no guidance file to probe`; the dry run and the `--only` check now stop with the run's message and exit code (1).
- **`repo reach --dry-run` refuses what a run refuses before its first harness call** — the dry run printed its
  plan and exited 0 where a run stopped before calling the harness: a selection that leaves no scenario (every
  shadow path already in the tree, exit 2), `--format receipt` over a link to `reach.json` (2), a tree with no
  guidance file, a guidance file above the workspace, a guidance file that is not UTF-8, a tree path the workspace
  cannot hold, and a shadow path under a probe file (1). The dry run now makes the same checks, building the
  workspace and removing it, and stops before the harness's version command, with the run's message and exit code.
- **`repo reach` with a `--work-dir` it cannot use fails with a message, not a traceback** — a `--work-dir` that is
  missing, not a directory or not writable raised `FileNotFoundError`, `NotADirectoryError` or `PermissionError`
  from `tempfile.mkdtemp`, and a write the workspace refused while it was seeded (a full disk) raised `OSError`, in
  a dry run and a run alike. Each is now a `✗ cannot make the probe workspace in --work-dir <dir>: <reason>` (or
  `cannot build the probe workspace <dir>: <reason>`) line and exit 1.
- **`repo reach` on a tree with no guidance file names that cause** — with a selection that leaves no scenario on
  such a tree (`--scenarios cwd`: no subdirectory to start in), a run and a dry run stopped with
  `no scenario selected` (exit 2). The check for a guidance file now comes before the scenario selection, so both
  stop with `no guidance files to probe` and exit 1, as a run with the default scenarios does.

- **`repo budget --harness copilot` (and `copilot-review`) no longer reports a root `AGENTS.md`
  as `off` beside a root `CLAUDE.md`** — the rule that a root `CLAUDE.md`, `CLAUDE.local.md` or
  `.claude/CLAUDE.md` turns every `AGENTS.md` off is Claude Code's, but `repo budget` applied it to
  any profile whose guidance names list `CLAUDE.md`, and Copilot's do; Copilot CLI 1.0.91 reads
  both files. The root rule is now gated, as the same-directory rule already was, on the profile
  key `guidance.claude_displaces_agents`, which only `claude-code` sets: with any other profile no
  `AGENTS.md` is `off` and the "turns AGENTS.md loading off" warning is gone, so
  `repo budget --fail-on-warning --harness copilot` on a tree with a root `CLAUDE.md` and an
  `AGENTS.md` exits 0 where it exited 1: a CI gate on that exit code passes now. `@` imports are now
  followed only for a profile that declares an import syntax (`guidance.imports`, `@path` for
  `claude-code`): `codex`, `codex-review`, `copilot` and `copilot-review` declare none, so no file
  an `AGENTS.md` or `CLAUDE.md` imports is counted for them. `claude-code` budgets do not change.
  `repo receipt` gives an expected state only for `claude-code`, so its output does not change either.

- **A capped novelty run indexes its spans and writes `atoms.json` from every
  extracted atom** — under `--max-atoms` or a tier's cap, `novelty analyze`
  and `novelty atoms` used to keep only the cap's spread sample after
  extraction, so a span whose atom fell outside it read as carrying nothing:
  200 one-atom spans at `--tier standard` left 140 of them dim, with
  `frac_carrying_nothing` 0.70, dead weight a shortening could cut, and no sole
  carrier among them. Spans (`atom_ids`, `frac_carrying_nothing`,
  `n_atoms_indexed`), span labels, dead weight, sole carriers and shortening's
  structure now come from all M atoms, and `spans.capped_atom_set` is `false`
  on new runs; faithfulness, the probes, the conflict check, carrier
  confirmation and the shortening oracle still run on the n sampled atoms, so
  the cost stays at the cap. A shortening candidate that keeps no span of an
  unsampled atom counts it in `dropped_unscored_atoms` and is not `safe`, so
  `shorten --out` never writes one (without `--allow-unsafe`); the candidate
  list still shows it, marked not `safe`. `atoms.json` holds every atom and lists the
  sample in `sampled_atom_ids`: `novelty probe` and `novelty shorten` measure
  that sample by default and say so, and `--all-atoms` measures every atom in
  the file, reusing the extraction, with a stderr warning counting the added
  atoms whose faithfulness is `unchecked`. A file without the record (written
  before it) reads as every atom sampled; one that names an atom it does not
  hold exits 2. When such an older file comes from a cap that bound
  (`extraction.sampled` set, or any `extraction.reached`) or may have bound
  (`cap` set, no `extraction.reached`, and at least `cap` atoms),
  `novelty probe`, `novelty shorten` and `novelty view` warn on stderr that
  it holds only the probed atoms (or, for a cap met at the last span or a
  file without `reached`, may hold only those), so a shortening can cut
  spans whose atoms fell outside them, and to re-run `novelty atoms`. The `harmful_atoms` gate check still does not clear a 0 on a
  sampled run, now read from `extraction.sampled`, and the `dead_tokens`
  check judges a capped run's share. The cap no longer forfeits
  `frac_carrying_nothing`, and the shortening forfeit says `knowledge` and
  `value` are retention of the sample rather than that a cut looks free.
  `probes.json` records `sampling` (`atoms` probed, `of` the file's atoms,
  `all_atoms`), so a rate over the sample is not read as the file's.

- **The judge, persona and question generators and the question validator route and key like the
  answerer** — on Bedrock, Vertex and OpenRouter they used a provider table of their own, and it
  differed from the answerer's:
  - A bare Bedrock name off the profile table (`claude-sonnet-4-6`) was replaced by
    `$ANTHROPIC_DEFAULT_SONNET_MODEL`, so the judge ran a model other than the one its record
    names. Bedrock runs no such name. As a judge, `arms run`, `arms matrix-run`, `subjects run`,
    `evaluate` and `benchmark run`/`batch` now refuse it with exit 2 before any answer is paid for,
    as they do a dotted one (`claude-sonnet-4.5`, which is no Bedrock id either), and the error
    names the fix: the inference profile id (`us.anthropic.claude-sonnet-4-6`) or an
    ARN. The commands that judge stored answers send it as given, and every judge call fails; with
    `--judge-temperature`, `eval pairwise`/`checklist`/`judge-wording`/`cross-judge` and `eval
    judge-validity --mode pairwise` refuse it with exit 2 first, as a temperature litellm does not
    list for it. The persona and question generators and the validator send it as given.
  - An explicit `api_key` was ignored on Bedrock: only `AWS_BEARER_TOKEN_BEDROCK` was read. It now
    wins, as on every other provider.
  - An unreadable `file:` key was sent as the literal `file:...` text. It now raises
    `Cannot read API key from file ...` before any request.
  - OpenRouter output was capped at 2000 tokens. It now has no ceiling of its own, as the answerer
    there never did.
  - A model name that starts with a litellm route passes through, as for the answerer: on
    `google`/`google-vertex`, `gemini/gemini-2.5-pro` goes to Google AI Studio instead of
    `vertex_ai/gemini/...`, and on Bedrock `bedrock/...` is no longer doubled to `bedrock/bedrock/...`.
    A Vertex Model Garden name (`meta/llama-3.1-405b-instruct-maas`) keeps its `vertex_ai/` route;
    the answerer sent it without one, which litellm cannot place, and now adds it too.
  - The `vertex_ai` provider name sent no key. It now reads `GEMINI_API_KEY`, as `google-vertex`
    does. The answerer sent it `OPENAI_API_KEY`, and now reads `GEMINI_API_KEY` too.
  - The answerer routed a Bedrock ARN without its `bedrock/` prefix, so litellm could not place
    it. It now gets the prefix, as the judge always did.
  - On Bedrock, a `/` name with another vendor's route (`gemini/...`, `openai/...`) gets
    `bedrock/` too, so `AWS_BEARER_TOKEN_BEDROCK` goes only to Bedrock. The answerer sent it to
    that vendor's route, with the Bedrock token as its key.

  Response-cache entries these four stored on Bedrock, Vertex or OpenRouter are not hit, because
  the key now includes the streaming flag, as every other caller's does. Those calls are paid once
  more. Usage records name the provider and model, not `litellm` and the route.

- **`benchmark preflight` and the RAGAS judge read a `file:<path>` key as the run does** — the
  answerer and the judge read the key from the file; these two kept their own table of key
  variables and took the variable's value as it was:
  - `benchmark preflight` printed "Preflight OK" when the key variable named a file that is
    missing, unreadable or empty, and every call of the run then failed on it. It now refuses
    with exit 2, as it refuses a missing key: "Cannot read API key from file '…'
    (ANTHROPIC_API_KEY, provider anthropic)" or "ANTHROPIC_API_KEY for provider anthropic names
    an empty key file".
  - The RAGAS step (`answers generate --ragas`, `eval score ragas`) sent the `file:…` string itself
    as the key, from the environment or from an explicit `api_key`, so every RAGAS call failed
    authentication. That was the judge's key, and also `OPENAI_API_KEY`, which ragas uses to embed
    for `answer_relevancy` whatever the judge. Both now send the file's contents; the embeddings
    keep ragas's default model, `text-embedding-ada-002`. A key file that is unreadable or empty
    now ends the RAGAS step before any RAGAS call, with "RAGAS evaluation failed: Cannot read API
    key from file …" or "… names an empty key file", like any other RAGAS failure. In the Python
    API, the fallback judge that a provider other than openai and anthropic gets reads its key
    file too.
  - A `file:` key that is not UTF-8 is reported as "Cannot read API key from file '…'" too, by
    `benchmark preflight`, the RAGAS step and the answerer; it raised a bare decode error that
    did not name the file.

- **Shell reads the converters dropped where the shell could not have moved are now `read`
  events, and shell functions no longer keep a read at the wrong directory or of a command that
  did not run** — `cd $(printf sub; true) | cat; cat AGENTS.md` and its `&` and backtick forms
  (the substitution's own `;` no longer ends the piped or backgrounded part), `source
  .venv/bin/activate; cat AGENTS.md` (a virtualenv's activate script alone, also with a redirection
  such as `> /dev/null`, is assumed to run no `cd`), `case $x in a) echo case;; esac; cat AGENTS.md` (a `case` opens only as a command word),
  `time cat AGENTS.md`, `time -p cat AGENTS.md`, `time X=1 cat AGENTS.md`, `X=1 time cat
  AGENTS.md` and `command cat AGENTS.md` now read `AGENTS.md`. A `case` pattern's `)` no longer
  ends a `$( )`: `v=$(case x in x) echo hi; cat INNER.md;; esac); cat AGENTS.md` reads only
  `AGENTS.md`, and a `cd` in that arm no longer loses the directory. `f() { cd sub; }; f; cat AGENTS.md`
  read the root's `AGENTS.md`; a call of a function whose body names `cd`, `pushd`, `popd`,
  `eval`, `source` or `.` (these two as a command, not of an activate script: `git add .` names
  none) now loses the directory as an `eval` does; a function an earlier Claude
  Code Bash call defined is not carried to the next, which starts in a new shell. A definition whose `{` is on a later line (`f()`, a newline, `{ cd
  sub; }`) no longer runs its body's `cd`: it is only defined. A call of a function named like a
  printer (`cat() { echo hi; }; cat AGENTS.md`, `\cat AGENTS.md` and a body in parentheses,
  `cat() ( echo hi )` or `function cat ( echo hi )`, too) is no read, while `command
  cat` and `env cat`, which run the program, read the file; one named like a directory builtin (`cd() { ...; }`)
  loses the directory, while `command cd sub` and `builtin cd sub` run the builtin and are followed.
  A function named `command`, `env`, `builtin` or `time` (not the keyword) runs in place of that
  wrapper: `command() { echo hi; }; command cat AGENTS.md` and `command cd sub` read and move
  nothing. `time -x cat AGENTS.md` and `time --help cat AGENTS.md` are no read (the keyword takes
  only `-p` and `--`), and a failed `source 'my env/bin/activate' && cat AGENTS.md` is no read.
  `env command cat AGENTS.md` and `/usr/bin/env command cat AGENTS.md` are no read, and `env
  command -p cd sub` moves no shell: `env` runs a program named `command`, not the builtin. So `guidance_read` and
  `unknown_reads` can change for such traces.

- **A nested repository's own ignore rules now apply to `skill_sha` and `suite_hash`** — in a git work tree, a
  file that a nested repository or submodule inside a skill or suite ignores (its `.gitignore`, `info/exclude`)
  leaves both hashes, as a file the top repository ignores does; its tracked and untracked files still count, and so
  does a file the top repository tracks there. A nested repository git cannot read (a broken `.git` file or directory, a
  corrupt index, an owner git refuses) fails the hash with an error naming its directory, rather than leaving nothing out. A `git hash-object` that fails or times out fails the hash too, rather than
  hashing the file's bytes.
  A file whose name holds a newline, starts with `"` or ends with a carriage return now digests by its blob id
  whether it is committed or edited to the same content (it was hashed by its raw bytes once edited), so it
  digests the same both ways. Either change moves the hash of a tree that holds such files, once.

- **Same-name catalog copies with different content**: `novelty routing` now names the conflicting skills, the directory of each copy and the `--catalog <dir>` or `--keep-copies-under <dir>` to route with, instead of an empty field. A copy at the catalog root is named as such, not as `<catalog>/.`.
- **`--all-vs-all --queries-by-skill` with queries for a skill the field leaves out**: those queries are dropped with a WARNING naming the flag that keeps the skill (`--include-disabled`, `--include-invalid`, `--keep-copies-under`); the run is refused only when no query is left. A name no rule explains is still refused, as a likely typo, and a catalog skill that failed to load is refused with the load error.
- **`--all-vs-all` "leaves out" list**: names only skills the field does not have, so a skill with one copy still in the field is not reported as left out.
- **`--all-vs-all` missing flags**: a missing `--queries-by-skill <file>` and `--out <file>` are reported in one error.

- **`catalog scan` no longer flags a template's placeholder link** — a link whose target is a placeholder, such as
  the `[Title](URL)` of a "you might also find this helpful" line in a skill's reply template, warned `link-missing`
  (`link 'URL' names no file`). A target that is one of `URL`, `LINK`, `PATH`, `URI`, `HREF`, `TODO`, `TBD` (any
  case) or an ellipsis (`...`, `<...>`) is now not checked, in an inline link or a reference definition. Any other
  target, including a real file with no extension such as `LICENSE` or `README`, is still checked, and links inside
  code spans and fences were already not checked.

- **`novelty routing` keeps its paid calls when `--max-usd` stops it** — a
  single-artifact or `--all-vs-all` run that hit the cap (or a call with no
  known price) used to exit with `nothing was written`, losing every call
  already paid for. It now writes `--out` with the calls made scored, a
  `partial` block (`reason: spend_cap`, `calls_done`, `calls_planned`) and
  `status: "partial"`, prints the summary (marked `partial: N of M calls`) and
  `cost` lines, and still exits non-zero. If `--out` already holds a finished
  result, the partial one goes to `<out stem>.partial.json` instead (or the next
  free `<out stem>.partial2.json`, ... when that file already holds a finished result).
  The price-unknown call's reply is scored, not dropped. `skill novelty analyze`,
  `atoms` and `probe`, and `skill shorten`, still write nothing when the cap
  stops them.

- **`novelty routing --out` is checked before any model call** — a directory, a
  path under a non-directory or an unwritable path is refused up front for both
  the single-artifact run and `--all-vs-all`. Before, `--out <directory>` made
  every call and then crashed with `IsADirectoryError`, and nothing was saved. A
  missing parent directory is still created. Pass a file path.
- **A `SKILL.md` with broken frontmatter reports the YAML error** — the message
  names the file line and says to quote a value containing `: `, in place of
  "missing a 'description'". It shows in `catalog check` and in `novelty routing`
  (`--artifact`, `--competitors`, `--catalog`).
- **`novelty routing --estimate-only` prices the reply it measured** — output
  tokens are 65 a call (the average of a measured run, 23.3k over 360 calls), not
  one JSON pick (10). The JSON output has `output_tokens_per_call`.

- **A skill the OS refuses to read is an error row, not a crash** — `catalog check` and the skill review
  no longer stop on an `OSError` (`ENAMETOOLONG`, say) raised while reading one skill. A `SKILL.md` that
  cannot be read after it loaded is reported as an `unreadable` error for that skill; a skill that did not
  load and whose directory cannot be stat-ed gets no review. The other skills are still reviewed.

- **`extract_json_object` always returns an object** — a judge or agent reply
  that parsed to another JSON value (`7`, `[1, 2]`, `"x"`, `null`) came back as
  that value, so the judge's retry on a malformed reply never saw it and the
  call failed with an `AttributeError` at `.keys()`. It now raises `ValueError`
  like any reply with no JSON object, and is retried; so does JSON nested past
  the recursion limit, in `extract_json_array` too. Found by the new fuzzing.
- **Frontmatter with a value that does not fit its tag** — `date: 2026-02-30`,
  a `!!int` with no value or `!!bool x` made `parse_frontmatter` and
  `parse_frontmatter_strict` raise `ValueError`, `AttributeError`,
  `IndexError` or `KeyError`. `parse_frontmatter` now returns `{}` and the
  body, and `parse_frontmatter_strict` raises `yaml.YAMLError`.
- **`json` reference check on deeply nested JSON** — an answer nested past the
  recursion limit raised `RecursionError`; the check now fails with
  `(False, None)`.

- **Judge and conflict replies with no usable row are split and not cached** — a value-judge
  reply whose rows all have a non-boolean `needed` (`"false"`, or missing), or no `n` in
  range, or an empty list for a non-empty batch, used to be cached and counted as skipped. It is now split and re-asked, and the
  cache does not keep it. A row whose `needed` or `scaffolding` is not a boolean, in a reply
  that has a usable row, is counted apart as malformed (`unjudged_malformed`), not as
  skipped. A conflict reply none of whose rows names a pair of its batch is split, not read as
  "checked, no conflicts" (an empty list still means no conflicts). An unusable reply no longer replaces the last transport error in the "calls that failed" cause. Conflict rows that name no pair (a missing `a` or `b`, `a == b`,
  out of range) are dropped and counted in the warnings.
- **A reply with an unreadable statement number no longer aborts the run** — an `Infinity`
  or a number too long to parse, in a conflict or judge row, used to raise and stop the
  stage; now that row is dropped (or skipped) and the valid rows of the reply are kept. A
  bool or a fraction as a statement number is not read as one.
- **Retry warnings give `--max-tokens` only for cut-off replies** — a content-classification
  retry for rows with no usable verdict is worded `re-asked N unusable batches as halves`
  without the `--max-tokens` hint. Extraction uses the same rule.

- **An `mcp:` source's `;id=` now wins over the registry in every command** —
  `answers`, `questions`, `arms run` and `benchmark run` used a registered product's `context7_id`
  and ignored the `;id=` given on the source, so
  the same doc source could query different libraries depending on the command.
  All of them now resolve in one order: `--context7-id`, `;id=`, `context7_id`, then
  the client. Runs that passed `;id=` for a registered product queried the
  registry's library until now; rerun them if you meant the `;id=` one.
- **`arms run` resolves the library id per doc arm** — with several doc arms,
  the first arm's id was used for all of them, so an `;id=` on any later arm was
  ignored and an `;id=` on the first arm leaked into the others. `agent:` doc arms
  and doc arms inside a `+` stack are resolved too, and each doc arm records its
  `library_id`. An `agent:` arm on a product with no id now stops with the same
  error as an `mcp:` arm, instead of querying the product name. An empty
  `--context7-id ""` now counts as unset, as in `answers` and `questions`,
  instead of querying the product name.

- **`repo scan` reads a branch name or pattern the repository configures as a branch, not a
  path** — guidance such as "The `no-commit-to-branch` hook fails on `master` and
  `maintenance/*`" left `maintenance/*` `unresolved` ("matches nothing in the tree"). A path or
  glob that would be `unresolved`, outside a root directory (or a link to one), is now `ok` when a `--branch` name or
  `--pattern` regex of the `no-commit-to-branch` hook in `.pre-commit-config.yaml`, or a
  `branches` / `branches-ignore` filter of `on.push`, `on.pull_request`,
  `on.pull_request_target`, `on.merge_group` or `on.workflow_run` in a workflow, names
  it, and the reason gives the source and its line (`branch pattern of no-commit-to-branch
  (.pre-commit-config.yaml:17)`). A claim ending in a file extension the tree uses
  (`release/CHANGELOG.md`) matches only a name or pattern equal to it. A claim left
  `unresolved` names each of those files the scan did not read (`<file> was not read (<why>)`).

- **A drive-relative tool path stays as written in a converted trace** — when a `copilot-json` trace
  names a path such as `C:foo` (a drive letter, then no separator) and the run started in a
  subdirectory, `events.jsonl` recorded it joined to that directory (`sub/C:foo`), a file that does not
  exist. It now keeps `C:foo` unchanged. A rooted `\AGENTS.md` and a relative `a\b.txt` read as before
  (`/AGENTS.md`, `sub/a/b.txt`).

- **A reply cut at the output ceiling is an error, not a silent bad parse** — a reply with
  `finish_reason` `length` or `max_tokens` now fails the call with "reply cut at the output
  ceiling" (its cost is still recorded). Extraction splits the batch and re-asks, as it does
  for unparseable replies, and a cached cut reply replays as the same error.

- **A Claude CLI alias is the model it runs** — `report` no longer warns that cells run with `sonnet` were answered by `claude-sonnet-…`, and `baseline compare` reports such a model as not verified (not a mismatch). Model identity now has one rule, the CLI-aware one, in `dokimi.utils.same_model`. The novelty default judge now also skips a dated snapshot or alias of the answerer's family. With `--judge-provider anthropic` and an Anthropic answerer, no other default judge runs there, so the run now fails with a `ValueError` (pass `--judge-model`).

- **Panel verdicts need a quorum** — a panel reports a mean, spread and agreement
  only when a majority of its panelists return a valid vote. One valid vote out
  of three is now `insufficient_evidence` (no mean, delta or agreement; the
  `score_answer` result carries an `insufficient evidence` error), and a single
  vote has no spread, so `std` and `agreement_score` are `null` rather than 0 and 1.0.
- **Panel judges see the question's ground truth** — `score_answer` and
  `evaluate_answers` pass the reference passage to every role prompt, as the
  single judge's grounded prompt does, instead of dropping it. Flipping it
  changes the prompt.
- **Panel identity covers the role→model map** — `model` lists each role with
  its model, and `prompt_sha256` also covers each judge's provider, temperature
  and seed, and the aggregation policy. Swapping models between roles, or changing
  the policy, now changes the identity. Panel runs saved under the old identity
  no longer pool with new ones as replicates.
- **A panel cell that fails quorum carries an `error`** — the saved cell has the
  `score_answer` error text (`insufficient evidence: …`, or `all panel judges
  failed`), so readers that count `Score.error` see it. The dashboard's doc score
  now leaves such a question out, as it does for any judge error, where before it
  counted a quorum failure with retrieval missed as a context 0. The eval summary
  prints how many questions it leaves out (`Questions left out …`). Panel artifacts
  saved before this change have no such `error`, so the dashboard reads them as before.

- **`--provider openai-codex` is no longer offered where the run refuses it** —
  `answers generate`, `eval questions generate`, `eval personas discover`,
  `evaluate` and `benchmark run|preflight|batch` listed it, but their answerer
  and generators do not take it: the run stopped on a `ValueError: Unsupported
  provider` traceback (exit 1), `evaluate` after making its output directories,
  and `benchmark preflight` passed a plan `benchmark run` refuses. argparse now
  refuses it (exit 2) before anything runs. The commands that call the model
  through `dokimi.llm` directly (`arms`, `subjects run`, `skill novelty`,
  `skill shorten`, `novelty routing`, `eval questions analyze|review`,
  `eval score panel`, `tools preference`) keep it.

- **`catalog eval` report counts its selection in rows and in names.** The
  `Selected skills` headline counted distinct skill names (1,741 on a catalog of
  2,105 `catalog check` rows), while the table listed every row as selected. The
  headline now reads `N of M rows (K distinct names)`, states the rule (selection is
  by skill name; nested and load-error rows are candidates; rows that share a name
  with an earlier row are selected with it), and gives the excluded rows and why
  (none in a full run; in `--changed-from`, rows whose skill no changed path owns).
  The selection itself and `catalog.json` are unchanged.

- **`eval score panel` output carries the run envelope**: its incremental output is saved through `save_artifact` as an `eval.v1` artifact, so it gets `schema_version`, `run_id` and `dokimi_version` and shows in `runs list`. A save the schema rejects stops the run on the first save, not after the last judge call.

- **A guidance file whose path contains a carriage return is now found** — workspace
  preparation read git's `-z` listings as text, which turns each `\r` into a newline, so
  such a path named no file: a `guidance:rev:` arm did not install it or list it in
  `guidance_latest`, `guidance:none` and `guidance:overlay` did not remove it, a `patch:`
  arm recorded a wrong path, and the base check of a skill arm counted it as shipped. The
  listings are now split on NUL before each path is decoded.

- **A `skills.yaml` or a `benchmark --registry` file with a value PyYAML cannot build (a date such as `2026-99-99`) names the file** — `catalog check` says `invalid skills.yaml at <path>: ...` and the catalog evaluator `invalid YAML at <path>: ...`, as for a YAML syntax error, where they gave PyYAML's bare message. `benchmark preflight`, `run` and `batch` read `--registry` as `catalog list|show --kind product` do: a file that is not UTF-8 or not YAML, or that has such a value, prints `Error: cannot read --registry <path>: ...` and exits 2 instead of a traceback, and a missing path or a directory prints `Error: --registry <path>: not a file` and exits 2. A missing file was read as an empty registry (also exit 2, the target not found or `No libraries to run.`), and a directory was a traceback. A pipe, such as `--registry <(cat products.yaml)`, is still read; one that gives nothing but whitespace, as when the command behind `<(...)` fails, prints `Error: --registry <path>: the pipe was empty (did the command writing it fail?)` and exits 2, for `catalog list|show --kind product --registry` and every `--intents` too, instead of reading as an empty registry. A named pipe that nothing has opened for writing is waited on until something does, as `cat` waits on it. A device, such as `/dev/null` or `/dev/zero`, prints `Error: --registry <path>: not a file` and exits 2, where `/dev/null` was an empty registry and `/dev/zero` was read until memory ran out. A name the system cannot look up (too long, or under a directory without search permission) prints `Error: cannot read --registry <path>: ...` and exits 2, instead of a traceback, as `catalog list|show --kind product --registry` and the `--intents` of `catalog list|show|intents` now do.

- **A YAML file with a date that does not exist (`2026-99-99`), an integer of more than 4300 digits or nesting too deep for the parser is refused like any other invalid YAML, not with a traceback** — PyYAML raises `ValueError` or `RecursionError` for these, not `yaml.YAMLError`. The `--harness` profile (`repo budget`, `repo reach`, `repo skill-reach`, ...), the `repo skill-reach --catalogue`, the `trace consistency --contract`, a benchmark spec read by `python -m dokimi.schema_check`, `library --registry` and `catalog intents --intents`, and a local `config/products.yaml` read by `answers generate` now exit with the message they give for a YAML syntax error. Markdown frontmatter nested too deep is invalid frontmatter too, as a bad date or a big integer already was: `parse_frontmatter_strict` raises `yaml.YAMLError` for it, so `catalog check` reports such a `SKILL.md` as a `frontmatter-parse` error and its row says the file has invalid YAML in its frontmatter (`line unknown: ...`), and a `skill:` arm with one fails with that message, not a `RecursionError`; `catalog formats` falls back to the block's flat `key: value` lines, as it does for a YAML syntax error, so such a skill with a `description:` line is still loadable; the other lenient frontmatter readers read it as having no frontmatter, so `repo budget` now counts its body instead of calling it not readable. `repo scan` reads a `.pre-commit-config.yaml` with any of the three as declaring no hook, and an OKF doc source keeps such a card with its body only.

- **`catalog eval` no longer selects every skill for a tool cache git ignores** — an ignored file under a shared evaluator directory (`tools/`, `scripts/`, `dokimi/`, `.github/`), such as `.pytest_cache` or `.ruff_cache`, was a shared input: it selected every skill and marked the run dirty. Paths git ignores now select nothing there, whatever their name: an ignored file that is not a cache, such as a local flags file, no longer selects skills or marks the run dirty either. An untracked file there that git does not ignore still selects every skill and marks the run dirty. Ignored files inside a skill or task bundle still count, since the bundle hashes them, and select only that bundle's skill, also when the bundle is under one of those directories.

- **`catalog eval` no longer stamps an unrelated repository's commit** — a ROOT that git reaches only by walking up to an enclosing repository that does not track it (a skills folder under a git-tracked `$HOME`) now records `source: {"commit": null, "dirty": null}` instead of that repository's HEAD marked dirty, so its receipt cannot name a commit of the wrong repo. With `--changed-from`, such a ROOT checks every skill. A ROOT at the top of a repository or with tracked files under it is unchanged. No action is needed.
- **No scipy load at start-up** — `dokimi.eval.stats` imports scipy only when a hypothesis test runs. Loading it starts one OpenBLAS thread per core, so every command, `--help` included, spent ~18 CPU-seconds on a 224-core box; `catalog eval --tier static` now takes ~0.5 CPU-seconds. No action is needed.

- **`python -m dokimi.schema_compat` follows schemas moved out of a directory whose name
  contains a carriage return** — it read git's `-z` rename listing as text, which turns
  each `\r` into a newline, so it then asked git for a directory the base does not have
  and stopped with a git error instead of comparing the schemas. The listing is now split
  on NUL before each path is decoded.

- **`repo reach --format receipt` takes budget's start-up rules from the profile, not the harness name** — the receipt gave expected states only when the harness was named `claude-code`. It now applies them when the profile sets `guidance.claude_displaces_agents`, the key `repo budget` already reads, so a custom profile with the key gets expected states and `claude-code` without it gets none. A saved reach file without the profile's declared guidance still goes by the name. `repo reach --from <reach.json> --format receipt` now refuses (exit 2) a profile whose `guidance` differs from the one the probe saved (its free-text `notes` aside), since the expected states come from the profile and the gate from the probe.

- **`repo validate`'s note on tasks whose base holds no file the guidance globs match names the arms it covers** — it said every `guidance:*` arm is the same workspace there and to leave those tasks out of arm comparisons, which is wrong for `guidance:rev:<rev>` and `patch:<file>` arms and `+skill:` parts: they bring their own files, so following the note on replay tasks, whose bases are usually older than the guidance they test, left every task out. The note now says that there `guidance:none`, `raw` and `bridge` give the same workspace and `as-skill`, `overlay` and `format=` fail the cell's preparation, so the tasks it lists stay out of comparisons among those arms only. It also no longer lists a task whose base holds an `AGENTS.<agent>.md` overlay the globs miss: `guidance:none` removes that overlay and `guidance:overlay` keeps it whatever the globs match, so there `none` differs from `raw` and the overlay arm prepares. Nor does it list a task whose `setup_patches` name such a file, as `git apply` reads them, or are no patch: they apply before the arm, so a patch that adds an overlay does the same. A task whose setup patches name only other files, code for example, stays listed, whatever its bytes (not UTF-8, a lone CR in a line): the patch reaches `git apply` unchanged, as the workspace applies it.

- **`repo scan` judges an Action-shaped name below a root link to a directory as a path** — with
  a root link `owner -> src/pkg` and a workflow step `uses: owner/name@v1`, a guidance claim
  `owner/name` that the tree does not hold read `ok` (`used by .github/workflows/ci.yml`), though
  a checkout makes `owner/` a directory and a real `owner/` keeps such a name a path. A root link
  that resolves to a directory now counts as that root directory, so the claim keeps its path
  verdict (`unresolved`, `tried owner/name`) and its reason names no workflow the Action reading
  skipped. The same holds for a deeper name whose directory the link leads to: with `owner ->
  src/pkg` and a tracked `src/pkg/repo/x`, `owner/repo/path` is `unresolved` (`tried
  owner/repo/path`) as well. A link to a file is still no directory.

- **`repo scan` no longer walks a whole branch filter for each character of a claim** — to match
  a claim against a `branches` / `branches-ignore` filter, the walk read every atom of the filter
  for every character of the claim, and went on to the claim's end after the filter had stopped matching
  it, so a filter and claim of 2,000 characters took millions of steps. The walk now passes over
  only the optional atoms (`?`, `*`) that follow the atoms the claim can be at, and stops at the
  first character the filter does not take. What a filter matches is unchanged.

- **Input-file flags refuse a device instead of reading it without end** — `eval personas validate --file`,
  `evaluate --custom-questions`, `benchmark preflight|run --questions-from`, `eval questions generate --personas`
  and `--topics`, `--placebo-text` (`skill novelty analyze|probe`), every flag read through `read_json_input`
  (`eval triggers generate --trigger-set` among them) and the guidance file of `trace prefilter` read a device
  such as `/dev/zero` until memory ran out (a `MemoryError` traceback, or for `benchmark` the empty
  "questions source invalid: /dev/zero ()"), and read `/dev/null` as empty input. They now exit 2 with
  `Error: <flag> <path>: not a file` before reading (`guidance <path>` for `trace prefilter`). A regular file, a
  pipe and a directory are read as before, and a missing file keeps its message. A device among the sessions of
  `trace prefilter` is reported (`✗ <path>: not a file`) and the rest are still read; the exit code is 1, as for
  any unreadable session (#504).

- **`skill_sha` and `suite_hash` leave out git-ignored files in a repository whose root name ends in a space or a carriage return** — the work tree's top was read from `git rev-parse --show-toplevel` with all trailing whitespace stripped, so git was then asked about a directory that does not exist, and the digest fell back to hashing every byte on disk, ignored files included. Only git's final newline is removed now.

- **The guidance arms match the workspace a task's setup patches leave** — they matched
  `guidance.globs` against git's index, which a setup patch (applied with `git apply`) does
  not change. A guidance file the patch adds was missed: `guidance:none`,
  `guidance:as-skill` and `guidance:rev:` kept it, `guidance:bridge` wrote no bridge next to
  it and `guidance:format=xml` left it unrendered. A tracked guidance file the patch deletes
  stopped those three removing arms, `guidance:bridge` and `guidance:format=` with a bare
  `FileNotFoundError` instead of preparing the cell. The globs are now matched against the files the workspace holds, untracked ones
  included, which is what `guidance:overlay` already read.

- **`repo scan` no longer stalls on a `no-commit-to-branch` pattern that backtracks exponentially** — a hook
  `--pattern` such as `maintenance/(a+)+b` was matched with `re` against each path-like claim of the guidance,
  and a claim like `maintenance/aaaaaaaaaaaaaaaaaaaaaaaac` took about twice as long for each extra `a`. Such a
  pattern is now refused before any claim is matched against it, as an invalid regex is: it names no branch and
  the claim keeps its path verdict. Refused is a repeat that can run more than once around a varying item
  (a repeat, `?` too) that can end at the same place two ways, or around an alternation whose branches can start
  with the same character or match nothing (`(a+)+`, `(a*)*`, `(a{1,5})+`, `(a?a?)+`, `(a|aa)+`); a pattern whose
  passes end at a character the item does not take (`release/([0-9]+\.)+[0-9]+`) is still honoured, and other
  slow shapes still pass. The rule is in
  `docs/repo-evaluation-contract.md`.

- **`novelty routing --max-usd` counts an unpriced reply cut at the output ceiling as an
  abstention** — a call with no known price stops a capped run, and that stop came before the
  check for a cut reply, so the cut text was scored as a pick (a cut `{"pick": 1}` picked
  card 1). It is now an abstention with its `raw_reply` kept, as a priced
  cut reply already was, in a single-artifact run and in `--all-vs-all`, and the run still stops
  with the cap's message.

- **A `novelty routing` run stopped by `--max-usd` saves past a sidecar it cannot use** — when
  `--out` already holds a finished result, the partial one goes to the first free
  `<out stem>.partial.json`, `<out stem>.partial2.json`, ... or one that is already partial. A
  FIFO there blocked the run while it read it, a read-only partial file stopped it with a
  permission error, so the paid calls were not saved, and a dangling symlink was taken for a free
  name and written through, creating its target. A sidecar that is not a regular file (a FIFO, a
  symlink) is now skipped without being opened or followed, and a partial one this cannot write
  is skipped too, in a single-artifact run and in `--all-vs-all`. A sidecar name too long for the
  file system stops the save with that error rather than trying ever longer names.

- **`python -m dokimi.schema_compat` compares a schema whose file name git quotes, and runs
  in a repository whose root has a carriage return in its name** — it listed the base's
  schemas with `git ls-tree` without `-z`, which prints a name holding a carriage return,
  a `"` or a non-ASCII character in C quotes, so that schema did not end in `.json`, was
  skipped and a breaking edit to it passed; a space cut a name in two. A repository root
  whose name holds a carriage return, ends in one or in a space, or is not valid UTF-8 made
  the check stop with a `ValueError` whenever the base had no schemas at their current path,
  as after a move. The listings are now split on NUL, the root is read from git's raw output
  less its final newline, and each path is decoded with `os.fsdecode`, so a name that is not
  valid UTF-8 still names its file; the check prints such a name with a `?` for the byte.

- **`python -m dokimi.schema_compat` says so when git prints no repository root** — git
  before 2.25, run outside a work tree, prints nothing for `rev-parse --show-toplevel` and
  exits 0, and following moved schemas then took the working directory for the root: run
  from the repository root the check answered, from a directory between the root and the
  schemas it compared nothing and passed, and from anywhere else it stopped with a
  `ValueError` or a git error about `diff`. The check now stops, from any directory, with a
  git error that names the command: `git rev-parse --show-toplevel: printed no work tree to
  follow moved schemas in`.

- **`repo compare`'s leak check follows `@` imports between guidance files** — a nested guidance file that another guidance file imports, edited after a replay task was public, now flags the task: a `guidance:rev:` cell's `meta.json` `arm_rev` records `guidance_imported_by` (which files' import chains reach each guidance file the arm wrote, read with `repo budget`'s import walker by the cell's harness profile in the workspace as the agent gets it: after the review patch, with the files a `.gitignore` ignores), so a file a root file's chain reaches applies to every task, one a nested file imports to the tasks under that file's directory too, and the flag's `via` names the importer, which the text shows too (`by date, through an import: <task> (sub/CLAUDE.md via CLAUDE.md)`). Batches recorded earlier keep the directory-only reading, which their entries say (`imports_unrecorded`, and a line of the text).

- **`python` examples run in the `docker` backend of `example_pass_rate`** — the
  container was told to run the host's interpreter path (the `sys.executable` of the
  Python running dokimi), which the image does not have, so every `python` example failed
  with `Exit code 127: timeout: failed to run command …` and lowered the pass rate. The
  docker backend now runs the image's `python3`; the `subprocess` backend keeps the host
  interpreter. `bash` and `sh` examples are unchanged.

- **A provider call with no credentials fails at once** — litellm raises a missing
  `OPENAI_API_KEY` as `InternalServerError` and a missing Azure key as `APIError`; a missing
  `GEMINI_API_KEY`, and Vertex with no Application Default Credentials, no project or no
  `google-auth` installed, come as `APIConnectionError`. Every such call was retried three
  times, 14 s of backoff before the same error. These errors are now raised on the first
  attempt; a rate limit, a 5xx or a connection error is still retried.

- **A guidance or skill file whose path is not valid UTF-8 is now found** — git's `-z` listings were decoded as UTF-8 with replacement, so a byte that is not UTF-8 became U+FFFD and the path named no file: `guidance:overlay` left such an overlay in place, `guidance:none` failed on a file it could not find, a `guidance:rev:` arm could not check its file out, a `patch:` arm recorded a wrong path, the base check of a skill arm missed a skill the base already ships, and `python -m dokimi.schema_compat` looked for schemas moved out of such a directory at a path the base does not have. Each field of those listings is now decoded as a file name (`os.fsdecode`), so it keeps its bytes; `guidance:as-skill` packages such a file under a `## File:` line that shows the byte as U+FFFD, so `SKILL.md` stays valid UTF-8. On Windows, where a file name cannot hold such a byte, it is still replaced, as before.

- **A `guidance:rev:` arm's guidance history keys a file whose path is not valid UTF-8 as the arm lists it** — the history decoded a path with replacement, so `guidance_latest` left out the file and the date-based leak check reported clean for guidance committed after the task's `public_at`. A path is now decoded as a file name, as the rest of the `-z` listings are; a commit subject stays text.

- **`repo scan` judges the directory a root link makes as that directory** — with a root link
  `owner -> src/pkg`, a stale `owner/gone` was `unresolved` (`tried owner/gone`) and a glob
  `owner/*.cfg` that nothing matches was `unresolved` (`... a build may create it`), so both passed
  `repo scan --fail-on-fail`, while beside a real `owner/` they are `fail`. The parent-directory
  check now follows the link, also through a link higher up: `owner/gone` is `fail` (`owner/ exists
  but owner/gone does not`), a glob is matched in `src/pkg` and named below the link (`owner/x*` is
  `ok`, `glob owner/x* -> e.g. owner/x`) or is `fail` (`owner/ exists but nothing in it matches
  *.cfg`), and a directory claim `owner/f/` naming the file `src/pkg/f` is `fail` (`owner/f is a
  file, not a directory`) where it read `ok`. A link that dangles or names a file still makes no
  directory, so a path below it keeps its verdict. A `.gitignore` rule naming the path below the
  link's target (`src/pkg/.gitignore` with `*.cfg`) still reads it as a build output (`unresolved`).

- **`dokimi.eval.stats.paired_test` returns a result for finite deltas from 1e200 up to about 1e300** — the sample sd squared each deviation in plain floats, so a deviation above about 1.3e154 made it `inf` or raised `OverflowError`, out of `paired_test`, `arm_significance`, `mean_ci`, `point_biserial` and `dokimi.eval.interactions.interaction_test`. The sd now divides the deviations by the power of two at or below the largest one before squaring. Near the float limit (about 1e307 and above) the mean, the deviations or the confidence interval still overflow: `mean_ci` and `paired_test` (and `arm_significance`) raise `OverflowError` there rather than return an infinite interval or a zero-width one. Judge scores of 0 to 100 never reached this. For them the division is exact; the sd now takes its square root with `math.sqrt`, which is correctly rounded, so it can differ from before in the last bit.

- **`trace prefilter` reads a session log through a pipe whole** — a log passed as
  `<(cat session.jsonl)` or `/dev/fd/<n>` lost its first 16 bytes to the Goose store check, so a
  readable log was reported as `no prompt and no tool call` and the command exited 1. Only a
  regular file is checked for the SQLite header now; anything else is read as a JSONL log.
- **`eval tasks generate --from replay` and `--from prs` exit 2 on a source refused as typed** —
  a malformed source, an unpinned remote or a local source that is not a directory exited 1, the
  code for an unusable pull request file or a source that cannot be opened, while `--from graph`
  exited 2. All three now exit 2 with the same `Error:` line, before the threads or pull request
  file is read.

- **`eval score ragas` and `answers generate --ragas` return scores on ragas 0.3** — the ragas
  version dokimi pins (`ragas>=0.3.0,<0.4`) renamed the input columns, so both arms lost
  every score (logged only), and its `context_precision` demanded a reference answer that
  dokimi never sends. The rows now use ragas 0.3's names and the scores are read by the
  metric names. `context_precision` is now ragas's reference-free
  `LLMContextPrecisionWithoutReference` (whether the retrieved documents were useful in
  reaching the `with_docs` answer, the useful ones ranked first), not a comparison with a
  reference answer; `context_recall` (Python API only) reads the question's
  `ground_truth_chunk` as the reference. `answer_relevancy` embeds with OpenAI whatever the
  judge: without `OPENAI_API_KEY` both commands still return empty arms (logged only); set
  the key, or call `RagasEvaluator(metrics=["faithfulness", "context_precision"])` from Python.

- **`python -m dokimi.schema_compat` exits 2 when git cannot read `--base`** — a base that is not a revision (`--base no-such-ref`) ended in an uncaught `GitError` traceback and exit 1, the status of a breaking schema edit, so the `schema_compat vs the PR base` step let a PR with the `schema-break-ok` label pass on a base it never read. It now prints `cannot diff against <base>: <git's error>` to stderr and exits 2, and the CI step fails on status 2 whatever the label. The label accepts status 1 only when the run printed a `BREAKING` finding, so a crash (a schema that is not JSON, no `git`) that also exits 1 fails the step too.

- **`dokimi data import docling` no longer writes through a symlink** — the converted Markdown is never written through a link at `<stem>-<ext>.md` (dangling or not): a link counts as a used name and the counter moves on, so no file appears at the link's target.
- **A `dokimi novelty routing --max-usd` stop no longer loses its result to a long `--out` name** — the partial result of a stopped run, when `--out` holds a finished result, goes to `<out stem>.partial.json`, `.partial2.json`, ... as before, but a stem too long for those names to fit the file system is cut (on a character boundary, measured in the file system's units, bytes or, on Windows, UTF-16 code units, so a name with a non-UTF-8 byte still saves) and ends in a short digest of the whole stem, so two long `--out` names never share a sidecar; the save no longer fails with a "File name too long" error and the paid calls are kept.

- **`trace inspect` and `trace consistency` refuse a device instead of reading it without end** — a trace given as `/dev/zero`, and `trace consistency --outcomes /dev/zero` or `--contract /dev/zero`, were read until memory ran out (a `MemoryError` traceback), and `/dev/null` was read as an empty file. A device among the traces is now reported (`✗ <path>: not a file`), the other traces are still read and the exit code is 1, as for any unreadable trace. A device as `--outcomes` or `--contract` stops with `Error: --outcomes <path>: not a file` (exit 2). A regular file, a pipe and a directory are read as before, and a missing file keeps its message (#533).

- **`eval check`, `repo run` and `eval tasks generate --from replay|prs` exit 2 on a source the user mistyped** — a local path that is not a directory, an unpinned remote, or a pinned local path inside a repository (`<repo>/sub@<sha>`, which wants the root) exited 1 in `eval check` and `repo run`, and the pinned subdirectory exited 1 in `--from replay` and `--from prs`, while `--from graph` exited 2. All of them now exit 2 with the same `Error:` line, before the image build, the pin or the threads or pull request file. A `--manifest` path typed outside an unpinned local tree also exits 2. A source that parses but cannot be opened (clone, fetch, revision) still exits 1, as does a failed gate and a link in the tree that leads out of it (the default manifest, `.mcp.json`, a guidance file).

- **`evaluate --custom-questions <directory>` and `trace prefilter` with a guidance file it cannot read exit 2** — a directory given as `--custom-questions` ended in an `IsADirectoryError` traceback (exit 1) after the output directories were created; it now stops before any of that with `Error: --custom-questions <path>: not a file` (exit 2), as does a file it cannot read (`Error: --custom-questions <path>: <reason>`). A guidance file for `trace prefilter` that is missing, a directory or unreadable exited 1 with `✗ cannot read <path>`; it now exits 2 with `Error: guidance <path>: <reason>`. An unreadable session keeps its rule: reported, the rest read, exit 1 (#530).

### Security

- **The `dev` extra requires pytest 9.0.3 or later**, which fixes PYSEC-2026-1845; the old
  `pytest>=7.4.0` floor let a fresh install resolve an affected pytest. The lock already
  pinned 9.1.1. `osv-scanner.toml` lists the two advisories SECURITY.md documents
  (PYSEC-2026-2447, PYSEC-2026-3046), the same IDs `pip-audit` ignores in CI, so
  OpenSSF Scorecard (and `osv-scanner --no-resolve`) report the same baseline.

- **The locks pin certifi 2026.10.19 and pillow 13.0.0**, which fix GHSA-6q48-6c7w-9gw2
  (certifi) and eight pillow CVEs that `pip-audit` reported on 2026-10-20. Both fixes are
  newer than the 7-day `--exclude-newer` window, so they were resolved by hand with a
  per-package override; the header commands are unchanged.

- **The main lock pins gitpython 3.2.1**, which fixes CVE-2026-114145 (`pip-audit` failed on 3.2.0).
  The fix is newer than the 7-day `--exclude-newer` window, so the lock's header command now
  exempts gitpython (and the already hand-resolved certifi and pillow) with
  `--exclude-newer-package`. Drop each of the gitpython, certifi and pillow exemptions once the
  pinned release (3.2.1, 2026.10.19, 13.0.0) is 7 days old.

- **The tools lock pins setuptools 84.2.1**, which fixes GHSA-xmj3-9gf7-h52w (`pip-audit` failed
  on 84.0.0). The fix is newer than the 7-day `--exclude-newer` window, so the lock's header
  command exempts setuptools; drop the exemption once 84.2.1 is 7 days old. The three filelock
  3.32.7 advisories are documented in `SECURITY.md` and ignored: the fix is 4.2.1, and every
  litellm release requires `filelock<4.0`.

- **The CI and development lock no longer contains ragas, the langchain packages or
  diskcache**, which clears CVE-2025-69872 (diskcache, PYSEC-2026-2447) and CVE-2026-6587
  (ragas, PYSEC-2026-3046) from the locks (`requirements.lock.txt` was the only one with
  them), so `pip-audit` no longer has to ignore them. `pip install dokimi` was already free of both
  since ragas became the `[ragas]` extra (#126); `eval score ragas` and `answers generate --ragas`
  still need `pip install 'dokimi[ragas]'`, and the weekly `test-latest` CI job installs
  that extra and runs its import check. The three filelock advisories are unchanged.

- **The `docker` backend of example execution drops every capability** — a
  fenced example ran as the image's root with docker's default capabilities,
  and a setuid binary in the image (`su` in `python:3.11-slim`) still worked.
  The container now starts with `--cap-drop ALL` and `--security-opt
  no-new-privileges`, as `repo run` containers do. The snippet's directory is
  mounted world-readable (0755, the snippet 0644), because a root without
  capabilities cannot enter the host user's private temp directory.

- **The locks pin filelock 4.2.4**, which fixes the thirteen filelock 3.32.7 advisories
  that `pip-audit`, osv-scanner and dependency review ignored. Every litellm release still
  declares `filelock<4.0` (its main has lifted it), so both lock headers pass
  `--override=requirements-overrides.txt` and every install of `requirements.lock.txt`
  passes `--no-deps`. No advisory is ignored anymore. Drop the override and `--no-deps`
  once a litellm release allows filelock 4.

### Documentation

- **Module map matches the code** — the architecture overview and the API
  reference landing page now group the modules by import layer and cover
  `explore`, `tool_preference`, `plugins`, `datasets_export` and `caching`.
  `report` and `gate` are listed for every track they serve, and a subject
  scorecard is described as built per run rather than kept over time.

- **CLI flag registry and how to add a flag** — `dokimi/cli_flags.py` lists each flag concept with its canonical spelling, type and banned synonyms, and `tests/test_cli_flags.py` fails a flag that is neither canonical nor on `tests/data/cli_flags_allowlist.txt`, or a banned synonym, with the canonical spelling in the message. A contributor adding or renaming a flag now finds the steps in `CONTRIBUTING.md` (“Adding or renaming a CLI flag”): run `make cli-snapshot` and commit `docs/reference/cli-interface.json`. No command reads the registry yet; no flag, default or output changes.

- **`docs/reviewing.md`: running against an OpenAI-compatible server** — provider `openai` with
  `OPENAI_API_BASE` / `OPENAI_BASE_URL`, which names reach the server and which keep a
  litellm provider prefix's route (write `openai/<name>` for those), a placeholder
  `OPENAI_API_KEY` for a server that ignores it, and why cost is unknown and
  `novelty --max-usd` stops after one call.

- **The Inspect importer's docstrings name `datasets` a required dependency** — the docstrings
  of its `hf_dataset(...)` loader in `dokimi.task_formats.imports.inspect_ai` still called
  `datasets` optional, though `pyproject.toml` lists it under `[project.dependencies]`. No
  behaviour changes.

- **The catalog loaders say that skill bodies load up front** — `catalog check --per-skill` with `--jsonl`,
  `review_skill_dir` and `read_skill_manifest` now state that every `SKILL.md` is read before the first row
  or entry, so memory still grows with the catalog. Loading one skill at a time is tracked in #434.

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
