# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context consistency` reads one Bash call as the commands it runs (dokimi.context.shell)."""

from __future__ import annotations

import json

import pytest

from dokimi.context.consistency import (Matcher, apply_outcomes, check_contract, load_outcomes, parse_contract,
                                        parse_run, profile, run_from_events, _events)
from dokimi.context.shell import program, split_commands, written


def _steps(command: str) -> list[tuple[str, str]]:
    return [(e.kind, e.action) for e in _events("Bash", {"command": command})]


@pytest.mark.parametrize("command, steps", [
    ("for f in a b; do black $f; done", [("Bash:black", "Bash:black(black $f)")]),
    ("for f in a; do black x; done", [("Bash:black", "Bash:black(black x)")]),
    ("if [ -f x ]; then make; fi", [("Bash:make", "Bash:make(make)")]),
    ("if pytest -q; then make; fi", [("Bash:pytest", "Bash:pytest(pytest -q)"), ("Bash:make", "Bash:make(make)")]),
    ("(cd sub && pytest -q)", [("Bash:pytest", "Bash:pytest(pytest -q)")]),
    ("# note && x\npytest", [("Bash:pytest", "Bash:pytest(pytest)")]),
    (r"find . -name '*.pyc' -exec rm {} \; -print",
     [("Bash:find", r"Bash:find(find . -name '*.pyc' -exec rm {} \; -print)")]),
    (r"echo a\;b; ls", [("Bash:echo", r"Bash:echo(echo a\;b)"), ("Bash:ls", "Bash:ls(ls)")]),
    ("echo don\\'t; ls", [("Bash:echo", "Bash:echo(echo don\\'t)"), ("Bash:ls", "Bash:ls(ls)")]),
    ("x=$(echo a && echo b); pytest", [("Bash:pytest", "Bash:pytest(pytest)")]),
    ("x=$(git rev-parse HEAD; ls) && pytest", [("Bash:pytest", "Bash:pytest(pytest)")]),
    ("X=$(git rev-parse HEAD) && pytest", [("Bash:pytest", "Bash:pytest(pytest)")]),
    ("echo `a; b`; ls", [("Bash:echo", "Bash:echo(echo `a; b`)"), ("Bash:ls", "Bash:ls(ls)")]),
    ("make & pytest", [("Bash:make", "Bash:make(make)"), ("Bash:pytest", "Bash:pytest(pytest)")]),
    ("make 2>&1 && pytest", [("Bash:make", "Bash:make(make 2>&1)"), ("Bash:pytest", "Bash:pytest(pytest)")]),
    ("case $x in a) make ;; b) ls ;; esac", [("Bash:make", "Bash:make(make)"), ("Bash:ls", "Bash:ls(ls)")]),
    ("case $x in\n a) make ;;\n b) ls ;;\nesac", [("Bash:make", "Bash:make(make)"), ("Bash:ls", "Bash:ls(ls)")]),
    ("timeout 300 pytest", [("Bash:pytest", "Bash:pytest(timeout 300 pytest)")]),
    ("timeout -k 5 300 pytest", [("Bash:pytest", "Bash:pytest(timeout -k 5 300 pytest)")]),
    ("nohup pytest", [("Bash:pytest", "Bash:pytest(nohup pytest)")]),
    ("nice -n 10 pytest", [("Bash:pytest", "Bash:pytest(nice -n 10 pytest)")]),    ("{ ruff check .; pytest; }", [("Bash:ruff", "Bash:ruff(ruff check .)"), ("Bash:pytest", "Bash:pytest(pytest)")]),
    ("{ make; }", [("Bash:make", "Bash:make(make)")]),
    ("{ a; b; } > f.txt", [("Bash:a", "Bash:a(a)"), ("Bash:b", "Bash:b(b)")]),
    ("sudo -u bob make test", [("Bash:make", "Bash:make(sudo -u bob make test)")]),
    ("env -u FOO make", [("Bash:make", "Bash:make(env -u FOO make)")]),
    ("uv run --with rich pytest", [("Bash:pytest", "Bash:pytest(uv run --with rich pytest)")]),
    ("time -p pytest", [("Bash:pytest", "Bash:pytest(time -p pytest)")]),
    ("command -v make", [("Bash:make", "Bash:make(command -v make)")]),
])
def test_control_syntax_and_wrappers_are_not_steps(command, steps):
    assert _steps(command) == steps


