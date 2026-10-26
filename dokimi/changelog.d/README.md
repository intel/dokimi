<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Changelog fragments

A PR that changes the `dokimi` package (anything under `dokimi/dokimi/`: code,
bundled registries, schemas, profiles), the data it reads from a checkout
(`dokimi/data/`) or what `pip install` gives a user (the `[project]` and
`[tool.setuptools]` tables of `pyproject.toml`: dependencies, supported Python
versions, entry points, package-data) adds one small file here instead of
editing `CHANGELOG.md`. Every PR editing the same `## [Unreleased]` lines of one
file conflicts with every other open PR; one new file per PR never does. The
directory is managed by [scriv](https://scriv.readthedocs.io/), configured in
`[tool.scriv]` of `pyproject.toml`.

## Adding an entry

From `dokimi/`:

```bash
pip install scriv          # or: pip install -e ".[dev]"
scriv create               # writes changelog.d/<timestamp>_<you>_<branch>.md
```

Uncomment exactly one `### <Category>` section of the new file, replace the
example bullet with your entry, and delete the other sections. Write it for
someone deciding whether to upgrade: a bold lead-in, then what changed, for
whom, and what they have to do, with commands, flags and fields in backticks.
One fragment may hold several bullets; a PR with changes in two categories
adds two fragments (or two sections in one).

| Category | For |
|---|---|
| `Added` | new commands, flags, metrics, harnesses, artifact fields |
| `Changed` | different behavior or defaults, including anything that moves scores for the same inputs |
| `Deprecated` | still works, going away |
| `Removed` | gone |
| `Fixed` | bugs |
| `Security` | vulnerabilities and hardening |
| `Documentation` | docs-only changes worth announcing |

Commit the fragment in the same PR as the change. The `Fragment for package changes` step in `ci.yml`'s `static` job
(`scripts/check_changelog_fragment.py`) fails a PR that touches any of these
without a fragment that has an entry in it (the unedited template, or its
example bullet left under an uncommented category, does not count). For a
change with no user-visible effect (an internal refactor, a test-only or CI-only
change inside the package) a maintainer adds the `skip-changelog` label
instead; adding or removing the label re-runs the check. Changes outside them
(tests, docs, scripts, CI, the `dev` extra and tool settings such as
`[tool.ruff]` in `pyproject.toml`) need no fragment.

## Releasing

The release PR runs, from `dokimi/`:

```bash
scriv collect --version X.Y.Z
```

which renders every fragment into one `## [X.Y.Z] — YYYY-MM-DD` section at the
`scriv-insert-here` marker of `CHANGELOG.md` and deletes the fragments. The
rest of the release is in `docs/releasing.md`.
