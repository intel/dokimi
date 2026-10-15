# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The mypy exemption list in ``pyproject.toml`` only shrinks.

Modules under a ``[[tool.mypy.overrides]]`` entry with ``ignore_errors = true`` are not
type checked at all. The list holds the modules that carried errors when the gate was
widened to the whole package; a module added to it would leave the gate without a
reviewer noticing, so this test freezes the list and fails on any entry outside it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]

#: The modules still exempt. Never add to this: fix the module's types instead. When a
#: module's types are fixed, delete it here and from ``pyproject.toml`` in the same change.
EXEMPT_BASELINE = frozenset({
    "dokimi.commands.catalog",
    "dokimi.personas.analyzer",
})

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


def _exempt_entries() -> list[str]:
    """Every module named by an override that sets ``ignore_errors``, in file order."""
    entries: list[str] = []
    for override in _mypy_config().get("overrides", []):
        if override.get("ignore_errors"):
            module = override["module"]
            entries.extend([module] if isinstance(module, str) else module)
    return entries


def test_no_module_is_added_to_the_mypy_exemptions():
    added = sorted(set(_exempt_entries()) - EXEMPT_BASELINE)
    assert not added, (
        f"{added} added to the mypy ignore_errors list in pyproject.toml. The list only "
        "shrinks: fix the type errors (run `mypy` from dokimi/) instead of exempting the module."
    )


def test_a_fixed_module_leaves_the_baseline_too():
    # Otherwise the entry could be added back to pyproject.toml without failing the test above.
    fixed = sorted(EXEMPT_BASELINE - set(_exempt_entries()))
    assert not fixed, (
        f"{fixed} no longer exempt in pyproject.toml; delete them from EXEMPT_BASELINE in "
        "this test as well, so the exemption cannot come back."
    )


def test_exemptions_are_sorted_and_unique():
    entries = _exempt_entries()
    assert entries == sorted(set(entries)), "keep the mypy ignore_errors list sorted, no duplicates"


def test_every_exemption_names_an_existing_module():
    def exists(module: str) -> bool:
        path = PROJECT.joinpath(*module.split("."))
        return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()

    stale = [m for m in _exempt_entries() if not exists(m)]
    assert not stale, f"{stale} in the mypy ignore_errors list name no module; remove them"


def test_strictness_flags_stay_on():
    config = _mypy_config()
    off = [flag for flag in REQUIRED_FLAGS if config.get(flag) is not True]
    assert not off, f"{off} must stay true in [tool.mypy]; fix the new errors instead"
