# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""scripts/refresh_fidelity_golden.py: where it writes, and that it writes a whole pair."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from dokimi.harnesses import fidelity as fd

_spec = importlib.util.spec_from_file_location(
    "refresh_fidelity_golden", Path(__file__).parents[1] / "scripts" / "refresh_fidelity_golden.py")
assert _spec and _spec.loader
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(rf, "_version", lambda binary: fd.PINNED_CLAUDE_VERSION)


def test_host_capture_needs_out(pinned, monkeypatch):
    monkeypatch.setattr(rf, "capture_arm", lambda *a, **kw: pytest.fail("captured"))
    assert rf.main(["--host", "--binary", "/bin/true"]) == 2


def test_a_failed_second_case_writes_nothing(pinned, monkeypatch, tmp_path):
    def capture(case, **kw):
        if case == "skill":
            raise RuntimeError("client failed")
        return {"model": "<MODEL>", "tools": []}

    monkeypatch.setattr(rf, "capture_arm", capture)
    with pytest.raises(RuntimeError):
        rf.main(["--host", "--binary", "/bin/true", "--out", str(tmp_path)])
    assert list(tmp_path.iterdir()) == []


def test_both_cases_are_written_after_both_pass(pinned, monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "capture_arm", lambda case, **kw: {"model": "<MODEL>", "tools": []})
    assert rf.main(["--host", "--binary", "/bin/true", "--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bare.json", "skill.json"]
