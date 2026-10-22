# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context consistency`: result stability, path stability and contract conformance of repeated runs."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.context.consistency import (_program, _written, check_contract, jsd, parse_contract,
                                        parse_run, pass_at, pass_hat, profile, seq_distance)


def _trace(*steps, ok=True, turns=None):
    """steps: (tool, input) or (tool, input, failed)."""
    lines = [{"type": "system", "subtype": "init", "model": "m"}]
    for i, step in enumerate(steps):
        tool, args, failed = (*step, False)[:3]
        lines.append({"type": "assistant", "parent_tool_use_id": None,
                      "message": {"id": f"m{i}", "usage": {"output_tokens": 10},
                                  "content": [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": args}]}})
        lines.append({"type": "user", "parent_tool_use_id": None,
                      "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                               "content": "x", "is_error": failed}]}})
    lines.append({"type": "result", "subtype": "success" if ok else "error_max_turns",
                  "is_error": not ok, "num_turns": turns or len(steps)})
    return "\n".join(json.dumps(x) for x in lines)


READ = ("Read", {"file_path": "/w/AGENTS.md"})
EDIT = ("Edit", {"file_path": "/w/src/a.py"})
TEST = ("Bash", {"command": "cd /w && uv run pytest -q"})
FAILED_TEST = ("Bash", {"command": "pytest -q"}, True)
GREP = ("Grep", {"pattern": "foo"})


def _run(name, *steps, ok=True):
    return parse_run(_trace(*steps, ok=ok), name)


def test_pass_estimates():
    assert pass_hat(4, 4, 4) == 1.0
    assert pass_hat(4, 2, 2) == pytest.approx(1 / 6)
    assert pass_at(4, 2, 2) == pytest.approx(5 / 6)
    assert pass_at(4, 0, 4) == 0.0


def test_distances():
    assert seq_distance(["a", "b"], ["a", "b"]) == 0
    assert seq_distance(["a", "b", "c"], ["a", "c", "b"]) == pytest.approx(2 / 3)
    assert jsd(["a", "b"], ["b", "a"]) == 0  # same mix, any order
    assert jsd(["a"], ["b"]) == pytest.approx(1.0)


@pytest.mark.parametrize("cmd, want", [
    ("cd /w && uv run pytest -q", "pytest"), ("FOO=1 sudo make test", "make"),
    ("python3 -m pytest", "pytest"), ("git status; ls", "git"), ("export X=1", ""),
])
def test_program(cmd, want):
    assert _program(cmd) == want


def test_parse_run_reads_kinds_actions_and_failures():
    run = _run("r", READ, EDIT, FAILED_TEST)
    assert run.kinds() == ["Read", "Edit", "Bash:pytest"]
    assert run.actions()[:2] == ["Read(AGENTS.md)", "Edit(a.py)"]
    assert [e.failed for e in run.events] == [False, False, True]
    assert run.passed is True and run.output_tokens == 30


def test_subagent_calls_are_not_the_path():
    raw = _trace(READ)
    sub = {"type": "assistant", "parent_tool_use_id": "t0",
           "message": {"id": "s", "content": [{"type": "tool_use", "id": "s1", "name": "Grep", "input": {}}]}}
    run = parse_run(raw + "\n" + json.dumps(sub))
    assert run.kinds() == ["Read"]


def test_same_steps_different_order_is_order_not_mix():
    a, b = _run("a", READ, EDIT, TEST), _run("b", READ, TEST, EDIT)
    p = profile([a, b])["path"]
    assert p["mix_jsd"] == 0 and p["order_distance"] == pytest.approx(2 / 3)


def test_two_valid_strategies_are_diversity_not_instability():
    runs = [_run(f"g{i}", GREP, GREP, EDIT, TEST) for i in range(3)] + \
           [_run(f"r{i}", READ, EDIT, TEST, READ, EDIT, TEST) for i in range(3)]
    p = profile(runs)
    assert len(p["strategies"]) == 2
    assert {g["verdict"] for g in p["strategies"]} == {"stable success"}
    assert "valid diversity" in p["verdict"]
    assert p["result"]["pass^4"] == 1.0


def test_one_strategy_that_breaks_is_unstable():
    runs = [_run("a", READ, EDIT, TEST), _run("b", READ, EDIT, TEST, ok=False)]
    p = profile(runs)
    assert p["verdict"] == "one strategy, unstable" and p["result"]["pass^2"] == 0


CONTRACT = {
    "actions": {"guide": {"tool": "Read", "file_path": r"AGENTS\.md$"},
                "edit": {"tool": ["Edit", "Write"]},
                "test": {"tool": "Bash", "command": r"pytest"},
                "push": {"tool": "Bash", "command": r"git push"}},
    "rules": [{"before": ["guide", "edit"]}, {"after_last": ["edit", "test"]},
              {"last_ok": "test"}, {"forbids": "push"}],
}


def test_contract_catches_a_pass_that_skipped_the_final_check():
    c = parse_contract(CONTRACT)
    assert check_contract(_run("ok", READ, EDIT, TEST), c) == []
    assert check_contract(_run("late", EDIT, READ, TEST), c) == ["before: guide -> edit"]
    assert check_contract(_run("untested", READ, TEST, EDIT), c) == ["after_last: edit -> test"]
    assert check_contract(_run("red", READ, EDIT, FAILED_TEST), c) == ["last_ok: test"]
    p = profile([_run("ok", READ, EDIT, TEST), _run("untested", READ, TEST, EDIT)], c)
    assert p["contract"]["conformance_rate"] == 0.5
    assert p["contract"]["passed_but_nonconforming"] == 1


def test_contract_any_unconstrained_order_conforms():
    c = parse_contract(CONTRACT)
    assert check_contract(_run("x", GREP, READ, GREP, EDIT, EDIT, TEST), c) == []


@pytest.mark.parametrize("bad", [
    {"rules": []}, {"actions": {"a": {}}, "rules": [{"nope": "a"}]},
    {"actions": {"a": {}}, "rules": [{"before": "a"}]},
    {"actions": {"a": {"x": "("}}, "rules": []},
])
def test_contract_refuses_malformed(bad):
    with pytest.raises(ValueError):
        parse_contract(bad)


def test_cli_arms_outcomes_and_json(tmp_path, capsys):
    for arm, steps in (("base", [(READ, TEST, EDIT), (EDIT, TEST)]), ("skill", [(READ, EDIT, TEST)] * 2)):
        for i, s in enumerate(steps):
            (tmp_path / arm).mkdir(exist_ok=True)
            (tmp_path / arm / f"{i}.jsonl").write_text(_trace(*s))
    outcomes = tmp_path / "out.json"
    outcomes.write_text(json.dumps({str(tmp_path / "base/0.jsonl"): False, str(tmp_path / "base/1.jsonl"): True,
                                    str(tmp_path / "skill/0.jsonl"): True, str(tmp_path / "skill/1.jsonl"): True}))
    contract = tmp_path / "c.yaml"
    import yaml
    contract.write_text(yaml.safe_dump(CONTRACT))
    args = build_parser().parse_args([
        "context", "consistency", "--arm", f"base={tmp_path}/base/*.jsonl",
        "--arm", f"skill={tmp_path}/skill/*.jsonl", "--outcomes", str(outcomes),
        "--contract", str(contract), "--json"])
    args.func(args)
    rep = json.loads(capsys.readouterr().out)
    assert rep["base"]["result"]["success_rate"] == 0.5
    assert rep["skill"]["result"]["pass^2"] == 1.0
    assert rep["skill"]["path"]["order_distance"] == 0
    assert rep["skill"]["contract"]["conformance_rate"] == 1.0
    assert rep["base"]["contract"]["conformance_rate"] == 0.0

    args = build_parser().parse_args(["context", "consistency", "--arm", f"skill={tmp_path}/skill/*.jsonl"])
    args.func(args)
    text = capsys.readouterr().out
    assert "| skill | 2 | 100%" in text and "harness result event" in text


def test_one_shell_call_is_the_steps_it_chains():
    cmd = ("cd /w; sed -i 's/a/b/' shop/pricing.py\nsed -i 's/x/y/' CHANGELOG.md\n"
           "grep -n d shop/pricing.py; sh .claude/skills/s/finish.sh")
    run = _run("r", ("Bash", {"command": cmd}, True))
    assert run.kinds() == ["Bash:sed", "Bash:sed", "Bash:grep", "Bash:sh"]
    assert [e.input.get("file_path") for e in run.events] == ["shop/pricing.py", "CHANGELOG.md", None, None]
    assert all(e.failed for e in run.events)  # the trace does not say which chained command failed
    assert parse_run(_trace(("Bash", {"command": cmd})), shell="call").kinds() == ["Bash:sed"]


def test_shell_edit_satisfies_the_contract():
    c = parse_contract({"actions": {"log": {"tool": ["Edit", "Bash"], "file_path": r"CHANGELOG\.md$"}},
                        "rules": [{"requires": "log"}]})
    assert check_contract(_run("r", ("Bash", {"command": "echo '- fix' >> CHANGELOG.md"})), c) == []
    assert check_contract(_run("r", ("Bash", {"command": "cat CHANGELOG.md"})), c) == ["requires: log"]


@pytest.mark.parametrize("seg, want", [
    ("sed -i 's/a/b/' f.py", "f.py"), ("sed -n 1,5p f.py", ""), ("echo x > out.txt", "out.txt"),
    ("make 2>&1", ""), ("ls >/dev/null", ""), ("echo x | tee -a log.md", "log.md"),
    ("echo 'a > b' | grep a", ""), ('python3 -c "print(1 > 0)"', ""), ("echo x>out.txt", "out.txt"),
    ("make 2> err.log", ""),
])
def test_written(seg, want):
    assert _written(seg) == want


def test_heredoc_body_is_not_commands():
    cmd = "python3 - <<'EOF'\nimport os\nrm_everything()\nEOF\npytest -q"
    assert _run("r", ("Bash", {"command": cmd})).kinds() == ["Bash:python3", "Bash:pytest"]


def test_blocked_stop_hook_is_a_failed_step():
    raw = _trace(EDIT) + "\n" + json.dumps({"type": "user", "parent_tool_use_id": None, "message": {
        "content": [{"type": "text", "text": "Stop hook feedback:\n[sh hook]: Not done"}]}})
    run = parse_run(raw)
    assert run.kinds() == ["Edit", "Hook:Stop"] and run.events[-1].failed


def test_quoted_newlines_stay_one_command():
    cmd = 'cd /w && python3 -c "\nfrom a import b\n# note\nprint(b(1); 2)\n" && make check \\\n  -j2\n# done'
    assert _run("r", ("Bash", {"command": cmd})).kinds() == ["Bash:python3", "Bash:make"]


TEST_CONTRACT = {"actions": {"test": {"tool": "Bash", "command": r"\b(pytest|make check)\b"}},
                 "rules": [{"last_ok": "test"}]}


def test_failed_chain_fails_the_check_not_only_the_last_command():
    run = _run("r", ("Bash", {"command": "make check && git diff"}, True))
    assert check_contract(run, parse_contract(TEST_CONTRACT)) == ["last_ok: test"]


@pytest.mark.parametrize("cmd, broken", [
    ("pytest -q | tail -5", ["last_ok: test (status hidden by a pipe)"]),
    ("set -o pipefail; pytest -q | tail -5", []),
    ("pytest -q 'a|b'", []),
])
def test_pipe_hides_the_check_status(cmd, broken):
    run = _run("r", ("Bash", {"command": cmd}))
    assert check_contract(run, parse_contract(TEST_CONTRACT)) == broken
    assert check_contract(parse_run(_trace(("Bash", {"command": cmd})), shell="call"),
                          parse_contract(TEST_CONTRACT)) == broken
