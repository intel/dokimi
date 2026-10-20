#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Smoke-test an installed dokimi: the wheel, not the source tree it was built from.

Run it from a directory outside the checkout, with the Python the wheel went
into, so ``import dokimi`` cannot pick up the source tree:

    cd "$RUNNER_TEMP" && python /path/to/dokimi/scripts/smoke_installed.py [--expect-version X.Y.Z]

Checks the version, the console and module entry points, a command on a
cp1252 stdout, and every data file the package reads by default (the files a
wheel silently drops if package-data misses them). The CI package-smoke and
windows-smoke jobs and the release workflow run it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path


def _check(ok: bool, what: str) -> None:
    print(f"{'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect-version", help="fail unless the installed version is this")
    args = parser.parse_args(argv)

    import dokimi
    from dokimi.artifacts import SCHEMA_VERSIONS, _schema_path
    from dokimi.config import DEFAULT_PRODUCTS_PATH, load_products_config
    from dokimi.intents import IntentRegistry
    from dokimi.registry import ProductRegistry
    from dokimi.repos.harness_profile import builtin_profiles, load_profile
    from dokimi.runner.spec import DEFAULT_SCHEMA_PATH

    where = Path(dokimi.__file__).resolve().parent
    _check(not (where.parent / "pyproject.toml").exists(), f"imported the installed package ({where})")
    installed = metadata.version("dokimi")
    _check(dokimi.__version__ == installed, f"dokimi.__version__ == {installed}")
    if args.expect_version:
        _check(installed == args.expect_version, f"version is {args.expect_version}")

    exe = shutil.which("dokimi")
    _check(exe is not None, "`dokimi` console script on PATH")
    out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, check=True).stdout
    _check(out.strip() == f"dokimi {installed}", f"`dokimi --version` prints {out.strip()!r}")
    subprocess.run([sys.executable, "-m", "dokimi", "--help"], capture_output=True, check=True)
    _check(True, "`python -m dokimi --help`")
    # A Windows console is cp1252, and the rule `library list` prints is not: the
    # command crashes there unless the CLI tolerates the console encoding.
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    listed = subprocess.run([str(exe), "library", "list"], capture_output=True, env=env, check=False)
    _check(listed.returncode == 0, "`dokimi library list` on a cp1252 stdout")

    products = ProductRegistry()
    _check(bool(products.keys()), f"bundled products.yaml: {len(products.keys())} products")
    intents = IntentRegistry()
    _check(bool(intents.keys()), f"bundled intents.yaml: {len(intents.keys())} domains")
    _check(not intents.validate(products), "intents reference only registered products")
    load_products_config()
    _check(DEFAULT_PRODUCTS_PATH.is_file(), "bundled config/products.yaml")
    json.loads(DEFAULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    _check(True, "bundled runner/spec.schema.json")
    for kind in SCHEMA_VERSIONS:
        json.loads(_schema_path(kind).read_text(encoding="utf-8"))
    _check(True, f"{len(SCHEMA_VERSIONS)} artifact schemas")
    for name in builtin_profiles():
        load_profile(name)
    _check(bool(builtin_profiles()), f"harness profiles: {', '.join(builtin_profiles())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
