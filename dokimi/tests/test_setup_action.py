# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""actions/setup/action.yml: the pip cache key must sit where setup-python can hash it.

setup-python hashes ``cache-dependency-path`` with ``@actions/glob.hashFiles``, which only
sees files under GITHUB_WORKSPACE. A key file in ``runner.temp`` hashes to nothing, and
setup-python then fails with no dependency file matched. The check reads the composite's
steps and pins that the key is written under the workspace and that setup-python is given
the same path.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION = Path(__file__).resolve().parents[2] / "actions/setup/action.yml"
KEY_FILE = ".dokimi-setup/pip-key.txt"


def _steps() -> list[dict]:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    return action["runs"]["steps"]


def test_pip_key_is_written_under_the_workspace() -> None:
    writers = [s for s in _steps() if f"$GITHUB_WORKSPACE/{KEY_FILE}" in s.get("run", "")]
    assert writers, "no step writes the pip key under $GITHUB_WORKSPACE"


def test_setup_python_hashes_the_workspace_key_file() -> None:
    setup_python = [s for s in _steps() if str(s.get("uses", "")).startswith("actions/setup-python@")]
    assert len(setup_python) == 1
    path = setup_python[0]["with"]["cache-dependency-path"]
    assert path == f"${{{{ github.workspace }}}}/{KEY_FILE}", path


def test_no_step_keeps_the_key_under_runner_temp() -> None:
    text = ACTION.read_text(encoding="utf-8")
    assert "runner.temp }}/dokimi-setup" not in text
    assert "$RUNNER_TEMP/dokimi-setup" not in text
