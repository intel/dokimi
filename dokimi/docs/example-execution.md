---
depends_on:
  - dokimi/dokimi/metrics/example_runner.py
---
<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Example execution (`example_pass_rate`)

The static benchmark can execute fenced code blocks found in documentation to
measure whether examples actually run. Executing arbitrary code from docs is
dangerous, so execution is governed by an explicit **execution policy** in the
spec.

## Backends

Configure under `metrics.example_pass_rate.execution` in your spec:

```yaml
metrics:
  example_pass_rate:
    enabled: true
    weight: 0.20
    execution:
      backend: none        # none | subprocess | docker
      allow_host: false    # required true for the subprocess backend
      timeout: 5           # seconds per example
      docker_image: python:3.11-slim
      mem_mb: 512
      cpus: 1.0
```

| Backend | Isolation | Use when |
| --- | --- | --- |
| `none` (default) | Nothing runs; examples reported as `skipped` | Untrusted corpora, or you only want extraction |
| `subprocess` | Host subprocess with per-process CPU cap and default memory cap, minimal env, temp cwd; its process group is killed when it exits or times out. **No network isolation and no process-count cap.** Requires `allow_host: true`. | Trusted, self-owned documentation |
| `docker` | Container with `--network none`, no capabilities (`--cap-drop ALL`, `no-new-privileges`), read-only mounts, CPU/memory/pid limits, per-example timeout | Untrusted documentation |

### Why `none` is the default

Previously, fenced `python`/`bash`/`sh` blocks were executed directly on the
host with only a timeout. That is unsafe for any documentation you do not own.
The default is now `none`; you must explicitly opt into execution.

`example_pass_rate` is computed over **executed** examples only. A document with no
examples — or whose examples were all skipped — scores `1.0` (nothing failed).
Each example records a `status` of `passed` / `failed` / `skipped` / `error`.

## Overriding the backend

Set `DOC_BENCH_EXAMPLE_BACKEND` to force a backend regardless of the spec — for
example, to neutralize execution in a shared CI runner:

```bash
DOC_BENCH_EXAMPLE_BACKEND=none dokimi run --spec benchmarks/spec.v1.yaml
```

## Security notes

- The `subprocess` backend strips the parent environment (no API keys leak into
  examples) and applies the requested POSIX limits, but it **cannot** isolate the
  network or cap the number of processes a snippet spawns (POSIX `RLIMIT_NPROC`
  counts all of the user's processes, not the snippet's). Only use it on
  documentation you trust.
- For anything else, use `docker`, which adds `--network none` and a read-only
  filesystem, and drops every capability with `no-new-privileges`, so the
  image's root cannot gain any back through a setuid binary. The snippet's
  temp directory is mounted world-readable for that capability-less root.
  See also [SECURITY.md](../SECURITY.md).

Host resource limits are platform-specific: Linux supports the CPU and address-space
limits; macOS supports CPU limits but rejects a requested address-space memory
limit before the snippet starts. Windows rejects requested CPU or memory limits.
The default `mem_mb: 512` therefore fails on macOS before launching the snippet.
For trusted, self-owned documentation on macOS, explicitly set `allow_host: true`
and `mem_mb: null` with `backend: subprocess`. This omits only the memory cap;
the CPU cap, wall-clock timeout, environment scrubbing and process-group cleanup
remain active. Python callers can use `ExecutionPolicy(mem_mb=None)` with the
same backend and host opt-in. Linux keeps the default 512 MiB cap unless explicitly
omitted. Windows refuses this subprocess mode even with memory omitted, because
the CPU cap is still required.

A failure to apply a requested limit prevents execution. When the host cannot apply
the limits, the run stops with an error that names the limit (`dokimi run` exits 2)
instead of scoring snippets that never ran, as failures or as passes. The `docker` backend
requires a positive memory cap and rejects `null`, zero or negative values before
launch. Use that Linux-container backend when both limits are required.
