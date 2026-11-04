<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# How Intel Python CI tests dokimi

Intel Python CI (gh-ci) builds and tests dokimi's conda package and wheel in GitHub Actions. Intel's Jenkins then tests each package again on Intel machines and scans it. Everything it needs from this repository is in this directory, one workflow, the recipe, and `dokimi/pyproject.toml`. gh-ci only tests and scans: dokimi is published to PyPI by `release.yml`, which this does not replace.

| File | Role |
|---|---|
| `../conda-recipe/` | The conda recipe, `noarch: python` and built from conda-forge alone. `conda_build_config.yaml` lists the Python versions, and `meta.yaml` skips every platform but Linux. Its version, Python floor, console script and run requirements are checked against `[project]` by `dokimi/tests/test_conda_recipe.py`. |
| `../dokimi/pyproject.toml` | What the wheel is built from. Its Python classifiers list the wheels' Python versions. It names no Operating System, so the wheels follow the recipe's platforms. There is no `[tool.cibuildwheel]`: the wheel is pure Python. |
| `../.github/workflows/intel-ci.yml` | One job reads both matrices. One job per Python version builds and uploads `dokimi Linux Python <version>`, and one builds `dokimi Linux Wheels Python <version>`. Then one job per package tests it. |
| `matrix.py` | Reads the platforms and Python versions from the recipe, or with `--system wheel` from `dokimi/pyproject.toml`. A pull request gets the oldest and the newest versions. |
| `build.py` | Builds one Python version: with conda-build, which runs the recipe's own tests, or with `--system wheel` with `pip wheel`. |
| `hooks.py` | The workflow's test jobs: installs one package into a clean environment with the requirements in `config.yml`, and runs `test.py` the way Jenkins does. A conda package's environment comes from conda-forge; a wheel's has only Python from conda-forge and everything else from PyPI, pinned by `dokimi/requirements.lock.txt` except on a release tag. |
| `config.yml` | Tells gh-ci which workflow builds the packages, what its artifacts are called, and what the tests need. |
| `test.py` | Runs dokimi's own test suite, `dokimi/tests`, from the checkout against the installed package, in Actions and again in Jenkins. |

`matrix.py`, `build.py` and `hooks.py` are the shared gh-ci scripts. Here `matrix.py` and `build.py` also find the only `pyproject.toml` one directory down, as dokimi's lives in `dokimi/`, and `build.py` builds a pure Python wheel when `pyproject.toml` has no `[tool.cibuildwheel]`.

**What `test.py` proves.** It prints where `dokimi` was imported from and fails if that is inside the checkout. The suite's `conftest.py` puts the checkout on `sys.path`, so pytest imports `dokimi` first (`-p dokimi`), in every xdist worker too. The suite stays offline, as `dokimi/pyproject.toml` makes it. The suite runs with `dokimi/` as the working directory, so the tests that read `data/` and `docs/` find them there. It also checks that the installed package ships its default data files.

**A pull request into `main`.** Actions builds the oldest and the newest Python versions on Linux, as a conda package and as a wheel, and tests each package. Jenkins notices the pull request within 3 minutes, waits for that whole run, and then tests and scans every package at once. Each cell posts its own status on the pull request, and `intel-ci/pr` sums them up.

**A release tag.** Actions builds every declared version, and Jenkins tests and scans all of them. Publishing stays with `release.yml`: gh-ci releases nothing for dokimi.

To support another Python, add it to `conda-recipe/conda_build_config.yaml` for the conda package and as a classifier in `dokimi/pyproject.toml` for the wheel; nothing else lists versions. Try it locally from a conda base environment with conda-build:

```bash
python ci/matrix.py                           # every platform and Python version
python ci/matrix.py --event pull_request      # what a pull request builds
python ci/build.py --python VERSION --output /tmp/out     # VERSION: one matrix.py lists
python ci/hooks.py --python VERSION --artifacts /tmp/out --work /tmp/hooks
python ci/build.py --system wheel --python VERSION --output /tmp/wheels
python ci/hooks.py --kind wheel --python VERSION --artifacts /tmp/wheels --work /tmp/wheel-hooks
```
