# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``run_context.v1`` (docs/repo-evaluation-contract.md §4.9): what each CLI printed, nulls where it printed nothing."""

from __future__ import annotations

import hashlib
import json

import pytest

from dokimi.artifacts import stamp, validate_artifact
from dokimi.cli import build_parser
from dokimi.repos import run_context
from dokimi.repos.container import WORKSPACE
from dokimi.repos.events import convert
from dokimi.repos.grading import replaced_files
from dokimi.repos.harness_profile import load_profile
from dokimi.repos.transcript import Transcript
from repo_eval_fixture import FakeRunner, fake_harness, make_repo

CLAUDE_INIT = {
    "type": "system", "subtype": "init", "cwd": "/eval/run/workspace", "session_id": "s-1",
    "tools": ["Read", "Bash", "Edit"], "mcp_servers": [{"name": "docs", "status": "connected"}],
    "model": "claude-sonnet-x", "permissionMode": "bypassPermissions", "apiKeySource": "ANTHROPIC_API_KEY",
    "claude_code_version": "2.1.400", "output_style": "default", "agents": ["general-purpose"],
    "skills": ["lint-fix"], "plugins": [{"name": "cc-plugin-agents-md", "path": "/opt/plugins/agents-md"}],
    "per_turn_effort_active": "high",
}
CLAUDE_RESULT = {
    "type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "stop_reason": "end_turn",
    "terminal_reason": "completed", "result": "done", "permission_denials": [{"tool_name": "WebFetch",
                                                                              "tool_use_id": "w1", "tool_input": {}}],
    "modelUsage": {"claude-sonnet-x": {"inputTokens": 10, "outputTokens": 5, "costUSD": 0.2},
                   "claude-haiku-x": {"inputTokens": 2, "outputTokens": 1, "costUSD": 0.01}},
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


def _lines(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _claude_trace() -> str:
    refusal = {"type": "assistant", "message": {"model": "claude-sonnet-x", "stop_reason": "refusal",
                                                 "content": [{"type": "text", "text": "No."}]}}
    turn = {"type": "assistant", "message": {"model": "claude-sonnet-x", "stop_reason": "tool_use", "content": [
        {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "/eval/run/workspace/AGENTS.md"}}]}}
    return _lines(CLAUDE_INIT, turn, refusal, CLAUDE_RESULT)


def _claude_argv(tools: str = "all", present: frozenset[str] = frozenset()) -> list[str]:
    return load_profile("claude-code").argv(prompt="Fix it", model="claude-sonnet-x", tools=tools, max_turns=20,
                                            budget_usd=1.0, bin_path="/eval/harness/claude", present=present)


def _build(profile: str, raw: str, argv: list[str], *, harness_version: str | None = "9.9.9",
           config: dict[str, str] | None = None):
    prof = load_profile(profile)
    transcript = convert(prof.trace, raw, WORKSPACE)
    return run_context.build(prof, model="requested-m", harness_version=harness_version, argv=argv,
                             transcript=transcript, config_sha256=config or {})


def test_a_claude_trace_fills_each_field_from_what_the_cli_printed():
    record = _build("claude-code", _claude_trace(), _claude_argv(present=frozenset({".mcp.json"})))

    assert record["harness"] == "claude-code"
    assert record["harness_version"] == "2.1.400"  # the init record's own version, over the probe's
    assert record["model_requested"] == "requested-m"
    assert record["model_served"] == {"init": "claude-sonnet-x", "usage": ["claude-haiku-x", "claude-sonnet-x"]}
    assert record["effort"] == "high" and record["thinking"] is None
    assert record["credential_kind"] == "ANTHROPIC_API_KEY"
    assert record["permission_mode"] == {"mode": "bypassPermissions", "sandbox": None, "approval": None}
    assert record["tools"] == {"allowed": ["Read", "Bash", "Edit"], "disallowed": ["WebFetch", "WebSearch"]}
    assert record["extensions"] == {"plugins": ["cc-plugin-agents-md"], "skills": ["lint-fix"], "mcp_servers": ["docs"],
                                    "agents": ["general-purpose"], "output_style": "default"}
    assert record["outcome"] == {"stop_reason": "end_turn", "terminal_reason": "completed", "num_turns": 3,
                                 "permission_denials": 1, "safety_stops": 1}


def test_claude_allowed_tools_come_from_the_flag_when_the_init_record_does_not_list_them():
    init = {k: v for k, v in CLAUDE_INIT.items() if k != "tools"}
    record = _build("claude-code", _lines(init, CLAUDE_RESULT), _claude_argv(tools="read"))

    assert record["tools"] == {"allowed": ["Read"], "disallowed": None}  # no --disallowedTools in the read mode


def test_the_refused_safety_stop_is_counted_only_for_the_main_thread():
    subagent = {"type": "assistant", "parent_tool_use_id": "t9",
                "message": {"model": "claude-haiku-x", "stop_reason": "refusal", "content": []}}
    record = _build("claude-code", _lines(CLAUDE_INIT, subagent, CLAUDE_RESULT), _claude_argv())

    assert record["outcome"]["safety_stops"] is None  # no main-thread reply carried a stop reason


CODEX_LINES = _lines(
    {"type": "thread.started", "thread_id": "th-1"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "Fixed."}},
    {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 7}},
)


