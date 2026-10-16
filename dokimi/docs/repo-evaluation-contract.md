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
> python cli.py repo validate <dir | dir@rev | url@rev> --manifest <path> [--json]
> python cli.py repo fetch <url@rev>     # bare, blob-less clone cached per URL
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
> python cli.py repo tasks check <source> --manifest <path> [--task <id>] [--warm]
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
  `{source_dir}` read-only and `{run_dir}` read-write. It gets a `HOME` of its
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
  `{eval_dir}` is `/eval/content` and `{source_dir}` is `/eval/source`. The
  agent container mounts the workspace at `/eval/run/workspace`, the same path
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
  as `.bazelversion`) is read at run time, not baked in.
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
```

Rules:

- Every path is relative to the repository root.
- `eval_dir`, `tasks` and `entry_points` come together or not at all. Without them the
  manifest is *guidance-only*: `scan` and `reach` run on it and `validate` checks its
  globs, while `tasks check` and `run` refuse it. `image` and `explore_tasks` need
  `eval_dir`.
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
belongs to the repository's grader and is passed through unread.

| Key | Required | Read by | Meaning |
|---|---|---|---|
| `id` | yes | framework | equals the directory name |
| `family` | yes | framework (reporting) | free string; common values: `build`, `fix`, `feature`, `test`, `review` |
| `base` | yes | framework | commit to snapshot: a hex SHA, optionally followed by `^` (the parent, the usual form for a mined fix). Branch names, tags and `HEAD` are refused, because repetitions of one task must get one snapshot. Use the full 40-character SHA; a shorter one is accepted with a warning. It must resolve in the source. `meta.json` records it as `base_rev` and the resolved full SHA as `base_sha` |
| `prompt` | yes | framework | what the agent is told |
| `grader` | yes | repository | grader name, interpreted by the `grade` entry point |
| `setup_patches` | no | framework | applied after the snapshot, before the arm (for example an injected bug) |
| `review_patch` | no | framework | applied with `git am` as the HEAD commit under review |
| `gate` | no | framework | what counts as success; default `["pass"]`. Each entry is a boolean metric name, which must be `true`, or `{"metric": <name>, "min": <number>}` for a numeric metric |
| `score` | for score-only tasks | framework | `{"metric": <name>, "max": <number>}`: the metric that is the result when `pass` is `null`, and the value the oracle must reach (§4.3) |
| `timeout_s`, `max_turns`, `budget_usd` | no | framework | per-task caps, overriding run defaults; each a finite number > 0 |
| `egress` | no | framework | extra hosts (DNS names, optional `:port`; `*` stands for one whole label and the last two labels are literal, so `*.pythonhosted.org` passes and `*` or `*.org` does not) this task's agent and entry points reach through the proxy (§2), for example `["pypi.org", "files.pythonhosted.org"]` |
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
5. apply the arm
6. amend the commit
7. apply `review_patch` with `git am` under a neutral author
8. expire the reflog and prune unreachable objects: the
   commits steps 6 and 7 replaced hold the unpatched code, the removed
   guidance and the upstream author, and `git diff HEAD@{1}` would show them.
   Prep's git never runs `gc --auto`, which would pack them where `prune`
   cannot reach

Every `guidance:*` arm is made by the framework from `guidance.globs` and the
harness profile; a repository supplies no arm content:

- `none` deletes every file the globs match at the base; `raw` keeps them.
- `bridge` exists only for a profile with a `bridge` key. It writes that file
  (for Claude Code, a `CLAUDE.md` containing `@AGENTS.md`) next to every
  matched `AGENTS.md`, not only at the root. Whether a harness needs it
  depends on its version: Claude Code 2.1.286 loads `AGENTS.md` itself, but
  only when no `CLAUDE.md` is in its start-up chain, so a repository-side
  bridge arm would mean different things on different versions.

`meta.json` records the base (`base_rev`, `base_sha`, `base_date`), the arm,
the SHA of every applied patch, and `start_sha`: the workspace `HEAD` after
step 7, the commit the agent starts from (§4). `base_sha` names the upstream
commit and is not in the workspace, because step 3 starts a new history; a
grader diffs the agent's changes from `start_sha`.

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
| `meta.json` | `arm`, `harness`, `harness_version`, `model`, `rep`; `base_rev` (as written in `task.json`), `base_sha` (resolved, 40 characters), `base_date` (committer date, ISO 8601); `patches` (SHA of each applied patch); `start_sha` (workspace `HEAD` the agent started from, §3) |

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

Each task runs with its own network (`image.network` plus its `egress`).
`tasks check --warm` runs the same null and oracle steps with network and
writable caches, to fill the caches before offline runs (§2).

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

## 5. Aligning oneDAL `dev/agent_evals` (uxlfoundation/oneDAL#3818)

The harness stays in oneDAL and keeps working standalone. These changes make
the same content runnable by the framework.

| Today | Change |
|---|---|
| guidance globs in `config.py` | move to `repo-eval.yaml` `guidance.globs` |
| `task.json` keys | already match the core keys. Add `gate: ["strict_pass"]` to fix tasks, so a hidden-test pass that breaks a regression suite is not counted as success. Add `score: {metric: recall, max: 1.0}` to review tasks |
| `base` is a 8–12 character SHA | expand to the full 40-character SHA (keep a trailing `^`) |
| entry points call `dev/agent_evals/run.py` | call `{eval_dir}/run.py`; the graders must not assume they sit inside the workspace (§1.1) |
| `prep()` in `common.py` | unchanged for standalone use; under the framework the same steps run framework-side (§3) |
| arms `none` / `main-raw` | become `guidance:none` / `guidance:raw`. `guidance:bridge` is framework-only (§3); oneDAL keeps no bridge arm |
| graders read history from `ONEDAL_EVAL_SRC` | read `{source_dir}` under the framework; the variable stays for standalone runs |
| `contract.py` derives the base when `meta.json` has none | diff from `meta.json` `start_sha`, and read `base_sha` / `base_rev` for the upstream commit (§4); derive only standalone |
| `build_make_gnu` pip-installs MKL and TBB | install them in the image, or give the task `egress: [pypi.org, files.pythonhosted.org]` |
| Bazel install base in the container's `HOME` | put it on a declared cache (`bin/bazel --output_user_root`) |
| `static_check.py --out` writes `files` / `counts` / `claims` | add `"schema": "repo_static.v1"`; the claim fields already fit §4.4 |
| `run.py grade <run_dir>` writes a flat dict | add `--contract` mode that reads §4 inputs and writes `repo_grade.v1` (scalar fields move under `metrics`, lists and objects under `details`) |
| graders read Claude stream-json via `traces.py` | read `events.jsonl` instead; `traces.py` becomes the Claude Code → events converter, which moves to the framework's harness profile |
| oracle logic spread across `run.py check` | expose it as the `oracle` entry point |
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
    - A scenario that runs past `--timeout` is an `error`. Its harness is
      killed with its process group (POSIX) or job object (Windows). A process
      that leaves the group, such as one that calls `setsid()` or daemonizes,
      is not contained. It can outlive the run with the harness's environment,
      credentials included.
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
| generic | Markdown links (inline and reference definitions, `<angle-bracket>` targets; relative, and blob/tree links to `source.url`), paths and globs in code spans (see below), identifiers (a word in some tracked file that is not prose; a missing ALL_CAPS name is `unresolved`, since a tool outside the tree may read it), `python <script>` paths, a command wrapped in backticks inside a shell fence |
| `bazel` | `--config` values against `.bazelrc` and its imports, flag aliases, labels against the target and output names (`name`, `outs`) that the package's `BUILD.bazel`, else `BUILD`, spells out in either quote style (a package that calls a macro or computes a name, `"t_" + s` or a comprehension, gives `unresolved`, not `fail`), macros and keyword arguments in Starlark fences against tracked `.bzl` files |
| `make` | targets against the rules of every makefile (a `%:` catch-all or a rule named by a variable gives `unresolved`), `VAR=value` against the makefiles |
| `cmake` | `-D` variables against the CMake sources (CMake built-ins and `<Pkg>_DIR`/`_ROOT` pass), `--preset` against `CMake(User)Presets.json` |
| `pytest` | path arguments and `::test` nodes |
| `pre-commit` | `pre-commit run <hook>` against hook ids and aliases |

`build_systems` picks the adapters. Without it they are detected from the
files at the root (any `conftest.py` or a pytest section also selects
`pytest`), and the report says so.

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
`VAR=$(cmd)` is still checked.
A literal `cd dir` moves the relative paths of the later commands in the same
fence or span, and a subshell ends at its closing parenthesis. After a `cd` to a
directory the tree lacks or to a non-literal one, after `pushd`/`popd`, and
after a `cd` that follows `||` or `|`, relative paths are `unresolved`.

A path in a code span is tried at the file's directory and each of its
ancestors. If none has it, the path is `fail` when its parent directory exists
at one of those bases (a rename or a deleted file), `ok_suffix` when it ends a
longer path in the tree (a root file naming paths inside a subproject), and
`unresolved` otherwise (a build output, a path inside a submodule). `ok_suffix`
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

Not checked by L0 (a follow-up): the `#fragment` of a link (`docs/x.md#setup`
is checked as `docs/x.md`; a bare `#anchor` is skipped), and HTML in Markdown
(`<a href>`, `<img src>`).

