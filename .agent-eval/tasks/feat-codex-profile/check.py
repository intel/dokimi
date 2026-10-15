#!/usr/bin/env python3
"""Checks for feat-codex-profile. cwd = <workspace>/dokimi; argv: <run_dir> <task_dir>.

Prints one JSON object of metrics as the last stdout line.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, os.getcwd())  # the workspace's package, not an installed one
# run.py reads the last line of stdout+stderr: nothing may follow the JSON on stderr
sys.stderr = sys.stdout

# `codex exec --help` (and global options) of codex-cli 0.160.0.
CODEX_EXEC_FLAGS = {
    "-c", "--config", "--enable", "--disable", "--strict-config", "-i", "--image", "-m", "--model",
    "--oss", "--local-provider", "-p", "--profile", "-s", "--sandbox", "--approve-for-me",
    "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust", "-C", "--cd",
    "--worktree", "--add-dir", "--thread-source", "--skip-git-repo-check", "--ephemeral",
    "--ignore-user-config", "--ignore-rules", "--output-schema", "--color", "--json", "-o",
    "--output-last-message", "-h", "--help", "-V", "--version", "--",
}
DOC_FILES = re.compile(r"^(dokimi/docs/.*\.md|dokimi/README\.md|README\.md|CONTRIBUTING\.md|dokimi/CONTRIBUTING\.md)$")

metrics: dict = {}
notes: list = []


def guard(name):
    def deco(fn):
        try:
            metrics[name] = bool(fn())
        except (Exception, SystemExit):
            metrics[name] = False
            notes.append(f"{name}: {traceback.format_exc(limit=3)}")
        return fn
    return deco


def load():
    from dokimi.repos.harness_profile import load_profile
    return load_profile("codex")


@guard("profile_loads")
def _():
    import dokimi
    assert Path(dokimi.__file__).resolve().is_relative_to(Path.cwd().resolve()), dokimi.__file__
    from dokimi.repos.events import TRACE_FORMATS
    from dokimi.repos.harness_profile import PROFILE_DIR, builtin_profiles
    p = load()  # validates against harness_profile.v1 and the loader's own rules
    assert Path(p.path).resolve().parent == PROFILE_DIR.resolve(), p.path
    assert "codex" in builtin_profiles()
    assert p.name == "codex", p.name
    assert p.trace in TRACE_FORMATS, p.trace
    return True


@guard("guidance_agents_md")
def _():
    names = load().guidance_names
    return "AGENTS.md" in names and "CLAUDE.md" not in names


@guard("invoke_ok")
def _():
    p = load()
    inv = list(p.invoke)
    assert inv.count("{tools}") == 1
    assert any("{prompt}" in e for e in inv) and any("{model}" in e for e in inv)
    prompt = 'say "hi"; {model} --x'
    for mode in ("none", "read", "all"):  # `repo run` defaults to --tools all
        argv = p.argv(prompt=prompt, model="gpt-x", tools=mode, bin_path="/opt/codex")
        assert argv[0] == "/opt/codex" and prompt in argv and "gpt-x" in " ".join(argv), (mode, argv)
    return True


@guard("exec_mode")
def _():
    inv = list(load().invoke)
    pi = next(i for i, e in enumerate(inv) if "{prompt}" in e)
    return "exec" in inv[:pi]


@guard("flags_known")
def _():
    p = load()
    toks = list(p.invoke) + [t for args in p.tools.values() for t in args]
    bad = [t for t in toks if t.startswith("-") and t.split("=", 1)[0] not in CODEX_EXEC_FLAGS]
    if bad:
        notes.append(f"flags_known: unknown {bad}")
    return not bad


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@guard("reach_dry_run")
def _():
    import yaml
    from dokimi.cli import build_parser
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        _write(root, "AGENTS.md", "# root agents\n")
        _write(root, "CLAUDE.md", "# root claude\n")
        _write(root, "sub/AGENTS.md", "# sub agents\n")
        _write(root, "sub/CLAUDE.md", "# sub claude\n")
        _write(root, "docs/guide.md", "# guide\n")
        _write(root, "evals/Dockerfile", "FROM scratch\n")
        _write(root, "evals/tasks/.keep", "")
        _write(root, "evals/repo-eval.yaml", yaml.safe_dump({
            "schema": "repo_manifest.v1", "id": "toy", "eval_dir": "evals",
            "guidance": {"globs": ["docs/guide.md"]},
            "entry_points": {"grade": "python3 evals/grade.py {run_dir}"}, "tasks": "evals/tasks"}))
        args = build_parser().parse_args(["repo", "reach", str(root), "--manifest", "evals/repo-eval.yaml",
                                          "--harness", "codex", "--model", "m", "--dry-run"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            args.func(args)
        plan = json.loads(out.getvalue())
    files = set(plan["files"])
    assert {"AGENTS.md", "sub/AGENTS.md", "docs/guide.md"} <= files, files
    assert not files & {"CLAUDE.md", "sub/CLAUDE.md"}, files
    assert plan["runs"] and all(r["argv"][0] == "codex" for r in plan["runs"]), plan["runs"][:1]
    return True


@guard("run_parser_ok")
def _():
    from dokimi.cli import build_parser
    a = build_parser().parse_args(["repo", "run", ".", "--arm", "guidance:raw", "--model", "m",
                                   "--batch", "b", "--harness", "codex"])
    return a.harness == "codex"


def added_lines(path_re):
    rd = Path(sys.argv[1])
    start = json.loads((rd / "meta.json").read_text())["start_sha"]
    ws = Path.cwd().parent
    git = ["git", "-c", "core.hooksPath=/dev/null", "-C", str(ws)]
    diff = subprocess.run(git + ["diff", start], capture_output=True, text=True).stdout
    out, cur = [], None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            cur = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and cur and path_re.search(cur):
            out.append(line[1:])
    untracked = subprocess.run(git + ["ls-files", "--others", "--exclude-standard"],
                               capture_output=True, text=True).stdout.split()
    for rel in untracked:  # never committed: every line is new
        if path_re.search(rel):
            out.extend((ws / rel).read_text(encoding="utf-8", errors="replace").splitlines())
    return out


@guard("tests_added")
def _():
    return any(re.search(r"codex", l, re.I) for l in added_lines(re.compile(r"^dokimi/tests/.*\.py$")))


@guard("docs_updated")
def _():
    return any(re.search(r"codex", l, re.I) for l in added_lines(DOC_FILES))


for n in notes:
    print(n)
print(json.dumps(metrics))
