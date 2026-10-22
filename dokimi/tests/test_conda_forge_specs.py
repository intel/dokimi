# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/conda_forge_specs.py: pyproject dependencies as conda-forge match specs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "conda_forge_specs.py"
_spec = importlib.util.spec_from_file_location("conda_forge_specs", _SCRIPT)
assert _spec and _spec.loader
cfs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cfs)


def _pyproject(tmp_path: Path, *deps: str) -> Path:
    p = tmp_path / "pyproject.toml"
    listed = ", ".join(f"'{d}'" for d in deps)
    p.write_text(f"[project]\nname = 'x'\ndependencies = [{listed}]\n", encoding="utf-8")
    return p


def _conda_name(req: Requirement) -> str:
    name = canonicalize_name(req.name)
    return cfs.CONDA_NAMES.get(name, name)


def test_every_runtime_dependency_has_a_spec() -> None:
    """The real pyproject: each unconditional dependency is printed with its range, and nothing else is."""
    import tomllib

    listed = tomllib.loads(cfs.PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    deps = [Requirement(d) for d in listed]
    printed = {r.name: r.specifier for r in map(Requirement, cfs.specs(cfs.PYPROJECT, "3.11"))}
    assert {_conda_name(r): r.specifier for r in deps if r.marker is None}.items() <= printed.items()
    assert set(printed) <= {_conda_name(r) for r in deps}


def test_names_are_canonical_and_mapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PyPI name is lowercased and normalized; CONDA_NAMES renames one conda-forge calls otherwise."""
    monkeypatch.setitem(cfs.CONDA_NAMES, "torch", "pytorch")
    p = _pyproject(tmp_path, "Py_Yaml.Ext>=1", "torch>=2.0", "numpy")
    assert cfs.specs(p, "3.11") == ["py-yaml-ext>=1", "pytorch>=2.0", "numpy"]


def test_marker_is_evaluated_for_the_target_python(tmp_path: Path) -> None:
    """A dependency for older Pythons only is in the 3.11 environment and not in the 3.14 one."""
    p = _pyproject(tmp_path, 'tomli>=2; python_version < "3.12"', 'pywin32; sys_platform == "win32"')
    assert cfs.specs(p, "3.11") == ["tomli>=2"]
    assert cfs.specs(p, "3.14") == []


@pytest.mark.parametrize(("req", "why"), [("datasets[audio]>=2", "no extras"),
                                         ("x @ https://h/x.tar.gz", "URL requirement"),
                                         ("x===1.0", "arbitrary equality")])
def test_requirement_conda_cannot_express_is_an_error(req: str, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        cfs.match_spec(Requirement(req))


def test_main_prints_one_spec_per_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _pyproject(tmp_path, "a>=1", "b<2")
    assert cfs.main(["--python", "3.11", "--pyproject", str(p)]) == 0
    assert capsys.readouterr().out.splitlines() == ["a>=1", "b<2"]


def test_main_fails_on_an_unmappable_requirement(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _pyproject(tmp_path, "a[extra]>=1")
    assert cfs.main(["--python", "3.11", "--pyproject", str(p)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "ERROR: a[extra]>=1" in captured.err
