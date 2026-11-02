# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``bash=<dir>`` reach scenario: whether a Bash read of a nested file loads its directory's guidance (no LLM).

The fake harness prints Claude stream-json. With its Bash tool on and a prompt that names a ``cat``, it runs that
``cat`` as a Bash call; ``FAKE_MODE`` says what follows: ``lazy`` loads the file's directory's AGENTS.md as context,
``fetch`` also cats that AGENTS.md itself, ``none`` loads nothing more; ``hook`` and ``permissions`` load it as
``lazy`` does, after a ``cat`` of it that a hook or the permissions refuse.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos import WorkTree, load_manifest
from dokimi.repos.harness_profile import ProfileError, load_profile
from dokimi.repos.reach import ASK, ASK_AFTER_BASH, ReachError, run_reach, scenarios
from dokimi.repos.receipt import build_receipt
from tests.test_repo_reach import _write
from tests.test_repo_receipt import BUDGET, _record, _sources, _tree
from tests.test_repo_receipt import _reach as _saved_reach

BASH_HARNESS = r'''
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("9.8.7 (Fake Code)")
    sys.exit(0)
mode = os.environ.get("FAKE_MODE", "lazy")
prompt = args[args.index("-p") + 1]
tools = args[args.index("--tools") + 1]
root = Path.cwd()
context = [root / "AGENTS.md"]
events = [{"type": "system", "subtype": "init", "claude_code_version": "9.8.7"}]
REFUSALS = {"hook": "PreToolUse:Bash hook error: no", "permissions": "Permission to use Bash has been denied"}
def bash(n, command, output, error=False):
    events.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"b{n}", "name": "Bash", "input": {"command": command}}]}})
    events.append({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": f"b{n}", "is_error": error, "content": output}]}})
match = re.search(r"run `cat (\S+)`", prompt)
if match and tools == "Bash":
    probe = Path(match.group(1))
    bash(1, f"cat {probe}", (root / probe).read_text())
    if mode == "fetch":
        bash(2, f"cat {probe.parent}/AGENTS.md", (root / probe.parent / "AGENTS.md").read_text())
    if mode in REFUSALS:  # the cat of the guidance file is refused; the file was loaded lazily all the same
        bash(2, f"cat {probe.parent}/AGENTS.md", REFUSALS[mode], True)
    if mode != "none":
        context.append(root / probe.parent / "AGENTS.md")
text = "".join(p.read_text() for p in context)
answer = "\n".join(dict.fromkeys(re.findall(r"RC[0-9A-F]{6}", text))) or "NONE"
denials = [{"tool_name": "Bash", "tool_use_id": "b2"}] if mode == "permissions" else []
events.append({"type": "result", "subtype": "success", "is_error": False, "result": answer, "permission_denials": denials,
               "total_cost_usd": 0.001, "num_turns": 2, "usage": {"input_tokens": 10, "output_tokens": 5}})
for e in events:
    print(json.dumps(e))
'''


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """A repository with a root and a nested AGENTS.md, and a fake profile with a ``bash`` tool mode."""
    root = tmp_path / "repo"
    _write(root, "AGENTS.md", "# root\n")
    _write(root, "sub/AGENTS.md", "# sub\n")
    _write(root, "evals/tasks/.keep", "")
    _write(root, "evals/repo-eval.yaml", yaml.safe_dump({
        "schema": "repo_manifest.v1", "id": "toy", "eval_dir": "evals", "guidance": {"globs": ["**/AGENTS.md"]},
        "entry_points": {"grade": "python3 evals/grade.py {run_dir}"}, "tasks": "evals/tasks"}))
    script = tmp_path / "bash_harness.py"
    script.write_text(BASH_HARNESS, encoding="utf-8")
    profile = tmp_path / "fake.yaml"
    profile.write_text(yaml.safe_dump({
        "schema": "harness_profile.v1", "name": "fake", "bin": sys.executable,
        "version_cmd": ["{bin}", str(script), "--version"],
        "invoke": ["{bin}", str(script), "-p", "{prompt}", "--model", "{model}", "{tools}"],
        "tools": {"none": ["--tools", ""], "read": ["--tools", "Read"], "bash": ["--tools", "Bash"]},
        "env_allow": ["PATH", "FAKE_MODE", "LD_LIBRARY_PATH"], "trace": "claude-stream-json",
        "guidance": {"names": ["AGENTS.md"]}}), encoding="utf-8")
    return root, profile


def _probe(tmp_path: Path, monkeypatch, mode: str) -> dict:
    monkeypatch.setenv("FAKE_MODE", mode)
    root, profile = _setup(tmp_path)
    tree = WorkTree(root)
    (tmp_path / "work").mkdir()
    return run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"), profile=load_profile(profile),
                     model="m", out_dir=tmp_path / "out", work_dir=tmp_path / "work", salt="s", kinds=("bash",))


def test_bash_scenarios_cat_each_probe_file_from_the_root():
    files = ["AGENTS.md", "a/AGENTS.md", "sub/AGENTS.md"]
    got = scenarios(files, ("bash",))
    assert [(s.id, s.cwd, s.tools, s.read) for s in got] == [
        ("bash=a", "", "bash", "a/REACH_PROBE.txt"), ("bash=sub", "", "bash", "sub/REACH_PROBE.txt")]
    assert got[1].prompt == ("First use the Bash tool to run `cat sub/REACH_PROBE.txt`, and run no other command. "
                             "Then: " + ASK)
    assert got[1].prompt == ASK_AFTER_BASH.format(path="sub/REACH_PROBE.txt") + ASK
    assert not [s for s in scenarios(files) if s.tools == "bash"]  # the default kinds run no bash scenario