Guidance files are read with symlinks followed, the same way from a working
tree and from a commit. A guidance file that links to another guidance file is
scanned once, as the file it names.

The report lists `warnings` for guidance the manifest names but the tree lacks:
a glob with no match, a missing `also_scan` file, no guidance at all, and a
guidance file that cannot be read (a link that dangles or leaves the tree, a
binary file, a file over 2 MB), named with the reason. An invalid `--static`
document is not merged, and the command exits 1. `--fail-on-fail` also exits 1
when any claim fails, an `also_scan` file is missing, a guidance file cannot be
read, or no guidance file matched (`incomplete`). A glob that matches nothing
does not fail the gate, so a file the gate must see belongs in `also_scan`.

Statuses are those of §4.4. Only `fail` counts against the guidance;
`unresolved` needs a human look. Not implemented yet: the pre-commit↔CI
file-set parity check.

Measured on oneDAL and scikit-learn-intelex, claim by claim against their own
checkers: see the pull request that added the scan (#68).

## 9. Running

`repo tasks check` and `repo run` need docker. Both export `eval_dir` from the
eval tree to `<out>/_eval/content`, and build the image from the eval tree
without its tasks directory (tag `dokimi/<id>:<sha>`) unless
`--image` names one. Cache directories default to
`~/.cache/dokimi/caches/<id>/<name>`; `--cache NAME=PATH` points one
elsewhere. `{source_dir}` is the source's own git directory, or
`--source-dir`.

```bash
# self-check: no LLM; exits 1 if any task is broken or unsupported
python cli.py repo tasks check ~/oneDAL@<sha> --manifest dev/agent_evals/repo-eval.yaml \
    --task fix_finiteness_mut --build-network host --cache bazel-repo-cache=/cache/bazel-repo -j 3

# agents: one cell per (task, arm, model, rep) under repo-runs/<batch>/
python cli.py repo run ~/oneDAL@<sha> --manifest dev/agent_evals/repo-eval.yaml \
    --task fix_finiteness_mut --arm guidance:none,guidance:raw \
    --model us.anthropic.claude-haiku-4-5-20251001-v1:0 --reps 3 --batch pilot \
    --bin /opt/claude-2.1.286/claude --agent-network host --budget 1 --max-batch-usd 20
python cli.py repo report repo-runs/pilot
python cli.py repo compare repo-runs/pilot --baseline guidance:none --metric icpx_hit
```

- Each cell holds the §4 inputs plus `agent.stdout` (the raw trace) and
  `result.json`, the framework's verdict. A cell with `result.json` is skipped
  on a rerun unless `--force`; a cell without one is reported as missing, not
  failed, and the next run of the batch fills it. A batch stays on the eval
  tree it started with: its revision, or for a working tree the digests of
  its `eval_dir` and of the image build context.
- The agent's container mounts the workspace, the per-run `home`, `config` and
  scratch directories (each `image.env` value under `{run_dir}/`), the caches,
  the harness executable (`--bin`, read-only at `/eval/harness`) and the
  framework's helper scripts (read-only at `/eval/support`). Before a batch,
  the harness must report its version inside the image, and a probe container
  with the same mounts must see neither `/eval/content` nor `/eval/source`, no
  entry named like a task, and no guidance file above the workspace.
- `--timeout`, `--max-turns` and `--budget` apply unless `task.json` sets
  `timeout_s`, `max_turns` or `budget_usd`. On a timeout the container is
  killed and the cell is still graded. `--max-batch-usd` starts no new cell
  once that much has been spent.
- Every container runs with `--security-opt no-new-privileges`, `--cap-drop
  ALL` and a `--pids-limit`, as the caller's uid and gid.
- `--agent-network` is required while the egress proxy is missing: it gives the
  agent's container that docker network, so the agent's commands can reach any
  host, which is what §2 forbids. `meta.json` records it and the report warns.
- The report gives, per (task, arm, model), passes over finished cells with a
  Wilson 95% interval (score-only tasks: the mean of `score.metric`), grader
  failures (counted as failures too), missing cells, and mean cost and turns.
- `repo compare` reduces cells to one value per (task, arm, model) first:
  repetitions share a task's difficulty and are not paired across arms, so the
  task is the unit. Per treatment arm, model and measure (gate share, score,
  cost, turns, each `--metric` from `grade.json`) it gives the mean per-task
  delta against the baseline with a task bootstrap 95% interval, the tasks up,
  down and tied, and a two-sided sign-flip permutation p (exact up to 16
  changed tasks; at one cell per task and arm it equals McNemar's exact test).
  Holm's correction runs over the gate and score comparisons of one call; the
  rest are descriptive. `BATCH/ARM` takes an arm from one batch only, so two
  batches of the same arm (for example two revisions of the guidance) compare
  too. Tasks with cells in only one arm are listed as unpaired. The output
  names the batches behind each selector and each batch's pin (`tree`,
  `eval_dir`, `image` from `_eval/batch.json`) with its cells' harness
  versions, and warns when a bare `ARM` pools several batches or when the two
  arms ran from different `eval_dir` or `image` pins or harness versions.
