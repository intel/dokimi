# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1293: a plain directory without search permission is refused by name, not with a PermissionError traceback."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from dokimi.repos import RepoSource, open_source
from dokimi.repos.source_errors import SourceArgumentError

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permission bits and a non-root user")

MANIFEST = """schema: repo_manifest.v1
id: toy
source: {url: "https://example.invalid/toy"}
eval_dir: ev
guidance: {globs: ["**/AGENTS.md"]}
image: {dockerfile: ev/Dockerfile, network: none}
entry_points: {grade: "python3 {eval_dir}/grade.py grade {run_dir}", oracle: "python3 {eval_dir}/grade.py oracle {run_dir} {source_dir}"}
tasks: ev/tasks
"""


def _plain_dir(tmp_path: Path) -> Path:
    d = tmp_path / "d"
    (d / "tasks" / "a").mkdir(parents=True)
    (d / "tasks" / "a" / "task.json").write_text('{"id":"a","prompt":"p","base":"HEAD"}', encoding="utf-8")
    return d


def _check(d: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    d.chmod(0o444)
    try:
        return subprocess.run([sys.executable, str(ROOT / "cli.py"), "eval", "check", str(d), "--schema-only", *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    finally:
        d.chmod(0o755)


def test_check_unreadable_plain_dir_gives_message(tmp_path: Path) -> None:
    """#1293 scenario 1: no manifest lookup traceback; a message naming the directory and exit 2."""
    d = _plain_dir(tmp_path)
    run = _check(d)
    assert "Traceback" not in run.stderr, run.stderr
    assert run.returncode == 2, run.stderr
    assert repr(str(d)) in run.stderr and "cannot be searched" in run.stderr


def test_check_manifest_file_unreadable_plain_dir_is_refused(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    run = _check(_plain_dir(tmp_path), "--manifest-file", str(manifest))
    assert "Traceback" not in run.stderr, run.stderr
    assert run.returncode == 2, run.stderr
    assert "cannot be searched" in run.stderr


def test_open_source_refuses_unsearchable_dir_and_reads_a_searchable_one(tmp_path: Path) -> None:
    """#1293: open_source raises SourceArgumentError for a mode-444 directory and still opens a mode-755 one."""
    d = _plain_dir(tmp_path)
    assert open_source(RepoSource.parse(str(d))).is_dir("tasks")
    d.chmod(0o444)
    try:
        with pytest.raises(SourceArgumentError, match="cannot be searched"):
            open_source(RepoSource.parse(str(d)))
    finally:
        d.chmod(0o755)
