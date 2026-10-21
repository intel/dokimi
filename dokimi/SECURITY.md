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

`pip-audit` against the locks (`requirements.lock.txt` and
`requirements-docs.lock.txt`) reports two advisories with no
upstream fix as of 2026-10-05:

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

The `ragas>=0.3.0` floor keeps out 0.2.3–0.2.x, which are affected by
CVE-2025-45691 (GHSA-v2xr-wvrv-p969, file read through `retrieved_contexts`,
fixed in 0.3.0).

The `static` job in `.github/workflows/ci.yml` runs `pip-audit` with exactly
these two IDs ignored, and `bandit -ll` with no Medium or High finding allowed,
so a new advisory or finding fails CI until it is fixed or listed here.
The same two IDs are ignored by osv-scanner through `dokimi/osv-scanner.toml`,
which OpenSSF Scorecard runs. A removed advisory comes out of this section,
the `pip-audit` step, and that file together.
