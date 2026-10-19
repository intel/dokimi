# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for harness profiles, the events converter and the L1 reach probe (no LLM).

A fake harness script stands in for a real CLI. It loads ``CLAUDE.md`` from the
cwd up to the git root, as Claude Code is documented to, and, when its read
tool is enabled and the prompt names a file, "reads" that file and lazily loads
``CLAUDE.md`` from the file's directory. It prints Claude stream-json.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from dokimi.artifacts import validate_artifact
from dokimi.cli import build_parser
from dokimi.repos import RepoSource, SourceError, WorkTree, load_manifest, open_source
from dokimi.repos.events import OUTPUT_KEEP, OUTPUT_LIMIT, clip_output, convert
from dokimi.repos.harness_profile import ProfileError, load_profile, parse_profile
from dokimi.repos.reach import (
    ReachError,
    SeededFile,
    check_ancestors,
    classify,
    format_matrix,
    probe_files,
    run_reach,
    scenarios,
    seed_text,
    token,
)

FAKE_HARNESS = r'''
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
mode = os.environ.get("FAKE_MODE", "ok")
if args == ["--version"]:
    sys.stdout.buffer.write("9.8.7 (Fake Code с)\n".encode() if mode == "utf8" else b"9.8.7 (Fake Code)\n")
    sys.exit(0)
assert os.environ.get("FAKE_CONFIG_DIR"), "config dir not isolated"
assert "SECRET_NOT_ALLOWED" not in os.environ, "env not scrubbed"
prompt = args[args.index("-p") + 1]
tools = args[args.index("--tools") + 1] if "--tools" in args else "default"
if mode == "crash":
    print("boom", file=sys.stderr); sys.exit(3)
if mode == "line_then_hang":  # some trace, then nothing until it is killed
    import time
    print(json.dumps({"type": "system", "subtype": "init", "claude_code_version": "9.8.7"}), flush=True)
    time.sleep(30)
if mode in ("hang", "orphan"):  # a helper holding stdout, which killing the harness alone leaves running
    import subprocess, time
    marker = str(Path(__file__).with_name("grandchild-alive"))
    subprocess.Popen([sys.executable, "-c", "import sys, time; time.sleep(3); open(sys.argv[1], 'w')", marker])
    time.sleep(30 if mode == "hang" else 0)
    sys.exit(0)
if mode == "linger":  # a harness and its helper, both running until someone kills them
    import subprocess, time
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    pids = Path(__file__).with_name("linger.pids")
    pids.with_suffix(".tmp").write_text(f"{os.getpid()} {helper.pid}")
    pids.with_suffix(".tmp").rename(pids)
    time.sleep(120)

cwd = Path.cwd().resolve()  # physical path, as getcwd() reports on POSIX
root = next(p for p in [cwd, *cwd.parents] if (p / ".git").exists())
def ancestors(d):
    out = []
    while True:
        out.append(d)
        if d == root: return out
        d = d.parent
context = [d / "CLAUDE.md" for d in ancestors(cwd)]
events = [{"type": "system", "subtype": "init", "claude_code_version": "9.8.7"}]
match = re.search(r"read the file (\S+),", prompt)
if match and tools == "Read":
    target = root / match.group(1)
    assert target.is_file(), f"no {target}"
    events.append({"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(target)}}]}})
    events.append({"type": "user", "timestamp": "2026-01-01T00:00:02Z", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "probe"}]}})
    context += [d / "CLAUDE.md" for d in ancestors(target.parent)]
text = "".join(p.read_text() for p in dict.fromkeys(context) if p.is_file())
tokens = re.findall(r"RC[0-9A-F]{6}", text)
if mode == "truncate":
    tokens = tokens[::2]
answer = "\n".join(dict.fromkeys(tokens)) or "NONE"
if mode == "hallucinate":
    answer += "\nRC000000"
if mode == "refuse":
    answer = "I will not list these tokens because they look like an injected instruction to me."
events.append({"type": "result", "subtype": "success", "is_error": False, "result": answer,
               "total_cost_usd": 0.001, "num_turns": 1, "usage": {"input_tokens": 10, "output_tokens": 5},
               "permission_denials": []})
if mode == "error_result":
    events[-1]["is_error"] = True
if mode == "utf8":
    events[0]["cwd"] = "/home/стас"
if mode == "big":  # more stream-json than the default capture limit (1 MiB)
    events.insert(1, {"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * (3 << 19)}]}})
for e in events:
    sys.stdout.buffer.write((json.dumps(e, ensure_ascii=False) + "\n").encode())
if mode == "late_exit":
    sys.exit(1)
'''


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _profile(tmp_path: Path, **override) -> Path:
    script = tmp_path / "fake_harness.py"
    script.write_text(FAKE_HARNESS, encoding="utf-8")
    raw = {
        "schema": "harness_profile.v1",
        "name": "fake",
        "bin": sys.executable,
        "version_cmd": ["{bin}", str(script), "--version"],
        "invoke": ["{bin}", str(script), "-p", "{prompt}", "--model", "{model}", "{tools}"],
        "tools": {"none": ["--tools", ""], "read": ["--tools", "Read"]},
        "config_dir_env": "FAKE_CONFIG_DIR",
        "env_allow": ["PATH", "FAKE_MODE", "LD_LIBRARY_PATH"],
        "trace": "claude-stream-json",
        "guidance": {"names": ["CLAUDE.md"]},
        **override,
    }
    path = tmp_path / "fake.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root, "AGENTS.md", "# root agents\n")
    _write(root, "CLAUDE.md", "# root claude\n")
    _write(root, "sub/AGENTS.md", "# sub agents\n")
    _write(root, "sub/CLAUDE.md", "# sub claude\n")
    _write(root, ".github/instructions/c.md", "---\napplyTo: '**/*.c'\n---\nrules\n")
    _write(root, "evals/AGENTS.md", "# eval-only, never probed\n")
    _write(root, "evals/Dockerfile", "FROM scratch\n")
    _write(root, "evals/tasks/.keep", "")
    _write(root, "evals/repo-eval.yaml", yaml.safe_dump({
        "schema": "repo_manifest.v1", "id": "toy", "eval_dir": "evals",
        "guidance": {"globs": ["**/AGENTS.md", ".github/instructions/*.md"]},
        "entry_points": {"grade": "python3 evals/grade.py {run_dir}"},
        "tasks": "evals/tasks",
    }))
    return root


