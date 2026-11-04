# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Intel Python CI (ci/ and .github/workflows/intel-ci.yml) keeps what Jenkins relies on."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

from dokimi import config, intents, registry
from dokimi.runner import spec

_REPO = Path(__file__).resolve().parents[2]
_WORKFLOWS = _REPO / ".github" / "workflows"
pytestmark = pytest.mark.skipif(not (_REPO / "ci" / "build.py").is_file(),
                                reason="not a checkout: ci/ is next to the project")


def _workflow(name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS / name).read_text(encoding="utf-8"))


def test_every_push_and_tag_run_keeps_its_own_concurrency_group() -> None:
    """Jenkins tests every main push: a shared group would drop a queued one, so group as ci.yml does."""
    assert _workflow("intel-ci.yml")["concurrency"] == _workflow("ci.yml")["concurrency"]


def _script(name: str, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A ci/ script as a module; build.py imports matrix.py from next to it."""
    monkeypatch.syspath_prepend(str(_REPO / "ci"))
    spec = importlib.util.spec_from_file_location(f"gh_ci_{name}", _REPO / "ci" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_lists_only_the_packages_it_built(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A package an earlier build left in --output is neither reported nor rewritten as this build's."""
    build = _script("build", monkeypatch)
    out, scratch = tmp_path / "out", tmp_path / "scratch" / "wheel"
    out.mkdir()
    scratch.mkdir(parents=True)
    (out / "dokimi-0.1.0-py3-none-any.whl").write_bytes(b"old")
    (scratch / "dokimi-0.2.0-py3-none-any.whl").write_bytes(b"new")
    assert build.collect(scratch.parent, out) == [out / "dokimi-0.2.0-py3-none-any.whl"]


def test_the_data_check_covers_the_files_of_the_test_it_replaces(monkeypatch: pytest.MonkeyPatch) -> None:
    """ci/test.py deselects test_default_data_files_ship_in_the_wheel: it checks the same files."""
    shown = subprocess.run([sys.executable, "-c", _script("test", monkeypatch).CHECK_DATA],
                           cwd=_REPO / "dokimi", capture_output=True, text=True, check=True)
    files = (registry._DEFAULT_REGISTRY, intents._DEFAULT_INTENTS, config.DEFAULT_PRODUCTS_PATH,
             spec.DEFAULT_SCHEMA_PATH)
    assert {Path(p).resolve() for p in shown.stdout.splitlines()} == {p.resolve() for p in files}


def test_the_matrix_holds_only_linux_the_workflow_runs_on(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """intel-ci.yml's jobs run on Linux runners, so a recipe for every platform gets Linux cells only."""
    matrix = _script("matrix", monkeypatch)
    (tmp_path / "meta.yaml").write_text("package:\n  name: dokimi\n", encoding="utf-8")
    (tmp_path / "conda_build_config.yaml").write_text("python:\n  - 3.12\n", encoding="utf-8")
    cells = matrix.build_matrix("conda-build", tmp_path / "meta.yaml", "")["include"]
    assert [cell["platform"] for cell in cells] == ["linux"]


@pytest.mark.parametrize(("line", "platforms"), [
    ("skip: true  # [win]", ["linux", "macos"]),
    ("skip: true  # [win]  # windows not supported", ["linux", "macos"]),
    ("skip: True  # [not linux]", ["linux"]),
    ("skip: true  # [py<311]", ["linux", "windows", "macos"]),
])
def test_a_skip_selector_rules_out_its_platform_as_conda_build_reads_it(
        line: str, platforms: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A comment after the selector keeps it a selector, as in conda-build's sel_pat."""
    matrix = _script("matrix", monkeypatch)
    recipe = tmp_path / "meta.yaml"
    recipe.write_text(f"package:\n  name: dokimi\nbuild:\n  {line}\n", encoding="utf-8")
    assert matrix.from_conda_recipe(recipe) == platforms


def _pyproject(tmp_path: Path, classifiers: list[str], requires: str = "") -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f"[project]\nname = 'dokimi'\nrequires-python = {requires!r}\n"
                    f"classifiers = {classifiers!r}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(("requires", "minors", "excluded"), [
    (">=3.11", [11, 14], ""),
    (">3.10", [10], ""),
    (">=3.10, <3.15", [10, 14], ""),
    (">=3.11", [10, 11], "3.10"),
    ("<3.13", [12, 13], "3.13"),
    ("<=3.13", [13], "3.13"),
    (">=3.10,!=3.12.*", [10, 12, 13], "3.12"),
    ("~=3.12", [11, 12], "3.11"),
])
def test_every_classified_python_is_one_requires_python_admits(
        requires: str, minors: list[int], excluded: str, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A classifier is tested on its newest patch release: >3.10 admits 3.10.1, <3.13 no 3.13."""
    matrix = _script("matrix", monkeypatch)
    path = _pyproject(tmp_path, [f"Programming Language :: Python :: 3.{m}" for m in minors],
                      requires)
    if not excluded:
        assert [python.version for python in matrix.from_pyproject(path)] == [
            f"3.{m}" for m in minors]
        return
    with pytest.raises(matrix.DeclarationError, match=f"classifies {excluded}, which"):
        matrix.from_pyproject(path)


@pytest.mark.parametrize(("classifiers", "platforms"), [
    (["Operating System :: POSIX"], ["linux"]),
    (["Operating System :: POSIX :: Linux"], ["linux"]),
    (["Operating System :: POSIX :: BSD :: FreeBSD", "Operating System :: POSIX :: Linux"],
     ["linux"]),
    (["Operating System :: Microsoft :: Windows :: Windows 11"], ["windows"]),
    (["Operating System :: POSIX :: BSD :: FreeBSD"], None),
])
def test_a_posix_sub_classifier_names_its_own_system_not_linux(
        classifiers: list[str], platforms: list[str] | None, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Only bare POSIX, POSIX :: Linux or Unix mean Linux; a FreeBSD-only project has no cells."""
    matrix = _script("matrix", monkeypatch)
    path = _pyproject(tmp_path, classifiers)
    if platforms is not None:
        assert matrix.from_os_classifiers(path) == platforms
        return
    with pytest.raises(matrix.DeclarationError, match="names no Operating System"):
        matrix.from_os_classifiers(path)

