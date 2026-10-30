---
depends_on:
  - dokimi/dokimi/repos/**
  - dokimi/dokimi/commands/repo.py
  - dokimi/dokimi/commands/repo_run.py
  - dokimi/dokimi/schemas/repo_*.json
  - dokimi/dokimi/schemas/guidance_reach.v1.json
  - dokimi/dokimi/schemas/harness_profile.v1.json
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Repository evaluation contract

> **Status: PROPOSED.** This is the interface proposed for evaluating a whole
> code repository as a subject; the rationale behind it is recorded in the pull
> requests that build it out.
> Implemented so far (slice S1): the `repo_manifest.v1` schema, validation of
> the manifest and of every `task.json` against one repository snapshot
> (with git history, every task base must resolve and the guidance globs are
> evaluated at each base), the `repo_static.v1` schema (§4.4), and the `repo`
> subject kind:
>
> ```bash
> python cli.py repo validate <dir | dir@rev | url@rev> --manifest <path> [--json] [--out FILE] [--fail-on-warning]
> python cli.py repo fetch <url@rev> [--json]     # bare, blob-less clone cached per URL
> ```
>
> Slice S3 adds harness profiles (§6) and the reach probe (§7):
>
> ```bash
> python cli.py repo reach <source> --manifest <path> --model <id> [--harness claude-code]
>     [--bin <cli>] [--scenarios root,cwd,read] [--only <scenario>] [--dry-run]
> ```
>
> Slice S2 adds the L0 scan (§8):
>
> ```bash
> python cli.py repo scan <source> --manifest <path> [--static <repo_static.json>] [--out readiness.json]
>     [--json] [--fail-on-fail]
> ```
>
> `repo budget` sizes what the tree puts into an agent's context (no manifest,
> no LLM call): root CLAUDE.md files and their `@` imports plus the skill listing at
> start (AGENTS.md files and their imports too, unless a root CLAUDE*, `.claude/CLAUDE.md` or
> `CLAUDE.local.md` turns them off), nested guidance lazily, skill bodies on demand, and AGENTS.md
> files a root CLAUDE* turns off. The `start` total is not an upper bound: it is too low by what it does
> not count (`.claude/rules/*.md` files, non-`.md` imports, skills behind a linked directory, nested
> `.claude/skills/` skills, and
> imports past the profile's `guidance.import_max_depth`, four hops for Claude Code, which warns
> naming what was skipped), and too high by whatever the harness cuts from the skill listing.
> Usage:
>
> ```bash
> python cli.py repo budget <source> [--harness claude-code] [--out FILE] [--json] [--fail-on-warning]
> ```
>
> `--out` and `--json` carry the inventory as a `repo_budget.v1` artifact (`schemas/repo_budget.v1.json`),
> stamped with its `schema_version`, so a CI gate on the JSON can tell a changed shape.
>
> `repo inspect` (§8.1) needs no manifest: a static look at what the tree offers an agent.
>
> ```bash
> python cli.py repo inspect <source> [--out environment.json] [--json] [--large-bytes N] [--fail-on-error]
> ```
>
> `--manifest-file <local.yaml>` on `validate`, `scan` and `reach` reads the manifest
> from outside the tree, for a repository that has not added it yet. `scan` and `reach`
> need only `guidance`; given neither a manifest in the tree nor `--manifest-file`, they
> check the default globs `**/AGENTS.md`, `**/CLAUDE.md`, `.github/copilot-instructions.md`
> and `.github/instructions/*.md`, and say so on stderr.
>
> Slice S5 adds the repository-mode runner: workspace preparation (§3), the
> containers of §1.1, the `repo_grade.v1` schema (§4.2), the self-check (§4.3)
> and agent runs with their report:
>
> ```bash
> python cli.py repo tasks check <source> --manifest <path> [--task <id>] [--warm] [--allow-unsupported]
> python cli.py repo run <source> --manifest <path> --arm guidance:raw --model <id> --reps N --batch NAME
>     --agent-network host
> python cli.py repo report <out>/<batch>
> python cli.py repo compare <out>/<batch> --baseline guidance:none
> ```
>
> Not implemented yet: the egress proxy (§2), so a task with `egress` is refused
> and an agent reaches its model only through an explicit `--agent-network`;
> running the `static` entry point; the regrade sample of §4.2; and the split
> by base date against a model's cutoff. See [Running](#9-running) below.

A repository that wants to be evaluated keeps its evaluation content in its own
tree: a manifest, tasks, graders and a build image. Dokimi reads that
content, runs agents against it, and reports the results. Nothing
repository-specific is added to dokimi.

## 1. What the repository provides

```text
<repo>/
  <eval-dir>/                       # any path; default .agent-eval/
    repo-eval.yaml                  # manifest (§2)
    tasks/<task-id>/task.json       # one directory per task (§3)
    tasks/<task-id>/...             # patches, hidden tests, review gold, mutants
    <grader code>                   # whatever the `grade` entry point runs (§4)
    Dockerfile                      # build image for this repository
```

The framework removes `<eval-dir>` from every agent workspace, so tasks, gold
answers and graders are never visible to the agent.

### 1.1 Where the evaluation content comes from

Two trees are involved in every run:

- the **eval tree**: the revision the manifest was loaded from, for example
  `oneDAL@<sha of the PR that added the tasks>`. Tasks, graders, the
  Dockerfile and the manifest are read from it.
- the **task tree**: each task's `base`, which is usually older and may not
  contain `<eval-dir>` at all.

The framework copies `<eval-dir>` from the eval tree to a read-only directory
outside every workspace and passes its absolute path to entry points as
`{eval_dir}` (§2). The agent never sees it:

- The agent runs in its own container, which mounts only the workspace, the
  per-run scratch and config dirs, and the caches. `{eval_dir}` and
  `{source_dir}` are not mounted. `task.json`, `meta.json`, `events.jsonl` and
  `answer.txt` are written to `{run_dir}` after the agent's container exits.
- `grade` and `oracle` run in a separate container from the same image, after
  the agent's container has exited. That container mounts `{eval_dir}` and
  `{source_dir}` read-only and `{run_dir}` read-write (and, for a built-in grader (§4.5), the framework's grader code at `/eval/graders`, read-only). It gets a `HOME` of its
  own (`{run_dir}/grade-home`, made empty before each run; the agent never had
  it), `PYTHONNOUSERSITE=1`, and git configuration overrides that disable
  `core.fsmonitor` and hooks (`core.hooksPath=/dev/null`); the workspace's
  `.git/config` and `.git/hooks` are moved aside to `{run_dir}/agent-git/`
  before it starts, so a filter, diff driver, alias or include the agent
  configured does not run in the grader. What a grader still trusts from the
  agent, and must treat as untrusted input: every file in the workspace
  (including `.gitattributes`, `conftest.py`, build files and the rest of
  `.git`), and any directory `image.env` places under `{run_dir}`, which both
  containers share. A grader that imports or runs workspace code runs the
  agent's code.
- **Fixed paths.** Inside every container `{run_dir}` is `/eval/run`,
  `{eval_dir}` is `/eval/content`, `{source_dir}` is `/eval/source`, and
  the built-in graders' code is `/eval/graders` (grading container only, §4.5).
  The agent container mounts the workspace at `/eval/run/workspace`, the same path
  graders see. An absolute path in an answer or grade therefore means the same
  thing after a run directory is archived or regraded on another host. On
  oneDAL, 14 of 308 copied runs regraded differently because build answers
  name an absolute artifact path; a fixed mount fixes that without changing
  any repository.
- **Image build.** The build context is the eval tree's repository root,
  checked out at the eval revision (no `.git`, no untracked files, every
  tracked file as git stores it) and with the `tasks` directory removed. One image serves every task
  of that eval revision; per-base images would mean one build per base (18 on
  oneDAL) for the same toolchain. The Dockerfile may `COPY` tooling from
  anywhere in that context, for example a build wrapper under `<eval-dir>`,
  and must work for every task base: a version pinned in the task tree (such
  as `.bazelversion`) is read at run time, not baked in. A build that runs
  past `--build-timeout` seconds (3 hours by default) fails as a container
  error rather than hanging the batch.
- `tasks check` (§4.3) fails a task whose agent container can read any file
  under `tasks` or `{source_dir}`.

## 2. `repo-eval.yaml`

```yaml
schema: repo_manifest.v1
id: onedal
source: {url: https://github.com/uxlfoundation/oneDAL}   # sha comes from each task's `base`
eval_dir: dev/agent_evals

guidance:                       # what the guidance:* arms add, remove or replace
  globs: ["**/AGENTS.md", ".github/instructions/*.md", ".github/copilot-instructions.md"]
  also_scan: ["CONTRIBUTING.md"]          # checked by L0, never changed by arms

image:
  dockerfile: dev/agent_evals/Dockerfile
  caches:                                 # volumes shared across runs, mounted read-write
    - {name: bazel-repo-cache, path: /cache/bazel-repo}
    - {name: bazel-disk-cache, path: /cache/bazel-disk}
  env: {EVAL_BAZEL_OUTPUT_BASE: "{run_dir}/ob"}   # per-run build state; a value under {run_dir}/ is a plain
                                          # name (`ob`, `ob/sub`), never a framework entry such as `agent-git`
  network: none                           # or: public

entry_points:                             # run inside the image, cwd = {run_dir}/workspace
  # the commands are the repository's own; these are examples
  grade:  "python3 {eval_dir}/run.py grade --contract {run_dir}"
  oracle: "python3 {eval_dir}/run.py oracle --contract {run_dir}"   # applies the reference solution
  static: "python3 {eval_dir}/static_check.py --out {out}"          # optional, repo_static.v1 (§4.4)

tasks: dev/agent_evals/tasks
explore_tasks: null                       # optional ExploreBench YAML directory

build_systems: [bazel, make]              # selects L0 claim-check adapters

process_metrics:                          # optional repository counters, result.json `process.counters` (§4.1)
  bazel_cmds: '\bbazel(?:isk)?\b'          # a plain regex searches the command text
  icpx_missing: {regex: "icpx is not found", match: output}   # match: text (default), output or any
```

Rules:

- Every path is relative to the repository root.
- `eval_dir`, `tasks` and `entry_points` come together or not at all. Without them the
  manifest is *guidance-only*: `scan` and `reach` run on it and `validate` checks its
  globs, while `tasks check` and `run` refuse it. `image` and `explore_tasks` need
  `eval_dir`, and so does `process_metrics`.
- `entry_points.grade` is required only when some task's `grader` does not
  start with `dokimi:` (a grader built into the framework); `repo validate`
  names the tasks that need it.
- `process_metrics` maps a name (letters, digits, `_`; not
  starting with a digit) to a Python regex,
  given as a string or as `{regex: <regex>, match: text|output|any}`. For each
  name the framework counts the `command` events of the run whose `text`
  (default), `output` or either contains a match (`re.search`); denied commands
  count too, since the agent asked for them, but a denied command is searched in
  its `text` only (its `output` is the hook's refusal message). `repo validate` refuses a regex
  that does not compile.
- `eval_dir` is a proper subdirectory, and `tasks` and `explore_tasks` sit inside it: it is the
  one directory deleted from every workspace (§3).
- `{eval_dir}`, `{source_dir}`, `{run_dir}`, `{out}` and `{task}` are the only
  placeholders. `{eval_dir}` is the read-only copy from the eval tree (§1.1),
  not a path inside the workspace.
- `{source_dir}` is a read-only, full-history clone of `source.url` with
  blobs, for graders that read hidden tests or mined fix commits (oneDAL's
  `fix` grader reads the fix commit named by `base`). It is allowed only in
  `grade` and `oracle`, and needs `source.url`. History holds every fix, so it
  is never mounted in the agent container (§1.1). A placeholder rather than a
  flag: using it is what tells the framework to provide it.
- Guidance globs are evaluated at every task base, not only at the eval
  tree: a file deleted upstream after a base still exists at that base and
  the `guidance:none` arm must remove it there. A glob warns only if it
  matches nothing at every base.
- `image.network` applies to entry points and to every command the agent
  runs. With `none`, those have no network at all. A task that needs a
  package index adds the hosts to its `egress` key (§3), which opens them
  through the proxy below for that task only. There is no per-task `public`:
  full network would let the agent fetch the upstream fix. Baking the
  dependency into the image is better when it can be done.
- **Caches** are mounted read-write in every run, because Bazel's disk cache
  writes. A run may depend on them for speed, never for correctness. With
  `network: none` they are filled by `tasks check --warm` (§4.3): one base is
  not enough when bases pin different tool versions (7 Bazel versions on
  oneDAL).
- **Container runtime.** The framework starts every container with an init
  process as PID 1 (`docker run --init`) and with an empty `--entrypoint`, so
  the command is the whole argv whatever the image's `ENTRYPOINT`;
  without one, a daemonizing build server such as Bazel's hangs on shutdown and
  its cache writes are lost. `HOME` is a per-run writable directory under
  `{run_dir}`, never the container layer. Large tool state that should be
  shared across runs (a Bazel install base is about 1 GB per version) goes to
  a declared cache, set through `image.env` or the repository's wrapper.
- The harness process itself must reach its model. The framework gives the
  agent container one egress route: a proxy it runs, which allows only the
  hosts in the harness profile's `egress` list (§6) and refuses everything
  else. A command the agent runs can reach those hosts too, so give the
  harness short-lived credentials scoped to the model endpoint. Every
  connection through the proxy is logged with the run.

## 3. `task.json`

The framework interprets the **core** and **workspace** keys. Every other key
belongs to the repository's grader and is passed through unread, except `grader`, which the framework reads to tell a `dokimi:` name (§4.5) from a repository's.

| Key | Required | Read by | Meaning |
|---|---|---|---|
| `id` | yes | framework | equals the directory name, and contains no whitespace or comma: the workflow's `tasks`, `smoke-tasks` and `exclude-tasks` inputs are lists of ids split on whitespace and commas, so `repo validate` refuses an id containing either |
| `family` | yes | framework (reporting) | free string; common values: `build`, `fix`, `feature`, `test`, `review` |
| `base` | yes | framework | commit to snapshot: a hex SHA, optionally followed by `^` (the parent, the usual form for a mined fix). Branch names, tags and `HEAD` are refused, because repetitions of one task must get one snapshot. Use the full 40-character SHA; a shorter one is accepted with a warning. It must resolve in the source. `meta.json` records it as `base_rev` and the resolved full SHA as `base_sha` |
| `prompt` | yes | framework | what the agent is told |
| `grader` | yes | framework for `dokimi:<name>` (§4.5); repository otherwise | grader name; any other name is interpreted by the `grade` entry point |
| `setup_patches` | no | framework | applied after the snapshot, before the arm (for example an injected bug) |
| `review_patch` | no | framework | applied with `git am` as the HEAD commit under review |
| `gate` | no | framework | what counts as success; default `["pass"]`. Each entry is a boolean metric name, which must be `true`, or `{"metric": <name>, "min": <number>}` for a numeric metric |
| `score` | for score-only tasks | framework | `{"metric": <name>, "max": <number>}`: the metric that is the result when `pass` is `null`, and the value the oracle must reach (§4.3) |
| `timeout_s`, `max_turns`, `budget_usd` | no | framework | per-task caps, overriding run defaults; each a finite number > 0 |
| `host` | no | framework | `{"min_cpus": <int>, "cpu_vendors": [<str>], "cpu_flags": [<str>]}`, every key optional: what the hidden test needs to show the bug (a thread race that needs 3+ CPUs, an ISA path). Vendor and flag names are `/proc/cpuinfo`'s (`GenuineIntel`, `avx512f`); both match in any case, and a vendor other than `GenuineIntel`, `AuthenticAMD`, `HygonGenuine`, `CentaurHauls`, `Shanghai` is refused (`repo validate` too). A probe that read neither vendor nor flags from `/proc/cpuinfo` is a probe failure, not a miss They are checked against a probe container started like the grading one: CPUs are its affinity mask capped by its cgroup CPU quota (`docker --cpus`). On a host that misses them the task is unsupported (§4.3), and the probed facts go to `meta.json` `host` of a cell that runs, or to its `unsupported.json` `host` when it does not |
| `egress` | no | framework | extra hosts (DNS names, optional `:port`; `*` stands for one whole label and the last two labels are literal, so `*.pythonhosted.org` passes and `*` or `*.org` does not) this task's agent and entry points reach through the proxy (§2), for example `["pypi.org", "files.pythonhosted.org"]` |
| `expected_skill` | no | framework | the name (directory name) of the skill the agent should use; a `skill-catalog:` arm must install it, or the arm is refused before anything runs. `result.json` `skills` records whether the agent used it and whether it used it first |
| anything else | — | repository | for example `hidden_files`, `targets`, `regression_targets`, `manifest` |

`setup_patch` (a single string) is accepted as a synonym for a one-element
`setup_patches`.

### Workspace preparation, done by the framework

1. `<base>`'s tree into `<run_dir>/workspace`, as git stores it: no
   `core.autocrlf` or other git configuration of the host, and no
   `export-ignore` / `export-subst` (unlike `git archive`)
2. delete `eval_dir`
3. `git init` and commit (no history, so fixes cannot leak)
4. apply `setup_patches`
5. apply the arm: `guidance:none`, `guidance:raw`, `guidance:bridge`,
   `guidance:rev:<rev>` or `patch:<file>`
6. amend the commit
7. apply `review_patch` with `git am` under a neutral author
8. expire the reflog and prune unreachable objects: the
   commits steps 6 and 7 replaced hold the unpatched code, the removed
   guidance and the upstream author, and `git diff HEAD@{1}` would show them.
   Prep's git never runs `gc --auto`, which would pack them where `prune`
   cannot reach

Every `guidance:*` arm is made by the framework from `guidance.globs`, the
harness profile and the source's own history; a repository supplies no arm
content:

- `none` deletes every file the globs match at the base; `raw` keeps them.
- `bridge` exists only for a profile with a `bridge` key. It writes that file
  (for Claude Code, a `CLAUDE.md` containing `@AGENTS.md`) next to every
  matched `AGENTS.md`, not only at the root. Whether a harness needs it
  depends on its version: Claude Code 2.1.286 loads `AGENTS.md` itself, but
  only when no `CLAUDE.md` is in its start-up chain, so a repository-side
  bridge arm would mean different things on different versions.
- `rev:<rev>` keeps the base's code and replaces its guidance with that of
  another revision of the source: the base's matched files are deleted and the
  files the globs match at `<rev>` (outside `eval_dir`) are written. It answers
  "does this PR's `AGENTS.md` help?" by running the same tasks with
  `guidance:rev:main` and with the PR's head. `<rev>` is a SHA, tag or branch
  (`/` allowed) of the source's git dir; for a remote source a branch or tag is
  fetched first. `repo run` resolves it once per batch and names the arm by the
  full SHA, `guidance:rev:<sha>`: a branch that moves later gives another arm
  with its own cells, never more cells of the old one, and `repo compare`
  selects the arm by that name.

`patch:<file>` is the one arm whose content comes from the evaluator rather
than from the globs: a unified diff on the host running `repo run`, applied
with `git apply` on top of `guidance:raw` (after `setup_patches`). It is for
tooling or layout treatments, such as a new script or a Makefile target, that
no task carries. The file is read once per batch, so every cell applies the
same bytes, and the arm is named `patch:<name>@<first 12 hex of its
sha256>`, where `<name>` is the file name without its directory: each run of
characters outside `[A-Za-z0-9.-]` becomes one `_`, a leading or trailing `_` is
dropped, the result is cut to 40 characters and a `_` the cut leaves at the end
is dropped too (`patch` if nothing is left), so `my fix (v2).diff` gives
`my_fix_v2_.diff`. The name holds no `/` for `BATCH/ARM` selectors; the exact
name is printed on the `repo run` stderr line `arm <name> = <path>, sha256 ...`.
A patch that does not apply to a task's base, or that writes into `eval_dir`,
fails that cell's preparation. A cell has one arm: `guidance:rev:` and `patch:`
do not combine.

Both write files, as step 1 does: no object of the source's git dir is copied
into the workspace, so its object store gains only the blobs of the files the
arm wrote, and step 8 drops the guidance the arm replaced.

A skill arm (`skill:<path>`, `skill-catalog:<dir>`, joined to a guidance part
with `+`) installs skill directories into `.claude/skills/<dir name>/` after the
guidance part, so `guidance:none+skill:x` keeps `x`. The arm is named by the
directory names (`guidance:none+skill:x`, no `/`), so `repo compare` can select it.
The `name` in a skill's `SKILL.md` has to equal its directory name. A skill is refused before
any cell runs if it holds a link that copying cannot take (out of the skill,
dangling, looping, to a directory) or a file that is not regular. Preparation
also refuses to write through a `.claude` or `.claude/skills` the repository
ships as a link. A batch records the content of each skill arm it ran
(`_eval/skills.json`), so resuming it with another path of the same
directory name, or with edited skill content, is refused.

`meta.json` records the base (`base_rev`, `base_sha`, `base_date`), the arm,
the SHA of every applied patch, and `start_sha`: the workspace `HEAD` after
step 7, the commit the agent starts from (§4). `base_sha` names the upstream
commit and is not in the workspace, because step 3 starts a new history; a
grader diffs the agent's changes from `start_sha`. With the arm come
`arm_files`, the files it deleted, wrote or changed; for `guidance:rev:` an
`arm_rev` object (`rev` as given, its `sha`, and `removed`: the base's guidance
files with no counterpart at `<rev>`); for `patch:` an `arm_patch` object (the
file's absolute `path` and full `sha256`, `added`: the files the patch added
that neither the guidance globs nor `.mcp.json` match, and `mcp_servers`: the servers
the patch added to the `.mcp.json` at the root, read before the agent runs). `args_if_present` lists the profile's
`args_if_present` files the cell's command line carries (§6): `[]` when the
workspace has none of them, and always outside the `all` tool mode.

Step 3 removes history only. A fix that landed upstream before a model's
training cutoff may still be known to the model, so reports split results by
base commit date against the model's cutoff where the cutoff is known.

## 4. Entry points

### Inputs available in `{run_dir}` when `grade` runs

| Path | Content |
|---|---|
| `workspace/` | the agent's final tree |
| `task.json` | copy of the task |
| `answer.txt` | the agent's final message |
| `events.jsonl` | normalized trace, one JSON object per line, harness-independent (§4.1) |
| `meta.json` | `arm`, `harness`, `harness_version`, `model`, `model_resolved` (the model(s) the trace says answered: a name, a sorted list, or `null` when the trace format cannot tell), `rep`; `base_rev` (as written in `task.json`), `base_sha` (resolved, 40 characters), `base_date` (committer date, ISO 8601); `patches` (SHA of each applied patch); `start_sha` (workspace `HEAD` the agent started from, §3) |

Graders must not parse a harness-specific trace. A grader that needs "did the
agent run `bazel`?" reads the `command` events in `events.jsonl`. A grader
reads the upstream base (`base_sha`) and the workspace start commit
(`start_sha`) from `meta.json` and does not derive either from the workspace.

### 4.1 `events.jsonl`

```json
{"t": 12.4, "kind": "command", "text": "bazel test //cpp/...:x_host", "rc": 0}
{"t": 3.1,  "kind": "read",    "path": "AGENTS.md"}
{"t": 5.0,  "kind": "edit",    "path": "cpp/oneapi/dal/algo/kmeans/detail/foo.cpp"}
{"t": 20.2, "kind": "tool",    "name": "WebFetch", "denied": true}
{"t": 31.0, "kind": "command", "text": "bazel test :x", "rc": 1, "output": "ERROR: icpx is not found", "output_truncated": false}
```

- `output` is optional: the command's output as the harness returned it.
  Whether stdout and stderr are merged depends on the harness (Claude Code
  merges them), so graders must not rely on telling them apart. Output over
  8 KB keeps its first and last 4 KB around a `[... N bytes omitted ...]`
  line, and `output_truncated` is `true`; build errors are usually at the end.
- `rc` is what the harness reports. Where it reports only success or failure
  (Claude Code), `rc` is `0` or `1`. It is `null` when no result came back,
  for example when the run stopped at its turn or budget limit; a grader
  must not count `null` as a failure.
- `agent` is present on an event a subagent made: the id of the parent's
  `Agent`/`Task` call that started it. Events without it are the parent's.

`result.json` reports the agent's tokens (`input_tokens`, `output_tokens`,
`cache_read_tokens`, `cache_write_tokens`) over the same scope as `cost_usd`.
`usage_scope` is `tree` when they include subagents and helper models (Claude
Code's `modelUsage`), `main` when the harness reported the parent's only, and
`null` when it reported none. `subagent_calls` counts the parent's and
subagents' `Agent`/`Task` calls; `null` when the harness wrote no result record.

`result.json` `process` holds what the framework counts in the events, the
parent's and subagents' together, so a grader need not:

| Key | Meaning |
|---|---|
| `tool_calls` | every event: each tool call the agent made |
| `commands` | `command` events |
| `failed_commands` | commands not denied whose `rc` is neither `0` nor `null` |
| `denied_commands` | commands with `denied: true` (a hook or the harness's permissions refused them) |
| `guidance_read` | sorted, distinct workspace paths (normalized: `./a`, `a/../b`; not `~`) of `read` events (the Read tool only, in a Claude Code trace), neither denied nor failed (`rc` 1: the file does not exist), that `guidance.globs` match. Guidance opened through Bash (`cat AGENTS.md`) is not counted, so `[]` means no Read-tool read; to count it, add a `process_metrics` counter on the command text (§2), which also counts denied commands, unlike this row |
| `counters` | each `process_metrics` name (§2) and its count |

`process` is `null` when the trace could not be read, the agent left none (no event and no result record), or computing the metrics failed (the error is in `agent.trace_errors`).

A cell of an arm that installs skills also has a `skills` object in
`result.json`: `measured`, `skills_installed` (the installed skill names),
`skills_used` (the installed skills the agent called with the `Skill` tool or
whose `SKILL.md` it read, in order of first use), `expected_skill` (the task's,
or `null`), `skill_invoked` (the expected skill was used; any installed skill
when the task names none) and `skill_selected_correct` (the expected skill was
the first one used; `null` when the task names none). When the trace could not
be read (the harness never started), `measured` is `false` and `skill_invoked`
and `skill_selected_correct` are `null`: the usage is unknown, not "did not use
the skill". `repo report` leaves such cells out of its skill counts.

For a `patch:` arm with `added` files or `mcp_servers`, `result.json` has an
`uptake` object: whether the agent used what the arm added. `files` and
`mcp_servers` list what it added (sorted); `commands` counts
command events that name an added file as a workspace path (`tools/fmt`,
`./tools/fmt`, `/eval/run/workspace/tools/fmt`, not `cd tools && ./fmt`; a file
with no `/` only as `./name`, under the workspace path or after an interpreter (a bare `name` resolves through `PATH`), so
`npm run lint` does not name a `lint` file), `reads` the read events of one (a
read of an added script counts as use), `mcp_calls` the calls of the servers' tools
(`mcp__<server>__<tool>`), none of them a call the harness denied; `used`
is whether any is non-zero, and `measured` is
`false` when the agent left no trace to count from. `repo report` shows the
cells that used it per row. `repo compare` puts an `uptake` entry per treatment
in its summary (`{cells, used, unmeasured}`: the measured cells, how many of
them used it, and how many could not be measured; the text lines say so), prints it as an `uptake: ...`
line, and warns about a treatment whose cells used none of it: its deltas then
measure the patch's text, such as a guidance line, not the tool.

### 4.2 `grade.json` (written by `grade` to `{run_dir}/grade.json`)

```json
{
  "schema": "repo_grade.v1",
  "task": "fix_avx2_dispatch",
  "grader": "test_pass_files",
  "pass": true,
  "metrics": {"strict_pass": true, "regress_pass": true, "format_ok": false,
              "diff_lines": 14, "root_cause_fixed": true},
  "details": {"changed_files": ["cpp/daal/src/foo.cpp"]},
  "errors": []
}
```

- `pass` is `true`, `false` or `null`. `null` means score-only, for example
  review replay, where recall is the result.
- `metrics` values are numbers, booleans or `null`. A metric that was not
  measured is `null`, never `false`. Only `metrics` are aggregated, gated and
  tested for significance.
- `details` is optional and free-form (lists, objects, strings: changed
  files, per-class recall, commands the grader ran). It is stored with the run
  and shown in reports, never aggregated.
- `pass: null` with empty `errors` means score-only, and is accepted only for
  a task whose `task.json` has `score`; for any other task it is a grader
  failure. `pass: null` with non-empty `errors` means the grader failed.
- A grader failure counts as a **failure** of the run on every gated metric.
  The same applies when `grade` exits non-zero, times out, or writes no valid
  `grade.json`. An agent that breaks the tree so badly the grader crashes has
  failed the task; excluding the run would favour arms that break workspaces.
  This holds for a host-side failure after the agent ran too (a read-only
  `.git`, an unreadable trace): the cell gets a grader-failure `result.json` and
  the batch budget is charged the run's cost, or its cap when none was reported.
  Reports show the grader-failure count per arm next to the pass rate.
- A grader failure on the null or oracle workspace is a grader bug. `tasks
  check` reports it and the task is excluded from paid runs until it is fixed.
- Grading must be deterministic. The framework regrades a sample and flags any
  mismatch.
- A task that also has a maintainer rubric reports `mergeable` and
  `merge_score`. They come from deterministic scope and test-integrity
  checks plus an LLM rubric judge that the framework runs; see
  [repo-evaluation-mergeability.md](repo-evaluation-mergeability.md)
  *(proposed)*. The judge is not deterministic. Instead of a determinism
  check, it reports agreement between two judges and must pass a calibration
  gate (`repo rubric check`, a separate paid subcommand; `tasks check` stays
  free of LLM calls). The regrade sample bypasses the verdict cache. Whether
  one judge repeats its own verdict on the same input is not measured yet.

### 4.3 Self-check (`tasks check`, no LLM)

For every task, before any paid run:

- the null workspace (prepared, untouched) must fail the gate
- the workspace after `oracle` must pass the gate
- for score-only tasks, the oracle must reach `score.max` on `score.metric`
- the agent container must not be able to read any file under `tasks` or
  `{source_dir}` (§1.1)

A task whose `host` requirements (§3) the probe container does not meet is
`unsupported` with the reason, like a task whose `egress` needs the missing
proxy, and `tasks check` exits 1 for it. With `--allow-unsupported` it is
still printed with its reason but does not make the exit code 1 unless no task
is ok (below); broken tasks, tasks that need egress and tasks whose host probe
failed still do. The probed facts are recorded in `check.json` `host`, and for
`repo run` in the batch's `_eval/host.json`. `repo run` does not run its cells:
each holds an `unsupported.json` with the reason, and `repo report` lists them
apart, neither failed nor missing. If the probe itself fails, a task with
`host` requirements is not run either, and `repo run` exits 1 when one of its
cells is left unrun; a cell that already has a `result.json` keeps it.

Nothing checked is not a pass. Both commands refuse an export with no task, and
exit 1 as `incomplete` when nothing was measured: `tasks check` when no task
passed the self-check (every one unsupported, even with `--allow-unsupported`),
`repo run` when no cell of the batch has a result (every one unsupported). The
`repo run` summary line counts unsupported cells beside ran, done, crashed and
not started.

Each task runs with its own network (`image.network` plus its `egress`).
`tasks check --warm` runs the same null and oracle steps with network and
writable caches, to fill the caches before offline runs (§2); it judges task
`host` requirements as an offline check does.

### 4.4 `repo_static.v1` (written by `static` to `{out}`)

```json
{"schema": "repo_static.v1",
 "claims": [{"file": "AGENTS.md", "line": 12, "status": "fail", "kind": "path",
             "claim": "cpp/daal/foo.h", "why": "no such file"}]}
```

Required: `schema`, and per claim `file`, `line` and `status`. The `status`
class is the part before the first `_`: `ok`, `unresolved` or `fail`, so a
refinement such as oneDAL's `ok_basename` counts as `ok`. Only `fail` counts
against the repository. Every other key, per claim and at the top level, is
free-form and stored with the scan. A fixed minimum lets L0 merge any
repository's checks with its own without knowing their rules.

### 4.5 Built-in graders

A task whose `grader` is `dokimi:<name>` is graded by the framework, not by
the repository's `grade` and `oracle` entry points; any other name goes to
those entry points. Such a task is data only: its directory holds
`task.json`, the patch and the JSON files the grader reads. The `dokimi:`
prefix is the framework's: an unknown `dokimi:` name fails `tasks check` and
the run's grade, and is never handed to the repository.

A built-in grader runs where `grade` and `oracle` run: in the grading
container, with the same mounts, network and environment (§4.2). The
framework's grader code is mounted read-only at `/eval/graders` (never into
the agent's container) and runs as
`python3 -I -B /eval/graders/main.py grade|oracle <name> <task dir> /eval/run`,
where `<task dir>` is the task's directory under `{eval_dir}`. It uses the
standard library only and the image's `python3` (3.8 or newer). `-I` keeps
`PYTHONPATH` from `image.env` and the workspace off `sys.path`; `-B` prevents
writing `.pyc` files to the read-only mount. An image with no `python3` on
`PATH` is a grader failure that says so: the grader exits 127 and its
`<kind>.err` file shows `exec python3 failed`. Any
other exit 127 is reported as a plain `grade exited 127`.

- `grade` reads `task.json` and `answer.txt` from `{run_dir}` and the data
  files from the task's directory, and writes `grade.json` (§4.2): numbers,
  booleans and `null` under `metrics`; strings, lists and objects under
  `details`, with `details.grader_version`. A grader that raises writes
  `pass: null` with `errors`. A metric the task's `gate` names that the grader
  did not produce is `null`.
- `oracle` writes the reference answer to `answer.txt` and leaves the
  workspace alone.

A review task with `pass: null` is scored, not gated: give it `score`. A `gate`
on its metrics is not applied (`success` is null), and without `score` the run
is a grader failure. A clean-diff control (no seeds) has `recall: null`, so it
takes no `score`; its gate is the default `["pass"]`. The manifest's
`entry_points.grade` is optional when every task's `grader` starts with
`dokimi:`.

Both review graders read the answer the same way. Its last parseable
` ```json ` fence (one nesting arrays or objects more than 100 deep counts
as unparseable, whatever `json.loads` accepts) must be an array of findings
`{"file", "line", "severity", "rule", "message"}`; entries that are not
objects are dropped. `file` is made repository-relative: a path is cut after
`/eval/run/workspace/`, then after the last `/repo/` (a standalone oneDAL
run's workspace; a relative `a/repo/b` becomes `b`, the seed's or thread's
`file` as well as the finding's), then leading `./` and `/` go. `line` is
read with Python's `int()` (`"6"` is 6, `5.9` is 5); a missing or falsy line
(`null`, `0`, `""`), or a value `int()` cannot read, is -99, near nothing. `severity` `blocker` or `major` counts toward
`n_severe`. An answer with no such array gives only `pass: false`,
`parse_error: true` and every recall 0 (`recall`; replay also `recall_all`
and `recall_addressed`; `null` when there is nothing to recall): the agent
failed the task, and on a task with `score` that is a failed run with score
0, not a grader failure.

| `grader` | data next to `task.json` (key that renames it) | `pass` | `metrics` | `details` |
|---|---|---|---|---|
| `dokimi:review_seeds` | `manifest.json` (`manifest`); oracle answer `oracle.json` (`oracle_answer`), a list of findings | `null`; for a manifest with no seeds (a clean-diff control), `true` when no finding is `blocker` or `major` | `n_findings`, `n_severe`, `n_decoys`, `unmatched`, `recall` (`null` with no seeds) | `seeds_hit`, `decoys_flagged`, `recall_by_class` (`"hit/total"`) |
| `dokimi:review_replay` | `gold.json` (`gold`); `task.json` `window`, default 5 | `null` (score-only) | `n_findings`, `n_severe`, `n_threads`, `recall` (= `recall_all`), `recall_addressed`, `precision_proxy` | `threads_hit`, `recall_by_kind`, `decoys_flagged` (always `[]`) |

`manifest.json` is `{"seeds": [{id, class, file, lines, kw}], "decoys":
[{id, file, lines, kw}]}`; `file` `"*"` matches any file, and `kw` is a
regular expression searched case-insensitively in the finding's `rule`,
`message` and `evidence`.

- A finding on a seed's file hits every seed whose `kw` matches. When none
  matches, it hits the one seed (not `"*"`) whose `lines` are nearest, if
  within 1 line.
- A finding that hits no seed is a false positive when it is on a decoy's
  file and matches its `kw` or one of its `lines`; it is listed in
  `decoys_flagged`. Otherwise it counts as `unmatched`.
- `recall` is seeds hit / seeds.

`gold.json` is `[{id, file, line, kind, comment, addressed}]`: one human
review thread of a merged PR, anchored on a line of the reviewed revision.

- A finding on a thread's file within `window` lines of its anchor can hit
  it. Matching is one-to-one and nearest first: one finding credits at most
  one thread, and one thread at most one finding.
- `recall_addressed` counts only threads with `addressed: true` (the code
  changed in response), `recall_by_kind` groups by `kind`, and
  `precision_proxy` is the share of findings within the window of any
  thread, a lower bound on precision. Humans miss problems and raise
  non-defects, so neither is a verdict.
- The oracle answer is one finding per thread, at its anchor.
- `repo tasks replay` writes these tasks from recorded review threads (§4.6).

### 4.6 Replay tasks from review threads

`repo tasks replay` writes `dokimi:review_replay` tasks (§4.5) from the review
threads of merged pull requests, so `gold.json` is not written by hand. It
makes no GitHub call: the threads come from a file `gh api graphql` wrote, so a
private repository works the same way and the command needs no network for a
local source.

```bash
# a window of merged PRs with their threads; --slurp writes the pages as one JSON list
gh api graphql --paginate --slurp -F query=@replay-threads.graphql \
    -F q='repo:IntelPython/dpnp is:pr is:merged merged:2026-10-01..2026-10-27' > threads.json
python cli.py repo tasks replay ~/dpnp --threads threads.json --out ~/dpnp/.agent-eval/tasks
```

`replay-threads.graphql` asks for the fields the command reads. A search
returns at most 1000 pull requests, so split a larger window by date. The
query reads up to 100 threads per pull request and 50 comments per thread;
raise `first:` for a pull request with more.

```graphql
query($q: String!, $endCursor: String) {
  search(query: $q, type: ISSUE, first: 10, after: $endCursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url author { login __typename }
        headRefOid mergeCommit { oid }
        reviewThreads(first: 100) {
          nodes {
            path originalLine diffSide subjectType
            comments(first: 50) {
              nodes { author { login __typename } body createdAt url originalCommit { oid } }
            }
          }
        }
        reviews(first: 50) { nodes { author { login __typename } body } }
      }
    }
  }
}
```

Any JSON holding pull request objects with `number` and `reviewThreads` is
read: one response, the pages `--paginate --slurp` writes, or a list of pull
requests. A pull request on two pages is read once. A commit that is not given
as a full 40- or 64-character hex SHA counts as missing.

**Gold threads.** A thread is gold when its first comment was written by
someone other than the PR's author and not by a bot (`__typename` `Bot`, or a
login ending in `[bot]`), on a line of the new file (`subjectType` other than
`FILE`, `diffSide` other than `LEFT`, an `originalLine`), and names its
`originalCommit`. Every
other thread is listed with the reason, and so is every non-empty review body
by a reviewer: it has no line and is never gold.

**One task per pull request**, in `<out>/review_replay_pr<number>/`:

- The reviewed revision is the `originalCommit` most gold threads were written
  on (on a tie, the one with the latest comment). `base` is its merge base with
  `mergeCommit^1`, the base branch as the PR merged. That holds for a squash,
  merge-commit or rebase merge; a PR merged by fast-forward (`mergeCommit` is
  `headRefOid`) is skipped, since its `mergeCommit^1` is one of its own commits.
- `change.patch` is the diff from `base` to the reviewed revision as one commit
  titled with the PR's title, binary files included. `git am` strips the CR at
  the end of each line of a plain mail, so a diff holding a CR (a file with
  CRLF line ends) is written base64-encoded, which keeps it.
- `task.json` has `family: review`, `grader: dokimi:review_replay`,
  `score: {metric: recall, max: 1.0}`, `review_patch`, `gold`, `window`
  (`--window`, default 5) and the prompt (`--prompt-file` replaces the default,
  which asks for the findings JSON of §4.5). For the reader it adds
  `source_pr`, `source_url`, `reviewed_commit`, `merge_commit` and
  `left_out_files`.
- `gold.json` holds `{id, file, line, kind, comment, addressed, reply, url}`
  per thread. `id` is `pr<number>-t<n>`, `n` being the thread's position among
  all the PR's threads, so ids stay the same when a thread is dropped. `kind`
  is `unknown`, and `reply` is the PR author's first later comment in the
  thread.

GitHub names a thread's file as it is now, so the file's name at the
thread's `originalCommit` is followed back through its renames from
`headRefOid`, one commit at a time (`git log --follow`). A thread written on
another revision is carried to the reviewed one commit by commit along the
first-parent path between the two, or through the one diff between them when
history was rewritten. It is left out when its line changed on the way, and so
is a thread whose line is not in the file at the reviewed revision. A thread
that a commit on the first-parent path up to the reviewed revision answered (as
`addressed` reads it) is left out too: the change under review holds the fix.

**Guidance and `eval_dir` are left out.** Workspace preparation applies the
arm before the review patch (§3, steps 5 and 7): `guidance:none` deletes the
files the guidance globs match and `guidance:rev:` replaces them, so a hunk on
one would not apply, and a guidance file the PR adds would reach every arm.
`eval_dir` is never in a workspace. Files of either kind are left out of the
patch and listed in `left_out_files`, and their threads are left out of
`gold.json`. A pull request that changes nothing else gets no task.

**`addressed`** is true when one of the PR's own commits after the thread
changed its file within `window` lines of its line; deleting the file is such
a change. The command walks the first-parent commits from the thread's
`originalCommit` to `headRefOid` and carries the line through each. A commit
counts when it has one parent and no bot wrote it (`[bot]` in its author's name
or email, as in pre-commit.ci's fixes); a commit GitHub made from a reviewer's
suggestion counts, since accepting it is the response. Any other commit only
moves the line: a merge of the base branch into the PR does, and the commits it
brings in are not on that path, so a change another PR made next to the line is
not a response. When such a commit rewrites the line itself, the walk stops
there with `addressed` false and the thread is listed for a maintainer to
check. A fix that a later merge undid is still addressed, since the author
accepted the comment. When no first-parent path leads from the thread's
`originalCommit` to `headRefOid` (a force-push rewrote history, or the revision
is reached only through a merge), the diff between the two is read instead,
and the thread is listed too.

**Before a run** a maintainer sets `kind`, and sets `addressed` to false where
`reply` declines the comment ("it's intended") but a commit changed the lines
nearby for another reason. A task directory that has a `task.json` is kept as
it is on the next run; `--force` rewrites it.

**Commits.** For a remote source the commits the clone lacks are fetched from
`origin` by SHA: the reviewed revision of a squash-merged PR is reachable only
from `refs/pull/<number>/head`, which GitHub serves. A local repository is read
as it is, so fetch those refs first, for example
`git fetch origin '+refs/pull/*/head:refs/remotes/origin/pull/*'`. A thread
whose revision is still missing is listed and left out.

The command exits 1 when the threads file cannot be read or holds no pull
request, when no pull request gave a task (written, or kept from before), and
on an error that is not one pull request's own: a malformed pull request, the
source, the manifest, the prompt file, or a task it cannot write. Each prints
`✗` and the reason. A git failure while one pull request is built skips that
pull request with the reason.
`repo tasks check` then runs the tasks, and two arms measure the guidance
(§9):

```bash
python cli.py repo compare repo-runs/replay --baseline guidance:none --metric recall_addressed
```

## 5. Aligning oneDAL `dev/agent_evals` (uxlfoundation/oneDAL#3818)

oneDAL drops its standalone mode: the generic parts of its harness move into
dokimi (#41), and `dev/agent_evals` keeps only its tasks, its image and the
graders that know oneDAL. These changes make that content runnable by the
framework.

| Today | Change |
|---|---|
| guidance globs in `config.py` | move to `repo-eval.yaml` `guidance.globs` |
| `task.json` keys | already match the core keys. Add `gate: ["strict_pass"]` to fix tasks, so a hidden-test pass that breaks a regression suite is not counted as success. Add `score: {metric: recall, max: 1.0}` to review tasks with seeds or threads, not to clean-diff controls (§4.5) |
| `base` is a 8–12 character SHA | expand to the full 40-character SHA (keep a trailing `^`) |
| entry points call `dev/agent_evals/run.py` | call `{eval_dir}/run.py`; the graders must not assume they sit inside the workspace (§1.1) |
| `prep()` in `common.py` | goes: the framework prepares every workspace (§3) |
| arms `none` / `main-raw` | become `guidance:none` / `guidance:raw`. `guidance:bridge` is framework-only (§3); oneDAL keeps no bridge arm |
| graders read history from `ONEDAL_EVAL_SRC` | read `{source_dir}`, the only full-history source; the variable goes |
| `contract.py` derives the base when `meta.json` has none | diff from `meta.json` `start_sha`, and read `base_sha` / `base_rev` for the upstream commit (§4); nothing derives it |
| `build_make_gnu` pip-installs MKL and TBB | install them in the image, or give the task `egress: [pypi.org, files.pythonhosted.org]` |
| Bazel install base in the container's `HOME` | put it on a declared cache (`bin/bazel --output_user_root`) |
| `static_check.py --out` writes `files` / `counts` / `claims` | add `"schema": "repo_static.v1"`; the claim fields already fit §4.4 |
| `run.py grade <run_dir>` writes a flat dict | `run.py grade --contract` reads the §4 inputs and writes `repo_grade.v1` (scalar fields under `metrics`, lists and objects under `details`); it stays for the graders that are not built in: `build.py` and `make.py`, which know oneDAL, and the fix and mutation graders until `dokimi:hidden_tests` and `dokimi:mutation` exist (#41) |
| `graders/review.py` and `replay.py` (task `grader` `review`, `review_replay`) | review tasks name `dokimi:review_seeds` or `dokimi:review_replay` (§4.5) and keep `manifest.json`, `oracle.json` or `gold.json`; both files go. The built-in graders give the same metrics for the same answers |
| graders read Claude stream-json via `traces.py` | read `events.jsonl` instead; `traces.py` becomes the Claude Code → events converter, which moves to the framework's harness profile |
| oracle logic spread across `run.py check` | expose it as the `oracle` entry point; review tasks need none (§4.5) |
| `bin/bazel` wrapper, shared caches, `MKLROOT` removal | expressed in the Dockerfile and `image.caches` / `image.env` |
| clean-ancestor guard, no-background hook, per-run config dir | done by the framework for every harness |
| fix and feature tasks have no review-level grading | add `rubric.json` per task ([mergeability](repo-evaluation-mergeability.md) §2), starting from the tasks mined from reviewed PRs; maintainers approve each rubric |
| `static_check.py` | exposed as the `static` entry point. Its generic parts (links, paths, identifiers, fences) are framework L0 checks (§8); oneDAL rules such as the `//...` policy stay in oneDAL, and `repo scan --static` merges them |

## 6. Harness profiles

A harness is described by a `harness_profile.v1` file. Built-in profiles live
in `dokimi/repos/profiles/`; `--harness` also takes a path. The model
is passed through as an opaque string.

```yaml
schema: harness_profile.v1
name: claude-code
bin: claude                          # --bin overrides it, for example to pin a CLI version
version_cmd: ["{bin}", "--version"]
invoke: ["{bin}", "-p", "{prompt}", "--model", "{model}", "--output-format", "stream-json",
         "--verbose", "--max-turns", "{max_turns}", "--max-budget-usd", "{budget}",
         "--no-session-persistence", "--strict-mcp-config",
         "--permission-mode", "bypassPermissions", "{tools}"]
tools:                               # what the "{tools}" element expands to
  none: ["--tools", ""]
  read: ["--tools", "Read"]
  all:  ["--disallowedTools", "WebFetch", "WebSearch"]
delegation:                          # --delegation none: these replace the tool mode's arguments
  none:
    none: ["--tools", ""]            # list every tool mode a run may use, even if unchanged
    read: ["--tools", "Read"]
    all: ["--disallowedTools", "WebFetch", "WebSearch", "Agent", "Task", "Workflow"]
config_dir_env: CLAUDE_CONFIG_DIR    # a fresh directory per run
env_allow: [PATH, HOME, AWS_REGION, AWS_BEARER_TOKEN_BEDROCK, ANTHROPIC_API_KEY,
            CLAUDE_CODE_USE_BEDROCK, https_proxy, no_proxy, ...]
env_set: {CLAUDE_CODE_DISABLE_BACKGROUND_TASKS: "1", DISABLE_AUTOUPDATER: "1"}
egress: ["bedrock-runtime.*.amazonaws.com", "api.anthropic.com"]   # the only hosts the agent container reaches (§2)
endpoint_env: ANTHROPIC_BASE_URL     # used by payload capture, if set
trace: claude-stream-json            # converter to events.jsonl; or: text
guidance:                            # DECLARED; verified by the reach probe, never trusted
  names: [CLAUDE.md, CLAUDE.local.md, AGENTS.md]
  resolution: ancestors-of-cwd + lazy-on-subtree-access
  imports: "@path"
bridge: {for: AGENTS.md, write: CLAUDE.md, content: "@AGENTS.md"}
args_if_present:                     # argv added after the "{tools}" element when the workspace file exists and the tool mode is `all`
  .mcp.json: ["--mcp-config", ".mcp.json"]
```

- `invoke` and `version_cmd` are argv lists. Each element becomes exactly one
  argument after substitution, and nothing passes through a shell.
- The placeholders are `{bin}`, `{prompt}`, `{model}`, `{max_turns}` and
  `{budget}`; `version_cmd` takes only `{bin}`. A substituted value is never
  substituted again. `invoke` holds the element `"{tools}"` exactly once.
- The child gets only the `env_allow` names, then `env_set`, then
  `config_dir_env`. User settings, memory and unrelated credentials stay out.
  In a container the host's `PATH` and `HOME` are dropped: the image's `PATH`
  and the per-run `HOME` apply.
- Optional `prompt_suffix` is appended to every task prompt, and optional
  `config_files` (name → content) are written into the per-run config
  directory, with `{config_dir}` and `{support_dir}` (the framework's helper
  scripts) as the agent's container sees them. The built-in `claude-code`
  profile uses them for a hook that refuses backgrounded shell commands.
- Optional `delegation.none` maps a tool mode to the arguments `repo run
  --delegation none` uses instead, so the agent cannot start a subagent. It
  is an allow-list: list every tool mode the run may use (for `none` and
  `read` the arguments are the native ones); a tool mode it leaves out is
  refused. A profile without `delegation.none` refuses `--delegation none`
  before anything runs. The default, `--delegation native`, runs the tool mode
  as written: under `all`, Claude Code may start subagents.
  `--delegation none` limits the tools the agent is given, not the skills it
  loads: a skill whose frontmatter says `context: fork` can still start a
  subagent under `none`. Do not read a `none` cell as subagent-free when the
  run's skills include one (tracked in intel/dokimi, "repo run: block
  fork-context skills under --delegation none").
- `args_if_present` checks that the file exists, not that the harness accepts
  it. The built-in `claude-code` profile passes `.mcp.json` as `--mcp-config`
  in the `all` tool mode, and the CLI exits 1 before its first turn on a file
  that is not valid MCP configuration (`{` for one). So a `.mcp.json` that is
  not a JSON object, or whose `mcpServers` is not an object, is caught up
  front: `repo validate` warns about the one at each task base (at
  the eval tree when the source has no git history) and still succeeds, `repo run --tools all`
  exits 1 before any cell, and a cell whose arm wrote
  such a file (a `patch:` arm) is a cell error, not a graded run. The check
  reads the task base, so a `patch:` arm that repairs the file does not help.
  Fix or remove the file in the task base; `--tools read` or `none` do not
  pass it, so `repo run` does not check it there.

Profiles for other harnesses (Codex, Gemini CLI, Copilot CLI, Cursor agent,
Aider, OpenCode, in-house agents) follow the same shape. Their `guidance`
blocks start as the vendor's documented behaviour and are corrected by the
reach probe. Loading changes between versions of one harness, so
reach results are keyed by version.

## 7. Reach probe (`guidance_reach.v1`)

Protocol:

1. Build a skeleton workspace in a temporary directory: the guidance files at
   their repository paths, one probe file per guidance directory, and a
   one-commit git repository.
    - Guidance files are those matching `guidance.globs`, plus every file named
      like the profile's `guidance.names`, outside `eval_dir`.
    - Loading depends on paths and names, not on the rest of the tree. A
      skeleton also keeps a blob-less clone from fetching every blob.
    - The probe refuses to run if any ancestor of the workspace holds a
      guidance file.
    - It also refuses a tree path with a `\`, a `:`, or a `.`, `..` or `.git`
      component, which would be written outside the workspace or into its git
      directory.
    - A symlink is one file, as in a checkout. With `CLAUDE.md -> AGENTS.md`,
      `AGENTS.md` is seeded once and `CLAUDE.md` is recreated as a link to it,
      so a harness that loads `CLAUDE.md` loads `AGENTS.md`'s tokens. Where the
      OS refuses a symlink (Windows without Developer Mode), the link is written
      as a copy of the seeded file, and `alias_kind: copy` says so: a harness
      that skips symlinks would then still show the file loaded.
    - A link that escapes the tree, dangles, loops or names a directory is not
      seeded. It is listed in `unresolved` with the reason.
2. Seed the workspace. Each guidance file gets a head token, placed after any
   YAML front matter, and a tail token. A token is `RC` plus the first 6 hex
   digits of sha256(salt, path, position).
3. Run each scenario once per (harness, version, model), with a cheap model.
   Each run gets a fresh config directory. The harness runs on the host, not
   in the agent container (§2), so the profile's `egress` list is not enforced.
    - `cwd=.`: started at the repository root, tools off.
    - `cwd=<dir>`: started in each guidance directory, tools off.
    - `read=<dir>`: started at the root, read tool on. The agent is told to
      read `<dir>/REACH_PROBE.txt`, then list tokens.
    - A scenario that runs past `--timeout` is an `error`; its raw stdout up
      to the kill is kept in the trace. Its harness is killed with its process
      group (POSIX) or job object (Windows), and so is anything left in that
      group when the harness exits on its own. The group is also killed when
      the `dokimi` CLI gets SIGTERM or SIGHUP (from `timeout(1)`, a CI
      cancel or a closed terminal); the CLI then exits with that signal. Not
      contained: the group of a CLI killed with SIGKILL, of a library caller
      that did not call `process.install_signal_handlers()`, and a process
      that leaves the group, such as one that calls `setsid()` or daemonizes.
      These can outlive the run with the harness's environment, credentials
      included.
4. Ask for every token in the instructions or context, including context
   attached to a tool result. Parse the reported tokens from the answer, and
   the reads from `events.jsonl`.

Output: `<out>/reach.json` and `<out>/traces/` (raw stdout and `events.jsonl`
per scenario).

- One record is written per (file, scenario):

    ```json
    {"file": "sub/AGENTS.md", "scenario": "cwd=.", "harness": "claude-code",
     "harness_version": "2.1.286", "model": "claude-haiku-4-5",
     "head": false, "tail": false, "read_by_tool": false, "class": "not_loaded"}
    ```

- `file` is the link target. Links to it are listed once, as `aliases` of
  its entry in `files`; they share its tokens and its records.
- `class` is one of:
    - `loaded`: both tokens reported;
    - `truncated`: one token reported;
    - `fetched`: reported only after the agent read the file with a tool;
    - `not_loaded`;
    - `error`: the scenario produced no usable answer: no answer, a non-zero
      exit, or an error result.
- Each scenario also records:
    - `unknown`: tokens the model reported that were never seeded;
    - `prose`: words besides tokens. A high count usually means an explanation
      or a refusal, so read that trace before trusting its `not_loaded` cells.
- If payload capture is available, a `payload` field records whether each
  token was in the request bytes. This settles cells where the model
  under-reports, which is how a small model produces false `truncated` cells.

## 8. L0 scan (`repo_readiness.v1`)

`repo scan <source>` checks every mechanically checkable claim in the guidance
files (`guidance.globs` plus `also_scan`) against the same snapshot. It makes
no LLM call and needs no checkout: a blob-less clone fetches the blobs it reads
in one request.

| Check | Claims |
|---|---|
| generic | Markdown links (inline and reference definitions, `<angle-bracket>` targets; relative, and blob/tree links to `source.url`), paths and globs in code spans (see below), identifiers (a word in some tracked file that is not prose; a missing ALL_CAPS name is `unresolved`, since a tool outside the tree may read it), `python <script>` paths, a command wrapped in backticks inside a shell fence, Python versions on a line that names a workflow, against what its jobs run (see below) |
| `bazel` | `--config` values against `.bazelrc` and its imports, flag aliases, labels against the target and output names (`name`, `outs`, and the files of a top-level `exports_files([...])`) that the package's `BUILD.bazel`, else `BUILD`, spells out in either quote style (a package that calls a macro or computes a name, `"t_" + s`, a comprehension or `exports_files(glob(...))`, gives `unresolved`, not `fail`), macros and keyword arguments in Starlark fences against tracked `.bzl` files, `load()` symbols against the public top-level names (a `def` or an assignment) of the in-tree `.bzl` an absolute label names, labels of an external repository against its BUILD file when the tree holds one (see below), and `@<module>//` alone as this repository |
| `make` | targets against the rules of every makefile (a `%:` catch-all or a rule named by a variable gives `unresolved`), `VAR=value` against the makefiles |
| `cmake` | `-D` variables against the CMake sources (CMake built-ins and `<Pkg>_DIR`/`_ROOT` pass), `--preset` against `CMake(User)Presets.json`, `-S <dir>` against a `CMakeLists.txt` in that directory (`ok` when any ancestor directory of the guidance file holds it; `fail` only after a literal `cd`, and there a miss inside a declared submodule is `unresolved`; without a `cd` the directory the command runs in is not known, so a miss is `unresolved`; a glob such as `examples/*` is not a literal directory and gets no claim; a `%VAR%`, a drive path such as `C:/x`, and a `-S` word holding a backslash get no claim) |
| `pytest` | path arguments and `::test` nodes; with `--pyargs`, a dotted name that is no path in the tree is a module: `pkg.tests` is `pkg/tests.py` or `pkg/tests/__init__.py` below any directory (a directory with no `__init__.py` is no module, as in pytest by default). `ok` means that file is in the tree, not that it imports without an install (a `src/` layout needs one); `fail` when the tree holds a package the name lies in but not the module (`pkg.tests.test_a` with `pkg/__init__.py` and no `pkg/tests/`), and that package is where an import would find it: at the root or in `src/`, of the tree or of a directory from the guidance file up; `unresolved` when the tree holds no such package (an installed one), or holds it only deeper (`docs/examples/pkg/`, a vendored copy), where it may not be the one imported |
| `pre-commit` | `pre-commit run <hook>` against hook ids and aliases |

`build_systems` picks the adapters. Without it they are detected from the
files at the root (any `conftest.py` or a pytest section also selects
`pytest`), and the report says so.

Starlark is a Python dialect, so guidance often fences a BUILD snippet as
`python`. With the `bazel` adapter on, such a fence is checked as Starlark when
it reads as BUILD code: it loads a label, or a line starts a call to a native
rule only Bazel has (`cc_library`, `http_archive`). A call to a macro of a
tracked `.bzl` file does not count (`run(` and `build(` are as often plain
Python), nor does `test_suite(`. Any other `python` fence is not checked.
Every string of a checked fence that is a label is looked up, in either quote
style; a string such as `//cdn.example.com/x.tar` (a host name) is not a label. An
external repository's BUILD file is in the tree when a tracked file is named
for the repository (`zlib.BUILD`, `BUILD.zlib`, `mkl.tpl.BUILD`,
`mkl_win.tpl.BUILD`, with an optional `.bazel`) and a `.bzl` file,
`MODULE.bazel` or `WORKSPACE` names that file, as a `build_file` or a
template, and `MODULE.bazel` does not take the repository from a registry
with `bazel_dep` (its `repo_name`, else its `name`). A label in the root package of such a repository is `ok` when one of
those files spells the name out (or lists it in `exports_files([...])`), or
the target is `all`, `*` or `all-targets`. It is `unresolved` when one of those
files computes names (a macro, `exports_files(glob(...))`, or a template's
`%{...}` substitution), when the target contains a `.`, or when the name
appears as any string in the file (a source in `srcs`); otherwise it is
`fail`. Labels of any other
external repository stay `unresolved`. A `BUILD`, `.bzl`, `WORKSPACE` or `MODULE.bazel` file that was not read
(binary, over the size limit, a dangling or escaping link) makes its `bazel_label` and `starlark_load` claims
`unresolved`, with the reason (`X was not read (...)`). Such a file also adds the warning
`build file not scanned: <path>: <why>` and makes the scan `incomplete`. Only these exact names count: `BUILD`,
`BUILD.*` (`BUILD.bazel`, an external `BUILD.zlib`), `WORKSPACE`, `WORKSPACE.bazel`, `MODULE.bazel`, `*.bzl`, `*.bazelrc`, `*.BUILD` (and an unread
`.bazelrc` import of any name); a file such as `BUILD_status.png` does not, and none counts unless `bazel` is a build system of the scan.

L0 checks only what holds for any repository. The rules of oneDAL's own
checker that are repository policy were not ported, and oneDAL dropped them
along with that checker: `bazel test` and `bazel run` without `--config`, the
refusal of `//...` and of bare `cc_library`/`cc_test`, a Starlark call to
anything not defined in its three `.bzl` files, `PLAT=`/`COMPILER=` values
from its `dev/make` tree, and a label or make target that the build files do
not spell out literally (L0 reports that one `unresolved`, because a macro or a
computed rule name may make it). A repository that wants rules like these adds
them through its own `static` entry point (§4.4), merged as described below.

`--static <file>` merges the output of the repository's `static` entry point
(§4.4). A repository claim replaces the framework claim on the same file, line
and claim text, because the repository knows its own rules; `by_source` keeps
both sides' counts before the merge. Running the entry point itself belongs to
the task runner (S5), which runs entry points in the image.

Shell lines are split at `&&`, `|`, `;` and the like, and redirections are
dropped, so each simple command is checked on its own. A command continued
with a trailing backslash is one line, reported at its first line; a comment
line ends a continuation, and the body of a heredoc is not a command. A word
that holds an expansion (`$VAR`, `${VAR}`, `$(cmd)`, a backtick substitution)
is never looked up: not as a make target, a `-D` variable, a preset, a
`--config` value, a hook id, a path or a label. The literal key of
`VAR=$(cmd)` is still checked. A `<placeholder>` inside a word
(`//pkg/<algo>:tests`, `make <target>`) is one too, not a pair of
redirections.
A literal `cd dir` moves the relative paths of the later commands in the same
fence or span, and a subshell ends at its closing parenthesis. After a `cd` to a
directory the tree lacks or to a non-literal one, after `pushd`/`popd`, and
after a `cd` that follows `||` or `|`, relative paths are `unresolved`.

A path in a code span is tried at the file's directory and each of its
ancestors. If none has it, the path is `fail` when its parent directory exists
at one of those bases (a rename or a deleted file), `ok_suffix` when it ends a
longer path in the tree (a root file naming paths inside a subproject), and
`unresolved` otherwise (a build output, a path inside a submodule). A missing
path that a tracked `.gitignore` ignores is `unresolved` rather than `fail`,
with the `.gitignore` line in the reason (also for a path that was `unresolved`
already): guidance that warns about generated files names paths no snapshot
holds. A directory counts as ignored when git ignores it, or, when no `!` line
names the directory itself (`!*/` names every directory, so it does not count),
would ignore every file probed directly inside it: a plain name and each name a
`!` line re-includes. So `include/*` ignores `include/`, and so does `out/*` with
`!*/`, while a whitelist of `*`, `!*/`, `!*.py` does not ignore a missing
`olddir/`, nor `!/setup.py` a missing `setup.py`. A path without a trailing slash counts when it
would be as a file or as a directory: under that whitelist `*` ignores a missing
`olddir` read as a file, so a directory needs its trailing slash to be checked
as a rename. A glob counts when one of the files it is
probed with would be: its wildcards filled in, and each name an ignoring line
matches that the glob matches too (`gen.h` for `pkg/g*.h`), when a literal
starts or ends the glob's last segment: `maintenance/*` or `pkg/*_*` would match
the name of any rule (`_skbuild`), which says nothing about the glob. The probe
tries a few names, not every name the glob matches. `.git/info/exclude` and
`core.excludesFile` belong to a clone, not the tree,
and are not read. `ok_suffix`
and `ok_basename` resolve a path but cannot see it renamed, which then reads as
`unresolved`; guidance the gate must protect names its paths from the repository
root. A bare name
that names some file or directory is `ok_basename`. A glob is tried the same
way: `ok` when something matches at the file's directory or an ancestor, `fail`
when its literal parent directory exists there and nothing in it matches
(`src/*.zzz`), `ok_suffix` when it ends a longer path in the tree, and
`unresolved` otherwise (`build/*.o`, `out/<run>/grade.json`). A `<placeholder>`
or a wildcard in the directory part is never `fail`. A `*` may span
directories and `**/` matches zero or more. A trailing slash on a file
(`src/mod.py/`) is `fail`. A GitHub Action reference (`actions/checkout@v6`) is not a
path claim. A link or path into a submodule is `unresolved`. A
blob or tree link to `source.url` holds `<ref>/<path>` and the ref may contain
slashes. A default branch, a commit or a version tag is one segment; any other
ref is tried at each slash, shortest first, and the link is `ok` if some
remainder is in the tree. Failing that, a blob link is `fail`, and so is a tree
link on a one-segment ref; a tree link on any other ref is `unresolved` (it may
name a branch). `refs/heads/` and `refs/tags/` belong to the ref, and a link to
a ref alone is not checked. A
link to a directory inside the tree is followed, as in a checkout, so
`linkdir/core.py` with `linkdir -> src/pkg` is `ok`.

A line that names a workflow of the tree (a code span or a link ending in
`.yml` or `.yaml` under `.github/workflows/`, a blob or tree link to
`source.url` too; a bare file name that several workflows end in is the one in
`.github/workflows/` itself, else none of them) is also checked for the Python
versions it gives. A run of versions that `py`, `Python` or `cp` starts is a
`python_versions` claim when it is a range written with a dash (`py3.12-3.14t`,
`Python 3.12–3.14`) or a list of two or more (`Python 3.12, 3.13 and 3.14`); a
single version gets no claim, `to` is no range ("moved from Python 3.11 to
3.13"), and a `cp` tag starts no range (`cp313-cp313` is the two tags of a
wheel's name). A version with no marker in front needs its dot (`30` in
"Python 3.12, 3.13, 30 min timeout" is no version), and the workflow's own
name, in a code span or a link text, is no version. What the workflow runs is
the values of every matrix key whose name holds `python` (`3.12`, `cp312` or
`py312`), the keys of `include` entries too, less a value that `exclude` takes
out of every combination of the matrix, and the `python-version` its steps set
up (`actions/setup-python`, or another action's input of that name;
`${{ matrix.<key> }}` there makes `<key>` a Python key), over its jobs and the
workflows of this tree its jobs call with `uses: ./…`. In a called workflow, a
step's `${{ inputs.<name> }}` is what the calling job passes as `<name>` in its
`with:`: a version it pins, or its own `${{ matrix.<key> }}`, which makes `<key>`
a Python key of the caller's matrix. A pre-release (`3.14.0-rc.1`, `3.14.0a1`,
`3.14-dev`) runs its minor version, and a `3.14t` value or a `cp314t` tag in
any matrix string is a free-threaded build, less a value that `exclude` takes
out. A range is `ok` when its ends are the lowest and highest version the
workflow runs (both ends written with `t`: of the free-threaded builds), a list
when each version runs, and a version written with `t` must run free-threaded;
anything else is `fail`. A range that holds for the matrices and not with the
versions setup steps pin, or with them and not for the matrices alone (a lint
or docs job on another Python), is `unresolved`, since the range may count that
job or leave it out on purpose; with no matrix version, the pins are what the
workflow runs. A matrix, an `include`, an `exclude` or a step version computed
from an expression (`${{ fromJSON(…) }}`), an `exclude` over more than 256
combinations or too large to read, a Python value that names no one version (`3.x`, `>=3.12`), a
`python-version-file` with no `python-version`, an input a called workflow
sets up Python from that its caller does not pass, a call to another
repository's workflow or to one the tree lacks, a workflow that is not valid
YAML, and a workflow with no Python version make the claim `unresolved`. Python that a job installs any other
way (a `run:` step, a container image) is not read.

Not checked by L0 (a follow-up): the `#fragment` of a link (`docs/x.md#setup`
is checked as `docs/x.md`; a bare `#anchor` is skipped), and HTML in Markdown
(`<a href>`, `<img src>`).

