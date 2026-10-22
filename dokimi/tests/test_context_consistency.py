# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context consistency`: result stability, path stability and contract conformance of repeated runs."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.context.consistency import (_program, _written, apply_outcomes, check_contract, jsd,
                                        load_contract, load_outcomes, parse_contract, parse_run,
                                        pass_at, pass_hat, profile, run_from_events, seq_distance)


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
    assert parse_run(_trace(("Bash", {"command": cmd})), grain="call").kinds() == ["Bash:sed"]


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
    assert check_contract(parse_run(_trace(("Bash", {"command": cmd})), grain="call"),
                          parse_contract(TEST_CONTRACT)) == broken


# --- review of #155: shell syntax, edits through the shell, bad input, ambiguous outcomes ---------

def _kinds(cmd, grain="split"):
    return parse_run(_trace(("Bash", {"command": cmd})), grain=grain).kinds()


@pytest.mark.parametrize("cmd, want", [
    ("for f in a b; do black $f; done", ["Bash:black"]),
    ("if [ -f x ]; then make; fi", ["Bash:make"]),
    ("(cd sub && pytest)", ["Bash:pytest"]),
    ("# note && x\npytest", ["Bash:pytest"]),
    ("while true; do git pull; done", ["Bash:true", "Bash:git"]),
    ("if make check; then echo ok; else git diff; fi", ["Bash:make", "Bash:echo", "Bash:git"]),
])
def test_control_syntax_is_not_a_step(cmd, want):
    assert _kinds(cmd) == want
    assert _kinds(cmd, "call")[0] == want[0]


@pytest.mark.parametrize("seg, want", [
    ("echo 3 > count.txt", "count.txt"), ("seq 1 10 > out.txt", "out.txt"),
    ("make 2> err.log", ""), ("make 2>> err.log", ""), ("make &> all.log", ""),
    ("perl -Mstrict -e 'print 1' foo.pl", ""), ("perl -pi -e 's/a/b/' foo.pl", "foo.pl"),
    ("sed --in-place 's/a/b/' f.py", "f.py"), ("sed -i.bak 's/a/b/' f.py", "f.py"),
])
def test_written_review_cases(seg, want):
    assert _written(seg) == want


def test_a_shell_edit_matches_an_edit_action():
    c = parse_contract({"actions": {"guide": {"tool": "Read", "file_path": r"AGENTS\.md$"},
                                    "edit": {"tool": ["Edit", "Write", "MultiEdit"]},
                                    "test": {"tool": "Bash", "command": "pytest"}},
                        "rules": [{"requires": "edit"}, {"before": ["guide", "edit"]},
                                  {"after_last": ["edit", "test"]}]})
    shell = ("Bash", {"command": "sed -i s/a/b/ f.py && pytest -q"})
    assert check_contract(_run("r", READ, shell), c) == []
    bare = ("Bash", {"command": "sed -i s/a/b/ f.py"})
    assert check_contract(_run("r", bare, READ), c) == ["before: guide -> edit", "after_last: edit -> test"]
    # a shell step that writes nothing is not an edit
    assert check_contract(_run("r", READ, ("Bash", {"command": "pytest -q"})), c) == ["requires: edit"]


def test_no_runs_is_not_a_stable_success():
    p = profile([])
    assert p["verdict"] == "no runs" and p["strategies"] == []


def test_malformed_outcomes_and_contract_are_value_errors(tmp_path):
    o = tmp_path / "o.json"
    o.write_text('[{"trace": "t1.jsonl"}]')
    with pytest.raises(ValueError, match="passed"):
        load_outcomes(o)
    c = tmp_path / "c.yaml"
    c.write_text("actions: [unclosed")
    with pytest.raises(ValueError, match="c.yaml"):
        load_contract(c)
    c.write_text("actions: {a: {tool: Edit}}\nrules: 5")
    with pytest.raises(ValueError):
        load_contract(c)


