# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Probe events (repos/probes.py, contract §4.1): a command that asks the host for its facts gets a ``probe`` event."""

from __future__ import annotations

import json

import pytest

from dokimi.repos.events import convert
from dokimi.repos.manifest import ManifestError, parse_manifest
from dokimi.repos.probes import DEFAULT_PROBES, load_probe_file, probes_for, validate_config, with_probes

WS = "/ws"


def _claude_call(tool_id: str, command: str, t: str, name: str = "Bash", agent: str | None = None) -> dict:
    rec = {"type": "assistant", "timestamp": t, "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": {"command": command}}]}}
    return {**rec, "parent_tool_use_id": agent} if agent else rec


def _claude_result(tool_id: str, text: str = "ok", t: str = "2026-01-01T00:00:06Z") -> dict:
    return {"type": "user", "timestamp": t, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": False, "content": text}]}}


def _claude(*lines: dict) -> list[dict]:
    """The command and probe events of a Claude Code stream whose first line starts the clock."""
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    raw = "\n".join(json.dumps(x) for x in (first, *lines))
    return with_probes(convert("claude-stream-json", raw, WS).events, probes_for())


def _commands(events: list[dict]) -> list[dict]:
    return [e for e in events if e["kind"] in ("command", "probe")]


def _probe(target: str, pattern: str, text: str, t: float | None, rc: int | None) -> dict:
    return {"t": t, "kind": "probe", "target": target, "pattern": pattern, "text": text, "rc": rc}


def test_nproc_then_a_threaded_solver_gives_the_probe_after_its_command():
    events = _claude(
        _claude_call("a", "nproc", "2026-01-01T00:00:05Z"),
        _claude_result("a", "8", "2026-01-01T00:00:05Z"),
        _claude_call("b", "python solve.py --threads 8", "2026-01-01T00:00:09Z"),
        _claude_result("b", "done", "2026-01-01T00:00:20Z"),
    )
    assert events == [
        {"t": 5.0, "kind": "command", "text": "nproc", "rc": 0, "output": "8", "output_truncated": False},
        _probe("cpu", "nproc", "nproc", 5.0, 0),
        {"t": 9.0, "kind": "command", "text": "python solve.py --threads 8", "rc": 0, "output": "done",
         "output_truncated": False},
    ]


def test_a_grep_of_the_word_is_no_probe():
    events = _claude(_claude_call("a", "grep nproc README", "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["kind"] for e in events] == ["command"]


def test_a_probe_is_matched_on_the_program_not_on_an_argument_of_another_one():
    events = _claude(_claude_call("a", "pip install nproc && echo lscpu", "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["kind"] for e in events] == ["command"]


def test_an_agent_that_never_probes_gives_no_probe_event():
    events = _claude(_claude_call("a", "ls -la", "2026-01-01T00:00:05Z"), _claude_result("a"),
                     _claude_call("b", "git status", "2026-01-01T00:00:06Z"), _claude_result("b"))
    assert all(e["kind"] != "probe" for e in events)


def test_one_command_gives_the_command_and_every_probe_it_runs():
    events = _claude(_claude_call("a", "grep -c processor /proc/cpuinfo | cat && nproc", "2026-01-01T00:00:05Z"),
                     _claude_result("a", "4"))
    assert _commands(events)[1:] == [
        _probe("cpu", "/proc/cpuinfo", events[0]["text"], 5.0, 0),
        _probe("cpu", "nproc", events[0]["text"], 5.0, 0),
    ]
    assert events[0]["kind"] == "command"


def test_a_probe_in_a_substitution_is_run_by_the_command():
    events = _claude(_claude_call("a", "make -j$(nproc)", "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert _commands(events)[1] == _probe("cpu", "nproc", "make -j$(nproc)", 5.0, 0)


@pytest.mark.parametrize(("command", "pattern"), [
    ("lscpu", "lscpu"),
    ("time nproc", "nproc"),
    ("uname -a", "uname"),
    ("free -h", "free"),
    ("nvidia-smi --query-gpu=name --format=csv", "nvidia-smi"),
    ("/usr/bin/nvidia-smi", "nvidia-smi"),
    ("head -n 3 /etc/os-release", "/etc/os-release"),
    ("cat \"/proc/meminfo\"", "/proc/meminfo"),
    ("pip list --format=json", "pip list"),
    ("python -m pip freeze", "pip freeze"),
    ("pip3 show numpy", "pip show"),
    ("conda list -n base", "conda list"),
    ("printenv PATH", "printenv"),
    ("env", "env"),
    ("sysctl -a | grep machdep.cpu", "sysctl"),
    ("sysctl -n machdep.cpu.brand_string", "sysctl"),
    ("sysctl -n hw.ncpu", "sysctl"),
    ("sysctl hw.memsize", "sysctl"),
])
def test_default_command_patterns_match_the_program_that_runs(command, pattern):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["pattern"] for e in events if e["kind"] == "probe"] == [pattern]


@pytest.mark.parametrize(("command", "target"), [
    ('python -c "import os; print(os.cpu_count())"', "cpu"),
    ("python3 -c 'import torch; print(torch.cuda.is_available())'", "gpu"),
    ('python -c "import platform; print(platform.processor())"', "cpu"),
])
def test_a_python_one_liner_that_calls_a_probe_is_a_probe(command, target):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["target"] for e in events if e["kind"] == "probe"] == [target]


@pytest.mark.parametrize("command", [
    'echo "os.cpu_count()"',
    "python -c 'print(1)' # os.cpu_count()",
    'python -c "import os; os.makedirs(\'x\')"',
    "cat README | grep 'nproc'",
    "git diff | grep nproc",
])
def test_text_that_is_not_run_code_is_no_probe(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


@pytest.mark.parametrize("command", ["grep /proc/cpuinfo README", "grep -e /proc/meminfo README"])
def test_a_search_expression_that_looks_like_a_path_is_no_probe(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


@pytest.mark.parametrize("command", ["grep -m 1 /proc/cpuinfo README", "cat > /proc/cpuinfo", "cat 2>> /proc/cpuinfo"])
def test_an_option_value_or_a_redirection_target_is_no_file_the_command_reads(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


@pytest.mark.parametrize(("command", "pattern"), [
    ("sudo -E lscpu", "lscpu"),
    ("nice -n 5 nproc", "nproc"),
    ("sudo -u root nproc", "nproc"),
])
def test_a_wrappers_options_do_not_hide_the_program_it_runs(command, pattern):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["pattern"] for e in events if e["kind"] == "probe"] == [pattern]


@pytest.mark.parametrize("command", ["if true; then nproc; fi", "for x in 1; do nproc; done"])
def test_a_command_in_the_body_of_a_conditional_or_loop_is_a_probe(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["pattern"] for e in events if e["kind"] == "probe"] == ["nproc"]


@pytest.mark.parametrize("command", [
    "echo '$(nproc)'",
    "echo 'sysctl -n hw.ncpu'",
])
def test_quoted_text_is_no_probe(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


def test_a_call_inside_a_python_string_is_no_probe():
    events = _claude(_claude_call("a", "python -c 'print(\"os.cpu_count()\")'", "2026-01-01T00:00:05Z"),
                     _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


@pytest.mark.parametrize(("command", "pattern"), [
    ("make -j$(python -c 'import os; print(os.cpu_count())')", "py:os.cpu_count"),
    ("echo $(echo $(nproc))", "nproc"),
    ("bash -c '>/dev/null; nproc'", "nproc"),
    ("grep -m 1 >/tmp/cpu.txt processor /proc/cpuinfo", "/proc/cpuinfo"),
    ("grep '>' /proc/cpuinfo", "/proc/cpuinfo"),
    ("python -c'import os;print(os.cpu_count())'", "py:os.cpu_count"),
    ("python -I -m pip list", "pip list"),
    ("< /proc/cpuinfo cat", "/proc/cpuinfo"),
    ("time bash -c 'nproc'", "nproc"),
    ("FOO=bar bash -c 'nproc'", "nproc"),
    ("bash --noprofile -c 'nproc'", "nproc"),
    ("! nproc", "nproc"),
    ('echo "$(printf "(" ; nproc)"', "nproc"),
    ("python -W ignore -c 'import os; os.cpu_count()'", "py:os.cpu_count"),
    ("grep --file=/proc/cpuinfo README", "/proc/cpuinfo"),
    ("2>/dev/null nproc", "nproc"),
    ("C:\\Windows\\System32\\nvidia-smi.exe --query-gpu=name", "nvidia-smi"),
    ("/mnt/c/Program\\ Files/NVIDIA\\ Corporation/NVSMI/nvidia-smi.exe -L", "nvidia-smi"),
])
def test_a_probe_the_shell_runs_in_another_form_is_a_probe(command, pattern):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["pattern"] for e in events if e["kind"] == "probe"] == [pattern]


@pytest.mark.parametrize("command", [
    "python echo_args.py -c 'os.cpu_count()'", "rg /proc/cpuinfo README", "command -v nproc",
    "python -- -c 'import os; os.cpu_count()'", "grep 2>/dev/null /proc/cpuinfo README",
])
def test_a_script_argument_or_a_search_expression_is_no_probe(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


def test_a_probe_file_nested_too_deep_is_a_value_error_that_names_it(tmp_path):
    path = tmp_path / "probes.yaml"
    path.write_text("[" * 2000, encoding="utf-8")
    with pytest.raises(ValueError, match="probes.yaml"):
        load_probe_file(str(path))


def test_a_windows_powershell_probe_is_matched_case_blind_and_through_parentheses():
    events = _claude(
        _claude_call("a", "Get-CimInstance -ClassName Win32_Processor | Select-Object NumberOfCores",
                     "2026-01-01T00:00:05Z", name="PowerShell"),
        _claude_result("a", "8"),
        _claude_call("b", "(get-ciminstance win32_processor).NumberOfCores", "2026-01-01T00:00:07Z",
                     name="PowerShell"),
        _claude_result("b", "8", "2026-01-01T00:00:07Z"),
        _claude_call("c", "Get-CimInstance Win32_ComputerSystem", "2026-01-01T00:00:08Z", name="PowerShell"),
        _claude_result("c", "", "2026-01-01T00:00:08Z"),
    )
    assert [(e["kind"], e.get("target")) for e in events] == [
        ("command", None), ("probe", "cpu"), ("command", None), ("probe", "cpu"), ("command", None)]


def test_a_subagents_probe_carries_its_agent():
    events = _claude(_claude_call("a", "nproc", "2026-01-01T00:00:05Z", agent="task-1"), _claude_result("a"))
    assert events[1] == {**_probe("cpu", "nproc", "nproc", 5.0, 0), "agent": "task-1"}


def _codex(*items: dict) -> list[dict]:
    raw = "\n".join(json.dumps(x) for x in items)
    return with_probes(convert("codex-json", raw, WS).events, probes_for())


def test_codex_unwraps_the_shell_before_it_matches_a_probe():
    events = _codex(
        {"type": "item.completed", "item": {"type": "command_execution", "command": "/usr/bin/bash -lc 'nproc'",
                                            "aggregated_output": "4\n", "exit_code": 0}},
        {"type": "item.completed", "item": {"type": "command_execution",
                                            "command": "/usr/bin/bash -lc 'nvidia-smi'",
                                            "aggregated_output": "", "exit_code": 127}},
    )
    assert events == [
        {"t": None, "kind": "command", "text": "nproc", "rc": 0, "output": "4\n", "output_truncated": False},
        _probe("cpu", "nproc", "nproc", None, 0),
        {"t": None, "kind": "command", "text": "nvidia-smi", "rc": 127},
        _probe("gpu", "nvidia-smi", "nvidia-smi", None, 127),
    ]


def _copilot(*records: dict) -> list[dict]:
    raw = "\n".join(json.dumps(x) for x in records)
    return with_probes(convert("copilot-json", raw, WS).events, probes_for())


def test_copilot_bash_and_powershell_probes_give_probe_events():
    events = _copilot(
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:01Z",
         "data": {"toolCallId": "a", "toolName": "bash", "arguments": {"command": "nvidia-smi"}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "a", "success": True,
                                                     "shellExecution": {"exitCode": 0},
                                                     "result": {"content": "GPU 0"}}},
        {"type": "tool.execution_start", "timestamp": "2026-01-01T00:00:02Z",
         "data": {"toolCallId": "b", "toolName": "powershell",
                  "arguments": {"command": "Get-CimInstance Win32_Processor"}}},
        {"type": "tool.execution_complete", "data": {"toolCallId": "b", "success": True,
                                                     "shellExecution": {"exitCode": 0}, "result": {"content": "x"}}},
    )
    assert [(e["kind"], e.get("target"), e.get("pattern")) for e in events] == [
        ("command", None, None), ("probe", "gpu", "nvidia-smi"),
        ("command", None, None), ("probe", "cpu", "Get-CimInstance Win32_Processor"),
    ]


def test_a_manifest_replaces_a_target_and_an_empty_list_turns_it_off():
    probes = probes_for({"cpu": ["lscpu"], "env": []})
    assert [p.text for p in probes if p.target == "cpu"] == ["lscpu"]
    assert all(p.target != "env" for p in probes)
    assert {p.text for p in probes if p.target == "gpu"} == set(DEFAULT_PROBES["gpu"])


def test_a_run_layer_replaces_the_manifest_layer():
    manifest = {"cpu": ["lscpu"]}
    run = {"cpu": ["nproc"]}
    assert [p.text for p in probes_for(manifest, run) if p.target == "cpu"] == ["nproc"]


def test_no_probes_leaves_the_events_as_they_are():
    events = [{"t": 1.0, "kind": "command", "text": "nproc", "rc": 0}]
    assert with_probes(events, ()) == events


def test_probe_events_go_to_a_copy_so_the_trace_events_the_counters_read_are_unchanged():
    events = [{"t": 1.0, "kind": "command", "text": "nproc", "rc": 0}]
    out = with_probes(events, probes_for())
    assert [e["kind"] for e in out] == ["command", "probe"]
    assert events == [{"t": 1.0, "kind": "command", "text": "nproc", "rc": 0}]


@pytest.mark.parametrize(("raw", "message"), [
    ({"gpus": ["nvidia-smi"]}, "unknown target 'gpus'"),
    ({"cpu": "nproc"}, "cpu must be a list"),
    ({"cpu": ["  "]}, "cpu must be a list"),
    ({"cpu": ["py:os.cpu_count()"]}, "dotted Python name"),
    ({"cpu": ['nproc "']}, "No closing quotation"),
    ({"cpu": ["|"]}, "names a program"),
    ({"cpu": ["nproc;lscpu"]}, "separate commands"),
    ({"cpu": ["/proc/cpu info"]}, "one word with no spaces"),
    (["cpu"], "expected a mapping"),
])
def test_a_bad_pattern_config_is_refused_with_what_is_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        validate_config(raw, "probes")


def test_a_run_probe_file_is_yaml_or_json_and_checked(tmp_path):
    good = tmp_path / "probes.yaml"
    good.write_text("cpu: [lscpu]\nnetwork: [curl]\n", encoding="utf-8")
    assert load_probe_file(str(good)) == {"cpu": ("lscpu",), "network": ("curl",)}
    bad = tmp_path / "bad.json"
    bad.write_text('{"cpu": ["nproc"', encoding="utf-8")
    with pytest.raises(ValueError, match="--probes .*bad.json"):
        load_probe_file(str(bad))


def test_a_manifest_probes_key_is_read_and_a_bad_one_is_a_manifest_error():
    base = {"schema": "repo_manifest.v1", "id": "x", "guidance": {"globs": ["AGENTS.md"]}}
    assert parse_manifest({**base, "probes": {"memory": ["free"]}}).probes == {"memory": ("free",)}
    with pytest.raises(ManifestError, match="probes: 'disk' is not one of"):
        parse_manifest({**base, "probes": {"disk": ["df"]}})


@pytest.mark.parametrize(("command", "pattern"), [
    ("python \\\n-c 'import os; print(os.cpu_count())'", "py:os.cpu_count"),
    ("npro\\\nc", "nproc"),
])
def test_a_backslash_newline_continues_the_command_it_is_in(command, pattern):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert any(e["kind"] == "probe" and e["pattern"] == pattern for e in events)


def test_a_quoted_parenthesis_inside_a_double_quoted_substitution_does_not_end_it():
    events = _claude(_claude_call("a", 'echo "$(printf \')\'; nproc)"', "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert any(e["kind"] == "probe" and e["pattern"] == "nproc" for e in events)


def test_a_search_option_value_is_no_operand_so_the_file_after_it_is_read():
    events = _claude(_claude_call("a", "grep -f /proc/cpuinfo README", "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert any(e["kind"] == "probe" and e["pattern"] == "/proc/cpuinfo" for e in events)


@pytest.mark.parametrize("command", ["rg -g '*.c' /proc/cpuinfo src", "rg -t c /proc/cpuinfo src"])
def test_a_glob_or_type_value_of_a_search_is_no_expression_or_file(command):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


def test_a_powershell_assignment_is_skipped_so_the_command_after_it_is_the_probe():
    command = "$cpu = Get-CimInstance Win32_Processor; $cpu.NumberOfCores"
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z", name="PowerShell"), _claude_result("a", "8"))
    assert [e["target"] for e in events if e["kind"] == "probe"] == ["cpu"]


@pytest.mark.parametrize(("command", "target"), [
    ("python -Ic 'import os; print(os.cpu_count())'", "cpu"),
    ("python -Im pip list", "packages"),
])
def test_python_flags_with_c_or_m_in_one_word_are_read_as_the_code_or_the_module(command, target):
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert [e["target"] for e in events if e["kind"] == "probe"] == [target]


def test_a_here_string_is_no_file_the_command_reads():
    events = _claude(_claude_call("a", "cat <<< /proc/cpuinfo", "2026-01-01T00:00:05Z"), _claude_result("a"))
    assert all(e["kind"] != "probe" for e in events)


def _matched(command: str, layer: dict | None = None) -> list[str]:
    raw = "\n".join(json.dumps(x) for x in (
        {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"},
        _claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a")))
    events = with_probes(convert("claude-stream-json", raw, WS).events, probes_for(layer))
    return [e["pattern"] for e in events if e["kind"] == "probe"]


@pytest.mark.parametrize(("command", "pattern"), [
    ("timeout 5 nproc", "nproc"),
    ("echo | xargs -n 1 nproc", "nproc"),
    ("uv run python -c 'import os; print(os.cpu_count())'", "py:os.cpu_count"),
    ("bash -o pipefail -c nproc", "nproc"),
    ("pwsh -Command Get-CimInstance Win32_Processor", "Get-CimInstance Win32_Processor"),
    ("conda run -n x pip list", "pip list"),
])
def test_a_common_launcher_does_not_hide_the_probe_it_runs(command, pattern):
    assert _matched(command) == [pattern]


@pytest.mark.parametrize(("command", "pattern"), [
    ("env PYTHONPATH=. pytest -q", None),
    ("env FOO=1 nproc", "nproc"),
    ("env -u HOME nproc", "nproc"),
    ("env", "env"),
])
def test_env_runs_the_program_after_its_assignments_and_is_a_probe_only_alone(command, pattern):
    assert _matched(command) == ([] if pattern is None else [pattern])


def test_a_conda_run_is_no_conda_list_probe():
    assert "conda list" not in _matched("conda run -n x pip list")


def test_a_pattern_spelled_as_a_pip3_or_python_m_pip_command_matches_it():
    assert _matched("pip3 list", {"packages": ["pip3 list"]}) == ["pip3 list"]
    assert _matched("python -m pip list", {"packages": ["python -m pip list"]}) == ["python -m pip list"]


def test_a_pattern_that_starts_with_a_launcher_matches_the_program_it_runs():
    assert _matched("uv run pip list", {"packages": ["uv run pip list"]}) == ["uv run pip list"]


def test_a_powershell_command_after_its_options_is_a_probe():
    cmd = "pwsh -NoProfile -ExecutionPolicy Bypass -com Get-CimInstance Win32_Processor"
    assert _matched(cmd) == ["Get-CimInstance Win32_Processor"]


@pytest.mark.parametrize("command", ["grep -nA 2 /proc/cpuinfo README", "grep -rm 1 /proc/cpuinfo README"])
def test_a_search_value_ending_a_flag_cluster_is_no_operand(command):
    assert _matched(command) == []


def test_a_powershell_assignment_without_spaces_is_skipped():
    assert _matched("$cpu=Get-CimInstance Win32_Processor") == ["Get-CimInstance Win32_Processor"]
    assert _matched("$cpu= Get-CimInstance Win32_Processor") == ["Get-CimInstance Win32_Processor"]


def test_a_command_with_an_unbalanced_quote_is_still_probed():
    assert _matched('nproc "unterminated') == ["nproc"]


def _probes(command: str) -> list[str]:
    events = _claude(_claude_call("a", command, "2026-01-01T00:00:05Z"), _claude_result("a"))
    return [e["pattern"] for e in events if e["kind"] == "probe"]


@pytest.mark.parametrize("command", [
    "cat <<'EOF' > script.sh\nnproc\nEOF",
    "cat <<-EOF > script.sh\n\tnproc\n\tEOF",
    "echo '$(nproc)'",
    "echo '`nproc`'",
    "echo \\$(nproc)",
    "echo '|' nproc",
    "grep -e /proc/cpuinfo README",
    "grep /proc/cpuinfo README",
    "rg -e /proc/cpuinfo README",
    "sed -n /proc/cpuinfo README",
    "python -c 'print(\"os.cpu_count()\")'",
    "python -c '# os.cpu_count()\nprint(1)'",
    "python -c 'x = os.cpu_count'",
    "echo \\| nproc",
    "cat README > /etc/os-release",
    "cat README >> /etc/os-release",
    "cat README 2>/etc/os-release",
    "grep --regexp /proc/cpuinfo README",
    "grep --regexp=/proc/cpuinfo README",
    "grep -f patterns README /proc/cpuinfo.bak",
    "cat <<'EOF'\n$(nproc)\nEOF",
    "true || nproc",
    ": || nproc",
    "python tool.py -c 'import os; os.cpu_count()'",
])
def test_text_the_shell_does_not_run_gives_no_probe(command):
    assert _probes(command) == []


@pytest.mark.parametrize(("command", "found"), [
    ("make -j$(nproc --ignore=$(printf 1))", ["nproc"]),
    ("sudo -n bash -c 'nproc'", ["nproc"]),
    ("time bash -lc 'nproc'", ["nproc"]),
    ("cat <<EOF\n$(nproc)\nEOF", ["nproc"]),
    ("cat <<EOF # the cores\n$(nproc)\nEOF", ["nproc"]),
    ("cat <<EOF > cfg  # note\n$(nproc)\nEOF", ["nproc"]),
    ("cat <<EOF\nthe cores: `nproc`\nEOF", ["nproc"]),
    ("cat < /etc/os-release", ["/etc/os-release"]),
    ("cat README > out.txt < /etc/os-release", ["/etc/os-release"]),
    ("grep -f patterns /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("grep '>' /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("false || nproc", ["nproc"]),
    ("grep '1'>/dev/null /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("grep 1>/dev/null cores /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("grep \"<\" /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("grep --file patterns /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("sed -f script.sed /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("grep -n cores /proc/cpuinfo 2>/dev/null", ["/proc/cpuinfo"]),
    ('make -j"$(nproc)"', ["nproc"]),
    ("make -j`nproc`", ["nproc"]),
    ("bash <<EOF\nnproc\nEOF", ["nproc"]),
    ("sudo -n nproc", ["nproc"]),
    ("nice -n 5 nproc", ["nproc"]),
    ("sudo -u root nproc", ["nproc"]),
    ("time -p nproc", ["nproc"]),
    ("sudo nohup nproc", ["nproc"]),
    ("exec nproc", ["nproc"]),
    ("command nproc", ["nproc"]),
    ("C:\\Windows\\System32\\nproc.exe", ["nproc"]),
    ("grep -n cores /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("head -n 3 /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("sed -n 1,3p /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("grep -e cores /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("wc -l /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("tac /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("less /proc/cpuinfo", ["/proc/cpuinfo"]),
    ("python -c 'import os\nprint(os.cpu_count())'", ["py:os.cpu_count"]),
    ("python -c 'print(f\"{os.cpu_count()}\")'", ["py:os.cpu_count"]),
    ("python3.11 -c 'import os; os.cpu_count()'", ["py:os.cpu_count"]),
    ("nvidia-smi | grep GPU", ["nvidia-smi"]),
])
def test_commands_the_shell_runs_give_their_probe(command, found):
    assert _probes(command) == found


def test_pattern_stages_must_be_consecutive():
    config = probes_for({"cpu": ("sysctl -a | grep machdep.cpu",)})
    def patterns(text):
        return [e["pattern"] for e in with_probes([{"kind": "command", "text": text}], config) if e["kind"] == "probe"]
    assert patterns("sysctl -a '|' grep machdep.cpu") == []
    assert patterns("sysctl -a | head | grep machdep.cpu") == []
    assert patterns("sysctl -a | grep machdep.cpu") == ["sysctl -a | grep machdep.cpu"]


def test_a_python_probe_needs_a_call_on_a_name_boundary():
    assert _probes("python -c 'x = myos.cpu_count()'") == []
    assert _probes("python -c 'x = a.os.cpu_count()'") == []
    assert _probes("python -c 'os.cpu_count()'") == ["py:os.cpu_count"]


@pytest.mark.parametrize("command, patterns", [
    ("true || false || nproc", []),  # ``||`` after a success skips the rest of the chain
    ("true || false && nproc", ["nproc"]),  # ``&&`` after a skipped part runs its own command
    ("false || nproc", ["nproc"]),
    ("true || cat <<EOF\n$(nproc)\nEOF", []),  # an unquoted heredoc's substitution runs with its command
    ("cat <<EOF\n$(nproc)\nEOF", ["nproc"]),
    ("echo \\\\| nproc", ["nproc"]),  # ``\\`` is a backslash: the pipe after it is real
    ("echo \"a\\\\\" | nproc", ["nproc"]),  # inside double quotes too: the closing quote is found, the pipe is real
    ("echo \\| nproc", []),
    ("cat >$(printf /tmp/probe.log) /etc/os-release", ["/etc/os-release"]),  # the file is the operand, not the target
    ("cat <<-EOF\n\tEOF\nnproc", ["nproc"]),  # ``<<-`` takes a tab-indented terminator: the body ends there
    ("cat <<-EOF\n  EOF\nnproc\nEOF", []),  # a space does not indent a ``<<-`` terminator: nproc is data
])
def test_the_shell_structure_decides_which_probe_runs(command, patterns):
    assert _probes(command) == patterns


def _probed(text: str, **event: object) -> list[tuple[str, str]]:
    from dokimi.repos.probes import probes_for, with_probes

    events = [{"t": 0.0, "kind": "command", "text": text, "rc": 0, **event}]
    return [(e["target"], e["pattern"]) for e in with_probes(events, probes_for(None)) if e["kind"] == "probe"]


@pytest.mark.parametrize("text, event", [
    ("nproc", {"denied": True}),  # a denied command ran nothing
    ("true || echo skipped || nproc", {}),
    ("command -v nproc", {}),  # ``command -v`` only looks the program up
    ("sudo -V nproc", {}),
    ("sudo --help nproc", {}),  # ``--help`` prints text and runs nothing
    ("false && nproc", {}),
    ("exit 0; nproc", {}),
    ("exec true && nproc", {}),  # exec replaces the shell: nothing after it runs
    ("cat <<EOF\n EOF\nnproc\nEOF", {}),  # a leading space does not end an unquoted heredoc
    ("echo $((nproc + 1))", {}),  # arithmetic, not a command substitution
    ("pip --version > list", {}),  # a redirection target is no pattern word
])
def test_a_command_that_runs_nothing_probed_gives_no_probe(text, event):
    assert not [p for p in _probed(text, **event) if p[0] == "cpu" or p[1] in ("pip list",)]


@pytest.mark.parametrize("text", ["exec -l && nproc", "exec </dev/null; nproc", "exec <&-; nproc", "env FOO=1 nproc", "echo \"<<EOF\"\nnproc\nEOF",
                                  "cat <<EOF\n$(nproc)\nEOF", "cd repo || exit 1; nproc", "(exit 0); nproc",
                                  "cd repo || { echo missing; exit 1; }; nproc", "if [ ! -d x ]; then\n  exit 1\nfi\nnproc"])
def test_a_command_that_runs_the_probe_gives_the_probe(text):
    assert ("cpu", "nproc") in _probed(text)


def test_env_runs_its_program_not_an_env_probe():
    assert ("env", "env") not in _probed("env FOO=1 python train.py")
    assert ("env", "env") not in _probed("env -i python train.py")
    assert ("env", "env") in _probed("env | grep X")
    assert ("env", "env") in _probed("env > f")
    assert ("env", "printenv") in _probed("printenv")


@pytest.mark.parametrize("program", ["exit 0", "exec true"])
def test_pipeline_termination_leaves_the_parent_running(program):
    assert _probes(f"{program} | cat; nproc") == ["nproc"]


@pytest.mark.parametrize("conditional", ["false || exit 1", "true && exit 0"])
def test_a_known_condition_runs_its_shell_exit(conditional):
    assert _probes(f"{conditional}; nproc") == []


def test_a_later_substitution_does_not_cancel_an_earlier_exit():
    assert _probes("exit 0; echo $(date); nproc") == []


def test_multiple_heredocs_are_consumed_in_order():
    assert _probes("cat <<A <<B\nignored\nA\nnproc\nB") == []


@pytest.mark.parametrize("invocation", ["bash -c 'cat'", "bash --help", "sh script.sh"])
def test_a_shell_with_its_own_script_treats_stdin_as_data(invocation):
    assert _probes(f"{invocation} <<'EOF'\nnproc\nEOF") == []


@pytest.mark.parametrize(("script", "expected"), [
    ("cat <<<EOF\nnproc", ["nproc"]), ("# later: cat <<EOF\nnproc", ["nproc"]),
    ("echo $((1 << n))\nnproc", ["nproc"]), ("cat <<\\EOF\nnproc\nEOF", []),
    ("cat <<'my-eof'\nnproc\nmy-eof", []),
    ("python -c '\nprint(1 << 2)\n'\nnproc", ["nproc"]),
    ("cat <<2\nnproc\n2", []),
])
def test_only_a_real_heredoc_masks_its_body(script, expected):
    assert _probes(script) == expected


@pytest.mark.parametrize("text", ["stdbuf -oL nproc", "stdbuf -o L nproc", "doas -u user nproc",
                                  "timeout 3 nproc", "uv run python -W default -c 'import os; os.cpu_count()'",
                                  "bash -o pipefail -c nproc"])
def test_probe_follows_a_launcher_to_its_program(text):
    assert _probes(text)


@pytest.mark.parametrize("text", ["bash --norc <<'EOF'\nnproc\nEOF", "bash -l <<'EOF'\nnproc\nEOF", "bash --noprofile <<'EOF'\nnproc\nEOF"])
def test_a_heredoc_fed_to_bash_with_an_option_runs_its_body(text):
    out = with_probes([{"t": 1.0, "kind": "command", "text": text, "rc": 0}], probes_for())
    assert [e for e in out if e.get("kind") == "probe" and e.get("target") == "cpu"]


def test_a_backgrounded_exit_does_not_end_the_scan():
    out = with_probes([{"t": 1.0, "kind": "command", "text": "exit 0 & nproc", "rc": 0}], probes_for())
    assert [e for e in out if e.get("kind") == "probe" and e.get("target") == "cpu"]
