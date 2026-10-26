---
depends_on:
  - dokimi/dokimi/harnesses/tasks.py
  - dokimi/dokimi/harnesses/docker_solver.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Authoring an executable (terminal-bench) task

An executable task evaluates an LLM coding agent on real work: the agent gets an
`instruction.md` and a container, has to produce working code, and is graded by a
deterministic verifier inside Docker. The verifier's pass/fail *is* the metric —
no LLM judge is involved.

Task definitions are evaluation content, so they live in a separate content
repository, not here. This framework runs them from a checkout you name:

```bash
python cli.py tasks list --tasks-root <checkout>/evaluation/harbor/tasks
python cli.py tasks run  --tasks-root <checkout>/evaluation/harbor/tasks \
  --tasks <task-name> --harnesses docker-oracle --out-json results/runs.json
```

This page is the format reference: everything a task directory must contain for
`tasks list`/`tasks run`, the `docker-*` harnesses and the coverage contract to
read it. Contribute the task itself to the content repository; contribute changes
to this format here, next to the code that parses it
([`dokimi/harnesses/tasks.py`](../dokimi/harnesses/tasks.py),
[`dokimi/harnesses/docker_solver.py`](../dokimi/harnesses/docker_solver.py)).

## 1 — Pick a task

Aim for one focused API or concept per task. A task should:

- Exercise a single primary API (`parallel_for`, `parallel_reduce`,
  `flow::graph`, `dpnp.fft`, …) plus minimal scaffolding.
- Be small enough to compile and run reliably in an offline
  (`--network none`) sandbox well inside its `verifier.timeout_sec`.
- Be strong enough to reject keyword-only or no-op solutions — the verifier
  must check observable behavior, not just file presence or comment patterns.

Difficulty, level and trigger tagging is defined in
[`difficulty-rubric.md`](./difficulty-rubric.md); a task should be placed on that
grid deliberately rather than labelled by intuition.

## 2 — Directory layout

One directory per task, named in lower-kebab-case with the library prefix that
matches the primary API surface (`onetbb-…`, `onemkl-…`, `dpnp-…`):

```text
<tasks-root>/<library>-<concept>/
  instruction.md          # REQUIRED: task description handed to the agent
  task.toml               # REQUIRED: metadata, timeouts, container settings
  environment/
    Dockerfile            # REQUIRED: base image + preinstalled libs + baseline
    <seed files>          # starter source, headers, data (build context)
  tests/
    test.sh               # REQUIRED: entry point; writes the reward file
    test_*.py             # pytest cases
  solution/
    solve.sh              # REQUIRED for the oracle harness; never shown to the agent
```