def _reach(tmp_path: Path, monkeypatch, mode: str = "ok", **kwargs):
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("SECRET_NOT_ALLOWED", "1")
    root = _repo(tmp_path)
    tree = WorkTree(root)
    work = tmp_path / "work"
    work.mkdir()
    return run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                     profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out",
                     work_dir=work, salt="s", **kwargs)


# --- profiles ----------------------------------------------------------------


def test_builtin_claude_code_profile_builds_argv_without_a_shell(tmp_path):
    profile = load_profile("claude-code")
    prompt = 'say "hi"; rm -rf / {model}'
    argv = profile.argv(prompt=prompt, model="us.anthropic.claude-haiku", tools="none",
                        max_turns=3, budget_usd=0.05, bin_path="/opt/claude")
    assert argv[:3] == ["/opt/claude", "-p", prompt]  # prompt verbatim, one element, not re-substituted
    assert argv[argv.index("--model") + 1] == "us.anthropic.claude-haiku"
    assert argv[argv.index("--max-budget-usd") + 1] == "0.05"
    assert argv[-2:] == ["--tools", ""]
    assert profile.argv(prompt="p", model="m", tools="read")[-2:] == ["--tools", "Read"]
    with pytest.raises(ProfileError, match="tool mode"):
        profile.argv(prompt="p", model="m", tools="everything")


def test_profile_env_is_allow_listed_and_isolated(tmp_path):
    profile = load_profile("claude-code")
    env = profile.env(tmp_path / "cfg").resolve({"PATH": "/bin", "AWS_REGION": "us-east-1",
                                                 "GITHUB_TOKEN": "x", "HOME": "/home/u",
                                                 "SYSTEMROOT": "C:/WINDOWS"})
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert "GITHUB_TOKEN" not in env and env["AWS_REGION"] == "us-east-1"
    assert {"api.anthropic.com"} <= set(profile.egress)
    assert env["SYSTEMROOT"] == "C:/WINDOWS"  # Bun, inside claude, makes no request without it


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda r: r.update(schema="harness_profile.v2"), "schema"),
        (lambda r: r.update(invoke=["x", "{workspace}"]), "unknown placeholder"),
        (lambda r: r.update(invoke=["x", "{model}"]), "never passes {prompt}"),
        (lambda r: r.update(version_cmd=["x", "{tools}"]), "version_cmd cannot"),
        (lambda r: r.update(version_cmd=["x", "{prompt}"]), "unknown placeholder"),
        (lambda r: r["invoke"].remove("{tools}"), "exactly one {tools}"),
        (lambda r: r["tools"].pop("read"), "read"),
        (lambda r: r.update(trace="xml"), "trace"),
        (lambda r: r.update(egress=["https://api.anthropic.com/v1"]), "egress"),
        (lambda r: r.update(egress=["*"]), "egress"),
        (lambda r: r.update(egress=["*.com"]), "egress"),
    ],
)
def test_bad_profile_is_refused(mutate, needle):
    raw = yaml.safe_load((Path(load_profile("claude-code").path)).read_text())
    mutate(raw)
    with pytest.raises(ProfileError) as exc:
        parse_profile(raw)
    assert needle in str(exc.value)


