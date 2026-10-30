# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A toy evaluated repository, a fake container runner and a fake harness, for the repo-run tests.

The repository has two tasks under ``.agent-eval/tasks``:

- ``fix_add``: a setup patch breaks ``add``; the grader imports ``src/calc.py``
  and checks ``add(2, 3) == 5`` (gate ``tests_pass``). Its oracle restores it.
  A workspace without ``src/calc.py`` crashes the grader.
- ``review_add``: a review patch adds a bug; score-only, ``recall`` is 1.0 when
  the answer names ``src/calc.py``. Its oracle writes such an answer.

:class:`FakeRunner` runs each container's command on the host, with every fixed
container path mapped back to its mount source. The fake harness is a Python
script printing Claude stream-json; ``FAKE_ACTION`` picks what it does.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from dokimi.repos.container import CONTENT_DIR, SOURCE_DIR, ContainerResult, ContainerSpec, run_process

GRADER = r'''
import json, sys, importlib.util
from pathlib import Path

mode, rd = sys.argv[1], Path(sys.argv[2])
task = json.loads((rd / "task.json").read_text())
ws = rd / "workspace"
if mode == "oracle":
    if task["grader"] == "calc":
        (ws / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
    else:
        (rd / "answer.txt").write_text("BUG: src/calc.py subtracts")
    sys.exit(0)
meta = json.loads((rd / "meta.json").read_text())
assert len(meta["start_sha"]) == 40 and len(meta["base_sha"]) == 40
if task["grader"] == "calc":
    import subprocess
    subprocess.run(["git", "-C", str(ws), "status", "--porcelain"], capture_output=True)  # as a diff-based grader
    spec = importlib.util.spec_from_file_location("calc", ws / "src/calc.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # raises when the agent deleted the file: a grader crash
    ok = mod.add(2, 3) == 5
    edits = [json.loads(l) for l in (rd / "events.jsonl").read_text().splitlines() if l.strip()]
    out = {"pass": ok, "metrics": {"tests_pass": ok, "n_edits": sum(e["kind"] == "edit" for e in edits)}}
else:
    recall = 1.0 if "src/calc.py" in (rd / "answer.txt").read_text() else 0.0
    out = {"pass": None, "metrics": {"recall": recall}}
(rd / "grade.json").write_text(json.dumps({"schema": "repo_grade.v1", "task": task["id"],
                                           "grader": task["grader"], "details": {}, "errors": [], **out}))
'''

FAKE_HARNESS = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("1.2.3 (Fake Agent)")
    sys.exit(0)
assert os.environ.get("FAKE_CONFIG_DIR"), "config dir not set"
assert "SECRET_NOT_ALLOWED" not in os.environ, "environment not scrubbed"
action = os.environ.get("FAKE_ACTION", "fix")
if action in ("silent", "startfail"):  # silent: dies before a line; startfail: refuses its configuration
    if action == "startfail":
        sys.stderr.write("Error: Invalid MCP configuration\n")
    sys.exit(1)
cwd = Path.cwd()
# Guard against --model missing or being the last arg
try:
    model_idx = args.index("--model")
    model = args[model_idx + 1] if model_idx + 1 < len(args) else "unknown"
except (ValueError, IndexError):
    model = "unknown"
answered = os.environ.get("FAKE_ANSWER_MODEL") or model  # a different model may answer the request
lines = [{"type": "system", "subtype": "init", "claude_code_version": "1.2.3", "cwd": str(cwd), "model": answered}]
if action == "sleep":
    time.sleep(60)
if action == "fix":
    (cwd / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
if action == "lock":  # the agent fixes the code, then makes its repository read-only
    (cwd / "src/calc.py").write_text("def add(a, b):\n    return a + b\n")
    (cwd / ".git").chmod(0o555)
if action == "break":
    (cwd / "src/calc.py").unlink()
if action == "tamper":  # leave state for the grader: a .pth in HOME, and an fsmonitor in .git/config
    (Path(os.environ["HOME"]) / "agent-was-here").write_text("x")
    site = Path(os.environ["HOME"], ".local/lib/python%d.%d/site-packages" % sys.version_info[:2])
    site.mkdir(parents=True, exist_ok=True)
    (site / "evil.pth").write_text("import pathlib; pathlib.Path(%r).touch()\n" % (os.environ["FAKE_MARKER"] + ".pth"))
    hook = cwd / ".git" / "fsmonitor.sh"
    hook.write_text("#!/bin/sh\ntouch " + os.environ["FAKE_MARKER"] + "\n")
    hook.chmod(0o755)
    with open(cwd / ".git" / "config", "a") as f:
        f.write("[core]\n\tfsmonitor = " + str(hook) + "\n")
if action != "idle":  # idle: the agent starts and ends without a tool call
    lines.append({"type": "assistant", "message": {"model": answered, "content": [
        {"type": "tool_use", "id": "t1", "name": "Edit",
         "input": {"file_path": os.environ.get("FAKE_CONTAINER_CWD", str(cwd)) + "/src/calc.py"}}]}})
if os.environ.get("FAKE_PROCESS"):  # a guidance read and one build command, for the process metrics
    lines.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t3", "name": "Read",
         "input": {"file_path": os.environ.get("FAKE_CONTAINER_CWD", str(cwd)) + "/AGENTS.md"}},
        {"type": "tool_use", "id": "t4", "name": "Bash", "input": {"command": "bazel build //x"}}]}})
    lines.append({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t4", "is_error": True, "content": "ERROR: icpx is not found"}]}})

for name in filter(None, os.environ.get("FAKE_SKILLS", "").split(",")):  # use installed skills, in order
    if (cwd / ".claude/skills" / name / "SKILL.md").is_file():
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "s-" + name, "name": "Skill", "input": {"skill": name}}]}})
if os.environ.get("FAKE_TOOL_USES"):  # more tool calls, as a JSON list of [name, input]
    lines.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"u{i}", "name": n, "input": a}
        for i, (n, a) in enumerate(json.loads(os.environ["FAKE_TOOL_USES"]))]}})
