# Hidden test for the fix-docker-reward task: a verifier's reward.json is the verdict,
# and a reward of 1e-1 is 0.1, not 1.0.
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

import dokimi.harnesses.docker_solver as mod
from dokimi.harnesses import load_task
from dokimi.harnesses.command import CommandHarness
from dokimi.harnesses.docker_solver import DockerSolverHarness


def _harbor(root: Path, name: str = "hb-task") -> Path:
    task = root / name
    for sub in ("environment", "solution", "tests"):
        (task / sub).mkdir(parents=True)
    (task / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (task / "instruction.md").write_text("Fix it.", encoding="utf-8")
    (task / "task.toml").write_text('[environment]\nworkdir = "/app"\n', encoding="utf-8")
    return task


class _Container:
    """A fake task container: the verifier (``test.sh``) writes *files* under
    /logs/verifier and exits *rc*; ``docker cp`` / ``docker exec cat`` read them."""

    def __init__(self, files: dict[str, str], rc: int = 0):
        self.pending = {f"/logs/verifier/{k}": v for k, v in files.items()}
        self.fs: dict[str, str] = {}
        self.rc = rc

    def _cp_out(self, src: str, dest: Path) -> int:
        src = src.rstrip("/")
        contents = src.endswith("/.")
        src = src[:-2] if contents else src
        if src in self.fs:
            target = dest / Path(src).name if dest.is_dir() else dest
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(self.fs[src], encoding="utf-8")
            return 0
        under = {p: t for p, t in self.fs.items() if p.startswith(src + "/")}
        if not under and not any(p.startswith(src + "/") for p in self.pending):
            return 1
        root = dest if (contents or not dest.exists()) else dest / Path(src).name
        for p, text in under.items():
            target = root / p[len(src) + 1:]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        root.mkdir(parents=True, exist_ok=True)
        return 0

    def docker(self, args, log=None, *, check=True, timeout=None, **_kw):
        args = [str(a) for a in args]
        rc, out = 0, ""
        if args and args[0] == "cp" and len(args) >= 3 and ":" in args[1] and not args[1].startswith("/"):
            rc = self._cp_out(args[1].split(":", 1)[1], Path(args[2]))
        elif args and args[0] == "exec":
            cmd = " ".join(args)
            if "test.sh" in cmd:
                self.fs.update(self.pending)
                rc = self.rc
            if "cat" in cmd:
                words = set(shlex.split(cmd.replace(";", " ").replace("&&", " ")))
                hits = [p for p in self.fs if p in words]
                out = "".join(self.fs[p] for p in hits)
                if not hits and "test.sh" not in cmd:
                    rc = 1
        elif args and args[0] == "inspect":
            out = "true\n"
        proc = subprocess.CompletedProcess(["docker", *args], rc, out, "")
        if check and rc != 0:
            raise RuntimeError(f"docker {args[0]} failed (exit {rc})")
        return proc


def _run_oracle(tmp_path, monkeypatch, files: dict[str, str], rc: int = 0):
    container = _Container(files, rc)
    monkeypatch.setattr(mod, "_docker", container.docker)
    monkeypatch.setattr(DockerSolverHarness, "_build_image", lambda self, t, i, log: None)
    monkeypatch.setattr(DockerSolverHarness, "_start_container",
                        lambda self, t, i, c, log, sandbox=None: None)
    monkeypatch.setattr(DockerSolverHarness, "_solve_oracle", lambda self, t, c, log: None)
    task = load_task(_harbor(tmp_path / "tasks"))
    harness = DockerSolverHarness(harness_id="docker-oracle", mode="oracle")
    return harness.run(task, output_dir=tmp_path / "out")


@pytest.mark.parametrize("rc", [0, 1])
def test_reward_json_zero_fails_whatever_the_exit_code(tmp_path, monkeypatch, rc):
    result = _run_oracle(tmp_path, monkeypatch,
                         {"reward.json": json.dumps({"reward": 0.0, "answer_present": 0.0})}, rc)
    assert result.passed is False
    assert result.reward == 0.0


def test_reward_json_one_passes(tmp_path, monkeypatch):
    result = _run_oracle(tmp_path, monkeypatch, {"reward.json": json.dumps({"reward": 1.0})})
    assert result.passed is True
    assert result.reward == 1.0


@pytest.mark.parametrize("text, reward", [
    ('{"reward": 1e-1}', 0.1),
    ('{"reward": 0.75, "answer_present": 1.0}', 0.75),
])
def test_reward_json_partial_credit_is_kept(tmp_path, monkeypatch, text, reward):
    result = _run_oracle(tmp_path, monkeypatch, {"reward.json": text})
    assert result.reward == pytest.approx(reward)
    assert result.passed is False


def test_reward_txt_still_read(tmp_path, monkeypatch):
    result = _run_oracle(tmp_path, monkeypatch, {"reward.txt": "0\n"})
    assert (result.reward, result.passed) == (0.0, False)
    result = _run_oracle(tmp_path / "again", monkeypatch, {"reward.txt": "1\n"}, rc=1)
    assert (result.reward, result.passed) == (1.0, True)


def test_command_harness_reads_1e_minus_1_as_a_tenth(tmp_path):
    task = load_task(_harbor(tmp_path / "tasks"))
    src = tmp_path / "verdict.json"
    src.write_text('{"reward": 1e-1}', encoding="utf-8")
    harness = CommandHarness(
        "fake",
        f"sh -c 'mkdir -p {{output_dir}}/verifier && cp {src.as_posix()} {{output_dir}}/verifier/reward.json'",
    )
    out = tmp_path / "out"
    result = harness.run(task, output_dir=out)
    assert (out / "verifier" / "reward.json").is_file()
    assert result.returncode == 0
    assert result.reward == pytest.approx(0.1)
    assert result.passed is False
