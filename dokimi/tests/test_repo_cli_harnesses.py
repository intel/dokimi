# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Codex and Copilot CLI profiles, their JSON traces, and native review answers (contract §4.1, §6). No LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from dokimi.cli import build_parser
from dokimi.repos.answers import codex_review_findings, convert_answer
from dokimi.repos.cli_traces import shell_reads, unwrap_shell
from dokimi.repos.events import convert
from dokimi.repos.harness_profile import ProfileError, builtin_profiles, load_profile, parse_profile
from dokimi.repos.reach import ReachError, probe_files
from repo_eval_fixture import FakeRunner, make_repo, write

# --- profiles ----------------------------------------------------------------


def test_builtin_cli_profiles_load_and_build_argv_without_a_shell():
    assert {"codex", "codex-review", "copilot", "copilot-review"} <= set(builtin_profiles())
    prompt = '-say "hi"; {model}'
    codex = load_profile("codex").argv(prompt=prompt, model="gpt-x", tools="all", bin_path="/opt/codex")
    assert codex[:2] == ["/opt/codex", "exec"] and codex[-2:] == ["--", prompt]  # after --, never a flag
    assert "--json" in codex and "--dangerously-bypass-approvals-and-sandbox" in codex
    copilot = load_profile("copilot")
    assert copilot.argv(prompt=prompt, model="m", tools="read")[-4:] == ["--available-tools", "view", "grep", "glob"]
    # an empty --available-tools list would leave every tool on
    assert copilot.argv(prompt=prompt, model="m", tools="none")[-2:] == ["--available-tools", "__none__"]
    review = load_profile("copilot-review").argv(prompt="Review HEAD.", model="m")
    assert review[review.index("-p") + 1] == "/review Review HEAD."
    assert load_profile("codex").guidance_names == ("AGENTS.md", "AGENTS.override.md")
    assert "copilot-instructions.md" in copilot.guidance_names


def test_codex_review_runs_its_own_prompt():
    profile = load_profile("codex-review")
    assert profile.native_prompt and profile.answer_format == "codex-review" and profile.trace == "codex-json"
    argv = profile.argv(prompt="Review HEAD as a maintainer", model="gpt-x", tools="all")
    assert argv[1:5] == ["exec", "review", "--commit", "HEAD"]
    assert not any("Review HEAD as" in a for a in argv)  # `--commit` refuses a custom prompt


def test_profile_env_passes_byok_names_only():
    env = load_profile("copilot").env(Path("/cfg")).resolve({
        "COPILOT_PROVIDER_BASE_URL": "http://127.0.0.1:4000/v1", "COPILOT_PROVIDER_API_KEY": "k",
        "AWS_BEARER_TOKEN_BEDROCK": "x", "PATH": "/bin"})
    assert env["COPILOT_HOME"] == "/cfg" and env["COPILOT_AUTO_UPDATE"] == "false"
    assert env["COPILOT_PROVIDER_BASE_URL"] == "http://127.0.0.1:4000/v1"
    assert "AWS_BEARER_TOKEN_BEDROCK" not in env
    assert load_profile("codex").env(Path("/c")).resolve({"OPENAI_API_KEY": "k"})["CODEX_HOME"] == "/c"


@pytest.mark.parametrize("mutate, needle", [
    (lambda r: r.update(native_prompt=False), "never passes {prompt}"),
    (lambda r: r["invoke"].insert(1, "{prompt}"), "cannot pass"),
    (lambda r: r.update(prompt_suffix="x"), "prompt_suffix"),
    (lambda r: r.update(answer_format="xml"), "answer_format"),
    (lambda r: r.update(trace="codex"), "trace"),
])
def test_bad_native_profile_is_refused(mutate, needle):
    raw = yaml.safe_load(Path(load_profile("codex-review").path).read_text())
    mutate(raw)
    with pytest.raises(ProfileError) as exc:
        parse_profile(raw)
    assert needle in str(exc.value)


def test_reach_refuses_a_native_prompt_profile(tmp_path):
    root, _ = make_repo(tmp_path)
    from dokimi.repos import RepoSource, load_manifest, open_source

    tree = open_source(RepoSource.parse(f"{root}@HEAD"))
    manifest = load_manifest(tree, ".agent-eval/repo-eval.yaml")
    with pytest.raises(ReachError, match="native_prompt"):
        probe_files(tree, manifest, load_profile("codex-review"))
    assert "AGENTS.md" in probe_files(tree, manifest, load_profile("codex"))