def test_profile_and_manifest_share_the_egress_host_rule():
    from dokimi.repos.manifest import HOST_PATTERN

    schema = json.loads((Path(load_profile("claude-code").path).parents[2] / "schemas" / "harness_profile.v1.json")
                        .read_text(encoding="utf-8"))
    assert schema["properties"]["egress"]["items"]["pattern"] == HOST_PATTERN


def test_unknown_profile_name():
    with pytest.raises(ProfileError, match="built-in: claude-code"):
        load_profile("no-such-harness")


def test_profile_version_parses_x_y_z(tmp_path):
    profile = load_profile(_profile(tmp_path))
    assert profile.version(profile.env(tmp_path / "c")) == "9.8.7"


@pytest.mark.parametrize("version_cmd, needle", [
    (["{bin}", "-c", "import sys; sys.exit(2)"], "exited 2"),
    (["no-such-harness-binary"], "cannot run"),
])
def test_profile_version_failure_is_a_profile_error(tmp_path, version_cmd, needle):
    profile = load_profile(_profile(tmp_path, version_cmd=version_cmd))
    with pytest.raises(ProfileError, match=needle):
        profile.version(profile.env(tmp_path / "c"))


# --- events ------------------------------------------------------------------


def test_claude_stream_json_to_events():
    lines = [
        {"type": "system", "subtype": "init", "claude_code_version": "2.1.286"},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "bazel test //x"}},
            {"type": "tool_use", "id": "b", "name": "Read", "input": {"file_path": "/ws/sub/AGENTS.md"}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:04.5Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "fail"}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:06Z", "message": {"content": [
            {"type": "tool_use", "id": "c", "name": "Edit", "input": {"file_path": "/ws/src/a.c"}},
            {"type": "tool_use", "id": "d", "name": "WebFetch", "input": {"url": "x"}}]}},
        {"type": "result", "result": " done ", "total_cost_usd": 0.02, "num_turns": 3,
         "usage": {"input_tokens": 7, "output_tokens": 2},
         "permission_denials": [{"tool_name": "WebFetch", "tool_use_id": "d"}]},
    ]
    raw = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"
    t = convert("claude-stream-json", raw, "/ws")
    assert t.answer == "done" and t.cost_usd == 0.02 and t.harness_version == "2.1.286"
    assert t.events == [
        {"t": 0.0, "kind": "command", "text": "bazel test //x", "rc": 1, "output": "fail",
         "output_truncated": False},
        {"t": 0.0, "kind": "read", "path": "sub/AGENTS.md"},
        {"t": 6.0, "kind": "edit", "path": "src/a.c"},
        {"t": 6.0, "kind": "tool", "name": "WebFetch", "denied": True},
    ]
    assert t.reads() == {"sub/AGENTS.md"} and t.errors == ["line 6: not JSON"]
    assert convert("text", " hi \n").answer == "hi"
    with pytest.raises(ValueError):
        convert("xml", "")


def test_claude_stream_json_names_the_models_that_answered():
    def reply(model, **kw):
        return {"type": "assistant", "message": {"model": model, "content": []}, **kw}

    init = {"type": "system", "subtype": "init", "model": "us.anthropic.claude-x"}
    lines = [init, reply("claude-x"), reply("claude-y", parent_tool_use_id="t1"), reply("<synthetic>")]
    trace = "\n".join(json.dumps(x) for x in lines)
    assert convert("claude-stream-json", trace).model_resolved() == "claude-x"  # not the subagent's
    assert convert("claude-stream-json", trace + "\n" + json.dumps(reply("claude-old"))).model_resolved() == [
        "claude-old", "claude-x"]
    assert convert("claude-stream-json", json.dumps(init)).model_resolved() == "us.anthropic.claude-x"  # no reply
    assert convert("claude-stream-json", json.dumps(reply(None))).model_resolved() is None
    assert convert("text", "hi").model_resolved() is None


@pytest.mark.parametrize("line", [
    {"type": "assistant", "message": "hi"},
    {"type": "result", "result": "x", "usage": "n/a"},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": [1], "name": "Read"}]}},
])
def test_a_wrong_shaped_trace_line_is_an_error_not_a_crash(line):
    t = convert("claude-stream-json", json.dumps(line) + '\n{"type": "result", "result": "ok"}\n')
    assert t.answer == "ok" and len(t.errors) == 1 and t.errors[0].startswith("line 1: ")


def test_naive_timestamps_are_read_as_utc():
    lines = [{"type": "system", "timestamp": "2026-01-01T00:00:00"},
             {"type": "assistant", "timestamp": "2026-01-01T00:00:02Z", "message": {"content": [
                 {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "x"}}]}}]
    t = convert("claude-stream-json", "\n".join(json.dumps(x) for x in lines))
    assert t.events == [{"t": 2.0, "kind": "read", "path": "x"}] and not t.errors


