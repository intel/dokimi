#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Print the runtime dependencies of pyproject.toml as conda-forge match specs.

One spec per line, for `micromamba create -c conda-forge`: the conda-forge
workflow solves them so a PR that adds or narrows a dependency learns that
conda-forge cannot provide it before a release, not on the feedstock's
version-bump PR after it.

    python scripts/conda_forge_specs.py --python 3.11

A requirement whose environment marker is false on that Python (on Linux, the
platform the noarch package is built on) is left out. Optional extras are not
part of the conda package and are not printed.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# PyPI name (canonical) -> conda-forge name, where they differ (pytorch for
# torch, for example). Every other name is the canonical PyPI name.
CONDA_NAMES: dict[str, str] = {}


def match_spec(req: Requirement) -> str:
    """The conda match spec of one requirement: its conda-forge name and version range."""
    if req.extras:
        raise ValueError(f"{req}: a conda package has no extras; depend on what the extra pulls in")
    if req.url:
        raise ValueError(f"{req}: a URL requirement has no conda-forge equivalent")
    if any(s.operator == "===" for s in req.specifier):
        raise ValueError(f"{req}: conda has no arbitrary equality (===)")
    name = canonicalize_name(req.name)
    return CONDA_NAMES.get(name, name) + str(req.specifier)


def specs(pyproject: Path, python: str) -> list[str]:
    """Match specs of the `[project].dependencies` that apply on `python`."""
    deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    env = {"python_version": python, "python_full_version": f"{python}.0",
           "sys_platform": "linux", "platform_system": "Linux", "os_name": "posix"}
    reqs = [Requirement(d) for d in deps]
    return [match_spec(r) for r in reqs if r.marker is None or r.marker.evaluate(env)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", required=True, help="X.Y the environment markers are evaluated for")
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT)
    args = parser.parse_args(argv)
    try:
        lines = specs(args.pyproject, args.python)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