def test_a_separator_inside_quotes_or_substitution_does_not_split():
    assert [s for s, _ in split_commands("echo 'a;b' \"c && d\" $(e; f) `g; h`")] == [
        "echo 'a;b' \"c && d\" $(e; f) `g; h`"]


def test_a_chained_line_reports_the_separator_after_each_command():
    assert split_commands("a && b || c; d & e\nf") == [
        ("a", "&&"), ("b", "||"), ("c", ";"), ("d", "&"), ("e", "\n"), ("f", "")]


@pytest.mark.parametrize("command, file", [
    ("sed -i s/a/b/ f", "f"), ("FOO=1 sed -i s/a/b/ f", "f"), ("sudo sed -i s/a/b/ f", "f"),
    ("env FOO=1 sed -i s/a/b/ f", "f"), ("uv run sed -i s/a/b/ f", "f"), ("sed --in-place s/a/b/ f", "f"),
    ("sed -ni s/a/b/ f", "f"), ("perl -pi -e s/a/b/ src/a.py", "src/a.py"), ("perl -Mstrict -e 'print 1' foo.pl", ""),
    ("sudo tee x", "x"), ("echo hi | sudo tee x", "x"), ("echo hi | tee -a x", "x"),
    ("echo tee x", ""), ("echo tee out.txt", ""), ("sed -i s/a/b/", ""),
    ("echo x >| out.txt", "out.txt"), ("echo x > f", "f"), ("echo 3 > count.txt", "count.txt"),
    ("seq 1 10 > out.txt", "out.txt"), ("pytest 2>/dev/null", ""), ("pytest 2> err.log", ""),
    ("echo a2>/dev/null 2 > f", "f"),
    ("echo $# > out.txt", "out.txt"), ("echo ${#x} > out.txt", "out.txt"),
])
def test_written_file_after_prefixes_and_flags(command, file):
    assert written(command) == file


def test_a_hash_inside_a_word_does_not_cut_the_line_before_a_pipe():
    assert [e.masked for e in _events("Bash", {"command": "echo ${#x} | grep 1"})] == [True]


def test_a_shell_write_is_an_edit_for_edit_matchers_only():
    ev = _events("Bash", {"command": "sed -i s/a/b/ f"})[0]
    assert Matcher(tools=("Edit",))(ev) and not Matcher(tools=("Read",))(ev)
    assert not Matcher(tools=("Edit",))(_events("Bash", {"command": "pytest > test.log"})[0])


def test_wrapper_programs_are_found():
    assert program("timeout 300 uv run python -m pytest") == "pytest"
    assert program("FOO=1 nice -n 10 nohup make") == "make"


