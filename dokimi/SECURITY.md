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

- **filelock 3.32.7 — thirteen advisories** (fixed in 4.2.1, GHSA-j8f7-rjxc-mr56
  in 4.0.9). Every litellm release requires `filelock<4.0`, so neither fix can be
  locked. datasets, huggingface-hub and litellm (main lock) and cachecontrol,
  python-discovery and virtualenv (tools lock) take `FileLock`, which is
  `UnixFileLock` on Linux and macOS and `WindowsFileLock` on Windows; on Unix it
  falls back to `SoftFileLock` on a filesystem without `flock`, and
  huggingface-hub does the same itself. Their lock directories are the Hugging
  Face cache (`~/.cache/huggingface` unless `HF_HOME` moves it) and the tools'
  user cache and data directories. Drop all thirteen when litellm allows
  `filelock>=4.2.1`.
  - In the SQLite-backed `ReadWriteLock`, which no dependency of Dokimi uses:
    GHSA-v4x8-3wr5-3wq8 and GHSA-9vhv-jxww-xfm5 (CVE-2026-112940,
    CVE-2026-112913; POSIX and Windows), GHSA-j8f7-rjxc-mr56 (CVE-2026-112936;
    it follows a symlink at the lock path into SQLite) and GHSA-cwqr-hh33-gh44
    (CVE-2026-112915; a crafted lock database stalls every acquire).
  - In `StrictSoftFileLock` and `SoftReadWriteLock`, which no dependency of
    Dokimi uses: GHSA-9c4x-gm8v-wgc4 (CVE-2026-112911; they adopt a protocol
    directory another account created), GHSA-6627-m3xw-93mp (CVE-2026-112908;
    claim directories created without the lock's mode) and GHSA-m462-7rxw-wvpg
    (CVE-2026-112938; the strict lock follows a symlink swapped in at
    `<path>.filelock`).
  - In `UnixFileLock`, GHSA-69hj-f68j-2879 (CVE-2026-112909): another local
    account that can rename or hard-link a file of the lock user onto a lock path
    not yet created makes the next acquire truncate that file and, with an
    explicit `mode` (huggingface-hub passes `0o664` and `0o666`), chmod it. It
    applies where another account can rename files in the lock directory, such
    as a Hugging Face cache several accounts share.
  - In `WindowsFileLock`, GHSA-8mx5-7m5m-r77g (CVE-2026-112910): it ignores
    `mode`, so the lock file takes the directory's inherited ACL. Where that ACL
    lets another account read new files (a cache outside the user profile on a
    shared Windows host), that account can hold the lock and stall a holder.
  - In the soft locks. GHSA-567q-5qvw-pf92 (CVE-2026-112907) is in the soft
    locks on Windows: another local account that can create files in the lock
    directory can stall or break a lock holder. huggingface-hub takes a
    `SoftFileLock` for its download cache, so it applies to dataset and model
    downloads on a shared Windows host. Where `SoftFileLock` stands in for a
    missing `flock`, three more apply: GHSA-9j6q-wvj5-jgjg (CVE-2026-112912,
    low) tells another account that can write the lock directory whether a path
    exists and is writable for the lock user; GHSA-rfpc-qcw5-3pc4
    (CVE-2026-112939) lets an account that can write a holder's marker but not
    remove it forge the holder's PID and admit a second holder
    (huggingface-hub's shared-blob fallback marker is `0o666`); and
    GHSA-fq56-3cx3-fjxp (CVE-2026-112935) admits two holders when an account
    that can create files in the lock directory gets a stale-lock break to meet
    a reused inode (ext4, xfs).

The locks do not contain ragas: it is the optional `[ragas]` extra (`eval ragas`,
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

`.github/workflows/pip-audit.yml` runs `pip-audit` against the locks with exactly
the thirteen filelock IDs ignored, on every PR or push that changes a lock or requirements file and daily
on `main`; a daily run that finds a new advisory opens an issue rather than
failing unrelated PRs. The `static` job in `.github/workflows/ci.yml` runs
`bandit -ll` with no Medium or High finding allowed.
The same IDs are ignored by osv-scanner through `dokimi/osv-scanner.toml`,
which OpenSSF Scorecard runs. A removed advisory comes out of the first list
above, the `pip-audit` step, and that file together.