@pytest.mark.parametrize("flag, content", [("--outcomes", '[{"trace": "t"}]'), ("--contract", "a: [")])
def test_cli_refuses_malformed_files_cleanly(tmp_path, flag, content):
    (tmp_path / "t.jsonl").write_text(_trace(READ))
    f = tmp_path / "bad"
    f.write_text(content)
    args = build_parser().parse_args(["context", "consistency", str(tmp_path / "t.jsonl"), flag, str(f)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert str(exc.value).startswith("✗")


def test_an_outcome_key_shared_by_two_arms_must_be_the_full_path(tmp_path):
    a, b = _run(str(tmp_path / "base/1.jsonl"), READ), _run(str(tmp_path / "skill/1.jsonl"), READ)
    with pytest.raises(ValueError, match="1.jsonl"):
        apply_outcomes([a, b], {"1.jsonl": False})
    assert apply_outcomes([a, b], {a.name: False, b.name: True}) == []
    assert (a.passed, b.passed) == (False, True)


def test_a_directory_name_is_not_an_outcome_key(tmp_path):
    r = _run(str(tmp_path / "base/1.jsonl"), READ)
    assert apply_outcomes([r], {"base": False}) == [r.name] and r.passed is None


def test_cli_catches_an_outcome_key_shared_across_arms(tmp_path):
    for arm in ("base", "skill"):
        (tmp_path / arm).mkdir()
        (tmp_path / arm / "1.jsonl").write_text(_trace(READ))
    o = tmp_path / "o.json"
    o.write_text('{"1.jsonl": false}')
    args = build_parser().parse_args(["context", "consistency", "--arm", f"base={tmp_path}/base/*.jsonl",
                                      "--arm", f"skill={tmp_path}/skill/*.jsonl", "--outcomes", str(o)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert "1.jsonl" in str(exc.value)


# --- review of #155, test gaps: each rule and denominator fails when the code stops enforcing it ---

def test_forbids_fires_on_a_run_that_pushes():
    c = parse_contract(CONTRACT)
    push = ("Bash", {"command": "git push origin main"})
    assert "forbids: push" in check_contract(_run("p", READ, EDIT, TEST, push), c)
    assert "forbids: push" not in check_contract(_run("ok", READ, EDIT, TEST), c)


def test_before_needs_a_strictly_earlier_action():
    both = {"actions": {"any": {"tool": "Read"}, "guide": {"tool": "Read", "file_path": r"AGENTS\.md$"}},
            "rules": [{"before": ["any", "guide"]}]}
    # the one Read is both actions: it is not earlier than itself
    assert check_contract(_run("r", READ), parse_contract(both)) == ["before: any -> guide"]
    assert check_contract(_run("r", GREP, ("Read", {"file_path": "/w/x.py"}), READ), parse_contract(both)) == []


def test_after_last_looks_past_the_last_action_not_the_first():
    c = parse_contract({"actions": {"edit": {"tool": "Edit"}, "test": {"tool": "Bash", "command": "pytest"}},
                        "rules": [{"after_last": ["edit", "test"]}]})
    assert check_contract(_run("r", EDIT, TEST, EDIT), c) == ["after_last: edit -> test"]
    assert check_contract(_run("r", EDIT, TEST, EDIT, TEST), c) == []
    assert check_contract(_run("r", TEST), c) == []  # vacuous with no edit


def test_passed_but_nonconforming_counts_passes_only():
    c = parse_contract(CONTRACT)
    runs = [_run("pass-bad", READ, TEST, EDIT), _run("fail-bad", READ, TEST, EDIT, ok=False),
            _run("pass-ok", READ, EDIT, TEST)]
    p = profile(runs, c)["contract"]
    assert p["passed_but_nonconforming"] == 1 and p["conforming_runs"] == 1


def test_result_rates_count_judged_runs_only():
    runs = [_run("a", READ), _run("b", READ), _run("c", READ, ok=False), _run("d", READ)]
    runs[3].passed = None  # no verdict for this one
    r = profile(runs)["result"]
    assert (r["runs"], r["judged"], r["passed"]) == (4, 3, 2)
    assert r["success_rate"] == pytest.approx(2 / 3)
    assert r["pass^2"] == pytest.approx(1 / 3) and r["pass@2"] == pytest.approx(1.0)
    assert "pass^4" not in r  # k above the judged runs has no estimate


def test_run_from_events_marks_failed_commands_and_denied_tools():
    run = run_from_events([
        {"kind": "command", "text": "make check && git diff", "rc": 2},
        {"kind": "command", "text": "pytest", "rc": 0},
        {"kind": "command", "text": "ls", "rc": None},
        {"kind": "tool", "name": "WebFetch", "denied": True},
        {"kind": "tool", "name": "Task", "denied": False},
        {"kind": "read", "path": "/w/AGENTS.md"},
        {"kind": "command", "text": "sub", "rc": 1, "agent": "x"},
    ])
    assert run.kinds() == ["Bash:make", "Bash:git", "Bash:pytest", "Bash:ls", "WebFetch", "Task", "Read"]
    assert [e.failed for e in run.events] == [True, True, False, False, True, False, False]