# --- traces ------------------------------------------------------------------


@pytest.mark.parametrize("command, reads", [
    ("cat AGENTS.md", ["AGENTS.md"]),
    ("cat a b && sed -n '1,80p' cpp/AGENTS.md", ["a", "b", "cpp/AGENTS.md"]),
    ("head -n 40 x.md; tail -c 10 y", ["x.md", "y"]),
    ("nl -ba src/a.c", ["src/a.c"]),
    ("cat a > b", []),            # writes b
    ("cat a | grep x", []),       # a pipeline is not a plain print
    ("sed -i 's/a/b/' f", []),    # edits f
    ("sed 's/a/b/' f", []),       # no -n: a transform, not a range print
    ("ls -la", []),
    ("cat 'unterminated", []),
    ("sed -n '1p;2p' AGENTS.md", ["AGENTS.md"]),  # regression: quoted program should not be split
    ("cd sub && cat AGENTS.md", ["sub/AGENTS.md"]),  # regression: cd changes cwd for following commands
    ("cd sub && cd inner && cat AGENTS.md", ["sub/inner/AGENTS.md"]),  # successive cds compose
    ("cat ./AGENTS.md", ["AGENTS.md"]),                   # dot components are normalised
    ("cd sub && cat ../AGENTS.md", ["AGENTS.md"]),
    ("cd - && cat X", []),                                # the previous directory is not tracked
    ("cd missing || cat X", []),                          # runs in an unknown directory
    ("cd /abs && cat X", ["/abs/X"]),
    ("cd $D && cat X", []),
    ("cat a || cat b", ["a", "b"]),                       # no cd: the directory is known
    ("cat a & cat b", ["a", "b"]),                        # a single & separates commands
    ("cat a >> b", []),
    ("cat a < b", []),
    ("cat a 2>&1", ["a"]),                                # a fd dup or a /dev/null redirect is not a write
    ("cat a 2>/dev/null", ["a"]),
    ("sed -n '1,5p' a 2>/dev/null", ["a"]),
    ("cat a &>/dev/null", ["a"]),
    ("cat a 2> /dev/null", ["a"]),
    ("cat a\\\n b", ["a", "b"]),                          # a line continuation joins the command
    ("cat <<'EOF'\ncat secret\nEOF", []),                 # a heredoc body is input, not commands
    ("cat > f <<'EOF'\nhead other\nEOF", []),
    ("cat a <<EOF\nx\nEOF\ncat b", ["a", "b"]),
    ("(cd x && cat f)", []),                              # a subshell is not tracked
    ("head --lines 5 a", ["a"]),                          # option values are not files
    ("head -qn 5 a", ["a"]),
    ("head -n5 a", ["a"]),
    ("nl -w 3 a", ["a"]),
    ("nl -ba a", ["a"]),
    ("sed -n -e '1p' f", ["f"]),
    ("sed -n -E '1,3p' f", ["f"]),
    ("sed -n -i.bak 1p f", []),                           # in-place edit
    ("sed -n --in-place 1p f", []),
    ("sed -n '$p' f", ["f"]),                             # $ inside single quotes is the last line
    ("sed -n '1,$p' AGENTS.md", ["AGENTS.md"]),
    ("cat \"$HOME/f\"", []),                              # an expansion is not a path
])
def test_shell_reads(command, reads):
    assert shell_reads(command) == reads


def test_shell_reads_start_in_the_given_directory():
    assert shell_reads("cat AGENTS.md", "sub") == ["sub/AGENTS.md"]
    assert shell_reads("cd inner && cat ../x", "sub") == ["sub/x"]
    assert shell_reads("cat /ws/a", "sub") == ["/ws/a"]


def test_cli_traces_resolve_relative_reads_from_the_starting_directory():
    codex = _jsonl({"type": "item.completed", "item": {"id": "1", "type": "command_execution",
                                                       "command": "cat AGENTS.md", "exit_code": 0}})
    assert convert("codex-json", codex, "/ws", "sub").reads() == {"sub/AGENTS.md"}
    assert convert("codex-json", codex, "/ws").reads() == {"AGENTS.md"}
    copilot = _jsonl({"type": "tool.execution_start", "data": {"toolCallId": "c", "toolName": "bash",
                                                               "arguments": {"command": "cat AGENTS.md"}}})
    assert convert("copilot-json", copilot, "/ws", "sub").reads() == {"sub/AGENTS.md"}