def test_the_claude_code_profile_runs_the_bash_mode_with_the_bash_tool_alone():
    profile = load_profile("claude-code")
    assert profile.argv(prompt="p", model="m", tools="bash")[-2:] == ["--tools", "Bash"]
    with pytest.raises(ProfileError, match="lists no arguments for tool mode 'bash'"):  # a reach mode, not a run's
        profile.argv(prompt="p", model="m", tools="bash", delegation="none")


@pytest.mark.parametrize("harness", ["codex", "copilot"])
def test_a_profile_without_a_bash_mode_is_a_usage_error(tmp_path, capsys, harness):
    root, _ = _setup(tmp_path)
    args = build_parser().parse_args(["repo", "reach", str(root), "--manifest", "evals/repo-eval.yaml", "--harness",
                                      harness, "--model", "m", "--scenarios", "root,bash", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert f"profile {harness} has no `bash` tool mode" in capsys.readouterr().err


def test_run_reach_refuses_bash_for_a_profile_without_it_before_any_run(tmp_path):
    root, _ = _setup(tmp_path)
    tree = WorkTree(root)
    with pytest.raises(ReachError, match="profile codex has no `bash` tool mode"):
        run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"), profile=load_profile("codex"),
                  model="m", out_dir=tmp_path / "out", kinds=("root", "bash"))
    assert not (tmp_path / "out").exists()


def test_the_dry_run_plans_the_bash_scenarios(tmp_path, capsys):
    root, profile = _setup(tmp_path)
    args = build_parser().parse_args(["repo", "reach", str(root), "--manifest", "evals/repo-eval.yaml", "--harness",
                                      str(profile), "--model", "m", "--scenarios", "bash", "--only", "bash=sub",
                                      "--dry-run"])
    args.func(args)
    runs = json.loads(capsys.readouterr().out)["runs"]
    assert [(r["scenario"], r["cwd"], r["argv"][-2:]) for r in runs] == [("bash=sub", ".", ["--tools", "Bash"])]
    assert "run `cat sub/REACH_PROBE.txt`" in runs[0]["argv"][runs[0]["argv"].index("-p") + 1]


@pytest.mark.parametrize("mode, cls", [("fetch", "fetched"), ("lazy", "loaded"), ("none", "not_loaded")])
def test_a_bash_read_of_the_probe_shows_whether_the_directory_guidance_loads(tmp_path, monkeypatch, mode, cls):
    payload = _probe(tmp_path, monkeypatch, mode)
    assert [s["id"] for s in payload["scenarios"]] == ["bash=sub"] and not payload["scenarios"][0]["error"]
    assert payload["summary"]["matrix"] == {"AGENTS.md": {"bash=sub": "loaded"}, "sub/AGENTS.md": {"bash=sub": cls}}
    events = [json.loads(x) for x in (tmp_path / "out/traces/00-bash_sub.events.jsonl").read_text().splitlines()]
    assert events[0] == {"t": None, "kind": "read", "path": "sub/REACH_PROBE.txt", "rc": 0}


@pytest.mark.parametrize("mode", ["hook", "permissions"])
def test_a_refused_bash_read_of_the_guidance_is_no_fetch(tmp_path, monkeypatch, mode):
    payload = _probe(tmp_path, monkeypatch, mode)
    assert payload["summary"]["matrix"]["sub/AGENTS.md"] == {"bash=sub": "loaded"}
    events = [json.loads(x) for x in (tmp_path / "out/traces/00-bash_sub.events.jsonl").read_text().splitlines()]
    assert [e.get("denied") for e in events if e.get("path") == "sub/AGENTS.md"] == [True]  # kept for audit
    receipt = build_receipt(payload, BUDGET, WorkTree(tmp_path / "repo"))
    assert _sources(receipt, "bash=sub")["sub/AGENTS.md"]["states"]["accessed"] == "unknown"


def test_a_bash_scenario_receipt_expects_the_lazy_file_and_sees_the_shell_read(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a\n", "CLAUDE.md": "c\n", "sub/AGENTS.md": "s\n"})
    row = {"id": "bash=sub", "cwd": "", "tools": "bash", "read": "sub/REACH_PROBE.txt", "error": None, "prose": 0,
           "unknown": []}
    records = [_record("sub/AGENTS.md", "bash=sub", "fetched", read_by_tool=True),
               _record("CLAUDE.md", "bash=sub", "not_loaded")]
    receipt = build_receipt(_saved_reach(records, [row]), BUDGET, tree)
    src = _sources(receipt, "bash=sub")
    assert src["sub/AGENTS.md"]["expected"] is True  # lazy: the run reads inside sub, as read=sub does
    assert src["sub/AGENTS.md"]["states"]["accessed"] is True  # the cat was a read event
    assert src["CLAUDE.md"]["states"]["accessed"] == "unknown"  # a tools-on run: no read is not proof of none
    assert next(r for r in receipt["receipts"] if r["run_id"].endswith("bash=sub"))["target"] == "sub/REACH_PROBE.txt"
