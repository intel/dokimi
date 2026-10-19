<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Versioning and releases

How dokimi is versioned, how a change reaches the changelog, and how a release
is cut. The first release is 0.1.0.

## Version numbers

The version lives in one place: `project.version` in `pyproject.toml`.
`dokimi --version`, `dokimi.__version__` and the `dokimi_version` field of the
`dokimi run` manifest and of the subject scorecard `run_manifest` all read it. A source checkout reads `pyproject.toml` itself, an
installed wheel its package metadata.

dokimi follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before
1.0 the minor number is the breaking one:

| Bump | When |
|---|---|
| minor, `0.1.0` → `0.2.0` | anything a user may have to react to: a command, flag or output field removed or renamed; a changed default; a metric, judge prompt or scoring change that moves scores for the same inputs; a dropped Python version; new features |
| patch, `0.1.0` → `0.1.1` | fixes that change no interface and no score for inputs that already worked; docs; packaging |

A scoring change is a minor bump even when no interface changes: results from
two versions are comparable only when the scoring is the same, and the version
in the `dokimi run` manifest or the scorecard is how a reader tells.

Artifact formats are versioned separately, by the `schema_version` of each file
under `dokimi/schemas/` and the `contracts` workflow; a package release does not
replace that.

Between releases `main` keeps the version of the last release; the release PR
bumps it.

## Changelog

`CHANGELOG.md` is generated: each PR that changes the package adds a fragment to
`changelog.d/`, and the release PR collects them. See
[changelog.d/README.md](../changelog.d/README.md) for how to write one and when
the `changelog` workflow asks for it.

## Cutting a release

1. **Release PR** (from a fork, like any PR). From `dokimi/`:

   ```bash
   # bump project.version in pyproject.toml to X.Y.Z, then:
   scriv collect --version X.Y.Z          # fragments -> CHANGELOG.md section
   python scripts/check_release.py vX.Y.Z  # what the release workflow checks
   ```

   Read the collected section as a user would and tidy it. Merge the PR.

2. **Release.** A maintainer creates the GitHub Release, and with it the tag,
   on the merge commit:

   ```bash
   python scripts/check_release.py vX.Y.Z --notes > notes.md
   gh release create vX.Y.Z -R intel/dokimi --target <merge-commit-sha> \
     --title "dokimi X.Y.Z" --notes-file notes.md
   ```

   Publishing the release runs `.github/workflows/release.yml`. It checks the tag
   against the version, the changelog section and leftover fragments, builds the
   sdist and wheel (the wheel from the sdist), smoke-tests the installed wheel
   from outside the checkout (`scripts/smoke_installed.py`), attaches both files
   to the release and, once enabled, uploads them to PyPI. If it fails, fix on
   `main` and release the next patch version: a published version is never
   rebuilt.

`workflow_dispatch` runs the same build and smoke test without uploading
anything. Given a tag, it checks out that tag (and fails if it does not exist)
and also runs the readiness check on it; without one it builds the ref it was
dispatched from.

## Enabling PyPI

The `pypi` job of the release workflow is off until a maintainer, once:

1. On PyPI, adds a *pending trusted publisher* for project `dokimi`: owner
   `intel`, repository `dokimi`, workflow `release.yml`, environment `pypi`.
   Trusted Publishing authenticates the upload with the workflow's OIDC token,
   so no API token is created or stored. A pending publisher does not reserve
   the name: the first upload does.
2. In `intel/dokimi` settings, creates the environment `pypi`, limited to tags
   `v*`, optionally with required reviewers so each upload waits for approval.
3. Sets the repository variable `PYPI_PUBLISH` to `true`.

The next published release then goes to PyPI too.

## conda-forge

conda-forge packages are built by a feedstock repository under
`conda-forge/`, not from this repository. After the first PyPI release:

1. Generate a recipe from the PyPI sdist (`grayskull pypi dokimi`), make it
   `noarch: python` with `python {{ python_min }}.*` in host and test and
   `python >={{ python_min }}` in run, where `python_min` is `3.11` unless the
   conda-forge global value is already at least that (see conda-forge's
   [noarch guide](https://conda-forge.org/docs/how-to/basics/noarch/)), test with
   `dokimi --version` and `dokimi --help`, add the maintainers, and open a PR to
   [conda-forge/staged-recipes](https://github.com/conda-forge/staged-recipes).
   The sdist is the source because it is the project directory alone (this
   repository's `dokimi/`), with `LICENSE` at its root for `license_file`.
2. Once merged, conda-forge creates `conda-forge/dokimi-feedstock`. From then on
   its bot opens a version-bump PR for every new PyPI release; a maintainer
   checks the dependency changes in it and merges.

Every runtime dependency in `pyproject.toml` is on conda-forge (checked for
0.1.0), including versions inside the current upper bounds. An optional extra
(`ocr`, `mcp`, `embeddings`, `typed-scorer`) is not part of the conda package.