result = {"type": "result", "subtype": "success", "result": "done: src/calc.py", "total_cost_usd": 0.01,
          "num_turns": 3, "is_error": False,
          "usage": {"input_tokens": 5, "output_tokens": 2, "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 40}}
if os.environ.get("FAKE_USAGE"):  # a subagent (Agent tool) and the token counts of the whole tree
    lines.append({"type": "assistant", "message": {"id": "msg_1", "usage": {
        "input_tokens": 3, "cache_creation_input_tokens": 1200, "cache_read_input_tokens": 15000}, "content": [
        {"type": "tool_use", "id": "t2", "name": "Agent", "input": {"prompt": "look around"}}]}})
    result.update(usage={"input_tokens": 7, "output_tokens": 2},
                  modelUsage={"m-main": {"inputTokens": 7, "outputTokens": 2, "cacheReadInputTokens": 100},
                              "m-sub": {"inputTokens": 30, "outputTokens": 9, "cacheCreationInputTokens": 5}})
lines.append(result)
print("\n".join(json.dumps(x) for x in lines))
'''

PROFILE = {
    "schema": "harness_profile.v1", "name": "fake", "bin": "fake-agent",
    "version_cmd": ["{bin}", "--version"],
    "invoke": ["{bin}", "-p", "{prompt}", "--model", "{model}", "--max-turns", "{max_turns}",
               "--max-budget-usd", "{budget}", "{tools}"],
    "tools": {"none": ["--tools", ""], "read": ["--tools", "Read"], "all": []},
    "config_dir_env": "FAKE_CONFIG_DIR",
    "env_allow": ["FAKE_ACTION", "FAKE_ANSWER_MODEL", "FAKE_MARKER", "FAKE_SKILLS", "FAKE_USAGE", "FAKE_PROCESS", "FAKE_TOOL_USES", "PATH"],
    "egress": ["api.example.com"], "trace": "claude-stream-json",
    "prompt_suffix": "Stay in the foreground.",
    "config_files": {"settings.json": '{"hook": "python3 {support_dir}/no_background.py"}'},
    "bridge": {"for": "AGENTS.md", "write": "CLAUDE.md", "content": "@AGENTS.md"},
}

MANIFEST = {
    "schema": "repo_manifest.v1", "id": "toy", "source": {"url": "https://example.invalid/toy"},
    "eval_dir": ".agent-eval",
    "guidance": {"globs": ["**/AGENTS.md"]},
    "process_metrics": {"bazel": r"\bbazel\b", "icpx_missing": {"regex": "icpx is not found", "match": "output"}},
    "image": {"dockerfile": ".agent-eval/Dockerfile",
              "caches": [{"name": "build-cache", "path": "/cache/build"}],
              "env": {"TOY_STATE": "{run_dir}/state"}, "network": "none"},
    "entry_points": {"grade": "python3 {eval_dir}/grade.py grade {run_dir}",
                     "oracle": "python3 {eval_dir}/grade.py oracle {run_dir} {source_dir}"},
    "tasks": ".agent-eval/tasks",
}

REVIEW_PATCH = """From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Upstream Person <upstream@example.com>
Date: Mon, 1 Jan 2024 00:00:00 +0000
Subject: [PATCH] add mul

---
 src/calc.py | 4 ++++
 1 file changed, 4 insertions(+)

diff --git a/src/calc.py b/src/calc.py
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,6 @@
 def add(a, b):
     return a + b
+
+
+def mul(a, b):
+    return a + b
--
2.40.0
"""

SETUP_PATCH = """--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a + b
+    return a - b
"""


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp: Path, extra_tasks: dict[str, dict] | None = None) -> tuple[Path, str]:
    """The toy repository with one code commit (the task base) and one eval commit; returns (root, base)."""
    root = tmp / "toy"
    write(root, "src/calc.py", "def add(a, b):\n    return a + b\n")
    write(root, "AGENTS.md", "# guidance\n")
    write(root, "sub/AGENTS.md", "# sub guidance\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "code")
    base = git(root, "rev-parse", "HEAD")
    write(root, ".agent-eval/repo-eval.yaml", yaml.safe_dump(MANIFEST))
    write(root, ".agent-eval/Dockerfile", "FROM scratch\n")
    write(root, ".agent-eval/grade.py", GRADER)
    tasks = {
        "fix_add": {"family": "fix", "grader": "calc", "gate": ["tests_pass"], "setup_patch": "setup.patch",
                    "prompt": "add() is wrong; fix it."},
        "review_add": {"family": "review", "grader": "review", "review_patch": "change.patch",
                       "score": {"metric": "recall", "max": 1.0}, "prompt": "Review HEAD."},
        **(extra_tasks or {}),
    }
    for task_id, body in tasks.items():
        write(root, f".agent-eval/tasks/{task_id}/task.json", json.dumps({"id": task_id, "base": base, **body}))
    write(root, ".agent-eval/tasks/fix_add/setup.patch", SETUP_PATCH)
    write(root, ".agent-eval/tasks/review_add/change.patch", REVIEW_PATCH)
    for task_id, body in (extra_tasks or {}).items():
        for rel in [body.get("setup_patch"), body.get("review_patch")]:
            if rel:
                write(root, f".agent-eval/tasks/{task_id}/{rel}",
                      SETUP_PATCH if rel == body.get("setup_patch") else REVIEW_PATCH)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "evals")
    return root, base


def fake_harness(tmp: Path) -> tuple[Path, Path]:
    """(executable, profile yaml)."""
    exe = tmp / "bin" / "fake-agent"
    write(exe.parent, exe.name, FAKE_HARNESS)
    exe.chmod(0o755)
    profile = tmp / "fake.yaml"
    profile.write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
    return exe, profile


class FakeRunner:
    """Runs a container's command on the host, mapping every mounted container path to its source."""

    def __init__(self, images: tuple[str, ...] = ()):
        self.specs: list[ContainerSpec] = []
        self.images = set(images)
        self.builds: list[tuple[Path, list[str]]] = []
        self.build_timeouts: list[float] = []
        self.leak = False

    def image_exists(self, tag: str) -> bool:
        return tag in self.images

    def build(self, context: Path, dockerfile: Path, tag: str, network: str | None = None,
              timeout_s: float = 0) -> None:
        assert dockerfile.is_file()
        self.build_timeouts.append(timeout_s)
        self.builds.append((context, sorted(p.relative_to(context).as_posix() for p in context.rglob("*"))))
        self.images.add(tag)

    def run(self, spec: ContainerSpec, *, stdout: Path, stderr: Path, timeout_s: float) -> ContainerResult:
        self.specs.append(spec)
        pairs = sorted(((m.container, str(m.host)) for m in spec.mounts), key=lambda p: -len(p[0]))

        def host(text: str) -> str:
            for inside, outside in pairs:
                text = re.sub(re.escape(inside) + r"(?=/|$|\s)", outside.replace("\\", "\\\\"), text)
            return text

        argv = [host(a) for a in spec.argv]
        if argv[:2] == ["sh", "-c"]:  # the isolation probe: judge the mounts instead of running find /
            seen = [f"visible to the agent: {m.container}" for m in spec.mounts
                    if m.container in (CONTENT_DIR, SOURCE_DIR)]
            if self.leak:
                seen.append("named like a task, in the image: /opt/tasks/fix_add")
            stdout.write_text("".join(f"{s}\n" for s in seen))
            stderr.write_text("")
            return ContainerResult(returncode=1 if seen else 0, timed_out=False, wall_s=0.0)
        if argv[0] == "python3":
            argv[0] = sys.executable
        # FAKE_CONTAINER_CWD: the fake harness reports paths as a harness in the container would
        # The host's loader path is kept: a CI interpreter (setup-python) cannot start without it.
        loader = {k: os.environ[k] for k in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH") if k in os.environ}
        env = {"PATH": "/usr/bin:/bin", "FAKE_CONTAINER_CWD": spec.workdir, **loader,
               **{k: os.environ[k] for k in spec.pass_env if k in os.environ},
               **{k: host(v) for k, v in spec.env.items()}}
        cwd = Path(host(spec.workdir))
        if not cwd.is_dir():
            cwd = stdout.parent
        return run_process(argv, env=env, stdout=stdout, stderr=stderr, timeout_s=timeout_s, cwd=cwd)