Guidance files are read with symlinks followed, the same way from a working
tree and from a commit. A guidance file that links to another guidance file is
scanned once, as the file it names.

The report lists `warnings` for guidance the manifest names but the tree lacks:
a glob with no match, a missing `also_scan` file, no guidance at all, and a
guidance file that cannot be read (a link that dangles or leaves the tree, a
binary file, a file over 2 MB), named with the reason. A `--static`
file that cannot be read or is not JSON exits 2; a document that fails the
`repo_static.v1` schema is not merged, and the command exits 1. `--fail-on-fail` also exits 1
when any claim fails, an `also_scan` file is missing, a guidance file cannot be
read, a Bazel build input (`BUILD`, `.bzl`, `.bazelrc`, ...) cannot be read, or no guidance file matched
(`incomplete`). A glob that matches nothing
does not fail the gate, so a file the gate must see belongs in `also_scan`.

Statuses are those of §4.4. Only `fail` counts against the guidance;
`unresolved` needs a human look. Not implemented yet: the pre-commit↔CI
file-set parity check.

Measured on oneDAL and scikit-learn-intelex, claim by claim against their own
checkers: see the pull request that added the scan (#68).

### 8.1 Inspection (`repo_environment.v1`)

`repo inspect <source>` reads the tree and nothing else: no manifest, no LLM
call, no command of the repository run. Where L0 asks whether what the
guidance says is true, inspection asks what the guidance may have left out:
which instruction files exist and where they apply, how a change can be built
and checked, and what a session or a command it runs could do beyond editing
files. Its findings are hypotheses for `repo reach` and `repo run` to test,
not measured effects.

The report keeps observations apart from findings:

- `instructions`: one record per instruction file and per harness rule it
  matches (a root `AGENTS.md`, a nested `CLAUDE.md`, `.cursor/rules/*.mdc` at
  the root or in a subdirectory, `.github/instructions/*.instructions.md`, …),
  with its `scope` (`root`, `path-specific`, `mode-specific`, `local-private`,
  `legacy`, `capability`, `unknown`), `activation`, the `directory` it applies to
  (for a record whose scope is `unknown`, the one it would apply to) and its size
  (for a link, the size of the file it names). This is what each
  harness documents; which files a given harness version loads is measured by
  the reach probe (§7).
- `checks`: ways to build, test, lint and type-check, per project root (the
  root and every directory below it with a `package.json`, makefile,
  `pyproject.toml`, `setup.py`, `setup.cfg`, `Pipfile`, `go.mod` or `Cargo.toml`; Bazel, CMake,
  Gradle, Maven, .NET and Autotools are read tree-wide). `basis: declared`
  means the repository names it: a script, a make target, a config section,
  a pre-commit hook. `basis: toolchain` means the tool offers the command
  whether or not there is anything for it to run (`go test` beside a
  `go.mod`): a place to start, not evidence of tests.
- `capabilities`: what the harness gets from the repository: MCP configs,
  Claude Code hooks of any type (`command`, `http`, `prompt`, `agent`) and
  plugins, settings commands (`statusLine`, `apiKeyHelper`),
  `enableAllProjectMcpServers`, skills, editor settings, each with a risk tier
  (documentation under `.claude/hooks/` is low; a `.claude-plugin/` manifest is
  medium, and high when it declares `hooks` or `mcpServers` or cannot be read). `scripts`: root
  `package.json` scripts that are install hooks, delete the root, home, a glob
  or the whole tree (`rm -rf dist` is not flagged; `git clean -n` is a dry
  run), pipe a download into a shell, or publish and deploy.
  `hook_risks`: the concrete path, a hook the harness fires on its own
  (`SessionStart`, `SessionEnd`, `PreCompact`, `Notification`, `UserPromptSubmit`, `Stop`, `SubagentStop`) that installs
  packages with lifecycle scripts on, runs a `package.json` script (`npm run`, `npm test`, `yarn build`, …),
  `make`, or executes a path in the tree (also behind `VAR=value`, `sudo -u x`, `nice -n 10`, `timeout -s KILL 30`,
  `env -u X`, `/usr/bin/env`, `source`, `sh -c`, `eval`, `$(…)` in double quotes, or `echo '…' | sh`),
  so starting a session or running tools executes code from the checked-out branch. Hook
  commands are read word by word, with quotes (also across lines) and comments
  honoured, so a command named inside `echo '…'` or after `#` does not count. A heredoc or
  here-string body counts only when a shell reads it (`bash <<EOF`, `cat <<EOF | sh`,
  `bash <<< '…'`), not under `bash -c` or a non-shell command. `npm update`, `dedupe` and the like
  count as installs. `--ignore-scripts` (the last such flag wins), `npm_config_ignore_scripts=true`,
  a root `.npmrc` with `ignore-scripts=true` (npm, pnpm) and a global install (`-g`, `yarn global`)
  clear the install risk. A `.claude/settings.json` or `.claude/settings.local.json` that cannot be
  parsed is listed under `not_observed`, since its hooks were not read.
- `files`: counts and bytes, generated files (lockfiles, `vendor/`,
  `third_party/`, test fixtures, `*_pb2.py`, …), minified bundles, test files,
  and files over `--large-bytes` (default 100 000, about 30k tokens: large
  for an agent to read, far below what a disk-size lint flags). From a
  blob-less clone sizes are not read, since listing them would fetch every
  blob; the report says so under `not_observed`.

Each finding has a `rule`, a `severity` (`error`, `warning`, `info`) and,
separately, a `confidence`, the `stages` it affects (`orient`, `bootstrap`,
`navigate`, `edit`, `verify`, `deploy`), a `path` (null for
the whole repository) and `why`. Only stages that some warning or error can
reach are reported; how a change is reviewed and merged is not visible in the
tree and is listed under `not_observed`. A stage is `blocked` by an error,
`attention` on a warning, and at best `no_known_blocker`, never ready:
`not_observed` lists what the tree cannot show, such as whether the commands
pass, CI secrets and the agent's network. `risk` is the worst of the launch
observations. There is no overall score. `--fail-on-error` exits 1 when any
finding is an error.

## 9. Running

`repo tasks check` and `repo run` need docker. Both export `eval_dir` from the
eval tree to `<out>/_eval/content`, and build the image from the eval tree
without its tasks directory (tag `dokimi/<id>:<sha>`) unless
`--image` names one. Cache directories default to
`~/.cache/dokimi/caches/<id>/<name>`; `--cache NAME=PATH` points one
elsewhere. `{source_dir}` is the source's own git directory, or
`--source-dir`.

Cloning and fetching a remote source (`url@rev`, in every `repo` command) is
non-interactive by design: git runs with `GIT_TERMINAL_PROMPT=0`, stdin
closed and no terminal, so neither git nor ssh prompts on a terminal for a
password, a token, a key passphrase or a new host key. An askpass program the
environment names (`GIT_ASKPASS`, `SSH_ASKPASS`, or `core.askPass`) still runs:
it is how some CI systems and editors hand git a token, and a graphical one
(VS Code sets `GIT_ASKPASS`) can still show a dialog. Set up access before
the run: a credential helper holding a token for an `https://` URL (for
GitHub, `gh auth setup-git`), or for an SSH URL a key loaded into
`ssh-agent` and the host in `~/.ssh/known_hosts`. When git would have prompted, the error names the line
git or ssh printed (`Host key verification failed.`,
`Permission denied (publickey)`, `could not read Username ...: terminal
prompts disabled`) and the fix; any other git failure shows its last `fatal:`
line, else its last `error:` line, else its last line.

```bash
# self-check: no LLM; exits 1 if any task is broken or unsupported, or none is ok
# (with --allow-unsupported, a task this host cannot run does not fail it on its own)
python cli.py repo tasks check ~/oneDAL@<sha> --manifest dev/agent_evals/repo-eval.yaml \
    --task fix_finiteness_mut --build-network host --cache bazel-repo-cache=/cache/bazel-repo -j 3

# agents: one cell per (task, arm, model, rep) under repo-runs/<batch>/
python cli.py repo run ~/oneDAL@<sha> --manifest dev/agent_evals/repo-eval.yaml \
    --task fix_finiteness_mut --arm guidance:none,guidance:raw \
    --model us.anthropic.claude-haiku-4-5-20251001-v1:0 --reps 3 --batch pilot \
    --bin /opt/claude-2.1.286/claude --agent-network host --budget 1 --max-batch-usd 20
python cli.py repo report repo-runs/pilot
python cli.py repo compare repo-runs/pilot --baseline guidance:none --metric root_cause_fixed
```

```bash
# review tasks from the threads of merged PRs (§4.6): no LLM, no docker, no GitHub call
python cli.py repo tasks replay ~/dpnp --threads threads.json --out ~/dpnp/.agent-eval/tasks
python cli.py repo run ~/dpnp --task review_replay_pr3107 --arm guidance:none,guidance:raw \
    --model claude-sonnet-5-5 --batch replay --agent-network host
python cli.py repo compare repo-runs/replay --baseline guidance:none --metric recall_addressed
```

- Each cell holds the §4 inputs plus `agent.stdout` (the raw trace) and
  `result.json`, the framework's verdict. A cell with `result.json` is skipped
  on a rerun unless `--force`; a cell without one is reported as missing, not
  failed, and the next run of the batch fills it. `meta.json` and
  `result.json` record `execution`: the tool mode, the delegation mode (the one
  whose arguments ran; a skill with `context: fork` can still start a subagent
  under `none`) and an
  `id` hashing the profile's name, `invoke`, tool arguments, `env_set`,
  `env_allow`, `config_dir_env`, `config_files`, `prompt_suffix`, `egress`,
  `bridge`, the SHA-256 of the harness executable (`--bin`) and harness version.
  A rerun whose `id` differs (another `--tools`, a `--delegation` that changes the tool arguments, profile,
  harness binary or harness version, or an edited `invoke`, `env_set`, `env_allow`, `config_dir_env`, `config_files`, `prompt_suffix`, `egress` or `bridge`) is refused before any agent starts, and
  `--force` or another `--batch` runs it. `--force` replaces the cells of the
  run's plan only: if the batch dir holds finished cells outside that plan
  whose `id` differs from this run's (e.g. `--reps 2`, then `--reps 1
  --delegation none --force`), the run is refused before any agent starts, because
  the report would pool both executions in one row; use another `--batch`. The
  same goes for the finished cells of a task this host cannot run, which
  `--force` keeps; that refusal comes after the host probe, before any agent starts. A result from before `execution` was
  recorded is refused as `unrecorded`, not counted as native; `--force` or
  another `--batch` runs it. A batch stays on the eval tree it
  started with: its revision, or for a working tree the digests of its `eval_dir`
  and of the image build context. A batch with a task that
  names a `dokimi:` grader (or a task directory that cannot be read) also stays on the built-in graders' sources.
- The agent's container mounts the workspace, the per-run `home`, `config` and
  scratch directories (each `image.env` value under `{run_dir}/`), the caches,
  the harness executable (`--bin`, read-only at `/eval/harness`) and the
  framework's helper scripts (read-only at `/eval/support`). Before a batch,
  the harness must report its version inside the image, and a probe container
  with the same mounts must see neither `/eval/content` nor `/eval/source`, no
  entry named like a task, and no guidance file above the workspace.
- `--timeout`, `--max-turns` and `--budget` apply unless `task.json` sets
  `timeout_s`, `max_turns` or `budget_usd`. On a timeout the `docker run`
  CLI gets SIGTERM, which it passes to the container, and 10 s to stop it;
  then the CLI's process group is killed, the container is killed by name,
  and the cell is still graded. `--max-batch-usd` starts no new cell
  once that much has been spent.
- Every container runs with `--security-opt no-new-privileges`, `--cap-drop
  ALL` and a `--pids-limit`, as the caller's uid and gid.
- `--agent-network` is required while the egress proxy is missing: it gives the
  agent's container that docker network, so the agent's commands can reach any
  host, which is what §2 forbids. `meta.json` records it and the report warns.
- The report gives, per (task, arm, model), passes over finished cells with a
  Wilson 95% interval (score-only tasks: the mean of `score.metric`), grader
  failures (counted as failures too), missing cells, and mean cost and turns.
  When cells carry `result.json` `process` (§4.1), a line under the row gives
  the mean of each count over the cells that have it (`guidance_read` counted
  as paths; `counter:<name>` for `process_metrics`); a `null` or absent
  `process` is left out, not counted as zero.
- `repo compare` reduces cells to one value per (task, arm, model) first:
  repetitions share a task's difficulty and are not paired across arms, so the
  task is the unit. Per treatment arm, model and measure (gate share, score,
  cost, turns, each `--metric` from `grade.json`, and each `process` count a
  compared cell reports as `process:<key>` or `counter:<name>`) it gives the mean per-task
  delta against the baseline with a 95% interval over tasks (a bootstrap for
  gate and score, a t interval for cost, turns, metrics and process counts, `-` for a single
  task), the tasks up, down and tied, and a two-sided sign-flip permutation p (exact up to 16
  changed tasks; at one cell per task and arm it equals McNemar's exact test).
  A `process` count only one arm reports for the shared tasks of a
  (treatment, model) is not compared; a warning names it. Holm's correction runs over the gate and score comparisons of one call; the
  rest are descriptive. A gate or score comparison carries `headroom`:
  `ceiling` (both arms at 1.0 or the task's `score.max` on every paired
  task), `floor` (both at 0 on every one), `near_ceiling` (the baseline at
  the top on all but at most one of 2+ tasks and the treatment nowhere below
  it), else `null`; a grader failure in either arm also gives `null`, as does
  an agent outage (an error result with no turn count or at most one turn, or
  a harness that exited non-zero without reporting a turn and was not killed
  at its timeout): neither is a measurement. A pair
  is at the top only when both arms reach it and tie: a score past `score.max`
  is an effect, not a top. The text prints a note under a flagged comparison. A
  zero delta there means no room to move, not no effect. Cost, turns and
  metric rows have `headroom` `null`.
  The top of a score task is the `score.max` that `repo run` records in
  `result.json` as `score_max`: a batch run before that has no known top, so
  its score rows are never `ceiling`, and arms whose tops differ are on two
  scales and get `null`. `BATCH/ARM` takes an arm from one batch only, so two
  batches of the same arm (for example two revisions of the guidance) compare
  too; two selectors of the same cells are refused. A `--metric` no finished
  cell reports as a number or boolean is refused. Tasks left out are listed as
  unpaired with the reason: no cells in one arm, a task that is gated in one
  arm and score-only in the other, or a measure (such as a metric) one arm
  lacks. A metric's `n_baseline`/`n_treatment` count the cells that report it.
  With repeated cells per task (from `repo run --reps N`), two descriptive measures read the repetitions themselves:
  `--pass-k K` adds `pass^K`, the chance K of a task's cells all pass its gate
  (`C(c,K)/C(n,K)`; a task with fewer than K cells has none), so an arm that
  moves a task from 2/3 to 3/3 is told apart from one that moves the rate
  without making any task reliable; `--path-stability` adds `path_distance`,
  the mean order distance of the cells' tool paths from `events.jsonl` (0 same
  steps in the same order, 1 nothing in common; see
  [run-consistency.md](run-consistency.md)). A steadier path is not a better
  one: read it beside the gate.
  The output names the batches behind each selector and each batch's pin
  (`tree`, `eval_dir`, `image`, `graders` from `_eval/batch.json`) with its cells'
  harness versions, and warns when a bare `ARM` pools several batches, when a
  batch of a comparison has no pin, or when the two arms ran from different
  `eval_dir`, `image` or `graders` pins or harness versions. An image `repo run` tagged
  from the tree (`dokimi/<id>:<sha>`) follows the tree, so two revisions are
  not warned about it; an `--image` of its own is compared. `repo report` warns
  (`model_mismatch` in its summary) when a cell's `model_resolved` is another
  model than the one requested; a snapshot of the requested id (`-20250929`,
  `@20250929`) or its provider-prefixed form is the same model, `-v2` against
  `-v3` is not. `repo compare` warns the same way when the two arms of a
  comparison were answered by different models.

CI for a repository: `.github/workflows/repo-eval.yml` is a reusable workflow
that runs the no-LLM checks on the caller's checkout and needs no secret.

- `validate` runs `repo validate` and gates the other two jobs; `scan` runs `repo scan`
  (report only: the claim counts and failing claims go to the job summary, the
  `repo_readiness.v1` report to an artifact); `tasks-check` runs `repo tasks check`
  for each selected task in a job of its own (docker; the image is built from the
  manifest's Dockerfile), and uploads `check.json` and the grade logs. If the job is
  cancelled by its `timeout-minutes` before the check finishes, the Summary step
  writes a `check.json` with the verdict `timeout` for the task.
- `tasks: changed` (the default, also for an empty value) checks, on a pull request, the tasks whose
  directories it touches, plus `smoke-tasks` (empty: every task) when it changes
  the eval directory outside its tasks (Markdown there is excepted, unless it is the manifest,
  the calling workflow file or under a `smoke-paths` entry), the manifest, the calling workflow
  file or a path under `smoke-paths` (entries are space- or comma-separated prefixes,
  normalised like the manifest path; keep the trailing slash for a directory). A file in
  the tasks directory that is not under an existing task (a shared helper, or the files of
  a deleted task) does too. On a push it diffs against the previous tip, on any other event
  it checks every task. `all` or a list of
  task ids selects directly; `exclude-tasks` always drops tasks.
- Pin dokimi by a commit SHA or a release tag, the same one in `uses:` and in
  `dokimi-ref` (a branch is refused). It installs
  `dokimi @ git+https://github.com/intel/dokimi@<sha>#subdirectory=dokimi` with
  that commit's `requirements.lock.txt` as constraints, so a rerun checks with the
  same framework and dependencies. Moving the pin is a change to the calling
  workflow, so it runs the smoke set.
- Other inputs: `manifest`, `max-parallel` (4), `warm` (`--warm`, for a cache that
  starts empty on every runner), `runs-on` (`ubuntu-latest`), `timeout-minutes`
  (180, per task) and `dokimi-repository` (`intel/dokimi`).

```yaml
# .github/workflows/agent-evals.yml in the repository
name: Agent evals
on:
  pull_request:
    paths: ['dev/agent_evals/**', '**/AGENTS.md', '.github/workflows/agent-evals.yml']
  schedule:
    - cron: '0 3 * * 0'
  workflow_dispatch:
permissions: {}
jobs:
  evals:
    permissions:
      contents: read
    uses: intel/dokimi/.github/workflows/repo-eval.yml@<sha>  # vX.Y.Z
    with:
      dokimi-ref: <sha>
      manifest: dev/agent_evals/repo-eval.yaml
      smoke-tasks: build_kmeans_host fix_finiteness_mut review_seeded_fc
      max-parallel: 4
      warm: true
```

dokimi runs the workflow on its own `.agent-eval/` (`repo-eval-self.yml`), with
the commit under test as `dokimi-ref`.
