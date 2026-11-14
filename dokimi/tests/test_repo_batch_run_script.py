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

# prints a verdict line, then exits with the status in FAKE_TLC_RC
FAKE_TLC_STATUS = f"""#!{sys.executable} -I
import os
import sys

print(os.environ["FAKE_TLC_OUT"])
sys.exit(int(os.environ["FAKE_TLC_RC"]))
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


def test_a_relative_java_path_resolves_from_the_callers_directory(tmp_path):
    # JAVA given relative to the caller is run after run.sh changes into its own directory: it must still be the
    # caller's executable, as the jar, the cfg and OUT are
    (tmp_path / "tools").mkdir()
    java = _fake_java(tmp_path)
    java.rename(tmp_path / "tools" / "java")
    (tmp_path / "cfgs").mkdir()
    shutil.copy(FIXED, tmp_path / "cfgs" / "fixed.cfg")
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {**os.environ, "TLA2TOOLS_JAR": "tla2tools.jar", "OUT": "out", "JAVA": "tools/java",
           "FAKE_TLC_LOG": str(tmp_path / "tlc.log")}
    res = subprocess.run(["bash", str(RUN), "cfgs/fixed.cfg"], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "fixed\tpass\tpass\tyes" in (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8")


def test_a_bare_java_name_is_still_looked_up_on_path(tmp_path):
    # a command name without a slash is not a path: it stays a PATH lookup, not a file in the caller's directory
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake_java(tmp_path).rename(bindir / "fake-java-bare")
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "TLA2TOOLS_JAR": "tla2tools.jar",
           "OUT": "out", "JAVA": "fake-java-bare", "FAKE_TLC_LOG": str(tmp_path / "tlc.log")}
    res = subprocess.run(["bash", str(RUN), str(FIXED)], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "pass" in (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8")


@pytest.mark.parametrize("out, rc, cfg, got, exit_code", [
    ("Model checking completed. No error has been found.", 1, "fixed", "error rc=1", 1),
    ("Error: Invariant CancelContract is violated.", 1, "mut_no_cancel_guard", "error rc=1", 1),
    ("Error: Temporal property InvocationEnds was violated.", 1, "no_fairness", "error rc=1", 1),
    ("Error: Invariant CancelContract is violated.", 12, "mut_no_cancel_guard", "violation CancelContract", 0),
    ("Error: Temporal property InvocationEnds was violated.", 13, "no_fairness", "violation InvocationEnds", 0),
], ids=["pass-then-failure", "invariant-then-failure", "temporal-then-failure", "invariant-12", "temporal-13"])
def test_a_verdict_in_the_log_counts_only_with_the_status_tlc_gives_it(tmp_path, out, rc, cfg, got, exit_code):
    java = tmp_path / "fake-java"
    java.write_text(FAKE_TLC_STATUS, encoding="utf-8")
    java.chmod(0o755)
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {**os.environ, "TLA2TOOLS_JAR": str(tmp_path / "tla2tools.jar"), "OUT": str(tmp_path / "out"),
           "JAVA": str(java), "FAKE_TLC_OUT": out, "FAKE_TLC_RC": str(rc)}
    res = subprocess.run(["bash", str(RUN), str(FIXED.with_name(f"{cfg}.cfg"))], env=env,
                         capture_output=True, text=True, check=False)
    row = (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert row[2] == got, res.stdout + res.stderr
    assert res.returncode == exit_code, res.stdout + res.stderr
