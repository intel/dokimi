# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The conda recipe Intel Python CI builds (conda-recipe/meta.yaml) matches pyproject.toml."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

_PROJECT = Path(__file__).resolve().parents[1]
_RECIPE = _PROJECT.parent / "conda-recipe" / "meta.yaml"
pytestmark = pytest.mark.skipif(not _RECIPE.is_file(), reason="not a checkout: the recipe is next to the project")

_SCRIPT = _PROJECT / "scripts" / "conda_forge_specs.py"
_spec = importlib.util.spec_from_file_location("conda_forge_specs", _SCRIPT)
assert _spec and _spec.loader
cfs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfs)


def _canonical(spec: str) -> tuple[str, frozenset[str]]:
    """A match spec as its name and set of version clauses, whatever their order or spacing."""
    req = Requirement(spec)
    return req.name, frozenset(map(str, req.specifier))


def _recipe() -> dict:
    return yaml.safe_load(_RECIPE.read_text(encoding="utf-8"))


def _project() -> dict:
    return tomllib.loads(cfs.PYPROJECT.read_text(encoding="utf-8"))["project"]


def test_recipe_run_requirements_are_the_conda_forge_specs() -> None:
    """A dependency added or narrowed in pyproject.toml fails here until the recipe follows."""
    listed = [r for r in _recipe()["requirements"]["run"] if not r.startswith("python ")]
    assert {_canonical(r) for r in listed} == {_canonical(s) for s in cfs.specs(cfs.PYPROJECT, "3.11")}
    assert len({_canonical(r) for r in listed}) == len(listed)


def test_recipe_version_python_floor_and_script_follow_pyproject() -> None:
    """A release bump or a new floor in pyproject.toml fails here until the recipe follows."""
    recipe, project = _recipe(), _project()
    assert str(recipe["package"]["version"]) == project["version"]
    assert f"python {project['requires-python']}" in recipe["requirements"]["run"]
    entries = dict(e.split(" = ") for e in recipe["build"]["entry_points"])
    assert entries == project["scripts"]