def test_unwrap_shell():
    assert unwrap_shell("/usr/bin/bash -lc 'cat a.txt'") == "cat a.txt"
    assert unwrap_shell("bash -lc \"git show HEAD | head\"") == "git show HEAD | head"
    assert unwrap_shell("rg foo") == "rg foo"


def _jsonl(*records):
    return "\n".join(json.dumps(r) for r in records) + "\nnot json\n"


def test_codex_json_to_events():
    raw = _jsonl(
        {"type": "thread.started", "thread_id": "x"},
        {"type": "error", "message": "Reconnecting... 2/5 (request timed out)"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "I will look."}},
        {"type": "item.started", "item": {"type": "command_execution", "command": "bash -lc 'cat /ws/AGENTS.md'"}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "/usr/bin/bash -lc 'cat /ws/AGENTS.md'",
                                            "aggregated_output": "# guide\n", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "bazel test //x",
                                            "aggregated_output": "", "exit_code": 3}},
        {"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "/ws/src/a.c", "kind": "update"}]}},
        {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "gh", "tool": "search"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": " DONE "}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7}},
    )
    t = convert("codex-json", raw, "/ws")
    assert t.answer == "DONE" and t.num_turns == 1 and (t.input_tokens, t.output_tokens) == (100, 7)
    assert t.cost_usd is None  # Codex reports no cost: the batch budget counts the cap
    assert t.events == [
        {"t": None, "kind": "read", "path": "AGENTS.md"},
        {"t": None, "kind": "command", "text": "cat /ws/AGENTS.md", "rc": 0, "output": "# guide\n",
         "output_truncated": False},
        {"t": None, "kind": "command", "text": "bazel test //x", "rc": 3},
        {"t": None, "kind": "edit", "path": "src/a.c"},
        {"t": None, "kind": "tool", "name": "gh.search"},
    ]
    assert t.errors == ["line 11: not JSON"] and not t.is_error  # a reconnect is not an error


def test_codex_turn_failure_is_an_error_result():
    t = convert("codex-json", _jsonl({"type": "turn.failed", "error": {"message": "quota"}}), None)
    assert t.is_error and "turn.failed: quota" in t.errors and t.answer == ""


def test_codex_overlapping_commands_are_matched_by_item_id():
    def cmd(kind, i, **extra):
        return {"type": kind, "item": {"id": i, "type": "command_execution", "command": "make build", **extra}}

    raw = _jsonl(cmd("item.started", "a"), cmd("item.started", "b"),
                 cmd("item.completed", "b", exit_code=1, aggregated_output="bad"),
                 cmd("item.completed", "a", exit_code=0, aggregated_output="ok"))
    events = convert("codex-json", raw, None).events
    assert [(e["rc"], e["output"]) for e in events] == [(0, "ok"), (1, "bad")]  # in start order


def test_codex_turn_failed_is_not_cleared_by_a_later_error():
    raw = _jsonl({"type": "turn.failed", "message": "x"}, {"type": "error", "message": "boom"})
    t = convert("codex-json", raw, None)
    assert t.is_error and t.errors[:2] == ["turn.failed: x", "error: boom"]


def test_codex_incomplete_command_is_recorded_with_rc_null():
    """Regression: a command with item.started but no item.completed (timeout) should have rc: null."""
    raw = _jsonl(
        {"type": "item.started", "item": {"type": "command_execution", "command": "make build"}},
        # No item.completed (timeout scenario)
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 1}},
    )
    t = convert("codex-json", raw, None)
    assert len(t.events) == 1
    assert t.events[0] == {"t": None, "kind": "command", "text": "make build", "rc": None}