def test_a_codex_trace_fills_what_codex_exposes_and_sandboxes_in_place_of_tool_lists():
    argv = load_profile("codex").argv(prompt="x", model="gpt-x", tools="read", bin_path="/eval/harness/codex")
    record = _build("codex", CODEX_LINES, argv, harness_version="codex-cli 0.160.0")

    assert record["harness"] == "codex" and record["harness_version"] == "codex-cli 0.160.0"
    assert record["model_served"] == {"init": None, "usage": None}  # the turn record names no model
    assert record["permission_mode"] == {"mode": None, "sandbox": "read-only", "approval": None}
    assert record["tools"] == {"allowed": None, "disallowed": None}
    assert record["extensions"] == {"plugins": None, "skills": None, "mcp_servers": None, "agents": None,
                                    "output_style": None}
    assert record["credential_kind"] is None and record["effort"] is None
    assert record["outcome"] == {"stop_reason": None, "terminal_reason": "turn.completed", "num_turns": 1,
                                 "permission_denials": None, "safety_stops": None}


def test_a_codex_run_bypassing_its_sandbox_and_set_to_a_reasoning_effort_says_so():
    argv = ["codex", "exec", "-c", "model_reasoning_effort=high", "--dangerously-bypass-approvals-and-sandbox",
            "--json", "--", "prompt"]
    record = _build("codex", CODEX_LINES, argv)

    assert record["effort"] == "high"
    assert record["permission_mode"] == {"mode": "dangerously-bypass-approvals-and-sandbox", "sandbox": None,
                                         "approval": None}


def test_a_codex_turn_that_failed_is_a_terminal_reason_of_its_own_record():
    failed = _lines({"type": "thread.started", "thread_id": "t"}, {"type": "turn.failed", "error": {"message": "x"}})
    record = _build("codex", failed, [], harness_version=None)

    assert record["outcome"]["terminal_reason"] == "turn.failed"
    assert record["harness_version"] is None


def test_a_sparse_trace_gives_nulls_and_nothing_inferred():
    """A result record with no init record, and no argv, names nothing but the harness and the model dokimi asked for."""
    record = _build("claude-code", _lines({"type": "result"}), [], harness_version=None)

    assert record == {
        "harness": "claude-code", "harness_version": None, "model_requested": "requested-m",
        "model_served": {"init": None, "usage": None}, "effort": None, "thinking": None, "credential_kind": None,
        "permission_mode": {"mode": None, "sandbox": None, "approval": None},
        "tools": {"allowed": None, "disallowed": None},
        "extensions": {"plugins": None, "skills": None, "mcp_servers": None, "agents": None, "output_style": None},
        "outcome": {"stop_reason": None, "terminal_reason": None, "num_turns": None, "permission_denials": None,
                    "safety_stops": None},
        "config_sha256": {},
    }


def test_an_empty_stdout_is_still_a_valid_record():
    record = _build("codex", "", [], harness_version=None)

    validate_artifact("run_context", stamp("run_context", record))
    assert record["outcome"]["num_turns"] is None and record["model_served"] == {"init": None, "usage": None}


def test_a_copilot_session_names_its_model_best_effort():
    raw = _lines({"type": "session.start", "timestamp": "2026-11-14T10:00:00Z",
                  "data": {"sessionId": "c-1", "copilotVersion": "1.0.91", "selectedModel": "gpt-copilot"}},
                 {"type": "result", "exitCode": 0})
    record = _build("copilot", raw, [], harness_version="1.0.91")

    assert record["model_served"] == {"init": "gpt-copilot", "usage": None}
    assert record["harness_version"] == "1.0.91"
    assert record["outcome"]["terminal_reason"] is None  # Copilot's result carries no reason


