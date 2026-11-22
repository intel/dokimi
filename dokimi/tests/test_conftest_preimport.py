# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A third-party import-time warning fails the session once, under its own name (#1035)."""

import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
STUB_MESSAGE = "stub litellm import-time warning"
STUB = f"""\
import os
import warnings

if os.environ.get("STUB_LITELLM_WARNS") == "1":
    warnings.warn({STUB_MESSAGE!r}, UserWarning)
"""


def _collect(tmp_path: Path, warns: bool) -> subprocess.CompletedProcess:
    """Collect this file in a child pytest with the repo's config and conftest and a stub litellm first on the path."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "litellm.py").write_text(STUB, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(stub_dir), env.get("PYTHONPATH", "")])
    env["STUB_LITELLM_WARNS"] = "1" if warns else "0"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         str(Path(__file__).resolve())],
        cwd=PROJECT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )


def test_a_quiet_litellm_import_leaves_collection_green(tmp_path):
    done = _collect(tmp_path, warns=False)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_litellm_import_warning_fails_the_session_under_its_own_name(tmp_path):
    done = _collect(tmp_path, warns=True)
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert STUB_MESSAGE in out
