# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""formal/repo_batch/run.sh: explicit paths resolve from the caller's directory, and the script needs no GNU-only tools.

A fake ``java`` stands in for TLC: it checks that the jar and the config it is given exist, then prints the line
run.sh reads as a pass. No TLA+ tools, no JVM.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "formal" / "repo_batch" / "run.sh"
FIXED = REPO / "formal" / "repo_batch" / "cfg" / "fixed.cfg"

pytestmark = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="run.sh is a bash script")

FAKE_TLC = f"""#!{sys.executable} -I
import os
import sys

args = sys.argv[1:]
jar = args[args.index("-cp") + 1]
cfg = args[args.index("-config") + 1]
with open(os.environ["FAKE_TLC_LOG"], "a", encoding="utf-8") as log:
    log.write(repr(args) + "\\n")
if os.path.isfile(jar) and os.path.isfile(cfg):
    print("Model checking completed. No error has been found.")
    sys.exit(0)
print("Error: no such file")
sys.exit(1)
"""


def _fake_java(tmp_path: Path) -> Path:
    java = tmp_path / "fake-java"
    java.write_text(FAKE_TLC, encoding="utf-8")
    java.chmod(0o755)
    return java


def test_relative_paths_resolve_from_the_callers_directory(tmp_path):
    # run.sh changes into its own directory to run TLC; a cfg, a jar or an OUT given relative to the caller must
    # still be the caller's files
    (tmp_path / "cfgs").mkdir()
    shutil.copy(FIXED, tmp_path / "cfgs" / "fixed.cfg")
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {**os.environ, "TLA2TOOLS_JAR": "tla2tools.jar", "OUT": "out", "JAVA": str(_fake_java(tmp_path)),
           "FAKE_TLC_LOG": str(tmp_path / "tlc.log")}
    res = subprocess.run(["bash", str(RUN), "cfgs/fixed.cfg"], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "out" / "fixed.log").is_file()
    assert "fixed\tpass\tpass\tyes" in (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8")
    args = (tmp_path / "tlc.log").read_text(encoding="utf-8")
    assert str((tmp_path / "cfgs" / "fixed.cfg").resolve()) in args
    assert str((tmp_path / "tla2tools.jar").resolve()) in args


def test_the_script_runs_with_only_posix_tools_on_path(tmp_path):
    # no bc, no date %N, no grep -P: the timing and the verdict parsing use what every POSIX host has
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("sed", "grep", "head", "tail", "mkdir", "basename", "dirname", "cat", "awk"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {"PATH": str(bindir), "TLA2TOOLS_JAR": str(tmp_path / "tla2tools.jar"), "OUT": str(tmp_path / "out"),
           "JAVA": str(_fake_java(tmp_path)), "FAKE_TLC_LOG": str(tmp_path / "tlc.log")}
    res = subprocess.run([shutil.which("bash"), str(RUN), str(FIXED)], env=env,
                         capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "pass" in (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8")
