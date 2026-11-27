# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`trace consistency`: result stability, path stability and contract conformance of repeated runs."""

from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.context.consistency import (_cv, apply_outcomes, check_contract, jsd,
                                        load_contract, load_outcomes, parse_contract, parse_run,
                                        pass_at, pass_hat, profile, run_from_events, seq_distance)
from dokimi.context.shell import program as _program
from dokimi.utils import InputError
from dokimi.context.shell import written as _written


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


def test_one_run_strategies_are_not_stable_diversity():
    runs = [_run("g", GREP, EDIT, TEST), _run("r", READ, READ, EDIT, EDIT, TEST, READ, EDIT, TEST)]
    p = profile(runs)
    assert len(p["strategies"]) == 2
    assert [g["inner_distance"] for g in p["strategies"]] == [None, None]
    assert "valid diversity" not in p["verdict"]


def test_traces_without_usage_have_no_token_spread():
    runs = [parse_run(_trace(TEST).replace(', "usage": {"output_tokens": 10}', "")) for _ in range(3)]
    assert runs[0].output_tokens is None
    assert profile(runs)["path"]["output_tokens_cv"] is None


def test_a_huge_token_count_is_counted_not_fatal():
    assert parse_run(_trace(TEST).replace('"output_tokens": 10', f'"output_tokens": {10 ** 400}')).output_tokens


@pytest.mark.parametrize("count", ['"n/a"', "Infinity", "NaN", "true", "[1]"])
def test_a_token_count_that_is_not_a_number_is_skipped(count):
    run = parse_run(_trace(TEST).replace('"output_tokens": 10', f'"output_tokens": {count}'))
    assert run.output_tokens is None and run.events


def test_a_list_pattern_is_an_alternation_not_a_character_class():
    c = parse_contract({"actions": {"test": {"tool": "Bash", "command": [r"\bpytest\b", r"\btox\b"]}},
                        "rules": [{"requires": "test"}]})
    assert check_contract(_run("r", ("Bash", {"command": "git status"})), c) == ["requires: test"]
    assert check_contract(_run("r", ("Bash", {"command": "tox -e py"})), c) == []


@pytest.mark.parametrize("value", [3, {"a": 1}, [1, 2], []])
def test_a_pattern_that_is_not_a_regex_is_refused(value):
    with pytest.raises(ValueError, match="wants a"):
        parse_contract({"actions": {"test": {"tool": "Bash", "command": value}}, "rules": []})


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
        "trace", "consistency", "--arm", f"base={tmp_path}/base/*.jsonl",
        "--arm", f"skill={tmp_path}/skill/*.jsonl", "--outcomes", str(outcomes),
        "--contract", str(contract), "--json"])
    args.func(args)
    rep = json.loads(capsys.readouterr().out)["data"]
    assert rep["base"]["result"]["success_rate"] == 0.5
    assert rep["skill"]["result"]["pass^2"] == 1.0
    assert rep["skill"]["path"]["order_distance"] == 0
    assert rep["skill"]["contract"]["conformance_rate"] == 1.0
    assert rep["base"]["contract"]["conformance_rate"] == 0.0

    args = build_parser().parse_args(["trace", "consistency", "--arm", f"skill={tmp_path}/skill/*.jsonl"])
    args.func(args)
    text = capsys.readouterr().out
    assert "| skill | 2 | 2 | 100%" in text and "harness result event" in text


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


def test_blocked_stop_hook_is_not_a_tool_call():
    """#936: docs/run-consistency.md says `tool_calls_*` and `failed_calls_mean` count tool calls."""
    hook = json.dumps({"type": "user", "parent_tool_use_id": None, "message": {
        "content": [{"type": "text", "text": "Stop hook feedback:\n- run make check"}]}})
    run = parse_run(_trace(EDIT, ("Bash", {"command": "pytest -q"})) + "\n" + hook)
    assert run.kinds() == ["Edit", "Bash:pytest", "Hook:Stop"]
    assert (run.calls(), run.failed_calls()) == (2, 0)
    only = parse_run(hook)
    assert (only.calls(), only.failed_calls()) == (0, 0) and only.kinds() == ["Hook:Stop"]