def test_command_output_is_kept_and_clipped_head_and_tail():
    long = "start\n" + "x" * 20000 + "\nERROR: icpx is not found\n"
    lines = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "make"}},
            {"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "content": [{"type": "text", "text": long}]},
            {"type": "tool_result", "tool_use_id": "b", "content": [{"type": "image"}]}]}},
    ]
    events = convert("claude-stream-json", "\n".join(json.dumps(x) for x in lines)).events
    make, ls = events
    assert make["output_truncated"] is True and make["rc"] == 0
    assert make["output"].startswith("start\n") and make["output"].endswith("icpx is not found\n")
    assert f"[... {len(long) - 2 * OUTPUT_KEEP} bytes omitted ...]" in make["output"]
    assert len(make["output"].encode()) < OUTPUT_LIMIT + 100
    assert "output" not in ls  # no text in the result
    assert "\ufffd" not in clip_output("é" * 5000 + "a")[0]  # the tail cut splits an é: dropped, not mangled


# --- seeding and classification ----------------------------------------------


def test_seed_keeps_front_matter_valid():
    seeded = seed_text("---\napplyTo: '**'\n---\nbody", "x.md", "RCAAAAAA", "RCBBBBBB")
    assert seeded.startswith("---\napplyTo: '**'\n---\nVerification token (head) for x.md: RCAAAAAA")
    assert seeded.rstrip().endswith("RCBBBBBB") and "body\n" in seeded
    plain = seed_text("rules", "y.md", "RCAAAAAA", "RCBBBBBB")
    assert plain.startswith("Verification token (head)")
    bom = seed_text("\ufeff---\napplyTo: x\n---\nb\n", "z.md", "RCAAAAAA", "RCBBBBBB")
    assert bom.startswith("\ufeff---\napplyTo: x\n---\nVerification token (head)")
    assert seed_text("---\nnot front matter", "h.md", "RCAAAAAA", "RCBBBBBB").startswith("Verification")
    empty = seed_text("---\n---\nbody\n", "e.md", "RCAAAAAA", "RCBBBBBB")
    assert empty.startswith("---\n---\nVerification token (head)")


def test_tokens_are_stable_and_distinct():
    assert token("s", "a", "head") == token("s", "a", "head")
    assert len({token("s", p, pos) for p in ("a", "b") for pos in ("head", "tail")}) == 4
    assert token("s", "a", "head") != token("t", "a", "head")


def test_probe_files_and_scenarios(tmp_path):
    tree = WorkTree(_repo(tmp_path))
    files = probe_files(tree, load_manifest(tree, "evals/repo-eval.yaml"), load_profile(_profile(tmp_path)))
    assert files == [".github/instructions/c.md", "AGENTS.md", "CLAUDE.md", "sub/AGENTS.md", "sub/CLAUDE.md"]
    ids = [s.id for s in scenarios(files)]
    assert ids == ["cwd=.", "cwd=.github/instructions", "cwd=sub",
                   "read=.github/instructions", "read=sub"]
    assert [s.id for s in scenarios(files, ("root",))] == ["cwd=."]
    assert [s.id for s in scenarios(files, only=("read=sub", "nope"))] == ["read=sub"]


@pytest.mark.parametrize("leak", ["AGENTS.md", ".claude/CLAUDE.md"])
def test_ancestor_guard(tmp_path, leak):
    _write(tmp_path, leak, "leak")
    (tmp_path / "w").mkdir()
    with pytest.raises(ReachError, match="leak into every scenario"):
        check_ancestors(tmp_path / "w")


@pytest.mark.parametrize("path", ["../out/CLAUDE.md", "{abs}/out/CLAUDE.md", "..\\..\\out/CLAUDE.md",
                                  ".git/hooks/pre-commit", "sub/.GIT./config"])
def test_build_workspace_refuses_paths_outside_it(tmp_path, path):
    from dokimi.repos import reach

    path = path.format(abs=tmp_path.as_posix())
    tree = type("T", (), {"label": "t", "read_text": lambda self, p: "#!/bin/sh\nexit 1\n"})()
    ws = tmp_path / "a" / "ws"
    ws.mkdir(parents=True)
    with pytest.raises(ReachError, match="outside the workspace"):
        reach.build_workspace(tree, ["CLAUDE.md", path], ws, "s")
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")] == ["a", "a/ws"]


# --- end to end with the fake harness -----------------------------------------