def test_copilot_json_to_events():
    raw = _jsonl(
        {"type": "assistant.turn_start", "timestamp": "2026-01-01T00:00:00Z", "data": {}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:01Z",
         "data": {"toolCallId": "a", "toolName": "view", "arguments": {"path": "/ws/AGENTS.md"}}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:01Z",
         "data": {"toolCallId": "b", "toolName": "bash", "arguments": {"command": "cat sub/AGENTS.md && make"}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "b", "success": False,
                                                     "shellExecution": {"exitCode": 2},
                                                     "result": {"content": "make: *** error"}}},
        {"type": "assistant.turn_start", "timestamp": "2026-01-01T00:00:03Z", "data": {}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:04Z",
         "data": {"toolCallId": "c", "toolName": "apply_patch",
                  "arguments": "*** Begin Patch\n*** Update File: /ws/src/a.c\n@@\n-x\n+y\n*** End Patch\n"}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:05Z",
         "data": {"toolCallId": "d", "toolName": "web_fetch", "arguments": {"url": "x"}}},
        {"type": "assistant.message", "data": {"content": "", "toolRequests": [{}]}},
        {"type": "assistant.message", "data": {"content": "final", "phase": "final_answer"}},
        {"type": "assistant.message", "data": {"content": "a late aside"}},
        {"type": "result", "exitCode": 0, "usage": {"premiumRequests": 0}},
    )
    t = convert("copilot-json", raw, "/ws")
    assert t.answer == "final" and t.num_turns == 2 and not t.is_error and t.cost_usd is None
    assert t.events == [
        {"t": 1.0, "kind": "read", "path": "AGENTS.md"},
        {"t": 1.0, "kind": "read", "path": "sub/AGENTS.md"},
        {"t": 1.0, "kind": "command", "text": "cat sub/AGENTS.md && make", "rc": 2, "output": "make: *** error",
         "output_truncated": False},
        {"t": 4.0, "kind": "edit", "path": "src/a.c"},
        {"t": 5.0, "kind": "tool", "name": "web_fetch"},
    ]
    assert t.reads() == {"AGENTS.md", "sub/AGENTS.md"}


def test_copilot_answer_falls_back_to_the_last_message_and_a_failed_exit_is_an_error():
    t = convert("copilot-json", _jsonl({"type": "assistant.message", "data": {"content": "only"}},
                                       {"type": "result", "exitCode": 1}), None)
    assert t.answer == "only" and t.is_error


# --- native review answers ---------------------------------------------------

CODEX_REVIEW = """The loop reads past the end.

Full review comments:

- [P1] Use a C++17-compatible span — /ws/cpp/x/scan.hpp:6-6
  When this header is built as C++17, `std::span` is unavailable.
  Use a pointer and a length.

- [P2] Stop before reading past the input — /ws/src/f.cpp:64-66
  This loop also reads `a[n]`.
"""


def test_codex_review_findings():
    found = codex_review_findings(CODEX_REVIEW, "/ws")
    assert found == [
        {"file": "cpp/x/scan.hpp", "line": 6, "end_line": 6, "severity": "major", "priority": "P1",
         "rule": "Use a C++17-compatible span",
         "message": "When this header is built as C++17, `std::span` is unavailable. Use a pointer and a length."},
        {"file": "src/f.cpp", "line": 64, "end_line": 66, "severity": "minor", "priority": "P2",
         "rule": "Stop before reading past the input", "message": "This loop also reads `a[n]`."},
    ]
    assert codex_review_findings("Review comment:\n\n- [P0] Crash — a.c:3\n  boom") == [
        {"file": "a.c", "line": 3, "end_line": 3, "severity": "blocker", "priority": "P0", "rule": "Crash",
         "message": "boom"}]
    assert codex_review_findings("No issues found.") == []


def test_codex_review_findings_titles_with_dashes_and_trailing_prose():
    text = ("- [P1] Leaks \u2014 secrets \u2014 /ws/a.py:3\n  body one\n\nOverall: patch is incorrect.\n")
    assert codex_review_findings(text, "/ws") == [
        {"file": "a.py", "line": 3, "end_line": 3, "severity": "major", "priority": "P1",
         "rule": "Leaks \u2014 secrets", "message": "body one"}]
    last = "- [P2] T \u2014 a.c:1\n  body\nOverall: bad."  # a non-indented line ends the body too
    assert codex_review_findings(last)[0]["message"] == "body"


def test_convert_answer_leaves_an_empty_answer_empty():
    # a failed or timed-out run must not read as a clean review (an empty findings block)
    assert convert_answer("codex-review", "") == ""
    assert convert_answer("codex-review", "  \n") == "  \n"


def test_convert_answer_maps_every_severity_and_separates_the_block(caplog):
    text = "\n".join(f"- [P{n}] t{n} \u2014 f{n}.c:{n}\n  b" for n in (0, 1, 2, 3, 9))
    out = convert_answer("codex-review", text)
    head, block = out.split("\n\n```json\n")
    assert head == text and block.endswith("\n```\n")
    assert [f["severity"] for f in json.loads(block.split("\n```")[0])] == ["blocker", "major", "minor", "minor", "minor"]
    assert not caplog.records
    with caplog.at_level("WARNING"):
        convert_answer("codex-review", "- [p1] lowercase \u2014 a.c:1\n  b\n- [P1] hyphen - a.c:2\n")
    assert "2 finding marker(s)" in caplog.text and "0 parsed" in caplog.text