def test_quoted_newlines_stay_one_command():
    cmd = 'cd /w && python3 -c "\nfrom a import b\n# note\nprint(b(1); 2)\n" && make check \\\n  -j2\n# done'
    assert _run("r", ("Bash", {"command": cmd})).kinds() == ["Bash:python3", "Bash:make"]


TEST_CONTRACT = {"actions": {"test": {"tool": "Bash", "command": r"\b(pytest|make check)\b"}},
                 "rules": [{"last_ok": "test"}]}


def test_failed_chain_fails_the_check_not_only_the_last_command():
    run = _run("r", ("Bash", {"command": "make check && git diff"}, True))
    assert check_contract(run, parse_contract(TEST_CONTRACT)) == ["last_ok: test"]


@pytest.mark.parametrize("cmd, broken", [
    ("pytest -q | tail -5", ["last_ok: test (status hidden)"]),
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
    ("make 2> err.log", ""), ("make 2>> err.log", ""), ("make &> all.log", "all.log"),
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


@pytest.mark.parametrize("cmd", ["pytest -q > test.log", "pytest -q > /tmp/test.log 2>&1",
                                 "set -o pipefail; pytest -q 2>&1 | tee test.log"])
def test_saving_a_test_log_is_not_an_edit(cmd):
    c = parse_contract({**CONTRACT, "rules": [*CONTRACT["rules"], {"requires": "edit"}]})
    assert check_contract(_run("r", READ, EDIT, ("Bash", {"command": cmd})), c) == []
    scratch = ("Bash", {"command": "ls > /tmp/files.txt"})
    assert check_contract(_run("r", scratch, READ, TEST), c) == ["requires: edit"]
    for write in ("echo x > src/a.py", "cat > src/a.py <<'EOF'\nx\nEOF", "printf x | tee src/a.py"):
        assert check_contract(_run("r", READ, ("Bash", {"command": write}), TEST), c) == []


def test_perl_in_place_is_an_edit_of_its_file():
    c = parse_contract({"actions": {"edit": {"tool": "Edit", "file_path": r"src/a\.py$"}},
                        "rules": [{"requires": "edit"}]})
    assert check_contract(_run("r", ("Bash", {"command": "perl -pi -e 's/a/b/' src/a.py"})), c) == []
    reads = ("Bash", {"command": "perl -Mstrict -e 'print 1' src/a.py"})
    assert check_contract(_run("r", reads), c) == ["requires: edit"]


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
    args = build_parser().parse_args(["trace", "consistency", str(tmp_path / "t.jsonl"), flag, str(f)])
    with pytest.raises(InputError) as exc:
        args.func(args)
    assert str(f) in str(exc.value)


@pytest.mark.parametrize("content", ["a: 2026-99-99\n", "[" * 100000], ids=["bad-date", "deep"])
def test_cli_names_a_contract_whose_yaml_does_not_load(tmp_path, content):
    (tmp_path / "t.jsonl").write_text(_trace(READ))
    f = tmp_path / "c.yaml"
    f.write_text(content)
    args = build_parser().parse_args(["trace", "consistency", str(tmp_path / "t.jsonl"), "--contract", str(f)])
    with pytest.raises(InputError) as exc:
        args.func(args)
    assert str(exc.value).startswith(f"{f}: not valid YAML")


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
    args = build_parser().parse_args(["trace", "consistency", "--arm", f"base={tmp_path}/base/*.jsonl",
                                      "--arm", f"skill={tmp_path}/skill/*.jsonl", "--outcomes", str(o)])
    with pytest.raises(InputError) as exc:
        args.func(args)
    assert "1.jsonl" in str(exc.value)


def test_a_trace_given_twice_counts_once(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "a/r1.jsonl").write_text(_trace(READ, TEST))
    (tmp_path / "a/r2.jsonl").write_text(_trace(READ, ("Bash", {"command": "git push"})))
    import yaml
    contract = tmp_path / "c.yaml"
    contract.write_text(yaml.safe_dump({"actions": CONTRACT["actions"], "rules": [{"forbids": "push"}]}))
    args = build_parser().parse_args([
        "trace", "consistency", "--arm", f"A={tmp_path}/a/*.jsonl", "--arm", f"A={tmp_path}/a/r2*.jsonl",
        f"{tmp_path}/a/./r1.jsonl", f"{tmp_path}/a/r1.jsonl", "--contract", str(contract), "--json"])
    args.func(args)
    out = capsys.readouterr()
    rep = json.loads(out.out)["data"]
    assert rep["A"]["result"]["runs"] == 2 and rep["A"]["contract"]["conformance_rate"] == 0.5
    assert rep["runs"]["result"]["runs"] == 1
    assert out.err.count("given twice") == 2


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


# --- documented paths and 911a507 guards no test reached ------------------------------------------

def test_a_file_name_or_stem_key_sets_the_outcome(tmp_path):
    a, b = _run(str(tmp_path / "x/a.jsonl"), READ), _run(str(tmp_path / "x/b.jsonl"), READ)
    assert apply_outcomes([a, b], {"a.jsonl": False, "b": False}) == []
    assert (a.passed, b.passed) == (False, False)
    o = tmp_path / "o.json"
    o.write_text('[{"trace": "t1.jsonl", "passed": false}]')
    assert load_outcomes(o) == {"t1.jsonl": False}


def test_all_failed_is_a_stable_failure_and_no_verdict_is_unjudged():
    runs = [_run(str(i), READ, ok=False) for i in range(3)]
    assert profile(runs)["strategies"][0]["verdict"] == "stable failure"
    for r in runs:
        r.passed = None
    assert profile(runs)["strategies"][0]["verdict"] == "unjudged"


@pytest.mark.parametrize("cmd, want", [
    ("( make test )", ["Bash:make"]), ("git log --grep=#12 && make test", ["Bash:git", "Bash:make"]),
])
def test_a_bare_paren_and_a_hash_inside_a_word(cmd, want):
    assert _kinds(cmd) == want


def test_a_shell_write_is_not_a_read_and_fd_one_is_stdout():
    assert _written("echo x 1> out.txt") == "out.txt"
    c = parse_contract({"actions": {"guide": {"tool": "Read", "file_path": r"AGENTS\.md$"}},
                        "rules": [{"requires": "guide"}]})
    assert check_contract(_run("r", ("Bash", {"command": "echo x > AGENTS.md"})), c) == ["requires: guide"]


def test_a_single_run_is_single_run_passed_or_failed_not_stable():
    assert profile([_run("a", READ, TEST)])["strategies"][0]["verdict"] == "single run, passed"
    assert profile([_run("a", READ, TEST, ok=False)])["strategies"][0]["verdict"] == "single run, failed"


def test_a_float_token_count_is_counted():
    assert parse_run(_trace(TEST).replace('"output_tokens": 10', '"output_tokens": 9.0')).output_tokens == 9


def test_a_message_id_seen_twice_counts_its_largest_usage_once():
    lines = [{"type": "assistant", "message": {"id": "x", "usage": {"output_tokens": n}, "content": []}}
             for n in (9, 5)]
    assert parse_run("\n".join(json.dumps(x) for x in lines)).output_tokens == 9


def test_cv_of_values_at_1e300_matches_the_closed_form():
    """Squaring a deviation of 1e300 overflowed: https://github.com/intel/dokimi/issues/543."""
    # [1, -1, 1, -1, 1, 1]: mean 1/3, sd = 4/sqrt(15), so cv = 12/sqrt(15).
    assert _cv([1e300, -1e300, 1e300, -1e300, 1e300, 1e300]) == pytest.approx(12 / 15 ** 0.5, rel=1e-12)


def test_cv_of_values_at_the_float_maximum_matches_the_closed_form():
    big = sys.float_info.max
    # [1, -1, -1]: mean -1/3, sd = sqrt(4/3), so cv = -2 sqrt(3); big - mean and sum(xs) overflow in plain floats.
    assert _cv([big, -big, -big]) == pytest.approx(-2 * 3 ** 0.5, rel=1e-12)


@pytest.mark.parametrize("xs", [
    [3.0, 5.0, 8.0, 13.0, 21.5],
    [0.0, 1000.0, 250.5, 3.25],
    [120.0, 118.0, 131.0, 99.0, 140.0, 125.0, 117.0],
])
def test_cv_of_ordinary_values_is_the_plain_formula_to_rounding(xs):
    """The shared sd scales by a power of two, so the last digit may differ from the plain formula; nothing more."""
    mu = sum(xs) / len(xs)
    assert _cv(xs) == pytest.approx(math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) / mu, rel=1e-15)


def test_cv_of_finite_values_whose_squares_underflow_is_the_value_to_rounding():
    """Squares of 1e-170 underflowed to 0.0, so the cv read 0.0: [1, 2, 3] scaled has mean 2 and sd 1, cv 0.5 to within an ulp."""
    assert _cv([1e-170, 2e-170, 3e-170]) == pytest.approx(0.5, rel=1e-12)


def test_cv_keeps_none_for_one_value_and_zero_for_a_zero_mean():
    assert _cv([5.0]) is None
    assert _cv([1e300, -1e300]) == 0.0


def test_an_absolute_path_key_matches_a_relatively_named_trace(tmp_path, monkeypatch):
    """#936: docs/run-consistency.md keys same-named traces by full path, whatever the command line spells."""
    monkeypatch.chdir(tmp_path)
    runs = [_run(f"{arm}/1.jsonl", EDIT) for arm in ("base", "skill")]
    keys = {os.path.abspath(r.name): i == 0 for i, r in enumerate(runs)}
    assert apply_outcomes(runs, keys) == []
    assert [r.passed for r in runs] == [True, False]


def test_two_spellings_of_one_trace_with_different_verdicts_are_refused(tmp_path, monkeypatch):
    """#936: one trace must not take either of two conflicting verdicts."""
    monkeypatch.chdir(tmp_path)
    r = _run("base/1.jsonl", EDIT)
    with pytest.raises(ValueError, match="different verdicts"):
        apply_outcomes([r], {"base/1.jsonl": True, os.path.abspath("base/1.jsonl"): False})
    r = _run("base/1.jsonl", EDIT)  # the same verdict twice is no conflict
    assert apply_outcomes([r], {"base/1.jsonl": False, os.path.abspath("base/1.jsonl"): False}) == []
    assert r.passed is False


def test_a_key_for_another_file_is_not_a_match(tmp_path, monkeypatch):
    """#936: only a key that resolves to the trace's own file judges it (docs/run-consistency.md)."""
    monkeypatch.chdir(tmp_path)
    r = _run("base/1.jsonl", EDIT)
    assert apply_outcomes([r], {os.path.abspath("skill/1.jsonl"): True}) == [r.name]
    assert apply_outcomes([r], {}) == [r.name]


_DOC = (Path(__file__).resolve().parent.parent / "docs" / "run-consistency.md").read_text()


def test_the_doc_example_contract_does_not_count_a_lookup_as_a_test():
    """#936: docs/run-consistency.md says `command -v pytest` is not a test run."""
    import yaml
    contract = parse_contract(yaml.safe_load(re.search(r"```yaml\n(actions:.*?)```", _DOC, re.S).group(1)))
    lead = (READ, EDIT)
    lookup = check_contract(_run("lookup.jsonl", *lead, ("Bash", {"command": "command -v pytest"})), contract)
    ran = check_contract(_run("ran.jsonl", *lead, ("Bash", {"command": "pytest -q"})), contract)
    assert lookup and not ran


def _table_cells(after, before):
    """The backticked first-column entries of the markdown table between two doc markers."""
    part = _DOC[_DOC.index(after):_DOC.index(before)]
    rows = [r for r in part.splitlines() if r.startswith("| `")]
    return {c.strip("` ") for r in rows for c in r.split("|")[1].split(" / ")}


def test_the_doc_verdict_table_lists_the_strings_the_command_prints():
    """#936: the verdict tables list exactly what `_verdict` and `_summary_verdict` return."""
    from dokimi.context.consistency import _summary_verdict, _verdict
    printed = {_verdict(p, j) for j in range(4) for p in range(j + 1)}
    assert _table_cells("A group's `verdict`", "The arm's `verdict`") == printed
    arm = _table_cells("The arm's `verdict`", "A strategy of one run")
    assert arm == {"one strategy, <group verdict>", "N strategies, all stable success (valid diversity)",
                   "N strategies: <group verdict> ×runs, ...", "no runs"}
    g = lambda v, n=1: {"verdict": v, "runs": ["r"] * n}  # noqa: E731
    assert _summary_verdict([]) == "no runs"
    assert _summary_verdict([g("unstable")]) == "one strategy, unstable"
    assert _summary_verdict([g("stable success"), g("stable success")]).endswith(
        "strategies, all stable success (valid diversity)")
    assert _summary_verdict([g("stable success", 2), g("stable failure", 2)]) == (
        "2 strategies: stable success ×2, stable failure ×2")


def test_a_key_through_a_symlink_to_another_directory_does_not_judge_the_trace(tmp_path):
    """#936: a key must resolve to the trace's own file, not to a lookalike beside a symlink."""
    (tmp_path / "example").mkdir()
    (tmp_path / "other/sub").mkdir(parents=True)
    try:
        (tmp_path / "example/link").symlink_to(tmp_path / "other/sub")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    (tmp_path / "other/trace.jsonl").write_text("")
    r = _run(str(tmp_path / "example/trace.jsonl"), EDIT)
    assert apply_outcomes([r], {str(tmp_path / "example/link/../trace.jsonl"): True}) == [r.name]
    assert r.passed is None
    # the same file reached through a symlinked directory is the trace's own file
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    t = _run(str(tmp_path / "alias/trace.jsonl"), EDIT)
    assert apply_outcomes([t], {str(tmp_path / "real/trace.jsonl"): False}) == []
    assert t.passed is False


def test_a_bare_name_shared_by_two_runs_is_refused_even_when_it_resolves_in_the_cwd(tmp_path, monkeypatch):
    """#936: `1.jsonl` resolves to one run from the cwd, but two runs share that name."""
    (tmp_path / "base").mkdir()
    monkeypatch.chdir(tmp_path)
    runs = [_run(str(tmp_path / "1.jsonl"), EDIT), _run(str(tmp_path / "base/1.jsonl"), EDIT)]
    with pytest.raises(ValueError, match="matches 2 runs"):
        apply_outcomes(runs, {"1.jsonl": True, str(tmp_path / "base/1.jsonl"): False})
    with pytest.raises(ValueError, match="matches 2 runs"):
        apply_outcomes(runs, {"1": True, str(tmp_path / "base/1.jsonl"): False})


def test_keys_equal_to_each_runs_relative_path_judge_each_run(tmp_path, monkeypatch):
    """A key that is a run's own path as given is no shared name: each verdict goes to its run."""
    (tmp_path / "base").mkdir()
    monkeypatch.chdir(tmp_path)
    runs = [_run("1.jsonl", EDIT), _run("base/1.jsonl", EDIT)]
    assert apply_outcomes(runs, {"1.jsonl": True, "base/1.jsonl": False}) == []
    assert [r.passed for r in runs] == [True, False]