This page describes the Harbor layout. In it, `instruction.md` and
`task.toml` are hard requirements: `load_task()` raises `FileNotFoundError`
without either. Directory discovery treats a directory as a task when it
contains `task.toml` or a BenchFlow `task.md`; see
[Task Layouts](coding-harnesses.md#task-layouts) for that layout and for which
harnesses run it.

## 3 — `task.toml`

```toml
version = "1.0"

[metadata]
author_name = "Your Name"
author_email = "you@example.com"
difficulty = "medium"                 # easy | medium | hard
difficulty_explanation = "Requires recognizing a serial accumulator dependency"
level = "diagnosis"                   # triage | diagnosis | end_to_end (optional)
category = "programming"
tags = ["c++", "performance", "onetbb"]

[verifier]
timeout_sec = 60.0

[agent]
timeout_sec = 300.0

[environment]
build_timeout_sec = 300.0
docker_image = "intel-hpc-bench/<task-name>:latest"
cpus = 4
memory_mb = 2048
storage_mb = 5120
gpus = 0
allow_internet = false
```

Field by field, with the consumer that actually reads it:

| Field | Read by | Meaning |
| --- | --- | --- |
| `metadata.tags` | `tasks list`, the coverage contract | Lowercased tag list. **This is the work signal**: a task counts as work for a product when its tags contain the product's key in `products.yaml` (or one of that product's `tag_aliases:`). Include the library key (`onetbb`, `dpnp`, `intel-performance-skills`), the concept (`parallel-reduce`), and any provenance tag (`parres`). |
| `metadata.difficulty` | `tasks list`, the task-runs report | `easy` / `medium` / `hard`; drives the difficulty rollup in the Markdown report. Missing renders as `?`. |
| `metadata.level` | [`difficulty-rubric.md`](./difficulty-rubric.md) | Workflow depth: `triage`, `diagnosis`, `end_to_end`. Optional but recommended for skill evaluation. |
| `metadata.difficulty_explanation`, `category`, `author_*` | humans | Provenance and review context; carried through in the run artifact. |
| `agent.timeout_sec` | `TaskSpec.timeout_sec`, the solver harnesses | Wall-clock budget for the agent's attempt. Typically 300. |
| `verifier.timeout_sec` | Harbor, human reviewers | Verifier budget. Keep it small (≤ 60s where possible) so CI stays fast. |
| `environment.build_timeout_sec` | `docker-*` harnesses | Image-build budget; used instead of the default when set (oneAPI/pip-heavy images need > 10 min). |
| `environment.docker_image` | Harbor | Image tag Harbor expects to exist (`harbor run` does **not** build it for you). The in-repo `docker-*` harnesses build and tag `dokimi/<task-name>:latest` themselves. |
| `environment.cpus`, `memory_mb` | `docker-*` harnesses | Container limits (`--cpus`, `--memory`); default to 4 CPUs / 2048 MB when absent. A malformed value falls back to the default rather than failing the run. |
| `environment.network_mode` | `docker-*` harnesses, Harbor | `"no-network"` (absent means the same), `"allowlist"` with `allowed_hosts`, or `"public"` (NAT'd internet, bridge). It applies to the **verifier** and `docker-oracle`'s solve, not to an agent: an agent solves on an internal network whose only way out is the egress proxy (see `environment.egress`), unless `[agent] network_mode` says otherwise. `[agent]` and `[verifier]` take the same keys for their phase. Any other value, or `allowed_hosts` without `"allowlist"`, errors the run. The image *build* gets host network unless the operator passes `--network build=none`. See [Network policy](./coding-harnesses.md#network-policy). Prefer offline. |
| `environment.egress` | `docker-claude` | Hosts the agent's solve may reach through the egress proxy, e.g. `["pypi.org", "files.pythonhosted.org"]`. A bare host means `host:443`; `host:port` is accepted. No wildcards (`*.pythonhosted.org`), commas or leading/trailing dots: list each host. A plain-HTTP host is reached directly, so it must be in the operator's `NO_PROXY`. |
| `environment.allow_internet` | `docker-*` harnesses, Harbor | Harbor's deprecated flag: `false` is `"no-network"`, `true` is `"public"`, read only when `network_mode` is absent. Keep it consistent with `network_mode`. |
| `environment.storage_mb`, `gpus` | Harbor | Declared resource envelope. |

Unknown tables and keys are ignored, so a task may carry extra fields the
content repository needs.

## 4 — `instruction.md`

The full problem statement handed to the agent: what to produce, where to put
it, how it will be judged, and the build/run command the verifier will use. It
must be solvable from the container's contents alone — no network, no reference
to files the agent cannot see (and never a pointer to `solution/`).

## 5 — `environment/Dockerfile`

- Preinstalls the toolchain and libraries, and lays down the *starting state*
  (for a performance task, the slow baseline at `/app`).
- Is the Docker build context: the harness runs
  `docker build -f <task>/environment/Dockerfile <task>/environment`, so seed
  files must sit next to the Dockerfile.
- Should pin what it installs. Dependencies may be pulled at **build** time;
  solve and verify then run offline.
- Must not contain the solution.

## 6 — `tests/` (the verifier)

`tests/test.sh` is the entry point. The harness copies `tests/` to `/tests` in
the container and runs `/tests/test.sh` after `mkdir -p /logs/verifier`.

The contract:

- Write the outcome to `/logs/verifier/reward.txt` — `1` (or `1.0`) for pass,
  `0` (or `0.0`) for fail. A fraction such as `0.75` (partial credit) is kept
  as the cell's reward and counts as a fail. The harness copies that file out;
  if it is missing it falls back to the verifier's exit code, so also exit
  non-zero on failure.
- Do not let a failure abort before the reward file is written (capture the
  pytest exit code, write the file, then exit with it).

The pytest cases must:

- Compile and run the agent's submitted source from a stable path stated in
  `instruction.md`.
- Use deterministic input (fixed seeds, formula-driven data; no
  wall-clock-dependent expectations unless timing *is* the task).
- Compare against a reference computed inside the test, not against a stored
  golden blob — and never check the agent's source for substrings as the only
  signal.
- Add at least one structural signal as well (e.g. the binary really links
  oneTBB, the ufunc really dispatches to the accelerated implementation) so a
  serial submission that happens to produce the right answer is still rejected.
- Where the task is about speed, gate on a conservative speedup threshold
  against a baseline built into the image, on a deterministic single-thread
  workload.

## 7 — `solution/solve.sh`

The oracle: a deterministic solution that makes the verifier pass end-to-end.
It is copied into the container as `/solution` and run as root, offline, with
only the image and seed files available. Keep it minimal — the oracle is a
correctness baseline for the *task*, not a tutorial. Every task needs one,
because the `docker-oracle` harness is how a task is proven runnable without
spending model tokens.

## 8 — Local verification

```bash
# Build the image and run the oracle through this repo's own harness
python cli.py tasks run \
  --tasks-root <checkout>/evaluation/harbor/tasks \
  --tasks <library>-<concept> \
  --harnesses docker-oracle \
  --out-json results/oracle.json
```

Expect `passed: 1` / `pass_rate: 1.0` in the summary. The oracle solves with
`network_mode`'s network, which a `docker-claude` agent never gets, so also run
the probe, and before a sweep one `docker-claude` cell (paid) if the solve
needs the network:

```bash
python cli.py tasks run \
  --tasks-root <checkout>/evaluation/harbor/tasks \
  --tasks <library>-<concept> \
  --harnesses docker-probe \
  --out-json results/probe.json
```

The probe needs no LLM. It runs the agent's launch (uid 10001 in the task
container, internal network, egress proxy) and passes only if every leak class
is closed. The container launch needs a glibc image (the host's `claude` binary
is copied in) and an `/app` that uid 10001 can own. A solve that needs pip or
another host gets a 403 from the proxy unless the host is under
`environment.egress`.

To check the two halves by hand:

```bash
TASK=<checkout>/evaluation/harbor/tasks/<library>-<concept>

docker build -t intel-hpc-bench/<library>-<concept>:latest "$TASK/environment"

# Oracle must pass
docker run --rm --network none -v "$TASK:/task" \
  intel-hpc-bench/<library>-<concept>:latest \
  bash -c "cp -r /task/solution /tmp/solution && bash /tmp/solution/solve.sh && \
           bash /task/tests/test.sh; cat /logs/verifier/reward.txt"
# → 1

# Negative check: the untouched baseline must fail
docker run --rm --network none -v "$TASK:/task" \
  intel-hpc-bench/<library>-<concept>:latest \
  bash -c "bash /task/tests/test.sh; cat /logs/verifier/reward.txt"
# → 0
```

A task that passes both checks is trustworthy; one that passes only the first
tells you nothing about whether the verifier discriminates.

Also confirm the task is discoverable and tagged as intended:

```bash
python cli.py tasks list --tasks-root <checkout>/evaluation/harbor/tasks
python -m dokimi.coverage \
  --tasks-root <checkout>/evaluation/harbor/tasks \
  --questions-root <checkout>/questions
```

The coverage matrix should count the new task under the product whose key is in
its `metadata.tags`.

## 9 — Provenance

Record where a task came from. If it is adapted from external code, note the
upstream repository, the commit or release consulted, the license, and what was
changed — and confirm the license allows redistribution. A task that is
"inspired by" upstream kernels rather than copied should say so explicitly, so
results are never reported as official upstream benchmark numbers. The content
repository is the right place for that record, kept next to the tasks it
describes; keep it current in the same change that adds the task.

## 10 — CI

CI in this repository proves the *framework* against external content: the
`external-catalog-smoke` job in `.github/workflows/agent-quality.yml` clones a
pinned public catalog, scans it, and drives one of its tasks through
`docker-oracle`, asserting the pass rate. It deliberately does not enumerate
individual tasks — verifying a task catalog is the content repository's job, and
this format spec is what the two sides share.
