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