@pytest.mark.parametrize("profile, raw, argv", [
    ("claude-code", _claude_trace(), _claude_argv()),
    ("codex", CODEX_LINES, ["codex", "exec", "--sandbox", "read-only"]),
    ("claude-code", "", []),
])
def test_every_record_validates_against_its_schema(profile, raw, argv):
    record = stamp("run_context", _build(profile, raw, argv))

    validate_artifact("run_context", record)
    assert record["schema_version"] == "run_context.v1"


def test_config_files_are_digests_only_and_a_link_is_not_read(tmp_path):
    prof = load_profile("claude-code")
    run_dir = tmp_path / "run"
    (run_dir / "config").mkdir(parents=True)
    (run_dir / "workspace").mkdir()
    (run_dir / "config" / "settings.json").write_text('{"hooks": {}}')
    (run_dir / "workspace" / ".mcp.json").write_text('{"mcpServers": {"docs": {}}}')
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (run_dir / "workspace" / "CLAUDE.md").symlink_to(outside)

    digests = run_context.config_digests(run_dir, prof, [".mcp.json", "CLAUDE.md"])

    assert digests == {
        ".mcp.json": hashlib.sha256((run_dir / "workspace" / ".mcp.json").read_bytes()).hexdigest(),
        "settings.json": hashlib.sha256((run_dir / "config" / "settings.json").read_bytes()).hexdigest(),
    }


def test_the_saved_record_names_no_host_path(tmp_path):
    run_dir = tmp_path / "cell"
    run_dir.mkdir()
    errors: list[str] = []
    record = _build("claude-code", _claude_trace(), _claude_argv(), config={"settings.json": "a" * 64})

    run_context.save(run_dir, record, errors)

    text = (run_dir / "run_context.json").read_text(encoding="utf-8")
    assert errors == [] and str(tmp_path) not in text
    assert json.loads(text)["schema_version"] == "run_context.v1"


def test_a_record_that_cannot_be_saved_is_a_trace_error_not_a_raise(tmp_path):
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    errors: list[str] = []

    run_context.save(blocker, _build("codex", CODEX_LINES, []), errors)

    assert len(errors) == 1 and errors[0].startswith("run_context.json not written")
    assert str(tmp_path) not in errors[0]


@pytest.fixture
def cell(tmp_path, monkeypatch):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    argv = ["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
            "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out),
            "--cache-root", str(out / "caches"), "--agent-network", "host", "--arm", "guidance:raw",
            "--task", "fix_add"]
    args = build_parser().parse_args(argv)
    args.func(args)
    (rd,) = (out / "b1").glob("*/result.json")
    return rd.parent, root


def test_a_repo_run_cell_writes_run_context_beside_its_events(cell):
    rd, root = cell
    record = json.loads((rd / "run_context.json").read_text(encoding="utf-8"))

    assert (rd / "events.jsonl").is_file()
    assert record["schema_version"] == "run_context.v1" and record["harness"] == "fake"
    assert record["harness_version"] == "1.2.3" and record["model_requested"] == "m-1"
    assert record["model_served"]["init"] == "m-1"
    assert str(root) not in (rd / "run_context.json").read_text(encoding="utf-8")


def test_a_closing_record_without_terminal_reason_gives_none_not_its_subtype():
    # the schema's description: terminal_reason is the closing record's own field; its subtype is another one
    transcript = Transcript(final_record={"type": "result", "subtype": "error_max_turns"})
    record = run_context.build(load_profile("claude-code"), model="m", harness_version=None, argv=[],
                               transcript=transcript, config_sha256={})
    assert record["outcome"]["terminal_reason"] is None


def test_a_cli_version_that_is_not_a_string_falls_back_to_the_probed_one(tmp_path):
    transcript = Transcript(start_record={"type": "system", "subtype": "init"}, harness_version=2)
    record = run_context.build(load_profile("claude-code"), model="m", harness_version="2.1.400", argv=[],
                               transcript=transcript, config_sha256={})
    errors: list[str] = []
    run_context.save(tmp_path, record, errors)
    assert record["harness_version"] == "2.1.400" and errors == []
    assert (tmp_path / run_context.RUN_CONTEXT_FILE).is_file()


def test_a_run_context_file_replaced_by_a_link_is_flagged_as_a_host_file(tmp_path):
    for name in ("events.jsonl", run_context.RUN_CONTEXT_FILE):
        (tmp_path / name).symlink_to("/nonexistent/host-target")
    assert sorted(replaced_files(tmp_path)) == ["events.jsonl", run_context.RUN_CONTEXT_FILE]
