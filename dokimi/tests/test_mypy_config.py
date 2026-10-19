# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every module in the package is type checked.

The ``[[tool.mypy.overrides]]`` entries with ``ignore_errors = true`` that exempted the
modules carrying errors when the gate was widened to the whole package are gone: the list
shrank to nothing. These tests keep it that way, and keep the strictness flags on.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

#: Global strictness flags that stay on once the package passes them.
REQUIRED_FLAGS = (
    "check_untyped_defs",
    "extra_checks",
    "no_implicit_optional",
    "strict_equality",
    "warn_redundant_casts",
    "warn_unreachable",
    "warn_unused_ignores",
)


def _mypy_config() -> dict:
    with open(PROJECT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["tool"]["mypy"]


def test_no_module_is_exempt_from_mypy():
    exempt = [
        o["module"] for o in _mypy_config().get("overrides", []) if o.get("ignore_errors")
    ]
    assert not exempt, (
        f"{exempt} exempted from mypy by ignore_errors in pyproject.toml. Every module is "
        "type checked: fix the type errors (run `mypy` from dokimi/) instead."
    )


def test_no_other_way_around_the_gate():
    """The test above sees only ``ignore_errors`` overrides. A flag switched off for one module,
    an ``exclude``, skipped imports or a ``# mypy:`` comment in the file exempt a module as
    surely, so they fail here too."""
    config = _mypy_config()
    loosened = [
        f"{o['module']}: {key}"
        for o in config.get("overrides", [])
        for key in (*REQUIRED_FLAGS, "follow_imports")
        if o.get(key) is False or o.get(key) in ("skip", "silent")
    ]
    loosened += [key for key in ("exclude", "ignore_errors") if config.get(key)]
    loosened += [
        str(path.relative_to(PROJECT))
        for path in (PROJECT / "dokimi").rglob("*.py")
        if "# mypy:" in path.read_text(encoding="utf-8")
    ]
    assert not loosened, f"{loosened} exempt code from mypy; fix its types"


def test_strictness_flags_stay_on():
    config = _mypy_config()
    off = [flag for flag in REQUIRED_FLAGS if config.get(flag) is not True]
    assert not off, f"{off} must stay true in [tool.mypy]; fix the new errors instead"
