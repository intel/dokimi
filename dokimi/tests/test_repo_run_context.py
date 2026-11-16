# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``run_context.v1`` (docs/repo-evaluation-contract.md §4.9): what each CLI printed, nulls where it printed nothing."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from dokimi.artifacts import ArtifactValidationError, stamp, validate_artifact
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


def test_a_codex_approval_flag_is_recorded_as_the_approval_mode():
    argv = ["codex", "exec", "--ask-for-approval", "never", "--", "prompt"]
    record = _build("codex", CODEX_LINES, argv)

    assert record["permission_mode"] == {"mode": None, "sandbox": None, "approval": "never"}


def test_a_codex_run_bypassing_its_sandbox_and_set_to_a_reasoning_effort_says_so():
    argv = ["codex", "exec", "-c", "model_reasoning_effort=high", "--dangerously-bypass-approvals-and-sandbox",
            "--json", "--", "prompt"]
    record = _build("codex", CODEX_LINES, argv)

    assert record["effort"] == "high"
    assert record["permission_mode"] == {"mode": "dangerously-bypass-approvals-and-sandbox", "sandbox": None,
                                         "approval": None}


def test_a_flag_given_as_name_equals_value_is_read_and_a_prompt_after_the_separator_is_not():
    argv = ["codex", "exec", "--sandbox=read-only", "--", "--dangerously-bypass-approvals-and-sandbox"]
    record = _build("codex", CODEX_LINES, argv)

    assert record["permission_mode"] == {"mode": None, "sandbox": "read-only", "approval": None}


def test_a_copilot_prompt_naming_a_bypass_flag_is_a_value_and_not_a_permission():
    argv = load_profile("copilot").argv(prompt="--dangerously-bypass-approvals-and-sandbox", model="gpt-x",
                                        tools="read", bin_path="/eval/harness/copilot")
    record = _build("codex", CODEX_LINES, argv)

    assert record["permission_mode"] == {"mode": "allow-all-tools", "sandbox": None, "approval": None}


def test_a_config_key_that_only_starts_with_the_effort_key_is_not_the_effort():
    argv = ["codex", "exec", "-c", "model_reasoning_effort_summary=auto", "-c", "model_reasoning_effort=high"]

    assert _build("codex", CODEX_LINES, argv)["effort"] == "high"


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


def _run_cell(tmp_path, monkeypatch, *extra: str):
    root, _ = make_repo(tmp_path)
    exe, profile = fake_harness(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr("dokimi.repos.container.DockerRunner", lambda *a, **k: runner)
    monkeypatch.setenv("FAKE_ACTION", "fix")
    out = tmp_path / "runs"
    argv = ["repo", "run", f"{root}@HEAD", "--manifest", ".agent-eval/repo-eval.yaml", "--harness", str(profile),
            "--bin", str(exe), "--model", "m-1", "--batch", "b1", "--out", str(out),
            "--cache-root", str(out / "caches"), "--agent-network", "host", "--arm", "guidance:raw",
            "--task", "fix_add", *extra]
    args = build_parser().parse_args(argv)
    args.func(args)
    (rd,) = (out / "b1").glob("*/result.json")
    return rd.parent, root


@pytest.fixture
def cell(tmp_path, monkeypatch):
    return _run_cell(tmp_path, monkeypatch)


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


def test_a_repo_run_cell_passes_its_argv_and_config_digests_to_the_record(tmp_path, monkeypatch):
    rd, _ = _run_cell(tmp_path, monkeypatch, "--tools", "read")
    record = json.loads((rd / "run_context.json").read_text(encoding="utf-8"))

    assert record["tools"]["allowed"] == ["Read"]
    assert list(record["config_sha256"]) == ["settings.json"]


def test_a_record_the_schema_rejects_is_a_trace_note_and_the_cell_still_finishes(tmp_path, monkeypatch):
    def reject(*_a, **_k):
        raise ArtifactValidationError("run_context artifact validation failed:\n  config_sha256: bad key")

    monkeypatch.setattr("dokimi.repos.run_context.save_artifact", reject)
    rd, _ = _run_cell(tmp_path, monkeypatch)
    result = json.loads((rd / "result.json").read_text(encoding="utf-8"))

    assert any(e.startswith("run_context.json not written") and "bad key" in e for e in result["agent"]["trace_errors"])


def test_a_nested_workspace_config_is_keyed_by_its_base_name_and_the_record_saves(tmp_path):
    prof = load_profile("claude-code")
    run_dir = tmp_path / "run"
    (run_dir / "config").mkdir(parents=True)
    (run_dir / "workspace" / ".config").mkdir(parents=True)
    (run_dir / "workspace" / ".config" / "agent.json").write_text("{}")

    digests = run_context.config_digests(run_dir, prof, [".config/agent.json"])
    errors: list[str] = []
    run_context.save(tmp_path, _build("claude-code", _claude_trace(), [], config=digests), errors)

    assert list(digests) == ["agent.json"] and errors == []


def test_a_file_reached_through_a_linked_directory_is_not_digested(tmp_path):
    prof = load_profile("claude-code")
    host = tmp_path / "host"
    host.mkdir()
    (host / "agent.json").write_text("host file")
    (tmp_path / "config").mkdir()
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / ".config").symlink_to(host)

    assert run_context.config_digests(tmp_path, prof, [".config/agent.json"]) == {}