def test_reach_matrix_matches_the_fake_harness_rules(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch)
    validate_artifact("guidance_reach", {**payload, "schema_version": "guidance_reach.v1"})
    m = payload["summary"]["matrix"]
    assert payload["harness"]["version"] == "9.8.7"
    assert m["CLAUDE.md"] == {s: "loaded" for s in m["CLAUDE.md"]}
    assert set(m["AGENTS.md"].values()) == {"not_loaded"}
    assert m["sub/CLAUDE.md"] == {"cwd=.": "not_loaded", "cwd=.github/instructions": "not_loaded",
                                  "cwd=sub": "loaded", "read=.github/instructions": "not_loaded",
                                  "read=sub": "loaded"}
    assert "evals/AGENTS.md" not in m
    assert all(not s["error"] and not s["unknown"] for s in payload["scenarios"])
    assert payload["summary"]["cost_usd"] == pytest.approx(0.005)
    traces = sorted(p.name for p in (tmp_path / "out" / "traces").iterdir())
    assert "04-read_sub.events.jsonl" in traces
    events = (tmp_path / "out/traces/04-read_sub.events.jsonl").read_text()
    assert json.loads(events.splitlines()[0])["path"] == "sub/REACH_PROBE.txt"
    assert not list((tmp_path / "work").iterdir())  # workspace removed


def test_reach_fetched_truncated_unknown_and_error(tmp_path, monkeypatch):
    seeded = SeededFile("a.md", "RC111111", "RC222222")
    t = convert("text", "RC111111")
    assert classify(seeded, t)["class"] == "truncated"
    t.events.append({"kind": "read", "path": "a.md", "t": None})
    assert classify(seeded, t)["class"] == "fetched"
    assert classify(seeded, None)["class"] == "error"

    payload = _reach(tmp_path, monkeypatch, mode="hallucinate", kinds=("root",))
    assert payload["scenarios"][0]["unknown"] == ["RC000000"]
    assert payload["scenarios"][0]["prose"] == 0
    assert "unknown tokens in cwd=.: RC000000" in format_matrix(payload)


def test_reach_flags_a_refusal(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch, mode="refuse", only=("read=sub",))
    assert [s["id"] for s in payload["scenarios"]] == ["read=sub"]
    assert payload["scenarios"][0]["prose"] > 8
    assert "answered with" in format_matrix(payload)
    assert "tool result" in payload["ask"]


def test_reach_records_errors_when_the_harness_fails(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch, mode="crash", kinds=("root",))
    assert payload["scenarios"][0]["error"].startswith("exit 3")
    assert "boom" in payload["scenarios"][0]["error"]
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "error in cwd=.: exit 3 (no answer): boom" in format_matrix(payload)


@pytest.mark.parametrize("mode, error", [("late_exit", "exit 1"), ("error_result", "exit 0 (error result)")])
def test_an_answer_with_a_failed_exit_or_an_error_result_is_not_scored(tmp_path, monkeypatch, mode, error):
    payload = _reach(tmp_path, monkeypatch, mode=mode, kinds=("root",))
    assert payload["scenarios"][0]["error"] == error and payload["scenarios"][0]["reported"] == []
    assert {r["class"] for r in payload["records"]} == {"error"}
    assert "RC" in (tmp_path / "out/traces/00-cwd_..stdout").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode, error", [("hang", "TimeoutExpired"),
                                         # exited at once: its helper is killed then, not at the timeout
                                         ("orphan", "exit 0 (no answer)")])