def _trace(*calls, result=True, is_error=False):
    lines = []
    for i, (tool, args, failed) in enumerate(calls):
        lines.append({"type": "assistant", "message": {"id": f"m{i}", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": tool, "input": args}]}})
        lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x", "is_error": failed}]}})
    if result:
        lines.append({"type": "result", "subtype": "success", "is_error": is_error, "num_turns": len(calls)})
    return "\n".join(json.dumps(x) for x in lines)


LAST_OK = parse_contract({"actions": {"test": {"tool": "Bash", "command": "pytest"}}, "rules": [{"last_ok": "test"}]})


@pytest.mark.parametrize("command, hidden", [
    ("pytest -q; echo done", True), ("pytest -q || true", True), ("pytest -q\ngit status", True),
    ("pytest -q & echo done", True), ("pytest -q && git status", False), ("pytest -q", False),
    ("pytest -q | tail -5", True), ("set -e; pytest -q; echo done", False),
    ("set -e; pytest -q || true", True), ("for f in a; do pytest $f; done", False),
])
def test_a_later_command_hides_the_status_of_an_earlier_one(command, hidden):
    for grain in ("split", "call"):
        run = parse_run(_trace(("Bash", {"command": command}, False)), grain=grain)
        assert (check_contract(run, LAST_OK) != []) is hidden, (command, grain)


def test_a_chained_call_is_one_call():
    run = parse_run(_trace(("Bash", {"command": "cd x && make && make test && git diff"}, True),
                           ("Read", {"file_path": "/w/a"}, False)))
    assert len(run.events) == 3 + 1 and run.calls() == 2 and run.failed_calls() == 1
    path = profile([run, run])["path"]
    assert path["tool_calls_mean"] == 2 and path["failed_calls_mean"] == 1


def test_a_run_that_made_calls_but_wrote_no_result_failed():
    died = parse_run(_trace(("Read", {"file_path": "/w/a"}, False), result=False))
    done = parse_run(_trace(("Read", {"file_path": "/w/a"}, False)))
    assert died.passed is False and done.passed is True
    rep = profile([done, died, died, died])["result"]
    assert (rep["judged"], rep["passed"], rep["success_rate"]) == (4, 1, 0.25)
    assert parse_run("").passed is None  # nothing happened: nothing to judge


@pytest.mark.parametrize("contract, needle", [
    ({"actions": {"a": {"tool": "Bash"}}, "rules": [{"requires": 5}]}, "requires"),
    ({"actions": {"a": {"tool": "Bash"}}, "rules": [{"requires": [["a"]]}]}, "requires"),
    ({"actions": {"a": {"tool": "Bash"}}, "rules": [{"requires": {"a": 1}}]}, "requires"),
    ({"actions": {"a": {"tool": 5}}, "rules": []}, "tool"),
    ({"actions": {"a": {"tool": [["Bash"]]}}, "rules": []}, "tool"),
])
def test_a_malformed_rule_argument_or_tool_is_a_value_error(contract, needle):
    with pytest.raises(ValueError, match=needle):
        parse_contract(contract)


def test_load_outcomes_wants_booleans(tmp_path):
    o = tmp_path / "o.json"
    o.write_text('{"a": 1}')
    with pytest.raises(ValueError, match="true/false"):
        load_outcomes(o)


def test_an_outcome_key_may_be_a_stem():
    runs = [parse_run("", "dir/run1.jsonl"), parse_run("", "dir/run2.jsonl")]
    assert apply_outcomes(runs, {"run1": True, "run2": False}) == []
    assert [r.passed for r in runs] == [True, False]


def test_a_deeply_nested_trace_line_is_skipped_not_fatal():
    raw = "[" * 200000 + "\n" + _trace(("Read", {"file_path": "/w/a"}, False))
    assert [e.kind for e in parse_run(raw).events] == ["Read"]


def test_repeated_steps_collapse_into_one_strategy_shape():
    a = parse_run(_trace(*[("Read", {"file_path": "/w/a"}, False)] * 3, ("Edit", {"file_path": "/w/b"}, False)))
    b = parse_run(_trace(("Read", {"file_path": "/w/a"}, False), ("Edit", {"file_path": "/w/b"}, False)))
    assert a.shape() == b.shape() == ["Read", "Edit"]


def test_events_from_a_normalized_trace_split_like_a_bash_call():
    run = run_from_events([{"kind": "command", "text": "make && pytest", "rc": 1}, {"kind": "read", "path": "a"}])
    assert [e.kind for e in run.events] == ["Bash:make", "Bash:pytest", "Read"]
    assert run.calls() == 2 and run.failed_calls() == 1
