---
depends_on:
  - dokimi/dokimi/harnesses/**
  - dokimi/dokimi/commands/tasks.py
  - dokimi/dokimi/report/task_runs_report.py
  - dokimi/dokimi/eval/binary_stats.py
  - dokimi/dokimi/schemas/task_runs.v1.json
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Coding Harnesses And Task Runs

Executable task runs measure whether an agent can do the work, not only answer
questions about an API. The `tasks run` command runs executable task
directories through named harnesses, records operation telemetry, and compares
pass rate against a baseline harness.

## Task Layouts

Two layouts are read, and a root may mix them. `task.md` is read by
`tasks list`, `coverage` and `catalog scan`; it is run by `docker-oracle` and
by the `benchflow*:<agent>` harnesses (BenchFlow reads it natively), and a
`command:` harness runs whatever it is given. What each harness refuses
follows the files it reads, and a refused cell is an error (missing data),
never a verdict:

- `docker-claude` and `docker-probe` run a `task.md` task when it works in
  `/app`: their agent runs in the task container, but in `/app` only, so a
  task whose image works elsewhere (`WORKDIR /root`, most SkillsBench images)
  is refused. The `-host` variants refuse every `task.md` task. Every
  `docker-*` mode but the probe refuses a task without the directory it
  copies in (`tests/` or `verifier/`, and `solution/` or `oracle/` for
  `docker-oracle`) before it builds anything.
- The Harbor-backed ones (`codex`, `claude-code`, `terminal-bench:<agent>`)
  refuse what Harbor 0.24 itself would reject (it exits 1, which would read
  as a fail): no `task.toml`, `environment/`, `instruction.md` or
  `tests/test.sh` (`test.bat` for a Windows verifier; not needed when a
  separate-mode verifier bundles its tests, or for a task with `steps`, whose
  files are left to Harbor), and no `solution/solve.sh` for
  `terminal-bench:oracle`. They also refuse a `test.sh`/`solve.sh` that reads
  `/verifier` or `/oracle`, which Harbor does not mount. Harbor's validation
  of the config values themselves is not repeated here.

A directory left by `bench tasks migrate` that kept `task.toml`,
`instruction.md`, `solution/` and `tests/` also runs under the Harbor-backed
harnesses. There the `docker-*` harnesses and the run artifact use the
`task.md`, while Harbor reads the split files; when the two differ (as
BenchFlow's `bench tasks check` also reports, though it compares more keys
than the tables and prompt a run uses), loading warns and the Harbor-backed
harnesses refuse the task.

| Layout | Files | Oracle | Verifier |
|---|---|---|---|
| Harbor / terminal-bench | `task.toml` + `instruction.md` | `solution/solve.sh` | `tests/test.sh` |
| BenchFlow `task.md` (SkillsBench) | `task.md` (YAML frontmatter + instruction body) | `oracle/solve.sh` | `verifier/test.sh` |

Both carry the same tables (`metadata`, `verifier`, `agent`, and the sandbox
table, which BenchFlow schema 1.3 renamed from `environment` to `sandbox`;
either name is read, and a file declaring both is rejected, as BenchFlow
rejects it). A directory holding both files loads as `task.md` only when its
frontmatter is a BenchFlow document: every top-level key is one BenchFlow
0.7.8 knows (its `TaskConfig` fields, document keys and authoring keys, plus
the legacy `environment`) and `task`, `metadata`, `agent` and the sandbox
table are mappings. Any other `task.md` there (no frontmatter, YAML frontmatter from
Jekyll or Hugo, an unclosed block) is an author's note, and the task loads
from `task.toml` with a warning (none for a note without frontmatter, or with
Hugo's TOML `+++` frontmatter, which is not frontmatter here).

Discovery skips a lone `task.md` without frontmatter, and a `task.yaml`
mapping with no `instruction`, with a warning; `load_task` on either raises. A lone `task.md` with frontmatter is a task, whatever
its keys. One that opens with `---` but does not parse (a horizontal rule, an
unclosed block, invalid YAML, a non-mapping) fails discovery of the whole
root, as a malformed `task.toml` does; `coverage` skips it. YAML aliases are
refused (a few hundred bytes of nested aliases expand without bound), and
frontmatter nested too deeply to parse is a parse error. YAML and TOML dates
and times become ISO strings, and YAML sets sorted lists.

### SWE-bench rows: `tasks run --swe-bench`

`python cli.py tasks run --swe-bench <rows.jsonl> --tasks <instance_id>[,...]
--harnesses docker-oracle,docker-claude` runs SWE-bench rows (Lite, Verified,
or any file with the same fields) with the `docker-*` harnesses; `--all`
runs every row. Each row is staged under `<output-dir>/swe-bench-tasks/<id>/`
as a task the harness already runs, by `dokimi/harnesses/swebench.py`:

- environment: the prebuilt SWE-bench instance image
  (`swebench/sweb.eval.x86_64.<id with __ as _1776_>:latest`, or the row's own
  `image_name`/`docker_image`), with the checkout moved to `/app` and `/testbed`
  left as a link to it. Nothing is built but that one layer; the first run pulls
  the image (about 4 GB, 1-2.5 GB of it unique per instance).
- oracle (`docker-oracle`): the row's gold `patch`, applied as SWE-bench applies
  a prediction (`git apply`, then `patch --fuzz=5`).
- verifier: SWE-bench's eval script (reinstall the repo, reset and apply
  `test_patch`, run the repo's test command on the files it touches), then
  `swebench_grade.py`, which parses the log with the repo's SWE-bench parser:
  reward 1 only when every `FAIL_TO_PASS` and `PASS_TO_PASS` test passes.
  Unlike upstream, a skipped `FAIL_TO_PASS` test (or a row with none) does
  not resolve. The end of the eval log goes to `run.log`; the grader's exit
  code is the verdict too.

The image's `testbed` conda env is first on `PATH`, so an in-container agent's
`python` and `pytest` are the repo's. A host-side agent
(`docker-claude-host`) is refused: the instruction names container paths and
the host has no `testbed` env.

The per-repo test commands and parsers are a table,
`dokimi/harnesses/swebench_specs.json`, generated from the `swebench`
package by `scripts/gen_swebench_specs.py`; dokimi does not depend on
`swebench`. Rows from a repo outside the table (SWE-bench Multilingual,
SWE-smith, SWE-Gym repos) are refused with the repo's name, and so is a
version the table lacks. The solve runs offline (see
[Network policy](#network-policy)); the verifier gets an allowlist of
`pypi.org` and `files.pythonhosted.org`, because the eval script's install
step fetches build requirements and the packages a gold patch adds
(`pylint-dev__pylint-4661`, `astropy__astropy-12907` fail offline).
`--network verify=none` runs fully offline, `--network verify=public` gives
the verifier upstream's network. A file is checked row by row before any task
is written.

`--all` skips the rows in `swebench.KNOWN_BAD`, whose gold patch fails in the
prebuilt image under that default: `psf__requests-863`, `-1724`, `-1766`,
`-1921`, `-1963`, `-2148`, `-2317` and `-2674` (their tests fetch httpbin.org),
`sphinx-doc__sphinx-7985`, `-8269` and `-8475` (`test_build_linkcheck.py`
checks live links), and the six Harbor's SWE-bench adapter lists as failing under its
oracle (harbor 6099b8c, `adapters/swebench/README.md`, Known Issues). Naming
one in `--tasks` still runs it.

### Other eval formats: `tasks inspect`

`python cli.py tasks inspect <path> [--json] [--out FILE]` names the eval-task format of a
file, a task directory, or every item under a tree, and reports its structure:
kind, item count, which parts are present or missing (instruction,
environment, oracle, verifier, metadata), lint problems, and what in dokimi
can consume it today. It only reads: nothing is run or converted, a Python
file is scanned as text and never imported, and a file that does not parse is
a problem on its report, not an error. A path no format claims is reported as
`unknown` with its files (or keys) and likely prompt and verifier files; when
two formats claim a path about equally, the report lists the other as
`ambiguous` with its evidence. `--json` prints and `--out` writes the report
as a `task_inspect.v1` artifact (`schemas/task_inspect.v1.json`).

A dataset file a config in the same tree names is read as that config says:
an Inspect `@task` file's `json_dataset("x.jsonl", FieldSpec(input="question",
target="answer"))` makes `x.jsonl` Inspect samples under those keys, an OpenAI
evals registry spec makes its `samples_jsonl` OpenAI evals (with no `ideal`
needed when the spec's class is model-graded), and promptfoo `tests:
file://tests.csv` makes that file promptfoo tests. The report says which file
named it. A file that is a git-LFS pointer stub (a clone without `git lfs
pull`) is reported as `git-lfs-pointer`, with the format a config expects it
to be when one names it. `tests/fixtures/task_formats/` holds a small corpus of
real files in each format, with their sources in its `MANIFEST.yaml`.

| Format | Recognised by | Kind | Consumed by |
|---|---|---|---|
| `harbor` | directory with `task.toml` | executable | Harbor-backed harnesses (unless Harbor would refuse it), `docker-*` (with `environment/Dockerfile`) |
| `task-md` | directory with a BenchFlow `task.md` | executable | `benchflow*`, `docker-*` (with `environment/Dockerfile`) |
| `terminal-bench-v1` | directory with `task.yaml` + `run-tests.sh` / `Dockerfile` / `solution.sh` | executable | `docker-*` run it as it is; `harbor`, `benchflow` through a cached Harbor export (not a `solution.yaml` task) |
| `swe-bench` | JSON/JSONL rows with `instance_id`, `repo`, `base_commit`, `problem_statement` (`FAIL_TO_PASS`) | executable | `docker-*` via `tasks run --swe-bench` (Python SWE-bench repos) |
| `inspect-samples` | JSON/JSONL/CSV rows with `input` and `target` (or the keys a `FieldSpec` names) | answer-only | `questions import` ([answer-only formats](answer-formats.md)) |
| `inspect-task` | `.py` importing `inspect_ai` with a `@task` function | answer-only (executable with a `sandbox=`) | `questions import` ([answer-only formats](answer-formats.md)) |
| `openai-evals` | JSONL rows with `input` (chat messages) and `ideal` | answer-only | `questions import` ([answer-only formats](answer-formats.md)) |
| `openai-evals-spec` | registry YAML with `class: evals.elsuite.*` run specs | answer-only | none yet |
| `skill-creator-evals` | `evals.json` with `skill_name` and `evals[].prompt` | answer-only | `questions import` ([answer-only formats](answer-formats.md)) |
| `promptfoo` | `promptfooconfig*.yaml`/`.json`, or a config with `prompts`, `providers`/`targets` and `tests`/`scenarios`/`redteam` | answer-only | `questions import` ([answer-only formats](answer-formats.md)) |
| `skill-creator-trigger` | rows with `query` and `should_trigger` | trigger | `catalog trigger-set` |
| `skill-reach` | `QuerySet` (`queries[]` with `query_id`/`id`, `text`, `expected_skill`) | trigger | `catalog trigger-set` |
| `promptfoo-tests` | CSV with `__expected` columns, or rows of `{vars, assert}` | answer-only | none yet |
| `dokimi-questions` | questions.v1/v2 JSON | answer-only (trigger with `expected_skill`) | questions track |
| `dokimi-explore-task` | YAML with `id`, `query` and `references[].path` | localization | explore track |
| `git-lfs-pointer` | a git-LFS spec v1 pointer stub | unknown | none (fetch it first) |

A terminal-bench v1 task (`task.yaml`, laude-institute/terminal-bench
`original-tasks/`) runs as it is under `docker-*`: `solution.sh` is copied to
`/oracle/solution.sh`, `run-tests.sh` and `tests/` to `/tests`, both run as the
image's user with `TEST_DIR=/tests` and the compose `environment`, and the
verdict is the pytest `short test summary info` (every listed test passed, skipped or
xfailed), not the exit code, as terminal-bench reads it. `docker-*` refuses,
with the reason, a task whose `docker-compose.yaml` needs more than one
`docker run` of its default build stage (a second service, volumes, tmpfs,
ports, a service `command` other than `sleep infinity`, `build.args` or
`build.target`, an `environment` value read from the host), a `parser_name` other
than `pytest`, `run_tests_in_same_shell`, or (oracle only) a `solution.yaml`.
The Harbor-backed harnesses and `benchflow*` run a Harbor copy written on first
use to `~/.cache/dokimi/harbor-export/<content hash>/` (mode 0700) by
[`harbor_export.py`](../dokimi/harnesses/harbor_export.py); its `test.sh` scores
the same pytest summary, so a task gets one verdict on every backend.

Memory: terminal-bench sets no limit, so a v1 task gets
`environment.memory_mb = 8192` (not the 2048 of other formats; tasks such as
`predicate-pushdown-bench` are OOM-killed at 2 GB), unless its compose file
sets `deploy.resources.limits.memory`, which `docker compose` honours and
dokimi reads as the task's own value. `DOKIMI_TBENCH_V1_MEMORY_MB` changes the
8192 default. The Harbor export writes the same value to `task.toml`.

`tasks list` and `tasks run` still read only the first three; `tasks run --swe-bench`
also takes a SWE-bench file.

The `docker-*` harnesses mount the oracle at `/solution` and `/oracle` and the
verifier at `/tests` and `/verifier`, because a script written for one layout
refers to its siblings by that layout's path. The directory's contents are
copied into each path, so an image that already creates `/tests` (as
`scheduling-email-assistant` does) still gets `/tests/test.sh` rather than a
nested `/tests/verifier/`. A task's bundled `environment/skills` is replaced
by an empty directory in the image build context, so a `COPY .` (or `COPY
skills ...`) builds but cannot leak skills into the no-skill arm. Literal
`[verifier.env]` / `[oracle.env]` values are forwarded; `${VAR}` host
references and non-scalar values are not, and are listed in the row's
`metrics.unresolved_env`. Any cell with such a value outstanding
(`[verifier.env]` for every `docker-*` arm that runs the verifier,
`[oracle.env]` too for `docker-oracle`; a probe runs neither and checks
neither) is recorded as `metrics.error` (missing data), whatever it would have
scored: dropping only the failures would make the task look solved. That is
decided from the task file alone, before the image is built and before a paid
solve, so `--dry-run` reports it too. Proxy credentials (`user:pw@`, with or
without a scheme) are redacted in `run.log`, timeout lines and the commands'
own output included, and so in the row's `stdout_tail`. After an in-container
agent the verifier gets the proxy without its credentials (see [What the agent
can reach](#what-the-agent-can-reach)). The task container lives for the solve
and verify timeouts plus slack (at least 1800 s), so a verifier with a long
`timeout_sec` is not cut off.

As in BenchFlow, a `task.md` body with a `## prompt` heading gives the agent
only that section; `## role:<name>`, `## scene:<name>` and `## user-persona`
sections are left out of it. Multi-role documents (`agents`, `scenes`, `user`
in the frontmatter) are not run as such: the one agent gets the base prompt.
BenchFlow's frontmatter shorthands (`verifier: <path>`, `oracle: <path>`,
`image:`, `profile:` / `profiles:`) are rejected with an error naming the key,
rather than read past: write the canonical tables. Other BenchFlow keys
(`agent.prompt_prefix`, `verifier.type`, `solution:`, `sandbox.image`, ...)
are not checked yet and are read past.

To convert between the two, use BenchFlow's own `bench tasks migrate`
(split → `task.md`) and `bench tasks export --target harbor` (`task.md` →
split) rather than a converter here. An export renames `oracle/` and
`verifier/` to `solution/` and `tests/` but keeps the scripts' `/oracle` and
`/verifier` paths (all 101 SkillsBench `test.sh` call `/verifier/...`). The
`docker-*` harnesses mount both names, so they run the exported task (as long
as it works in `/app`, for the agent arms); the Harbor-backed harnesses refuse
it.

## Supported Harnesses

Harbor-backed (delegate to an installed `harbor`):

- `codex` — runs Harbor with agent id `codex`.
- `claude-code` — runs Harbor with agent id `claude-code`.
- `terminal-bench:<agent>` — runs any Harbor agent id, for example
  `terminal-bench:terminus`.

Standalone Docker (no Harbor; **full LLM telemetry** — cost, tokens, cache,
latency, turns):

- `docker-oracle` — apply the task's own `solution/solve.sh` (no LLM). Free; use
  it to validate that a task image + verifier work end to end.
- `docker-claude` — solve with `claude -p --output-format stream-json`
  **inside the task container**, with egress to the LLM endpoint only (see
  [What the agent can reach](#what-the-agent-can-reach)). The final result
  event carries `total_cost_usd`, token `usage`, `duration_ms`, `ttft_ms`, and
  `num_turns`, which are folded into the run artifact's `metrics{}`, together
  with `tool_calls` (by tool name) and `offered_skills`. A run the CLI
  ended with an error before any attempt carries `metrics.solver_error` and
  is counted as errored, not as a miss (see below). A solve that ran out of
  time is a miss: it carries `metrics.solver_timeout`, and stays in the
  comparison unless it also carries `solver_error` (a skill arm whose plugin
  did not load).
- `docker-claude-skill:<skill-path>` — `docker-claude` with the skill installed
  the way Claude Code installs one. This is the **with-skill treatment arm**;
  compare it against `docker-claude` (without-skill) as the baseline. See
  [How the skill arm offers the skill](#how-the-skill-arm-offers-the-skill).
- `docker-claude-skill-discover:<skill-path>` — `docker-claude-skill` without
  the treatment notice: the **discovery arm**. See
  [Three skill arms](#three-skill-arms).
- `docker-probe` — no LLM, free. Runs an isolation probe in exactly the work
  tree and environment `docker-claude` would get, writes
  `isolation_probe.json`, and passes only when nothing in
  [What the agent can reach](#what-the-agent-can-reach) is open. Run it before
  a paid sweep. `docker-probe:<skill-path>` also forbids that skill. Use it
  when the with-skill arm's skill lives outside the task's checkout, so the
  baseline is checked against the treatment it must not see.
- `docker-claude-host`, `docker-claude-host-skill:<skill-path>`,
  `docker-claude-host-skill-discover:<skill-path>`, `docker-probe-host`, `docker-probe-host:<skill-path>` — the same, with the
  agent on the host. These are weaker: use them only where the in-container
  launch cannot run, and do not compare a host arm against a container
  baseline (`tasks run` warns). A `-host` agent edits a copy of `/app`, of
  which only the task's working tree is copied back (a read-only
  `/app/baseline` beside `/app/workspace` stays the image's), and refuses a
  `task.md` task (its instruction names absolute
  container paths) with an error before the agent starts; the in-container
  `docker-claude` runs it.
- **Both agents edit `/app` only.** A task that declares an
  `environment.workdir` outside `/app`, or whose image sets a `WORKDIR`
  outside `/app` (most SkillsBench images, and a SkillsBench task exported with
  `bench tasks export --target harbor`, work in `/root`), is refused with an
  error before the agent starts, in every `docker-claude*` and `docker-probe*`
  arm; so is an image without that directory. A `WORKDIR` inside `/app`
  (`/app/workspace`) runs, and an image with no absolute `WORKDIR` runs in
  `/app`. The image check needs the built image, so `--dry-run` does not show
  it. No `docker-*` arm runs a `/root` task yet.

### What the agent can reach

A task's `solution/` and `tests/` live beside the skills in one content
checkout. A solver that can read them copies the answer instead of solving the
task. A baseline arm that can read the skills is no longer a baseline. The
probe reports eight leak classes. Each is `open` or `closed`; `n/a` (nothing
to protect was declared) passes, and `untested` (the image lacks the tool, the
gateway lookup returned nothing, or the probe timed out) fails like `open`.
The result is in `metrics.isolation_probe` and `isolation_probe.json`:

| class | what the probe tries |
|---|---|
| `task_files` | read the task's `solution/`, `tests/`, `oracle/` or `verifier/`, or this task's output dir (the other arms' work trees, prompts and transcripts), by absolute path |
| `treatment` | read any `skills/` on the way up from the task (with or without a `.git`), or the `docker-probe:<skill>` skill, by absolute path. `n/a` if neither exists: nothing was tested, and that is not the same as `closed` |
| `operator_config` | read the operator's `~/.claude`, `~/.claude.json`, or `$CLAUDE_CONFIG_DIR` and its `.claude.json` |
| `checkout_walk` | find a `.git`, `task.toml`, or `CLAUDE.md` in a directory above the work tree |
| `network` | fetch `https://example.com/` through whatever proxy variables it was given; with `bash`, also a raw `CONNECT example.com:443` and a plain-HTTP `GET http://example.com/` through the proxy, and a direct TCP connect to `1.1.1.1:443` that ignores it. `untested` with no client at all |
| `env` | find the host value of a variable the allowlist drops in the environ of the probe, its parent chain or PID 1 (`/proc/<pid>/environ`); names in `env_leaked`. `n/a` if the allowlist dropped nothing |
| `docker_api` | a writable Docker or podman socket (rootful, rootless `/run/user/<uid>/...`, Desktop), or a daemon on the gateway's 2375/2376 (the host's loopback for a host launch); either gets around every other wall. `untested` when the gateway is unknown or not an IPv4 address. A `DOCKER_HOST` over tcp/ssh is not checked |
| `dns` | resolve a public name (`example.com`); an answer means queries reach a resolver outside the sandbox, e.g. a podman `--internal` network that forwards DNS upstream |

`docker-claude` runs the agent inside the task container, as uid 10001:

- **Grader files.** `solution/` and `tests/` (`oracle/` and `verifier/` for a
  `task.md` task) are copied in only by the verifier, after the agent has
  exited; the probe checks all four container paths, so an image that bakes
  them in is `open`.
- **Mounts.** Nothing is bind-mounted. Host files reach the container only
  by `docker cp`: the `claude` binary, the files named by the file-valued
  variables below (CA bundles, `AWS_SHARED_CREDENTIALS_FILE` with every
  profile in it, `AWS_CONFIG_FILE`, `GOOGLE_APPLICATION_CREDENTIALS`), and
  the skill arm's plugin at `/opt/abskill` (built with symlinks followed, as
  for the host arm).
- **Image needs.** A glibc image (the host's `claude` binary is copied in; a
  musl image fails the `--version` check), and a working tree in `/app` that
  uid 10001 can own. Only that tree is `chown`ed before the agent starts, and
  the agent starts in it: with `WORKDIR /app/workspace`, the rest of `/app`
  stays root's. A task that works elsewhere is refused.
- **Memory.** The agent's CLI and its tmpfs HOME run inside the task's
  cgroup, so they count against `environment.memory_mb` (about 100 MB for a
  short session); the host launch ran outside it.
- **Capabilities.** Every `docker-*` task container, `docker-oracle` included,
  starts with `--init`, `--cap-drop ALL`, `no-new-privileges` and `--pids-limit 32768`,
  and gets back only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETUID` and `SETGID`
  (`TASK_CONTAINER_HARDENING` in `harnesses/container_agent.py`). Every other
  capability Docker grants by default is dropped, so a step that needs one
  fails: signalling another user's process (`KILL`), a raw socket (`NET_RAW`),
  `mknod` (`MKNOD`), `chroot` (`SYS_CHROOT`). Without `FSETID`, `chmod g+s` on
  a file whose group root is not in exits 0 and leaves the bit off. A port
  below 1024 still binds: Docker sets the container's
  `ip_unprivileged_port_start` to 0.
- **Network.** The container sits on a per-run `--internal` Docker network,
  which has no route out. The agent reaches the LLM through an allowlisting
  CONNECT proxy (`harnesses/egress_proxy.py`, stdlib only). It runs in a
  sidecar container on that network and the default bridge, and listens on
  its internal-network address only. The sidecar image is
  `python:3.12-slim` pinned by digest; set `DOKIMI_PROXY_IMAGE` to a
  mirror (an offline runner, a Docker Hub rate limit). The sidecar holds the
  upstream proxy URL, credentials included. The verifier of a public task,
  which runs the agent's `/app` code as root, gets the host's proxy variables
  without their `user:pw@`: a verify-time install behind a proxy that needs
  credentials fails there, as it did before the proxy was forwarded at all.
    - **Allowlist.** Entries are `host:port`: the provider's hosts on 443 (the
      provider chosen as the CLI chooses it: Bedrock, then Foundry, then
      Vertex, each only for `1`/`true`/`yes`/`on`; Vertex's host follows
      `CLOUD_ML_REGION` as the CLI maps it, `global`, `us`, `eu` included), the
      `ANTHROPIC_BASE_URL` host on its own port, and the task's
      `environment.egress` entries. A bare `host` there means `host:443`; no
      wildcards, commas or leading/trailing dots.
    - **Plain HTTP.** An `http://` gateway such as LiteLLM is forwarded only
      directly. The request is rewritten to origin-form with `Connection: close`,
      so each connection carries one checked request. Plain HTTP is never
      chained through an upstream proxy, so a host behind one must be listed
      in `NO_PROXY`.
    - **Loopback.** A loopback base URL (`localhost`, `127.*`) fails fast,
      because no container can reach it; use `docker-claude-host` instead.
    - **Logging.** Every proxy decision is saved as `egress.jsonl` (an allowed
      one before the connect, so a pending CONNECT is logged too), and
      `metrics.egress` carries the `allowed`, `denied` and `errors` host sets
      and `denied_entries` (`host:port`), and a tri-state `status`: `violated`
      (a request was denied), `ok` (the log was read and nothing was denied), or
      `unknown` (the sidecar's log timed out or failed at teardown). An
      `unknown` cell is not a clean one: its host sets are empty for lack of
      data, and `scripts/check_smoke_runs.py` fails the run on it. No
      `egress.jsonl` is written for an `unknown` cell: a missing file is not
      "no requests". The file is also absent when no sandbox ran
      (`docker-oracle` and the `-host` variants with no allowlisted phase, a
      build failure), so use `metrics.egress.status` to tell unknown from a
      cell that ran no sandbox. The file and `metrics.egress` cover the
      solve's sidecar only: an allowlisted verifier's decisions are
      `metrics.network.verify_egress`, also saved in `network.json`.
      `docker-probe` reads `violated` on a passing
      cell, because the probe requests denied hosts (`example.com`) by design;
      judge a probe by `isolation_probe.json`, not by `egress.status`.
- **`network_mode = "public"`.** An `[environment]`-level `public` applies
  to the oracle and the verifier, not to an agent: every uxlfoundation/skills
  task declares it, and none of their solutions or tests uses the network. A
  task whose agent needs a host lists it under `environment.egress`. See
  [Network policy](#network-policy) for the other sources.
- **Environment.** HOME is its own tmpfs, `/run/abhome`; the image's `/tmp`
  is left alone. The environment is:
    - the provider-credential allowlist;
    - the proxy (also as `ALL_PROXY` and `FTP_PROXY`), and
      `NO_PROXY=localhost,127.0.0.1,::1`. They replace what the Docker client
      injects from `proxies.default`: a `NO_PROXY` would send an LLM host around
      the proxy, an `ALL_PROXY` would hand the agent the client's credentials;
    - `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`;
    - file-valued variables (`NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`,
      `AWS_SHARED_CREDENTIALS_FILE`, `GOOGLE_APPLICATION_CREDENTIALS`, ...),
      whose file is copied in and re-pointed.

    It reaches `docker exec` in a 0600 `--env-file`, never on argv. The `docker`
    CLI keeps the operator's environment, so its context and `DOCKER_HOST` stay
    the operator's.

- **Fails before the agent starts.** A setup that cannot work in a container
  errors the cell after its image build: a loopback base URL or upstream
  proxy, Bedrock with only an `AWS_PROFILE` or SSO login, Vertex without
  `GOOGLE_APPLICATION_CREDENTIALS` or `CLAUDE_CODE_SKIP_VERTEX_AUTH`, a
  provider the allowlist has no hosts for (`anthropicAws`, `mantle`, ...), or
  an `environment.egress` entry that is a URL. A `claude` binary that cannot
  run in the image (a musl image) is caught after the container starts.
- **Lifetime.** The task container lives for the solve timeout plus the
  verifier timeout plus 10 minutes (at least 30). A verifier that finds the
  container gone errors the cell instead of scoring it 0.
- **Leftovers.** The network, sidecar and task container carry the label
  `dokimi.run=<id>`, and the sidecar exits after 4 h on its own. A
  killed harness can also leave its `dokimi-env-*` dir, whose env file holds the
  agent's credentials. Clean up with:

    ```bash
    docker ps -aq --filter label=dokimi.run | xargs -r docker rm -f
    docker network ls -q --filter label=dokimi.run | xargs -r docker network rm
    rm -rf "${TMPDIR:-/tmp}"/dokimi-env-*
    ```

The launch goes only through the Docker API, never through a bind mount or a
listener in the harness process. It therefore works the same with a local
daemon and on a Docker-in-Docker CI runner. CI's `external-catalog-smoke`
job requires a container probe to report every class closed. It also
requires the proxy to have denied the probe's `example.com` CONNECT (`:443`)
and its plain-HTTP GET (`:80`), which shows both reached the sidecar.

One gap remains: a service on the Docker host that listens on the internal
network's gateway address or on `0.0.0.0` is reachable.

The `-host` variants run the agent on the host instead. The host cannot hide an
absolute path or close the network, so these harnesses state what they seal in
every run's `metrics.isolation`. What they do:

- solve in a fresh `dokimi-run-*` temp tree outside any checkout;
- point `HOME` and `CLAUDE_CONFIG_DIR` at empty scratch dirs;
- pass only an allowlist of variables;
- reach `task_files`, `treatment`, `operator_config`, `network` and `env` by
  absolute path, proxy or `/proc`, as a host probe shows.

`env` is open on the host because the agent runs as the operator's uid: it can
read the harness's `/proc/<pid>/environ` (every variable the allowlist
dropped) and its command line and cwd (`--tasks-root`, `--output-dir`, the
skill arm's path). Yama `ptrace_scope=1` does not stop this; it only gates
attach.

Both launches run `claude` with `--tools Read,Edit,Write,Bash,Glob,Grep,Skill
--setting-sources "" --strict-mcp-config --no-session-persistence`, built-in
plugins disabled through `--settings`, and
`CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1`. An older launch used `--allowedTools`,
which only pre-approves tools, so `WebFetch`, `Agent`, `Skill`, and the
operator's skills, CLAUDE.md files and MCP servers were loaded in both arms.
The Bash tool can still start a nested `claude -p` with the default tool set;
its spend is not in the outer `total_cost_usd`.

Provider credentials come from the environment, for example
`AWS_BEARER_TOKEN_BEDROCK`, `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`.
The `env` block of the operator's `~/.claude/settings.json` (or
`$CLAUDE_CONFIG_DIR/settings.json`) is merged in over the process
environment, as Claude Code applies it, through the same allowlist; a
malformed `settings.json` is logged and ignored. These are not visible from
the scratch `HOME`: an `apiKeyHelper`, an interactive OAuth login, or an
`~/.aws` profile unless `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE`
point at it (they pass through).

Every run records how the CLI ended it in `metrics.terminal_reason`. A run
the CLI ended with an error before any attempt (no output tokens, or at most
one turn: missing or rejected credentials, a flagged first request, an
unknown model id), or a skill arm whose plugin did not load ("treatment not
offered"), also carries `metrics.solver_error`. The runner counts that cell
as errored, like `metrics.error`: it is out of `pass_rate` and out of the
McNemar pairing (`n_excluded`), so do not drop cells by hand. An error after
an attempt (a 529 at turn 40, `prompt_too_long` in a skill arm that read too
much) is the arm's own outcome and is scored. So is a host-launch CLI that dies with no
result object (`terminal_reason: no_result`) after it edited the work tree; with
the tree untouched, or in the container launch, it is a `solver_error`. A timed-out solve is a model miss and
is not marked `solver_error` (it carries `metrics.solver_timeout`); it stays in,
or the arm that times out more would look better than it is. Without `--model`,
`metrics.model` is the `modelUsage` entry that cost the most.

`docker-probe*`, `docker-oracle` and BenchFlow `oracle` rows
(`benchflow:oracle`, `benchflow-skill:oracle[:<dir>]`, `benchflow-selfgen:oracle`)
are never compared against `--baseline-harness`: they are checks of the setup,
not arms, and would only enlarge the Holm family.

BenchFlow-backed (delegate to an installed `bench`, the SkillsBench runner;
the agent runs *inside* the task sandbox over ACP, and skills are installed
into the agent's native skill directory). They need `bench` (`pip install
benchflow`; written against 0.7.8) and Docker with the **Compose v2 plugin**
(`docker compose version` must work; Ubuntu's `docker.io` package does not ship
it, so install `docker-compose-plugin` or drop the binary into
`$DOCKER_CONFIG/cli-plugins`). `tasks run` checks both once before the first
cell and exits with one message when either is missing; `--dry-run` skips the
check.

- `benchflow:<agent>` — no-skill arm. `<agent>` is any id from
  `bench agent list` (`claude-agent-acp`, `codex-acp`, `gemini`, `opencode`,
  `openhands`, ...), a `<ns>:<id>` shorthand (`acp:pi`, `ai-sdk:codex`), or
  `oracle`. BenchFlow strips the task's `environment/skills` and the
  Dockerfile lines that copy it, so this arm sees no skill.
- `benchflow-skill:<agent>` — with-skill arm using the task's own
  `environment/skills`.
- `benchflow-skill:<agent>:<path>` — with-skill arm using an external skill: a
  skill directory, its `SKILL.md`, or a root whose children are skill
  directories (`<root>/<name>/SKILL.md`; a root without one is refused, since
  BenchFlow would run the arm with no skill).
- `benchflow-selfgen:<agent>[:<dir>]` — BenchFlow's self-generated-skill arm.
  `<dir>` names the skill-creator (the `skill-creator` dir, or a root holding
  it); without it BenchFlow uses whichever skill-creator the host has
  (`BENCHFLOW_SKILL_CREATOR_DIR`, `~/.claude/skills`, ...). A `<dir>` with no
  skill-creator `SKILL.md` is refused rather than left to that fallback. The
  named creator is copied alone to `<cell>/creator/<name>` (a symlink is read
  through, not followed to its neighbours): BenchFlow prefers a `skill-creator`
  pack next to the one it is given, so a stock creator beside a custom one
  would otherwise win.

How a BenchFlow cell is run and scored is in [BenchFlow Cells](#benchflow-cells).

Custom:

- `command:<template>` or `--command-template` — runs a custom command template
  for local wrappers and CI fakes.

A `command:` or Harbor command runs in the task root's parent directory with
the caller's whole environment: it is the operator's own CLI and needs its
credentials, so whatever it starts (an agent, `harbor`'s containers) gets them
unless that tool filters them. Its stdin is closed, and its output is read as
UTF-8. A timeout kills the command's whole process group (job object on
Windows), not only the command, and is a failed cell (reward 0, returncode 124)
that keeps the output printed before the kill; anything the command left
running when it exits is killed too. A timed-out `harbor` first gets SIGTERM
and 60 s to remove its trial container and image, which a kill would leave
behind (POSIX; Windows terminates the job at once). Harbor 0.20.0 exits on
SIGTERM before it removes them for a task with `allow_internet = false`.

The Harbor adapter command is:

```bash
harbor run -p <task-path> -a <agent> -m <model> --jobs-dir <run-dir>
```

Harbor writes `<run-dir>/<job>/result.json` (the job summary) and
`<run-dir>/<job>/<trial>/result.json` per trial. If a local Harbor version uses
different flags, pass `--command-template`; a template is graded as a
`command:` harness (below), not from these files. When Harbor is not installed,
prefer the `docker-*` harnesses — they build, solve, and verify against local
Docker directly and record telemetry.

**A Harbor cell is graded from its trial's `result.json`, never from
`harbor`'s exit code or output.** Harbor exits 0 for a trial that errored (an
agent install that failed, an environment that did not start, an agent
timeout), and its summary table on stdout has a `Reward` column, so neither is
a verdict. The rules mirror intel/skills' `tools/check_harbor_job.py`:

| Trial `result.json` | Cell |
|---|---|
| `verifier_result.rewards.reward` is a finite number in [0, 1], no `exception_info` | scored: that reward, a pass at 1.0 |
| the only `exception_info` (top or `step_results[]`) is `VerifierTimeoutError` | scored 0, `metrics.verifier_timeout` true: a verifier timeout is a failure, not an error |
| `exception_info` set, at the top or on a `step_results[]` entry (a multi-step task) | errored: `metrics.error` is `harbor: <exception_type>: <first line>`, `metrics.harbor_exception` the type; a reward the verifier still wrote is kept as `metrics.reward_after_exception`, not scored |
| no `exception_info`, no numeric reward or one outside [0, 1] | errored |
| no trial file (with or without the job's), more than one trial, or not a JSON object | errored |

An errored cell is out of the pass-rate denominator and the paired test, as
for `docker-*` and BenchFlow. When every cell of a `tasks run` errored, it
still writes the artifact and the report, then exits 1 as `incomplete`
(`0 scored, N errored of N cells`); a run with some cells scored exits 0 and
prints the errored count per harness. `metrics.n_trials` counts the trial files and
`metrics.result_json` names the one read. A `harbor` that `dokimi` kills at
the task budget is a failed cell (reward 0, returncode 124), as above.

Usage comes from the trial's `agent_result` (for a multi-step trial, the sum over
`step_results[].agent_result`): `n_input_tokens` →
`prompt_tokens` (Harbor's count includes the cached prompt, as BenchFlow's
does), `n_output_tokens` → `completion_tokens`, `n_cache_tokens` →
`cache_read_tokens`, input + output → `total_tokens`, and `cost_usd`; these feed
`summary.per_harness.<h>.cost`. A null count (the `oracle` agent, a trial that
never ran its agent) is unknown and left out, not recorded as 0.

These rules were checked against Harbor 0.24.0's files; CI does not install or
run Harbor. This repo's CI runner has also hit Harbor environment-start
failures: a Docker Compose v2 gap, and a `network_mode` rejection. The latter
is Harbor behaving correctly — a task declaring `allow_internet = false`
becomes `network_mode='no-network'`, which the docker provider only accepts
when the host kernel supports egress control, so on a runner without it Harbor
refuses the task instead of running it with the internet the task forbade.
Both now land as errored cells.

A `command:` harness (including a `--command-template` that wraps `harbor`)
keeps its own rule: a `reward.txt` / `reward.json` under the run dir, else a
`reward: x` on its output, else exit code 0 is a pass.

### Network policy

Every `docker-*` run has a network per phase, recorded in
`metrics.network` with the source of each phase's setting, so two runs can
be checked for the same network before their verdicts are compared:

| Phase | Default | What runs |
|---|---|---|
| `build` | `public` (host network, host proxy as build args) | `docker build` |
| `solve` | `none` (an in-container agent still reaches its LLM through the sidecar) | the oracle, or the agent |
| `verify` | `none` | `tests/test.sh` |

The modes are Harbor's: `none` (Harbor's `no-network`), `public` (the
default bridge plus the host's proxy variables), and `allowlist` (the listed
`host[:port]` entries only, through an egress sidecar; the verifier's
decisions land in `metrics.network.verify_egress`). Later sources win:

1. The defaults above.
2. The task: `[environment]` `network_mode` and `allowed_hosts` (or Harbor's
   deprecated `allow_internet`), then `[agent]` and `[verifier]` overrides,
   and `environment.egress` for an agent's solve. An unknown mode refuses
   the cell, and its `network.json` holds the reason.
3. The operator: `dokimi tasks run --network PHASE=MODE[:host,...]`,
   repeatable, with `all` for every phase. For example
   `--network verify=none` grades a public task offline, and
   `--network solve=allowlist:pypi.org,files.pythonhosted.org` lets every
   arm install packages.

A skill that needs the network at solve time declares it in its `SKILL.md`
frontmatter as `metadata.network` (`allowlist:host,...` or `public`), with
the reason for people in `compatibility`, the Agent Skills spec's field for
environment needs. The run does not widen the skill arm for it: its baseline
would then run on another network. `metrics.network.skill.missing` lists the
declared hosts this run did not grant; pass them with `--network solve=...`
so every arm gets them. A value that does not parse is recorded as it is, with
`error` and `missing: null`, and the arm still runs.

## BenchFlow Cells

Skills are not bind-mounted: BenchFlow copies the `--skills-dir` root into the
image build context, skipping symlinks, and bakes it into `/skills`, which it
links into each agent's skill directory. An external path is therefore staged
as a real copy under `<cell>/skills` first; a symlinked pack would otherwise be
dropped (measured: `skill_deployment_missing`).

The adapter runs, from `<cell>` as working directory:

```bash
bench eval run --tasks-dir <task> --agent <agent> --sandbox docker \
  --skill-mode <mode> --jobs-dir <cell>/jobs --retry-attempts 0 \
  --agent-idle-timeout 0 --quiet
```

`bench` reads `./.env` ahead of the exported environment, so it is not run from
the directory `cli.py` was started in, where a credentials `.env` may live.
Every path it is given is absolute, and so are the path variables it and
`docker` read (`BENCHFLOW_SKILL_CREATOR_DIR`, `DOCKER_CONFIG`, a local-dir
`BENCHFLOW_AGENTS_SOURCE`): a relative one is anchored at that directory. A
project-local `./.claude/skills/skill-creator` there is not one of BenchFlow's
candidates any more; name it in the id or in `BENCHFLOW_SKILL_CREATOR_DIR`.

**One cell is one attempt.** BenchFlow's default (`max_retries=2`) reruns the
whole rollout, agent included, after idle timeouts, ACP errors and verifier
infrastructure errors, even when the attempt already has a reward. The adapter
passes `--retry-attempts 0`, so `--repeats` is the only repetition, as for
`docker-*` and Harbor cells. The row records `metrics.n_attempts`.

**One agent budget.** BenchFlow's idle watchdog ends a prompt after 600 s
without a tool call, message or thought, so a slow model call would end a
cell long before a larger task budget. The adapter passes
`--agent-idle-timeout 0`, which disables it: as for `docker-*`, the task's
wall-clock budget is the agent's only limit.

**When a BenchFlow cell is a verdict.** A cell is a verdict only if exactly one
attempt ran, the agent attempted the task, and the verifier or the agent's own
budget decided it. "Attempted" means the `oracle` agent, or at least one tool
call, or completion tokens above 0, or an `agent_message`, `agent_thought` or
`tool_call` event in the ACP trajectory. An `agent_message` that is one of
Claude Code's synthetic error replies or notices ("API Error ...", "Failed to
authenticate ...", "Credit balance is too low", ...) does not count:
claude-agent-acp forwards those when the provider failed, with no model reply.
Verdicts:

- the verifier's reward, partial credit included (a 0.667 is kept as the
  reward; `passed` needs `reward >= 1`);
- an agent timeout after an attempt: 0, as for `docker-*`, whatever the
  verifier BenchFlow still runs after the timeout then does. A reward other
  than 0 it reads is kept as `metrics.reward_after_timeout`, not scored; a
  verifier that fails leaves its `verifier_error`. The row keeps
  `partial_trajectory`;
- a verifier timeout after an attempt: 0, with `metrics.verifier_timeout`, as
  for `docker-*`.

Everything else is `metrics.error`, which is missing data and stays out of the
McNemar pairing like a failed `docker build`:

- our own outer kill (`returncode` 124, `metrics.timed_out`): `bench`'s
  process group gets `SIGTERM`, then `SIGKILL` 15 s later, and the cell's
  rollout containers are removed;
- more than one rollout directory in the cell;
- a failure before the agent started (image build, agent install, sandbox
  start, a task BenchFlow rejects, BenchFlow's own hard deadline); with no
  `result.json`, `metrics.error` quotes the line of `bench`'s stderr that names
  the reason: the `[HARD-DEADLINE]` or `[ERR]` line once the job has started,
  otherwise the first line after BenchFlow's progress and registry banners;
- an ACP or provider error that leaves no reward (0.7.8 classifies a dead key,
  which LiteLLM reports as a 500, as `acp_error`);
- a timeout before any model response reached the agent (a provider that never
  answers);
- an `oracle` whose `solve.sh` overruns `agent.timeout_sec`: BenchFlow kills it
  as an exec timeout and does not run the verifier (`docker-oracle` gives
  `solve.sh` the same budget and scores an overrun as a failed cell, reward 0).

A provider failure that starts after the agent's first model response and
outlasts its budget (for example 429 backoff past the deadline) is a 0
verdict, like any agent timeout after an attempt. BenchFlow sets
`partial_trajectory` only when a tool call was still pending at the deadline:
measured end to end, a model reply that hangs after a finished tool call
leaves it `false`, a `sleep` still running sets it. BenchFlow's own `bench
eval compare-lift` drops the rollouts that have it; this framework counts them,
as it counts any agent timeout.

**Budgets.** The agent budget is the task's `agent.timeout_sec`. For a
non-oracle agent on a task without one, the adapter passes
`--config-override '{"agent":{"timeout_sec":600}}'` (the `docker-*` default)
and records `metrics.agent_timeout_defaulted_sec`; BenchFlow itself would use
0 s and score the untouched workspace. The adapter sets BenchFlow's
per-attempt hard deadline (`BENCHFLOW_ROLLOUT_HARD_DEADLINE`, unless it is
already exported) to what 0.7.8 computes for a one-turn task: agent budget ×
turns (2 for self-gen; an undeclared budget counts 900 s, since BenchFlow reads
it from the task file and not from the override) + verifier `timeout_sec`
(default 600) + `build_timeout_sec` (default 600) + the agent's install budget
(900 s, 1200 s for `mimo` and `prime-agent`, none for `oracle`) + 1800 s
margin. The outer kill is that deadline plus 600 s for job overhead, so
BenchFlow's deadline always fires first, also for task shapes the adapter does
not count (scenes, a `user:` loop, which then end at that deadline). A rollout
that reaches it writes no `result.json`; the cell is an error naming the
`[HARD-DEADLINE]`.

**Metrics.** `rewards.reward` is the reward; tokens, `cost_usd`,
`n_tool_calls`, `skill_source`, `usage_source`, `verifier_error`,
`verifier_error_category`, `partial_trajectory` and BenchFlow's own
`error`/`error_category` (as `benchflow_error*`) land in `metrics`. Skill use
comes from the ACP trajectory, because BenchFlow 0.7.8 misses the
`Load skill` calls of the claude-agent-acp it pins:

| Field | Meaning |
|---|---|
| `skills_loaded` | every skill load in the trajectory, by name, once per `tool_call_id` |
| `n_skill_invocations` | loads of the arm's treatment skills only: every pack in the deployed root (dir names and frontmatter names), so a distractor pack placed in that root counts; skills outside it and self-gen's `skill-creator` do not. Omitted on arms without a treatment (no-skill, self-gen) |
| `n_skill_invocations_benchflow` | BenchFlow's own count |

`prompt_tokens` is BenchFlow's `n_input_tokens`, which includes cache reads and
writes (0.7.8 adds the writes twice), so it is not comparable with
`docker-claude`'s uncached `prompt_tokens`; `total_tokens` and `cost_usd` are.
When BenchFlow captured no usage (`usage_source: unavailable`) the token
fields and `cost_usd` are omitted rather than written as 0.

**Limits.**

- One sweep per host. BenchFlow removes every `benchflow.owned` container on
  the host at each job start and end, so a concurrent sweep (or any other
  `bench` run) can remove a running cell's container. In 3 concurrent sweeps,
  1 of 9 cells lost its container (an error cell).
- Integrated review. BenchFlow starts it whenever a task has
  `tests/rubric.json` and rejects a rubric in another format before any
  rollout exists. The uxl answer-quality tasks ship their own, read by their
  `test.sh`, so 8 of the 17 tasks at uxlfoundation/skills 6feb3f3 cannot run
  through `benchflow:*`, even with the `artifacts` key BenchFlow also refuses
  removed: `oneccl-datatype-count-mismatch`,
  `oneccl-divergent-collective-sequence`, `onedal-sklearn-or-native-kmeans`,
  `onednn-framework-blocked-layout`, `onedpl-missing-device-synchronization`,
  `onetbb-bounded-image-flow-graph`, `sycl-cmake-compiler-cache`,
  `sycl-loader-plugin-mismatch`.

Model ids follow BenchFlow's provider prefixes, e.g.
`aws-bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0` with
`AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION` exported. Behind an HTTP proxy,
BenchFlow's image builds and in-sandbox agent installs need the proxy in the
Docker client config (`proxies.default` in `$DOCKER_CONFIG/config.json`), and
the docker bridge gateway must bypass it. The adapter adds the gateway (from
`docker network inspect bridge`, so a custom `bip` works), `172.17.0.1` and
`host.docker.internal` to both `no_proxy` and `NO_PROXY` for the `bench`
process. The Docker config's `noProxy` needs the same gateway (the actual one
on a custom `bip`, not `172.17.0.1`), or the agent's calls to BenchFlow's
host-side LiteLLM proxy are refused with "Access Denied". If you point
`DOCKER_CONFIG` at a scratch dir, symlink its `cli-plugins` too, or
`docker compose` disappears.

## Run Examples

List known harnesses:

```bash
python cli.py tasks harnesses
```

Run one task through Codex and compare it to Claude Code:

```bash
python cli.py tasks run \
  --tasks-root <content-checkout>/evaluation/harbor/tasks \
  --tasks onetbb-parallel-reduce \
  --harnesses claude-code,codex \
  --baseline-harness claude-code \
  --model anthropic/claude-opus-4-6 \
  --out-json results/task-runs/onetbb-reduce.json
```

If `--baseline-harness` is not listed in `--harnesses`, the CLI adds it to the
run matrix and prints that it will also run the baseline harness.

Run all tasks through a custom wrapper:

```bash
python cli.py tasks run \
  --tasks-root <content-checkout>/evaluation/harbor/tasks \
  --all \
  --harnesses command \
  --command-template "my-agent --task {task_path} --model {model} --out {output_dir}" \
  --model my-model
```

The command runs from the directory above the tasks root, so `{output_dir}`
and the `DOKIMI_OUTPUT_DIR` / `DOKIMI_OPERATIONS_FILE` environment variables
are absolute paths even when `--output-dir` is relative. A `reward.txt` the
command writes under that directory is the cell's reward; without one, the
cell falls back to the exit code. The directory is emptied before each run; if
a previous run's files there cannot be deleted, the cell is not run and is
recorded as errored.

Use `--dry-run` to validate matrix expansion and artifact shape without invoking
external agents.

## Subject Work Suites

`subjects run` uses the same task harnesses for the subject descriptor's
`suite.tasks` entries. Task execution is explicit because the work harness may
invoke Docker, Harbor, or a billable model-backed solver. If a descriptor
declares tasks, pass `--work-harnesses` or consciously skip them with
`--skip-work`:

No descriptor is committed here — a subject names evaluation content, so you write
one against your own checkout:

```toml
# my-subject.toml
baseline = "baseline"

[subject]
id = "onetbb-quickstart-skill"
kind = "skill"
ref = "<content-checkout>/skills/onetbb-quickstart"

[suite]
products = ["oneTBB"]
questions = ["<content-checkout>/questions/onetbb_golden.json"]
tasks = []

[[matrix.cells]]
id = "arms-runner"
model = "gpt-6.1-sol"
provider = "openai"
harness = "arms-runner"
plugins = []
```

```bash
python cli.py subjects run my-subject.toml \
  --work-harnesses docker-claude,docker-claude-skill:<content-checkout>/skills/onetbb-quickstart \
  --work-baseline-harness docker-claude \
  --work-model us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --work-repeats 3
```

The subject scorecard embeds the work status, the `task_runs.v1` artifact path,
the Markdown task report path, and the `summary.per_harness` pass-rate/cost
rollup. Use `--work-dry-run` to verify wiring without invoking the harness.

## Skill treatment experiment (without-skill vs with-skill)

The skill experiment is just two `docker-*` harnesses over one task set, with
the no-skill harness as the baseline. Everything — pass rate, cost, tokens,
per-task breakdown — lands in one schema-validated artifact:

```bash
python cli.py tasks run \
  --tasks-root <content-checkout>/evaluation/harbor/tasks \
  --tasks intel-perf-serial-accumulator,intel-perf-false-sharing,intel-perf-missing-restrict \
  --harnesses docker-claude,docker-claude-skill:<content-checkout>/skills/intel-performance-patterns/SKILL.md \
  --baseline-harness docker-claude \
  --model us.anthropic.claude-haiku-4-5-20251001-v1:0 \
  --repeats 3 \
  --out-json results/skill-task-arms.json
```

- `--repeats 3` runs every `(task, harness)` cell three times so the pass-rate
  and cost deltas carry variance (N=1 is noisy on a binary verifier).
- `--baseline-harness docker-claude` makes the no-skill harness the reference,
  so `summary.comparisons` reports the skill arm's `pass_rate_delta` and
  `cost_delta_usd`.

### How the skill arm offers the skill

The arms differ by the skill and one notice, and by nothing else:

- **Delivery.** The skill directory is wrapped in a one-skill plugin and passed
  with `--plugin-dir`, at `/opt/abskill` in the container (outside `/app`, and
  removed before the verifier runs) or beside the scratch work tree on the
  host. The agent loads it with its own `Skill` tool. The skill's top-level
  `evals/` (its prompts and expected outputs) is not copied. Symlinks are
  copied as the files they point to, and only when they point inside the
  skill directory and outside its `evals/`: a link out of the skill could
  ship a task's `solution/` and `tests/`, and a link to an ancestor would copy
  the checkout into itself. Such a skill fails the cell before the image build.
    - `$CLAUDE_CONFIG_DIR/skills` does not work here: it is not loaded under
      `--setting-sources ""` (measured, Claude Code 2.1.286).
    - `--setting-sources project` would also read the task tree's own
      `.claude/settings.json` and `CLAUDE.md`.
- **Tool set.** Both arms have the `Skill` tool. With it on, Claude Code also
  offers 13 bundled skills, two built-in plugins' skills, and any managed
  (policy) skill under `/etc/claude-code`. Both arms switch those off. In the
  container only the core `doctor` command remains; the host route also lists
  `design`, which the model cannot invoke. Any other offered skill is recorded
  in `metrics.unexpected_skills`, so a later CLI that adds one to both arms
  shows up in the rows.
- **Frontmatter.** The skill's frontmatter is part of the treatment. A skill
  with `model:` or `context: fork` is refused (a forked subagent can switch
  models mid-cell), and so is one with `disable-model-invocation: true` (the
  agent could never load it) or `hooks:` (code or another model runs in the
  skill arm only, and none of it shows in `tool_calls`).
- **Notice.** The skill arm's prompt opens with a fixed notice: a skill named
  `<plugin>:<plugin>` (the id the CLI lists it under) is installed; load it before you start, and trust the environment
  where it disagrees. The notice names no task, API or fix, and follows
  intel/skills' `evaluation/harbor/instructions/use-<skill>.md`. Without it the
  arm would also measure whether the description gets the skill selected,
  which is a second variable in the reward delta. The discovery arm measures
  that variable on its own (see [Three skill arms](#three-skill-arms)).
  Changing the notice changes what past deltas mean.
- **Evidence it reached the agent.** Read from the stream, per cell:
    - `metrics.offered_plugins` and `metrics.offered_skills` are what the
      session's `init` event lists, with `metrics.plugin_errors` when it reports
      any. If the treatment's plugin is not in `offered_plugins`, it did not
      load, and the cell gets `metrics.solver_error: "treatment not offered:
      ..."`. (`offered_skills` lists what a user can invoke, so a
      `user-invocable: false` skill is missing from it even when it loaded.)
    - `metrics.n_skill_invocations`, on skill-arm rows only, counts `Skill`
      calls for the treatment that the CLI did not reject: by `<plugin>:<plugin>`
      or the frontmatter name (a leading `/` is stripped), or by the command the
      CLI resolved the call to. At 0 the agent never loaded the skill through
      the `Skill` tool; it may still have `Read` the `SKILL.md` under the plugin
      directory, which this does not count. BenchFlow rows use the same key but
      treat every pack in the deployed skills root as the treatment, a
      distractor placed there included, so the two rates match only for a root
      that holds the treatment alone.
    - `metrics.treatment` records the skill's name and the plugin id it was
      installed under. The plugin id is a slug of the name (`[a-z0-9_-]`),
      because Claude Code refuses a plugin name with whitespace or any of
      `@ : / \`.
    - A solve that timed out keeps the stream it produced before it was killed,
      so these fields are filled in for it too. It is marked
      `metrics.solver_timeout`, and counts as a miss unless the plugin also did
      not load (`solver_error`).
- **Comparability.** The baseline changed with this delivery: it now has the
  `Skill` tool, with the bundled, built-in-plugin and policy skills off.
  `docker-claude` numbers from before it are not a baseline for runs after it.
- **Inputs.** `docker-claude-skill:<path>` takes a skill directory or its
  `SKILL.md`. A path with no loadable `SKILL.md`, a refused link or refused
  frontmatter fails the cell before the image build, rather than announcing a
  skill that is not there.
- **Tokens.** `prompt_tokens` and the other token counts are summed over the
  result's `modelUsage`, which also covers helper models; the result's own
  `usage` covers the parent session only. `metrics.models_used` lists them.

### Three skill arms

One skill, three arms over the same tasks, each answering its own question:

| Arm | Skill installed | Notice | What its pass rate measures |
| --- | :---: | :---: | --- |
| `docker-claude` | no | no | the task without the skill (baseline) |
| `docker-claude-skill:<path>` | yes | yes | the skill's content, given that it is loaded |
| `docker-claude-skill-discover:<path>` | yes | no | the skill as a user meets it: the agent has to find it from its description |

The discovery arm installs the skill exactly as the skill arm does: same
plugin, same `Skill` tool, same launch flags. Only the notice is dropped, so
the two arms differ by one variable. The default skill arm keeps the notice.
Run all three together:

```bash
python cli.py tasks run --tasks-root <content-checkout>/evaluation/harbor/tasks \
  --tasks onetbb-transpose \
  --harnesses docker-claude,docker-claude-skill:<skill>,docker-claude-skill-discover:<skill> \
  --baseline-harness docker-claude --model <model> --repeats 3 \
  --out-json results/onetbb-discover.json
```

- **Which arm a row is.** Rows of both skill arms carry `metrics.skill_notice`
  (`true` for the skill arm, `false` for the discovery arm), next to the
  `metrics.treatment`, `metrics.offered_plugins` and `metrics.n_skill_invocations`
  described above. A discovery cell with `n_skill_invocations: 0` is an agent
  that never loaded the skill through the `Skill` tool; it may still have `Read`
  the `SKILL.md` under the plugin directory, which this does not count.
- **Discovery gap.** When a discovery arm runs next to the skill arm with the
  same skill directory and launch (`s`, `./s/` and `s/SKILL.md` pair), `summary.discovery_gaps` holds both pass rates, the
  gap (with-skill − discovery) and both skill-invocation rates. The report
  shows them in a *Skill discovery* table. The gap is withheld when the two arms
  ran different cells (`attempted_cells_match` is false: one arm attempted a cell the other
  did not) or lost different cells (`errored_cells_match` is false: the errored cells differ).
  The report labels which one withheld it. Each arm also gets its own row against the baseline.
- **Was the description visible?** Claude Code lists skills to the model as
  name plus description (and `when_to_use`), cutting each description at
  `skillListingMaxDescChars` (1536 by default) and shortening the whole listing
  to fit `skillListingBudgetFraction` of the context window (1%, about 8000
  characters on a 200k window). A catalog of dozens of skills overruns that
  budget and shows bare names. These arms offer one plugin skill and a core
  command or two, so the budget is not reached; a description over the per-skill cap is still cut.
  `metrics.skill_listing` records `description_chars` and `truncated`; a truncated
  discovery cell gets a warning in `run.log`, and the report notes it. A
  treatment whose plugin is missing from the session's `init` event fails the
  cell (`solver_error`), in this arm too.

### Report and raw data

`tasks run` writes a Markdown report next to `--out-json` (override with
`--out-md`), rendered by `dokimi/report/task_runs_report.py` — the same
package the Q&A `arms`/`eval` reports live in. Sections: headline, difficulty
rollup, per-harness comparison, per-task pass/cost, and a per-cell answer detail
that puts the verifier verdict next to the model's own `solver.json` self-report
(⚠️ flags cells the model claimed it solved but the verifier scored 0 — typically
correct code that misses the performance bar). Re-render any existing artifact:

```python
import json
from dokimi.report.task_runs_report import render_task_runs_report
print(render_task_runs_report(json.load(open("results/skill-task-arms.json"))))
```

For analysis, export to a flat per-cell table — one row per cell with the
telemetry promoted to `metric_*` columns, the full `metrics`/`operations` blocks
kept as JSON-encoded columns:

```bash
python cli.py dataset export --kind task_runs \
  --input results/skill-task-arms.json \
  --out-dir results/skill-task-arms-dataset --format jsonl   # or parquet / hf
```

What the harnesses handle for you:
pass the host proxy through to `docker build` (corporate networks), run the
oracle and the verifier as root for images that set a non-root `USER` (the
agent runs as uid 10001), install the skill tree as a plugin (not just
`SKILL.md`; without its top-level `evals/`), and re-run the `g++`/`gcc` command from `instruction.md` inside
the container after the solve — a `-host` agent builds against the host's
newer glibc, and an in-container agent may not have built at all.

For a single throwaway oracle check, run one task through `docker-oracle`
rather than reaching for a shell script — it is the same Docker flow and it
still writes the artifact:

```bash
python cli.py tasks run --tasks <task-name> \
  --tasks-root <checkout>/evaluation/harbor/tasks \
  --harnesses docker-oracle --out-json results/oracle-check.json
```

## Output Artifact

`tasks run` writes a `task_runs.v1` JSON artifact. Important fields:

- `results[]`: one row per `(task, harness, model, repeat)` cell. With
  `--repeats > 1` each row carries a `repeat` index and the run lands in a
  `run<N>/` subdirectory.
- `results[].passed`: verifier pass/fail. If a reward file is present, its
  number is `results[].reward`: `1.0` passes, and anything lower, including
  partial credit such as `0.75`, is kept as the reward and counts as a fail. If
  no reward is found, the adapter's exit code is used as the fallback, except
  for a Harbor-backed cell, which is then errored (see the Harbor grading
  table above).
- `results[].metrics`: for LLM harnesses (`docker-claude*`), the per-run
  `UsageRecord` fields — `cost_usd`, `prompt_tokens`, `completion_tokens`,
  `total_tokens`, `cache_read_tokens`, `cache_write_tokens`, `latency_sec`,
  `ttft_sec`, `n_calls` (agent turns) — plus `operation_count` /
  `operations_by_type`, `model`, `models_used`, `terminal_reason`,
  `tool_calls` and `offered_skills`, and when set `offered_plugins`,
  `plugin_errors`, `unexpected_skills`, `solver_error`, `solver_timeout` and
  `recompile_failed`. Skill-arm rows add `treatment` and
  `n_skill_invocations`. The `docker-claude*` and `docker-probe*` rows
  also carry `isolation` (`agent_location`, `work_tree`, `operator_config`,
  `env`, `tools`, `host_filesystem`, `network`, and `memory` for the
  container launch); container rows carry `egress`, probe rows
  `isolation_probe`. Harbor-backed rows carry `prompt_tokens`,
  `completion_tokens`, `cache_read_tokens`, `total_tokens` and `cost_usd` when
  Harbor's trial file records them, plus `n_trials` and `result_json`.
- `summary.per_harness`: pass rate with a `pass_rate_ci{}` interval, elapsed
  time, operation totals, `skill_invocation_rate` / `skill_invocation_n`, a
  rolled-up `cost{}` block (`total_cost_usd` is `null` when no call was priced;
  `cost_per_pass_usd`), and a `per_task{}` pass breakdown.
- `summary.comparisons`: `pass_rate_delta`, `normalized_gain`,
  `normalized_change`, `passed_delta`, `cost_delta_usd`, and a `mcnemar{}`
  significance block against `--baseline-harness` (below).

The artifact is schema-validated on write, like question/answer/eval/arms
artifacts. Per-run detail is written under each cell's output directory: the
agent's result event (`solver.json`) and full stream (`solver.jsonl`; the
values of the agent's credential variables are redacted, also line by line and
in their JSON-escaped form, but anything else the agent printed, such as a credential file's contents, is not, so do not share
it),
`prompt.txt`, `run.log`, `reward.txt`, the agent's final tree (`work/`),
`egress.jsonl` for a container launch whose sidecar log was read and `isolation_probe.json` for a probe.

## Statistics on the execution-verified track

Ground truth here is binary: the task's own test suite exited 0 or it did not.
That rules out the paired t-test the judged Q&A track uses — on 0/1 outcomes the
per-task deltas are `{-1, 0, +1}`, badly non-normal, and all of the information
about a difference sits in the *disagreements*.

So `summary.comparisons.<harness>.mcnemar` holds **McNemar's exact test**
(`dokimi/eval/binary_stats.py`), pairing on the `(task, repeat)` cell
against the baseline harness. It is computed automatically whenever
`--baseline-harness` is set; no flag to enable. The report renders it as
_Paired significance vs baseline (McNemar)_.

The block reports the whole 2×2 table, not just a p-value:

| Field | Meaning |
|---|---|
| `n_gained` | cells the treatment passed and the baseline failed |
| `n_lost` | cells the baseline passed and the treatment failed |
| `n_both_passed` / `n_both_failed` | the concordant cells |
| `paired_pass_rate_delta`, `ci_lo`, `ci_hi` | paired delta and its bootstrap interval |
| `p_exact` | two-sided exact binomial on the discordant count |
| `p_adjusted` | Holm-corrected across the non-baseline harnesses in the sweep |
| `correction_family` | always `harnesses` — the judged track's arm and interaction families are corrected separately |
| `n_excluded` | cells dropped from the pairing (see below) |
| `n_units`, `pseudo_replicated` | independent tasks behind `n` pairs, and whether they are fewer |

Note the two similarly named deltas. `mcnemar.paired_pass_rate_delta` is
`(n_gained - n_lost) / n` over the **shared** cells; its sibling
`comparisons.<harness>.pass_rate_delta` subtracts two marginal pass rates.

An errored cell is one whose harness raised (`metrics.error`: a docker build
failure or timeout, an exec failure, or a previous run's files in the cell's
dir that could not be deleted), or whose solver CLI made no attempt
(`metrics.solver_error`). It produced no verdict, so it is
missing data, not a 0. An agent that exceeds its timeout, or a verifier that
does, is a failure (reward 0, returncode 124), not an error: otherwise an arm
that hangs would score above one that fails fast. So is a `docker-oracle`
`solve.sh` that exceeds the agent budget. A Harbor trial that raised, an agent
timeout included, is errored (`metrics.error`); only a lone verifier timeout is
scored 0. A `docker-*` step with no budget of
its own (`docker cp`, `inspect`, the `exec`s that copy files in) gets 600 s; one
that must succeed and runs out errors the cell. A timeout kills the step's whole process tree on
the host (a host agent's tool processes too); in the container, whatever the
agent uid left running is killed before the verifier starts.

Some timeouts leave no verdict and are still scored as failures (reward 0,
returncode 124, no `metrics.error`), so they count in pass-rate denominators:
the pre-verifier reap, the recompile, the credential-file and plugin removals,
the verifier's `reward.txt` copy-out, and the sidecar's removal. A slow daemon
on one of these reads as a model failure; such a cell's run.log has a `TIMEOUT:`
line, and the cell is not in `errored`.

- `per_harness.<h>` carries `errored` and `scored = n - errored`, and
  `pass_rate = passed / scored`. `per_task` carries `errored` too.
- `pass_rate_delta`, `passed_delta` and `cost_delta_usd` are null when the two
  arms lost different cells to errors, since an arm with fewer verdicts would
  look like fewer passes and less cost. Cells both arms lost cancel out. Each
  comparison also carries `errored` and `baseline_errored`.
- McNemar always pairs only the cells both arms scored; `n_excluded` counts
  the rest.

Four properties are worth internalising before reading the number:

- **Only discordant cells count.** Adding ten easy tasks both arms already solve
  changes `paired_pass_rate_delta` (it is a rate over all tasks) but leaves
  `p_exact` untouched. `n_both_passed` is reported so a null result can be told
  apart from a suite with no headroom left.
- **A small suite cannot reach significance.** Four fixes and zero regressions
  gives *p* = 0.125; six and zero gives 0.0312. That is the honest floor of a
  20-task suite, not a defect.
- **`--repeats` does not buy the power it looks like.** Repeats are paired as
  separate `(task, repeat)` cells, so `n` grows but the independent units do not:
  repeats of one task share its difficulty, and McNemar assumes independent
  pairs. `n_units` reports the distinct tasks, `pseudo_replicated` flags
  `n_units < n`, and the report prints a caveat — the p-value is then
  anti-conservative and should be read as a screening signal, not a claim at α.
  Adding *tasks* adds real power; adding repeats mostly reduces per-cell noise.
- **No discordant pair at all means no verdict.** `p_exact` and `significant` are
  `null`, and the report shows a dash. Absent evidence is not evidence of no
  difference.

`n_excluded` counts cells left out of the pairing, measured against every cell
either harness *attempted*: present for one harness only, or belonging to a row
whose harness itself errored (`metrics.error`, e.g. a docker build failure, or
`metrics.solver_error`).
Those are missing data, not verified failures — scoring them as failures would
charge infrastructure flakiness to the treatment.

The exact binomial is used rather than the chi-square approximation because the
discordant count on a suite this size is routinely under 10, where the
approximation is unreliable. It needs no SciPy, so this test is always available
— unlike the judged track's `p_ttest`.

Alongside the paired test, each harness and comparison carries per-arm lift
numbers. They use SkillsBench's lift formula but are not its leaderboard
numbers, so do not read the two side by side without the caveats below:

| Field | Meaning |
|---|---|
| `per_harness.<h>.pass_rate_ci` | Wilson 95% interval on `pass_rate`, over the same scored (non-errored) cells; same independence caveat as McNemar under `--repeats`. SkillsBench does not use Wilson: its paper uses a Wald interval (v4, App. G; a percentile bootstrap in v1) and its leaderboard a task-level `1.96·stdev(per-task means)/√87`, which is wider under repeats |
| `per_harness.<h>.skill_invocation_rate` | share of the rows reporting `metrics.n_skill_invocations` (errored rows excluded) where it is > 0; `null` when no row reports it. Only harnesses that write `metrics.n_skill_invocations` contribute; the key counts invocations of the treatment skill. A row marked `metrics.solver_error` is errored too and is left out |
| `per_harness.<h>.skill_invocation_n` | the rows behind `skill_invocation_rate` |
| `per_harness.<h>.cost.cost_per_pass_usd` | total cost / passes, only when every row, errored rows included, carries `metrics.cost_usd`. A runner-errored row (no `cost_usd`) or an agent timeout whose result was never parsed therefore nulls it for the arm |
| `comparisons.<h>.normalized_gain` | Hake's `(arm − base) / (1 − base)`, as SkillsBench uses it: the share of the baseline's headroom gained. It equals SkillsBench's per-configuration value only when every cell scored and rewards are binary (they average per-task mean rewards and count a missing cell as 0). Unbounded below (0.5 vs 0.95 is −9), so read regressions from the next field; `null` when the baseline passes everything, or when `pass_rate_delta` is withheld |
| `comparisons.<h>.normalized_change` | the Marx & Cummings piecewise formula applied to aggregate pass rates, in [−1, 1]: the gain above for a gain, `(arm − base) / base` (share of baseline passes lost) for a loss, 0 when equal. The halves are on different scales — at a 0.8 baseline +0.2 is +4 pp, −0.2 is −16 pp — so compare signs, not magnitudes across signs; `null` when both rates are 0% or both 100% (M&C drop that case), or when `pass_rate_delta` is withheld |

A with-skill arm whose `skill_invocation_rate` is near zero measures the
listing, not the skill: the lift, if any, did not come from the agent reading
it.

## Operation Telemetry

Every run records the top-level harness invocation. Wrappers and adapters can add
structured operation telemetry in two ways:

1. Append JSON lines to the path in `DOKIMI_OPERATIONS_FILE`.
2. Print a line prefixed with `DOKIMI_OP` followed by a JSON object.

Example:

```text
DOKIMI_OP {"type":"tool","name":"edit","elapsed_sec":0.12}
DOKIMI_OP {"type":"subagent","name":"reviewer","status":"ok"}
DOKIMI_OP {"type":"loop","name":"turn","elapsed_sec":3.4}
```

Unstructured stdout/stderr lines mentioning `tool`, `subagent`, or `loop` are
also counted as low-fidelity telemetry, but structured events are preferred.

## Fair Comparisons

Use one task suite, one model, and fixed task revisions when comparing harnesses.
The baseline harness is not a treatment arm; it is the reference cell for
pass-rate deltas. For example, compare `codex` to `claude-code` only when both
ran the same task set under the same model and task commit.

The Q&A `arms run` path remains separate. It measures context artifacts with an
LLM judge. The `tasks run` path measures executable work with verifier pass rate.
The two therefore need different statistics — continuous scores against a
baseline arm ([benchmarking-and-comparison.md](benchmarking-and-comparison.md))
versus paired binary outcomes against a baseline harness (above) — and their
numbers are not interchangeable. A skill that wins on the judged track has not
been shown to make anything run.

## External Sources

Executable tasks always come from a checkout of a content repository (there is
no bundled task tree), and skills can come from one too. Nothing here clones
anything — point the existing flags (`--tasks-root`, `skill:<path>`) at a
checkout you already have.
[external-content.md](external-content.md) is the canonical page for that: how
to lay a checkout out, how to scan it before you spend money on it, and what
every command does when a root is missing. This section covers only the
harness-specific part.

Scan the checkout first (`python cli.py catalog scan --root <checkout>`), then
use the entries it reports the same way as local ones:

```bash
# with-skill treatment arm using an external skill
python cli.py tasks run --tasks onetbb-histogram-local-aggregation \
  --tasks-root /tmp/uxl-skills/evaluation/harbor/tasks \
  --harnesses docker-oracle,docker-claude-skill:/tmp/uxl-skills/skills/uxl-onetbb \
  --baseline-harness docker-oracle

# or hand an external task straight to Harbor
harbor run -p /tmp/uxl-skills/evaluation/harbor/tasks/onetbb-histogram-local-aggregation -a oracle
```

The `docker-*` harnesses read `network_mode` from the task's sandbox table
(`[environment]` in `task.toml`, `sandbox:` in a `task.md`), alongside
`cpus`/`memory_mb`. Without it the task container has no network.
`network_mode = "public"` gives `docker-oracle` bridge for its solve and
verify, but for `docker-claude` it applies to the **verifier only**: the agent
solves on the internal network, and a solve that needs a host (pip, a package
mirror) must list it under `egress` in that table (`environment.egress`, or
`sandbox.egress` in a `task.md`). A `-host` agent
solves with the host's network whatever the task declares
(`metrics.isolation.network: open`).
The oracle and verifier steps of a public-network
task also receive the host's `http(s)_proxy`/`no_proxy`, since verifiers such
as SkillsBench's install packages at verify time (90 of its 101 `test.sh`
install something; 52 install both `uv` and pytest).

SkillsBench tasks (`task.md`) run through the `benchflow:*` harnesses:

```bash
git clone https://github.com/benchflow-ai/skillsbench /tmp/skillsbench
python cli.py tasks run --tasks parallel-tfidf-search \
  --tasks-root /tmp/skillsbench/tasks \
  --harnesses benchflow:claude-agent-acp,benchflow-skill:claude-agent-acp \
  --baseline-harness benchflow:claude-agent-acp \
  --model aws-bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
```

### Native host probe boundaries

Container probes run against Linux tools and Linux process metadata. A native
host probe uses only tools visible in the agent's environment: raw proxy and
TCP checks require Bash and `timeout`, and inspection of ancestor environments
requires Linux procfs. On macOS these checks can report `untested`; that result
fails the isolation check and must not be interpreted as `closed`. An observed
network leak still reports `open` even when another check lacks tools. Windows
host shell probes are not qualified by the Linux-container tests.