def test_a_workspace_file_name_outside_the_key_alphabet_still_saves_the_record(tmp_path):
    prof = dataclasses.replace(load_profile("claude-code"), config_files={"settings.json": "host configuration"})
    (tmp_path / "config").mkdir()
    (tmp_path / "workspace" / "config dir").mkdir(parents=True)
    (tmp_path / "config" / "settings.json").write_bytes(b"1")
    (tmp_path / "workspace" / "config dir" / "settings.json").write_bytes(b"2")
    (tmp_path / "workspace" / "agent config.json").write_bytes(b"3")

    digests = run_context.config_digests(tmp_path, prof, ["config dir/settings.json", "agent config.json"])
    errors: list[str] = []
    run_context.save(tmp_path, _build("claude-code", _claude_trace(), [], config=digests), errors)

    assert sorted(digests) == ["agent_config.json", "settings.json", "workspace.config_dir.settings.json"]
    assert errors == []


def test_a_workspace_file_does_not_hide_the_config_digest_of_the_same_name(tmp_path):
    prof = dataclasses.replace(load_profile("claude-code"), config_files={"settings.json": "host configuration"})
    (tmp_path / "config").mkdir()
    (tmp_path / "workspace").mkdir()
    (tmp_path / "config" / "settings.json").write_bytes(b"config bytes")
    (tmp_path / "workspace" / "settings.json").write_bytes(b"workspace bytes")

    digests = run_context.config_digests(tmp_path, prof, frozenset({"settings.json"}))

    assert digests == {"settings.json": hashlib.sha256(b"config bytes").hexdigest(),
                       "workspace.settings.json": hashlib.sha256(b"workspace bytes").hexdigest()}


def test_a_prefixed_workspace_key_does_not_overwrite_a_config_file_of_that_name(tmp_path):
    prof = dataclasses.replace(load_profile("claude-code"),
                               config_files={"settings.json": "a", "workspace.settings.json": "b"})
    (tmp_path / "config").mkdir()
    (tmp_path / "workspace").mkdir()
    for name, data in (("config/settings.json", b"1"), ("config/workspace.settings.json", b"2"),
                       ("workspace/settings.json", b"3")):
        (tmp_path / name).write_bytes(data)

    digests = run_context.config_digests(tmp_path, prof, ["settings.json"])

    assert sorted(digests.values()) == sorted(hashlib.sha256(d).hexdigest() for d in (b"1", b"2", b"3"))
    assert digests["workspace.settings.json"] == hashlib.sha256(b"2").hexdigest()


def test_a_config_file_that_cannot_be_read_is_left_out_and_does_not_raise(tmp_path, monkeypatch):
    prof = load_profile("claude-code")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / ".mcp.json").write_text("{}")
    (tmp_path / "workspace" / "other.json").write_text("{}")
    real = Path.read_bytes

    def read(self):
        if self.name == ".mcp.json":
            raise PermissionError(13, "denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", read)

    assert list(run_context.config_digests(tmp_path, prof, [".mcp.json", "other.json"])) == ["other.json"]


@pytest.mark.parametrize("tools, allowed", [("read", ["view", "grep", "glob"]), ("none", [])])
def test_a_copilot_tool_restriction_is_recorded_from_its_available_tools_flag(tools, allowed):
    argv = load_profile("copilot").argv(prompt="x", model="gpt-x", tools=tools, bin_path="/eval/harness/copilot")

    assert _build("copilot", "", argv)["tools"]["allowed"] == allowed


def test_one_reply_split_over_records_is_one_safety_stop():
    def part(stop):
        return {"type": "assistant", "message": {"id": "msg_1", "model": "claude-sonnet-x", "stop_reason": stop,
                                                 "content": []}}
    other = {"type": "assistant", "message": {"id": "msg_2", "stop_reason": "refusal", "content": []}}

    record = _build("claude-code", _lines(CLAUDE_INIT, part("refusal"), part("refusal"), other, CLAUDE_RESULT),
                    _claude_argv())

    assert record["outcome"]["safety_stops"] == 2


def test_the_last_stop_reason_of_a_split_reply_is_the_one_counted():
    def part(stop):
        return {"type": "assistant", "message": {"id": "msg_1", "stop_reason": stop, "content": []}}

    record = _build("claude-code", _lines(CLAUDE_INIT, part("refusal"), part("end_turn"), CLAUDE_RESULT),
                    _claude_argv())

    assert record["outcome"]["safety_stops"] == 0


def test_a_quoted_codex_effort_override_is_recorded_without_its_quotes():
    argv = ["codex", "exec", "-c", 'model_reasoning_effort="high"', "--json", "--", "task"]

    assert _build("codex", CODEX_LINES, argv)["effort"] == "high"


@pytest.mark.parametrize(
    "argv, effort",
    [
        (["codex", "exec", "-c", "model_reasoning_effort=low", "-c", "model_reasoning_effort=high", "--", "t"], "high"),
        (["codex", "exec", "-c", "model_reasoning_effort=high", "--config", "model_reasoning_effort=low", "--", "t"], "low"),
        (["codex", "exec", "--config", "model_reasoning_effort=low", "-c", "model_reasoning_effort=high", "--", "t"], "high"),
    ],
)
def test_the_last_codex_effort_override_on_the_line_is_the_one_applied(argv, effort):
    assert _build("codex", CODEX_LINES, argv)["effort"] == effort
