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
    ("Model checking completed. No error has been found.", 12, "fixed", "error rc=12", 1),
    ("Usage: java -cp tla2tools.jar tlc2.TLC", 0, "fixed", "error rc=0", 1),
    ("Error: Invariant CancelContract is violated.", 13, "mut_no_cancel_guard", "violation CancelContract", 0),
    ("Error: Invariant First is violated.\nError: Invariant Second is violated.", 12, "mut_no_cancel_guard",
     "violation Second", 1),  # the last violation named wins; this cfg expects CancelContract, so the run fails
], ids=["pass-then-failure", "invariant-then-failure", "temporal-then-failure", "invariant-12", "temporal-13",
        "pass-line-with-status-12", "completion-line-missing-status-0", "invariant-13", "last-named-violation"])
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


FAKE_TLC_BY_CFG = f"""#!{sys.executable} -I
import os
import sys

cfg = sys.argv[sys.argv.index("-config") + 1]
if os.path.basename(cfg).startswith("bad"):
    print("Error: no such file")
    sys.exit(1)
print("Model checking completed. No error has been found.")
sys.exit(0)
"""


def test_a_failing_cfg_fails_the_run_whichever_cfg_comes_after_it(tmp_path):
    # a failing cfg followed by a passing one must still exit 1: the failure flag is not reset per cfg
    java = tmp_path / "fake-java"
    java.write_text(FAKE_TLC_BY_CFG, encoding="utf-8")
    java.chmod(0o755)
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    for name in ("bad", "good"):
        shutil.copy(FIXED, tmp_path / f"{name}.cfg")
    env = {**os.environ, "TLA2TOOLS_JAR": str(tmp_path / "tla2tools.jar"), "OUT": str(tmp_path / "out"),
           "JAVA": str(java)}
    res = subprocess.run(["bash", str(RUN), str(tmp_path / "bad.cfg"), str(tmp_path / "good.cfg")], env=env,
                         capture_output=True, text=True, check=False)
    rows = {r.split("\t")[0]: r.split("\t") for r in
            (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8").splitlines()[1:]}
    assert rows["bad"][3] == "NO" and rows["good"][3] == "yes", res.stdout + res.stderr
    assert res.returncode == 1, res.stdout + res.stderr


def test_two_cfgs_with_one_basename_are_refused_before_any_run(tmp_path):
    # both would write out/<name>.log and share one -metadir: exit 2, name the cfg, run nothing
    for sub in ("before", "after"):
        (tmp_path / sub).mkdir()
        shutil.copy(FIXED, tmp_path / sub / "fixed.cfg")
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {**os.environ, "TLA2TOOLS_JAR": str(tmp_path / "tla2tools.jar"), "OUT": str(tmp_path / "out"),
           "JAVA": str(_fake_java(tmp_path)), "FAKE_TLC_LOG": str(tmp_path / "tlc.log")}
    cfgs = [str(tmp_path / "before" / "fixed.cfg"), str(tmp_path / "after" / "fixed.cfg")]
    res = subprocess.run(["bash", str(RUN), *cfgs], env=env, capture_output=True, text=True, check=False)
    assert res.returncode == 2, res.stdout + res.stderr
    assert "fixed.cfg" in res.stderr
    assert not (tmp_path / "tlc.log").exists()


def test_a_cfg_without_an_expect_line_is_not_ok(tmp_path):
    # no EXPECT line gives no expected verdict: the cfg is not ok, even when TLC passes it
    java = tmp_path / "fake-java"
    java.write_text(FAKE_TLC_STATUS, encoding="utf-8")
    java.chmod(0o755)
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    cfg = tmp_path / "noexpect.cfg"
    cfg.write_text("".join(line for line in FIXED.read_text(encoding="utf-8").splitlines(True)
                           if "EXPECT:" not in line), encoding="utf-8")
    env = {**os.environ, "TLA2TOOLS_JAR": str(tmp_path / "tla2tools.jar"), "OUT": str(tmp_path / "out"),
           "JAVA": str(java), "FAKE_TLC_OUT": "Model checking completed. No error has been found.",
           "FAKE_TLC_RC": "0"}
    res = subprocess.run(["bash", str(RUN), str(cfg)], env=env, capture_output=True, text=True, check=False)
    row = (tmp_path / "out" / "summary.tsv").read_text(encoding="utf-8").splitlines()[1].split("\t")
    assert row[3] == "NO", res.stdout + res.stderr
    assert res.returncode == 1, res.stdout + res.stderr


def test_a_relative_out_is_made_absolute_for_the_tlc_metadir(tmp_path):
    # the fake records its arguments: TLC's -metadir must be under the caller's OUT, not relative to run.sh's directory
    (tmp_path / "tla2tools.jar").write_text("", encoding="utf-8")
    env = {**os.environ, "TLA2TOOLS_JAR": "tla2tools.jar", "OUT": "out", "JAVA": str(_fake_java(tmp_path)),
           "FAKE_TLC_LOG": str(tmp_path / "tlc.log")}
    res = subprocess.run(["bash", str(RUN), str(FIXED)], cwd=tmp_path, env=env, capture_output=True, text=True,
                         check=False)
    assert res.returncode == 0, res.stdout + res.stderr
    args = (tmp_path / "tlc.log").read_text(encoding="utf-8")
    assert str((tmp_path / "out" / "states" / "fixed").resolve()) in args