def test_convert_answer_appends_a_last_json_block_and_keeps_the_text():
    out = convert_answer("codex-review", CODEX_REVIEW, "/ws")
    assert out.startswith(CODEX_REVIEW)
    block = out.rsplit("```json\n", 1)[1].split("\n```", 1)[0]
    assert [f["file"] for f in json.loads(block)] == ["cpp/x/scan.hpp", "src/f.cpp"]
    assert convert_answer(None, "as is") == "as is"
    with pytest.raises(ValueError, match="unknown answer format"):
        convert_answer("xml", "x")


FAKE_CODEX_REVIEW = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.160.0"); sys.exit(0)
assert args[:4] == ["exec", "review", "--commit", "HEAD"], args
text = ("One bug.\n\nReview comment:\n\n- [P1] add() subtracts — " + os.environ["FAKE_CONTAINER_CWD"]
        + "/src/calc.py:2-2\n  The change turns a + b into a - b.")
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}))
'''


def test_repo_run_grades_a_native_review_answer(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe = tmp_path / "bin" / "codex"
    write(exe.parent, exe.name, FAKE_CODEX_REVIEW)
    exe.chmod(0o755)
    write(exe.parent, "codex-code-mode-host", "")  # the profile's bin_companions
    raw = yaml.safe_load(Path(load_profile("codex-review").path).read_text())
    raw.update(env_allow=["PATH"], egress=["api.example.com"])
    profile = tmp_path / "codex-review.yaml"
    profile.write_text(yaml.safe_dump(raw), encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    out = tmp_path / "runs"
    args = build_parser().parse_args([
        "repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
        "--bin", str(exe), "--model", "gpt-x", "--batch", "b1", "--out", str(out),
        "--cache-root", str(out / "caches"), "--agent-network", "host", "--task", "review_add",
        "--arm", "guidance:raw"])
    args.func(args)
    rd = next((out / "b1").glob("review_add__*/"))
    answer = (rd / "answer.txt").read_text()
    findings = json.loads(answer.rsplit("```json\n", 1)[1].split("\n```", 1)[0])
    assert findings[0]["file"] == "src/calc.py" and findings[0]["severity"] == "major"
    result = json.loads((rd / "result.json").read_text())
    assert result["harness"] == "codex-review" and result["grade"]["score"] == 1.0
    agent = next(s for s in runner.specs if "review" in s.argv)
    assert "Review HEAD." not in " ".join(agent.argv)  # the task prompt is not passed


def _run_fake(tmp_path, monkeypatch, *, companion: bool):
    from repo_eval_fixture import PROFILE, fake_harness

    root, _ = make_repo(tmp_path)
    exe, _ = fake_harness(tmp_path)
    if companion:
        write(exe.parent, "fake-helper", "")
    profile = tmp_path / "fake-companion.yaml"
    profile.write_text(yaml.safe_dump({**PROFILE, "bin_companions": ["fake-helper"]}), encoding="utf-8")
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    args = build_parser().parse_args([
        "repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
        "--bin", str(exe), "--model", "m", "--batch", "b1", "--out", str(out), "--cache-root", str(out / "caches"),
        "--agent-network", "host", "--task", "fix_add", "--arm", "guidance:raw"])
    return runner, out, args


def test_bin_companions_are_mounted_beside_the_harness(tmp_path, monkeypatch):
    from dokimi.repos.container import HARNESS_DIR

    runner, out, args = _run_fake(tmp_path, monkeypatch, companion=True)
    args.func(args)
    agent = next(s for s in runner.specs if "-p" in s.argv)
    mounts = {m.container: m for m in agent.mounts}
    assert mounts[f"{HARNESS_DIR}/fake-helper"].read_only and mounts[f"{HARNESS_DIR}/fake-agent"].read_only
    assert next((out / "b1").glob("*/result.json")).is_file()


def test_a_missing_bin_companion_stops_before_any_agent_runs(tmp_path, monkeypatch, capsys):
    runner, out, args = _run_fake(tmp_path, monkeypatch, companion=False)
    with pytest.raises(SystemExit):
        args.func(args)
    assert "needs fake-helper beside its executable" in capsys.readouterr().err
    assert not any("-p" in s.argv for s in runner.specs) and not list(out.glob("b1/*/result.json"))
