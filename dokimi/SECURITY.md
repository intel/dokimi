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
`requirements-docs.lock.txt`, `requirements-ci.lock.txt`,
`requirements-mutation.lock.txt` and `requirements-fuzz.lock.txt`) reports no
advisory.

The locks pin filelock 4.2.4 although every litellm release up to 1.106.6
declares `filelock<4.0`. filelock 3.32.7, the newest version under that cap, has
thirteen advisories, all fixed in 4.2.1. litellm lifted the cap on its main
branch (`filelock<5`, BerriAI/litellm#49137) but has not released it yet, and its
own code calls only `FileLock(path, timeout=..., mode=...)`, which 4.x keeps. So
both lock headers pass `--override=requirements-overrides.txt`
(`filelock>=4.2.1`), and every install that includes `requirements.lock.txt`
passes `--no-deps`: the locks are the full closure, and pip's resolver would
otherwise refuse litellm's declared cap (`pip check` reports that one mismatch).
`pip install dokimi` outside the locks still resolves a filelock below 4 until
litellm releases the change. Drop the override file and those `--no-deps` flags
once a litellm release allows filelock 4.

The locks do not contain ragas: it is the optional `[ragas]` extra (`eval score ragas`,
`answers generate --ragas`), which installs ragas 0.3.9 and diskcache 5.6.3. Their
advisories do not affect Dokimi:

- **diskcache 5.6.3 — PYSEC-2026-2447** (CVE-2025-69872, GHSA-w8v5-vhqr-4h9v;
  pickle deserialization from a cache directory). Pulled in only by ragas,
  which imports it inside its opt-in `DiskCacheBackend`; Dokimi never
  constructs one, so no diskcache directory is created or read.
- **ragas 0.3.9 — PYSEC-2026-3046** (CVE-2026-6587, GHSA-95ww-475f-pr4f; SSRF
  and file read in the multi-modal faithfulness metric's loader). The
  advisory's range is 0.2.3–0.4.3, but the vulnerable module
  (`ragas/metrics/collections/multi_modal_faithfulness/util.py`) first ships
  in 0.4.2, so 0.3.9 does not contain it. A fixed release would be
  0.4.4 or later, which is outside the `ragas<0.4` cap: taking it means lifting
  the cap and migrating, not a bump, and 0.4.2–0.4.3 must be skipped.

The `ragas>=0.3.0` floor keeps out 0.2.3–0.2.x, which are affected by
CVE-2025-45691 (GHSA-v2xr-wvrv-p969, file read through `retrieved_contexts`,
fixed in 0.3.0).

`.github/workflows/pip-audit.yml` runs `pip-audit` against the locks with no
advisory ignored, on every PR or push that changes a lock or requirements file and
daily on `main`; a daily run that finds a new advisory opens an issue rather than
failing unrelated PRs. The `static` job in `.github/workflows/ci.yml` runs
`bandit -ll` with no Medium or High finding allowed. An advisory Dokimi has to
accept is explained in this section and ignored in three places together: the
`pip-audit` step, `dokimi/osv-scanner.toml` (which OpenSSF Scorecard runs) and
`allow-ghsas` in `.github/workflows/dependency-review.yml`, which fails a PR that
adds or moves a dependency to a version with a High or Critical advisory;
`tests/test_advisory_lists.py` keeps the three in step.
