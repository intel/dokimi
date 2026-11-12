# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""scripts/refresh_docker_run_golden.py: a re-take of the grid reproduces the committed golden."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parents[1] / "scripts" / "refresh_docker_run_golden.py"
_spec = importlib.util.spec_from_file_location("refresh_docker_run_golden", _SCRIPT)
assert _spec and _spec.loader
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)


def test_a_re_take_reproduces_the_committed_golden(tmp_path):
    out = tmp_path / "run_argv_golden.json"
    assert rd.main(["--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == rd.GOLDEN.read_text(encoding="utf-8")