def test_reach_timeout_kills_the_harness_process_tree(tmp_path, monkeypatch, mode, error):
    start = time.monotonic()
    payload = _reach(tmp_path, monkeypatch, mode=mode, kinds=("root",), timeout_s=1)
    assert time.monotonic() - start < 10
    assert payload["scenarios"][0]["error"].startswith(error)
    time.sleep(3)  # the helper would have written its marker by now
    assert not (tmp_path / "grandchild-alive").exists()
    assert not list((tmp_path / "work").iterdir())


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a killed child nobody reaped yet is a zombie: not running
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and process groups")
def test_sigterm_to_the_cli_kills_the_harness_and_its_helper(tmp_path):
    """The harness leads its own process group, so a SIGTERM to the CLI (as `timeout(1)` sends)
    does not reach it; the CLI's handler kills the group, then the CLI dies of the signal."""
    root, profile, work = _repo(tmp_path), _profile(tmp_path), tmp_path / "work"
    work.mkdir()
    env = {**os.environ, "FAKE_MODE": "linger", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    cli = subprocess.Popen([sys.executable, "-m", "dokimi", "repo", "reach", str(root),
                            "--manifest", "evals/repo-eval.yaml", "--harness", str(profile), "--model", "m",
                            "--out", str(tmp_path / "out"), "--work-dir", str(work), "--scenarios", "root",
                            "--timeout", "120"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    pid_file = tmp_path / "linger.pids"
    pids: list[int] = []
    try:
        deadline = time.monotonic() + 60
        while not pid_file.is_file():
            assert cli.poll() is None, cli.stderr.read().decode() if cli.stderr else ""
            assert time.monotonic() < deadline, "the harness never started"
            time.sleep(0.05)
        pids = [int(p) for p in pid_file.read_text().split()]
        cli.send_signal(signal.SIGTERM)
        assert cli.wait(timeout=30) == -signal.SIGTERM
        deadline = time.monotonic() + 5
        while any(map(_pid_alive, pids)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not [p for p in pids if _pid_alive(p)], "the harness or its helper outlived the CLI"
    finally:
        if cli.poll() is None:
            cli.kill()
        cli.wait()
        if cli.stderr:
            cli.stderr.close()  # an unclosed pipe is a ResourceWarning, now an error
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def test_repo_reach_cli_manifest_file_and_a_failed_scenario(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path)
    local = tmp_path / "local.yaml"
    local.write_text(yaml.safe_dump({"schema": "repo_manifest.v1", "id": "toy", "eval_dir": "evals",
                                     "guidance": {"globs": ["**/AGENTS.md"]}, "tasks": "evals/tasks",
                                     "entry_points": {"grade": "python3 evals/grade.py {run_dir}"}}))
    base = ["repo", "reach", str(root), "--manifest", "evals/repo-eval.yaml", "--harness",
            str(_profile(tmp_path)), "--model", "m"]
    args = build_parser().parse_args([*base, "--manifest-file", str(local), "--dry-run"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["files"] == ["AGENTS.md", "CLAUDE.md", "sub/AGENTS.md", "sub/CLAUDE.md"]
    args = build_parser().parse_args([*base, "--manifest-file", str(tmp_path / "nope.yaml"), "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "nope.yaml" in capsys.readouterr().err

    monkeypatch.setenv("FAKE_MODE", "crash")
    (tmp_path / "work").mkdir()
    args = build_parser().parse_args([*base, "--out", str(tmp_path / "o"), "--work-dir", str(tmp_path / "work"),
                                      "--scenarios", "root"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and (tmp_path / "o" / "reach.json").is_file()


def test_repo_reach_cli_dry_run_and_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODE", "ok")
    root = _repo(tmp_path)
    profile = _profile(tmp_path)
    base = ["repo", "reach", str(root), "--manifest", "evals/repo-eval.yaml", "--harness", str(profile),
            "--model", "m"]
    args = build_parser().parse_args([*base, "--dry-run"])
    args.func(args)
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["runs"]) == 5 and plan["runs"][0]["argv"][-2:] == ["--tools", ""]

    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "o"
    args = build_parser().parse_args([*base, "--out", str(out), "--work-dir", str(work),
                                      "--scenarios", "root,read"])
    args.func(args)
    text = capsys.readouterr().out
    assert "fake 9.8.7" in text and "wrote" in text
    stored = json.loads((out / "reach.json").read_text())
    assert stored["schema_version"] == "guidance_reach.v1" and len(stored["scenarios"]) == 3


def test_repo_reach_dry_run_shows_the_run_budget_and_turns(tmp_path, capsys):
    args = build_parser().parse_args(["repo", "reach", str(_repo(tmp_path)), "--manifest", "evals/repo-eval.yaml",
                                      "--model", "m", "--dry-run", "--budget", "0.02", "--max-turns", "2"])
    args.func(args)
    argv = json.loads(capsys.readouterr().out)["runs"][0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "0.02"
    assert argv[argv.index("--max-turns") + 1] == "2"


@pytest.mark.parametrize("extra, message", [
    (["--scenarios", "root,cw"], "unknown --scenarios kind(s) cw"),
    (["--only", "read=nope"], "--only read=nope matches no scenario"),
    (["--budget", "0"], "--budget must be > 0"),
    (["--budget", "nan"], "--budget must be > 0"),
    (["--budget", "inf"], "--budget must be > 0"),
])
def test_repo_reach_rejects_selections_that_would_drop_scenarios(tmp_path, capsys, extra, message):
    args = build_parser().parse_args(["repo", "reach", str(_repo(tmp_path)), "--manifest", "evals/repo-eval.yaml",
                                      "--model", "m", "--dry-run", *extra])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    assert message in capsys.readouterr().err


def test_reach_decodes_harness_output_as_utf8(tmp_path, monkeypatch):
    payload = _reach(tmp_path, monkeypatch, mode="utf8", kinds=("root",))
    profile = load_profile(tmp_path / "fake.yaml")
    assert profile.version(profile.env(tmp_path / "c")) == "9.8.7"
    assert not payload["scenarios"][0]["error"]
    assert payload["summary"]["matrix"]["CLAUDE.md"] == {"cwd=.": "loaded"}


@pytest.mark.parametrize("workdir, path", [
    ("/ws", "/ws/sub/AGENTS.md"),
    (r"C:\Users\u\ws", r"C:\Users\u\ws\sub\AGENTS.md"),
    (r"C:\Users\u\ws", "C:/Users/u/ws/sub/AGENTS.md"),
])
def test_read_paths_are_relative_to_the_workspace_on_any_os(workdir, path):
    raw = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": path}}]}})
    assert convert("claude-stream-json", raw, workdir).reads() == {"sub/AGENTS.md"}
    assert convert("claude-stream-json", raw, "/elsewhere").reads() == {path}


def test_reach_relativizes_reads_under_a_linked_work_dir(tmp_path, monkeypatch):
    real, link = tmp_path / "real", tmp_path / "link"
    real.mkdir()
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi  # a junction needs no symlink privilege
        _winapi.CreateJunction(str(real), str(link))
    monkeypatch.setenv("FAKE_MODE", "ok")
    tree = WorkTree(_repo(tmp_path))
    run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
              profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out",
              work_dir=link, salt="s", only=("read=sub",))
    events = (tmp_path / "out/traces/00-read_sub.events.jsonl").read_text()
    assert json.loads(events.splitlines()[0])["path"] == "sub/REACH_PROBE.txt"


def test_build_workspace_finds_git_on_the_callers_path(tmp_path, monkeypatch):
    from dokimi import process
    from dokimi.repos import reach

    def posix_lookup(call):  # POSIX execvpe searches the child's PATH, on any OS here
        if not Path(call.argv[0]).is_absolute() and not shutil.which(call.argv[0], path=call.env["PATH"]):
            raise FileNotFoundError(call.argv[0])
        return process.run(call.argv, cwd=call.cwd, env=process.Env.only((), call.env),
                           timeout=call.timeout)

    tree = WorkTree(_repo(tmp_path))
    (tmp_path / "ws").mkdir()
    reach.build_workspace(tree, ["CLAUDE.md"], tmp_path / "ws", "s", runner=process.FakeRunner(posix_lookup))
    assert (tmp_path / "ws/.git").is_dir()


def test_build_workspace_runs_git_without_the_callers_environment(tmp_path, monkeypatch):
    """The snapshot commit must not pick up the operator's git config or identity."""
    from dokimi import process
    from dokimi.repos import reach

    monkeypatch.setenv("PARENT_ONLY_SECRET", "x")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    tree = WorkTree(_repo(tmp_path))
    (tmp_path / "ws").mkdir()
    fake = process.FakeRunner()
    reach.build_workspace(tree, ["CLAUDE.md"], tmp_path / "ws", "s", runner=fake)
    assert len(fake.calls) == 3
    for call in fake.calls:
        assert "PARENT_ONLY_SECRET" not in call.env and "GIT_DIR" not in call.env
        assert call.env["GIT_AUTHOR_NAME"] == "reach"


def test_reach_records_the_full_commit_sha(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "x"]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                         capture_output=True, text=True).stdout.strip()
    monkeypatch.setenv("FAKE_MODE", "ok")
    tree = open_source(RepoSource.parse(f"{root}@HEAD"))
    payload = run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                        profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out",
                        work_dir=tmp_path, salt="s", kinds=("root",))
    validate_artifact("guidance_reach", {**payload, "schema_version": "guidance_reach.v1"})
    assert payload["repo"]["sha"] == sha and len(sha) == 40


def _can_symlink(tmp_path: Path) -> bool:
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to("x")
    except OSError:  # Windows without Developer Mode or the symlink privilege
        return False
    probe.unlink()
    return True


def _commit_links(root: Path, links: dict[str, str]):
    """Commit ``root`` plus ``links`` (path -> target text) as symlink entries, and open that commit.
    update-index writes a link without the symlink privilege a checkout would need."""
    def git(*args, stdin=None):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                              text=True, input=stdin).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    for link, target in links.items():
        git("update-index", "--add", "--cacheinfo", f"120000,{git('hash-object', '-w', '--stdin', stdin=target)},{link}")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign", "-m", "x")
    return open_source(RepoSource.parse(f"{root}@HEAD"))


def _linked_repo(tmp_path: Path, links: dict[str, str], source: str):
    """_repo with both CLAUDE.md files gone and ``links`` added, as a commit or as links on disk."""
    root = _repo(tmp_path)
    (root / "CLAUDE.md").unlink()
    (root / "sub/CLAUDE.md").unlink()
    if source == "git":
        return _commit_links(root, links)
    if not _can_symlink(tmp_path):
        pytest.skip("this OS refuses an unprivileged symlink")
    for link, target in links.items():
        (root / link).symlink_to(target, target_is_directory=(root / link).parent.joinpath(target).is_dir())
    return WorkTree(root)


def _reach_tree(tmp_path: Path, monkeypatch, tree, **kwargs):
    monkeypatch.setenv("FAKE_MODE", "ok")
    return run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                     profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out",
                     work_dir=tmp_path, salt="s", keep=True, **kwargs)


@pytest.mark.parametrize("source", ["git", "worktree"])
@pytest.mark.parametrize("kind", ["symlink", "copy"])
def test_reach_symlinked_guidance_is_one_file(tmp_path, monkeypatch, source, kind):
    """CLAUDE.md -> AGENTS.md is one file in a checkout: a harness that loads CLAUDE.md has loaded AGENTS.md."""
    from dokimi.repos import reach

    tree = _linked_repo(tmp_path, {"CLAUDE.md": "AGENTS.md", "sub/CLAUDE.md": "../CLAUDE.md"}, source)
    if kind == "copy":
        def refuse(*args, **kwargs):
            raise OSError(1314, "A required privilege is not held by the client")
        monkeypatch.setattr(reach.os, "symlink", refuse)
    elif not _can_symlink(tmp_path):
        pytest.skip("this OS refuses an unprivileged symlink")
    payload = _reach_tree(tmp_path, monkeypatch, tree, kinds=("root", "cwd"))
    validate_artifact("guidance_reach", {**payload, "schema_version": "guidance_reach.v1"})
    m = payload["summary"]["matrix"]
    assert sorted(m) == [".github/instructions/c.md", "AGENTS.md", "sub/AGENTS.md"]
    assert m["AGENTS.md"] == {"cwd=.": "loaded", "cwd=.github/instructions": "loaded", "cwd=sub": "loaded"}
    entry = next(f for f in payload["files"] if f["path"] == "AGENTS.md")
    assert entry["aliases"] == ["CLAUDE.md", "sub/CLAUDE.md"] and entry["alias_kind"] == kind
    assert all("aliases" not in f for f in payload["files"] if f["path"] != "AGENTS.md")
    ws = Path(payload["workspace"])
    seeded = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "# root agents" in seeded and entry["head"] in seeded
    for alias in entry["aliases"]:
        assert (ws / alias).is_symlink() is (kind == "symlink")
        assert (ws / alias).read_text(encoding="utf-8") == seeded
    report = format_matrix(payload)
    assert "AGENTS.md = CLAUDE.md, sub/CLAUDE.md" in report
    assert ("copies, not links" in report) is (kind == "copy")


@pytest.mark.parametrize("source", ["git", "worktree"])
def test_reach_reports_links_it_cannot_follow_and_probes_the_rest(tmp_path, monkeypatch, source):
    _write(tmp_path, "outside.md", "a host file\n")
    tree = _linked_repo(tmp_path, {"CLAUDE.md": "gone.md", "sub/CLAUDE.md": "../.github",
                                   ".github/instructions/x.md": "../../../outside.md"}, source)
    payload = _reach_tree(tmp_path, monkeypatch, tree, kinds=("root",))
    validate_artifact("guidance_reach", {**payload, "schema_version": "guidance_reach.v1"})
    reasons = {u["path"]: u["reason"] for u in payload["unresolved"]}
    assert sorted(reasons) == [".github/instructions/x.md", "CLAUDE.md", "sub/CLAUDE.md"]
    assert "escapes the repository" in reasons[".github/instructions/x.md"]
    assert "links to nothing" in reasons["CLAUDE.md"]
    assert "links to a directory" in reasons["sub/CLAUDE.md"]
    assert sorted(payload["summary"]["matrix"]) == [".github/instructions/c.md", "AGENTS.md", "sub/AGENTS.md"]
    ws = Path(payload["workspace"])
    assert not any(os.path.lexists(ws / path) for path in reasons)
    assert "not probed: CLAUDE.md (" in format_matrix(payload)


def test_gittree_symlink_escape_is_source_error(tmp_path):
    root = tmp_path / "repo"
    _write(root, "a.md", "a\n")
    tree = _commit_links(root, {"up.md": "../../etc/passwd", "abs.md": "/etc/passwd", "via.md": "up.md",
                                "deep/up.md": "../../a.md"})
    for link in ("up.md", "abs.md", "via.md", "deep/up.md"):
        with pytest.raises(SourceError, match="escapes the repository"):
            tree.read_text(link)


def test_gittree_link_to_a_directory_or_nothing_is_source_error(tmp_path):
    root = tmp_path / "repo"
    _write(root, "docs/a.md", "a\n")
    tree = _commit_links(root, {"dir.md": "docs", "gone.md": "missing.md", "loop.md": "loop.md"})
    assert tree.read_text("docs/a.md") == "a\n"
    for link, needle in (("dir.md", "links to a directory"), ("gone.md", "links to nothing"),
                         ("loop.md", "too many levels")):
        with pytest.raises(SourceError, match=needle):
            tree.read_text(link)


def test_reach_resalts_when_a_guidance_file_already_holds_its_token(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    _write(root, "CLAUDE.md", f"# root claude, quoting {token('s', 'CLAUDE.md', 'head')}\n")
    monkeypatch.setenv("FAKE_MODE", "ok")
    tree = WorkTree(root)
    payload = run_reach(tree=tree, manifest=load_manifest(tree, "evals/repo-eval.yaml"),
                        profile=load_profile(_profile(tmp_path)), model="m", out_dir=tmp_path / "out",
                        work_dir=tmp_path, salt="s", kinds=("root",))
    assert payload["salt"] == "s-1"
