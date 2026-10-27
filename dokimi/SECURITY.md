<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Security Policy

## Reporting a vulnerability

Please report security issues privately rather than opening a public issue.
Follow Intel's [vulnerability handling guidelines](https://www.intel.com/content/www/us/en/security-center/vulnerability-handling-guidelines.html),
or contact the maintainers directly.

When reporting, include:

- a description of the issue and its impact,
- steps to reproduce (a minimal proof of concept is ideal),
- affected versions/commits, and
- any suggested remediation.

We aim to acknowledge reports within a few business days and to keep you
updated as we investigate and remediate.

## Scope and threat model

This is a benchmarking and evaluation harness, not a hardened service. A few
properties are worth calling out for anyone running it:

- **Example execution.** The static benchmark can execute fenced code blocks
  found in documentation to compute `example_pass_rate`. By default this is
  **disabled**, and when enabled it runs in a constrained mode. Do **not** run
  example execution against untrusted documentation outside a sandbox — see
  [`docs/example-execution.md`](docs/example-execution.md).
- **LLM/provider calls.** The LLM track sends prompts (which may include
  retrieved documentation) to third-party model providers. Do not feed secrets
  or confidential material through the pipeline.
- **API keys.** Keys are read from environment variables and are never written
  to artifacts. Keep them out of committed configs and fixtures.

## Supported versions

The project is pre-1.0; only the latest `main` is supported. Fixes land on
`main` and are not backported.

## Known advisories in dependencies

`pip-audit` against the locks (`requirements.lock.txt`,
`requirements-docs.lock.txt` and `requirements-ci.lock.txt`) reports these advisories with no
fix the locks can take:

- **diskcache 5.6.3 — PYSEC-2026-2447** (CVE-2025-69872, GHSA-w8v5-vhqr-4h9v;
  pickle deserialization from a cache directory). Pulled in only by ragas,
  which imports it inside its opt-in `DiskCacheBackend`; Dokimi never
  constructs one, so no diskcache directory is created or read.
- **ragas 0.3.9 — PYSEC-2026-3046** (CVE-2026-6587, GHSA-95ww-475f-pr4f; SSRF
  and file read in the multi-modal faithfulness metric's loader). The
  advisory's range is 0.2.3–0.4.3, but the vulnerable module
  (`ragas/metrics/collections/multi_modal_faithfulness/util.py`) first ships
  in 0.4.2, so the pinned 0.3.9 does not contain it. A fixed release would be
  0.4.4 or later, which is outside the `ragas<0.4` cap: taking it means lifting
  the cap and migrating, not a bump, and 0.4.2–0.4.3 must be skipped.
- **filelock 3.32.7 — GHSA-v4x8-3wr5-3wq8, GHSA-9vhv-jxww-xfm5,
  GHSA-567q-5qvw-pf92** (CVE-2026-112940, CVE-2026-112913, CVE-2026-112907;
  fixed in 4.2.1). Every litellm release requires `filelock<4.0`, so 4.2.1
  cannot be locked. The first two are in the SQLite-backed `ReadWriteLock`
  (POSIX and Windows), which no dependency of Dokimi uses: datasets,
  huggingface-hub, litellm and virtualenv use `FileLock` and `SoftFileLock`.
  The third is in the soft locks on Windows: another local account that can
  create files in the lock directory can stall or break a lock holder.
  huggingface-hub takes a `SoftFileLock` for its download cache, so it applies
  to dataset and model downloads on a shared Windows host. Drop all three when
  litellm allows `filelock>=4.2.1`.

The `ragas>=0.3.0` floor keeps out 0.2.3–0.2.x, which are affected by
CVE-2025-45691 (GHSA-v2xr-wvrv-p969, file read through `retrieved_contexts`,
fixed in 0.3.0).

`.github/workflows/pip-audit.yml` runs `pip-audit` with exactly these IDs
ignored, on every PR or push that changes a lock or requirements file and daily
on `main`; a daily run that finds a new advisory opens an issue rather than
failing unrelated PRs. The `static` job in `.github/workflows/ci.yml` runs
`bandit -ll` with no Medium or High finding allowed.
The same IDs are ignored by osv-scanner through `dokimi/osv-scanner.toml`,
which OpenSSF Scorecard runs. A removed advisory comes out of this section,
the `pip-audit` step, and that file together.
