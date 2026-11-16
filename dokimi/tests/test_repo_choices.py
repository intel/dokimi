# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Choice events (repos/choices.py, contract §4.1): an install, import, use or removal of a named candidate."""

from __future__ import annotations

import json

import pytest

from dokimi.repos.choices import choice_timeline, validate_candidates, with_choices
from dokimi.repos.events import convert

WS = "/ws"
SKLEARN = {"sklearn": {"install": ["scikit-learn-intelex"], "import": ["sklearnex"], "use": ["patch_sklearn"]}}
SCIPY = {"lstsq": {"use": ["scipy.linalg.lstsq"]}}


def _call(tool_id: str, name: str, args: dict, t: str) -> dict:
    return {"type": "assistant", "timestamp": t,
            "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}]}}


def _result(tool_id: str, t: str) -> dict:
    return {"type": "user", "timestamp": t,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "is_error": False,
                                     "content": ""}]}}


def _bash(tool_id: str, command: str, t: str) -> list[dict]:
    return [_call(tool_id, "Bash", {"command": command}, t), _result(tool_id, t)]


def _edit(tool_id: str, path: str, new: str, t: str, old: str = "", name: str = "Edit") -> list[dict]:
    args = {"file_path": f"{WS}/{path}", "new_string": new, "old_string": old} if name == "Edit" else {
        "file_path": f"{WS}/{path}", "content": new}
    return [_call(tool_id, name, args, t), _result(tool_id, t)]


def _run(candidates: dict, *records: list[dict] | dict) -> list[dict]:
    flat = [r for rec in records for r in (rec if isinstance(rec, list) else [rec])]
    first = {"type": "system", "subtype": "init", "timestamp": "2026-01-01T00:00:00Z"}
    raw = "\n".join(json.dumps(x) for x in (first, *flat))
    out = convert("claude-stream-json", raw, WS)
    return with_choices(out.events, candidates, out.added_text)


def _choices(events: list[dict]) -> list[tuple]:
    return [(e["candidate"], e["act"], e["path"]) for e in events if e["kind"] == "choice"]


def test_install_then_import_then_use_give_one_choice_each_in_order():
    events = _run(SKLEARN,
                  _bash("a", "pip install scikit-learn-intelex", "2026-01-01T00:00:05Z"),
                  _edit("b", "app.py", "from sklearnex import patch_sklearn\npatch_sklearn()\n",
                        "2026-01-01T00:00:09Z"))
    assert [(e["kind"], e.get("act")) for e in events] == [
        ("command", None), ("choice", "install"), ("edit", None), ("choice", "import"), ("choice", "use")]
    assert events[1] == {"t": 5.0, "kind": "choice", "candidate": "sklearn", "act": "install", "path": None}
    assert events[3]["path"] == "app.py" and events[4]["path"] == "app.py"


def test_install_then_remove_are_two_choice_events():
    events = _run(SKLEARN,
                  _bash("a", "pip install scikit-learn-intelex", "2026-01-01T00:00:05Z"),
                  _bash("b", "pip uninstall scikit-learn-intelex", "2026-01-01T00:00:09Z"))
    assert _choices(events) == [("sklearn", "install", None), ("sklearn", "remove", None)]


def test_a_use_without_an_install_is_a_use_and_no_install():
    events = _run(SCIPY, _edit("a", "solve.py", "x = scipy.linalg.lstsq(A, b)\n", "2026-01-01T00:00:05Z"))
    assert _choices(events) == [("lstsq", "use", "solve.py")]


def test_only_the_added_side_of_an_edit_counts():
    events = _run(SKLEARN, _edit("a", "app.py", "import numpy\n", "2026-01-01T00:00:05Z",
                                 old="from sklearnex import patch_sklearn\n"))
    assert _choices(events) == []


def test_a_write_counts_its_whole_content():
    events = _run(SKLEARN, _edit("a", "app.py", "import sklearnex\n", "2026-01-01T00:00:05Z", name="Write"))
    assert _choices(events) == [("sklearn", "import", "app.py")]


@pytest.mark.parametrize("command", [
    "pip install 'scikit-learn-intelex==2024.7'",
    "uv pip install scikit_learn_intelex",
    "python3 -m pip install --upgrade scikit-learn-intelex",
    "pip install scikit-learn-intelex[extra]",
])
def test_a_package_manager_install_names_the_package_whatever_the_spelling(command):
    assert _choices(_run(SKLEARN, _bash("a", command, "2026-01-01T00:00:05Z"))) == [("sklearn", "install", None)]


@pytest.mark.parametrize("command", [
    "grep 'pip install scikit-learn-intelex' README",
    "echo pip install scikit-learn-intelex",
    "pip install numpy",
])
def test_a_command_that_only_mentions_the_package_is_no_choice(command):
    assert _choices(_run(SKLEARN, _bash("a", command, "2026-01-01T00:00:05Z"))) == []


def test_no_candidates_leaves_the_events_as_they_are():
    events = [{"t": 1.0, "kind": "command", "text": "pip install scikit-learn-intelex", "rc": 0}]
    assert with_choices(events, {}, []) == events


def test_the_timeline_lists_each_choice_in_trace_order():
    events = _run(SKLEARN,
                  _bash("a", "pip install scikit-learn-intelex", "2026-01-01T00:00:05Z"),
                  _edit("b", "app.py", "patch_sklearn()\n", "2026-01-01T00:00:09Z"))
    assert choice_timeline(events) == [(5.0, "sklearn", "install"), (9.0, "sklearn", "use")]


def test_a_name_listed_only_for_removal_is_no_install():
    events = _run({"x": {"remove": ["foo"]}}, _bash("a", "pip install foo", "2026-01-01T00:00:05Z"),
                  _bash("b", "pip uninstall foo", "2026-01-01T00:00:09Z"))
    assert _choices(events) == [("x", "remove", None)]


def test_a_subagents_choice_names_its_agent():
    record = {"type": "assistant", "timestamp": "2026-01-01T00:00:05Z", "parent_tool_use_id": "task-1",
              "message": {"content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                       "input": {"command": "pip install scikit-learn-intelex"}}]}}
    events = _run(SKLEARN, record, _result("a", "2026-01-01T00:00:06Z"))
    assert [e for e in events if e["kind"] == "choice"][0]["agent"] == "task-1"


def test_a_failed_install_gives_no_choice():
    failed = [_call("a", "Bash", {"command": "pip install scikit-learn-intelex"}, "2026-01-01T00:00:05Z"),
              {"type": "user", "timestamp": "2026-01-01T00:00:06Z", "message": {"content": [
                  {"type": "tool_result", "tool_use_id": "a", "is_error": True, "content": "no such package"}]}}]
    assert _choices(_run(SKLEARN, failed)) == []


def test_a_call_with_no_result_gives_no_choice():
    unresolved = [_call("a", "Bash", {"command": "pip install scikit-learn-intelex"}, "2026-01-01T00:00:05Z"),
                  _call("e", "Edit", {"file_path": f"{WS}/x.py", "old_string": "", "new_string": "import sklearnex"},
                        "2026-01-01T00:00:06Z")]
    assert _choices(_run(SKLEARN, unresolved)) == []


@pytest.mark.parametrize("rc", [None, 1])
def test_a_command_whose_exit_code_is_not_0_gives_no_choice(rc):
    assert _choices(with_choices(_bash_rc("pip install scikit-learn-intelex", rc=rc), SKLEARN, [])) == []


def test_a_module_in_a_multi_module_import_line_is_an_import():
    events = _run(SKLEARN, _edit("a", "app.py", "import os, sklearnex as sk\n", "2026-01-01T00:00:05Z"))
    assert _choices(events) == [("sklearn", "import", "app.py")]


@pytest.mark.parametrize("command", ["pip --quiet install scikit-learn-intelex", "pip3.11 install scikit-learn-intelex"])
def test_a_pip_option_or_a_versioned_pip_still_names_the_install(command):
    assert _choices(_run(SKLEARN, _bash("a", command, "2026-01-01T00:00:05Z"))) == [("sklearn", "install", None)]


def test_uvs_project_verbs_add_and_remove_name_the_package():
    events = _run(SKLEARN, _bash("a", "uv add scikit-learn-intelex", "2026-01-01T00:00:05Z"),
                  _bash("b", "uv remove scikit-learn-intelex", "2026-01-01T00:00:09Z"))
    assert _choices(events) == [("sklearn", "install", None), ("sklearn", "remove", None)]


def test_an_install_in_an_and_chain_names_the_install():
    assert _choices(_run(SKLEARN, _bash("a", "cd /ws && pip install scikit-learn-intelex",
                                        "2026-01-01T00:00:05Z"))) == [("sklearn", "install", None)]


@pytest.mark.parametrize("command", [
    # the exit code is the last part's: the install may have failed, or never run
    "python -c 'import sklearnex' || pip install scikit-learn-intelex",
    "pip install scikit-learn-intelex; echo done",
    "pip install scikit-learn-intelex 2>&1 | tail -3",
    "pip install scikit-learn-intelex &",
    "pip install --dry-run scikit-learn-intelex",
])
def test_an_install_whose_exit_code_is_not_its_own_gives_no_choice(command):
    assert _choices(_run(SKLEARN, _bash("a", command, "2026-01-01T00:00:05Z"))) == []


def test_an_import_after_a_semicolon_on_the_same_line_is_an_import():
    events = _run(SKLEARN, _edit("a", "app.py", "import os; from sklearnex import patch_sklearn\n",
                                 "2026-01-01T00:00:05Z"))
    assert ("sklearn", "import", "app.py") in _choices(events)
    events = _run(SKLEARN, _edit("a", "app.py", "import os; import sklearnex\n", "2026-01-01T00:00:05Z"))
    assert _choices(events) == [("sklearn", "import", "app.py")]


def _bash_rc(command: str, **extra) -> list[dict]:
    return [{"t": 1.0, "kind": "command", "text": command, **extra}]


def test_a_denied_install_gives_no_choice():
    events = with_choices(_bash_rc("pip install scikit-learn-intelex", rc=0, denied=True), SKLEARN, [])
    assert _choices(events) == []
    assert _choices(with_choices(_bash_rc("pip install scikit-learn-intelex", rc=0), SKLEARN, [])) == [
        ("sklearn", "install", None)]


@pytest.mark.parametrize("command", [
    "pip install scikit-learn-intelex |cat",
    "pip install scikit-learn-intelex|tail -3",
    "pip install scikit-learn-intelex --help",
    "pip install -h scikit-learn-intelex",
    "pip --version install scikit-learn-intelex",
    "pip uninstall scikit-learn-intelex --help",
    "pip install --target scikit-learn-intelex numpy",
    "pip install -r scikit-learn-intelex numpy",
    "pip install -c scikit-learn-intelex numpy",
    "pip install --index-url scikit-learn-intelex numpy",
    "conda install --name scikit-learn-intelex numpy",
    "exit 0 && pip install scikit-learn-intelex",
    "pip install numpy > scikit-learn-intelex",
    "pip install numpy 2>scikit-learn-intelex",
    "pip uninstall numpy > scikit-learn-intelex",
    "exec pip install numpy && pip install scikit-learn-intelex",
    "exec true && pip install scikit-learn-intelex",
    "FOO=1 exec true && pip install scikit-learn-intelex",
    "echo \\| pip install scikit-learn-intelex",
])
def test_a_command_that_installs_nothing_the_candidate_names_gives_no_choice(command):
    assert _choices(with_choices(_bash_rc(command, rc=0), SKLEARN, [])) == []


@pytest.mark.parametrize("command", [
    "sudo pip install scikit-learn-intelex",
    "time pip install scikit-learn-intelex",
    "sudo -n pip install scikit-learn-intelex",
    "nice -n 5 python -m pip install scikit-learn-intelex",
    "pip.exe install scikit-learn-intelex",
    "python.exe -m pip install scikit-learn-intelex",
    "pip install --target lib scikit-learn-intelex",
    "echo '|' && pip install scikit-learn-intelex",
    "exec pip install scikit-learn-intelex",
    "uv --quiet add scikit-learn-intelex",
    "pip install numpy 2>&1 > /dev/null scikit-learn-intelex",
    "pip install 'scikit-learn-intelex >= 1.0'",
])
def test_a_wrapped_or_option_laden_install_still_gives_its_choice(command):
    assert _choices(with_choices(_bash_rc(command, rc=0), SKLEARN, [])) == [("sklearn", "install", None)]


@pytest.mark.parametrize(("text", "found"), [
    ("# example; import sklearnex\n", False),
    ("# example; from sklearnex import patch_sklearn\n", False),
    ("import os, \\\n    sklearnex\n", True),
    ("if True: import sklearnex\n", True),
    ("from sklearnex.svm import SVC\n", True),
    ("from sklearnexx import y\n", False),
    ("import sklearnex.patches\n", True),
    ("import sklearnexx\n", False),
    ('s = "#"; import sklearnex\n', True),
    ('"""\nimport sklearnex\n"""\n', False),
    ("s = 'import sklearnex'\n", False),
    ('s = "a\\"b"; import sklearnex\n', True),
])
def test_an_import_is_read_from_code_not_from_a_comment(text, found):
    event = {"t": 1.0, "kind": "edit", "path": "a.py", "rc": 0}
    out = with_choices([event], SKLEARN, [(event, text)])
    assert (("sklearn", "import", "a.py") in _choices(out)) is found


def test_use_matches_a_dotted_name_only_at_a_name_boundary():
    event = {"t": 1.0, "kind": "edit", "path": "a.py", "rc": 0}
    for text, found in (("x = scipy.linalg.lstsq(a)\n", True), ("x = myscipy.linalg.lstsq(a)\n", False),
                        ("x = a.scipy.linalg.lstsq(a)\n", False), ("x = scipy.linalg.lstsqx(a)\n", False)):
        assert bool(_choices(with_choices([event], SCIPY, [(event, text)]))) is found, text


@pytest.mark.parametrize(("raw", "message"), [
    ({"sk": {"fit": ["x"]}}, "unknown act 'fit'"),
    ({"sk": {"import": ["sklearn learn"]}}, "takes a dotted Python name"),
    ({"sk": {"install": ["bad name;rm"]}}, "takes a package name"),
    ({"sk": {"install": "scikit-learn"}}, "must be a list of non-empty strings"),
    ({"sk": []}, "must map some of"),
    (["sk"], "expected a mapping"),
])
def test_a_bad_candidate_config_is_refused_with_what_is_wrong(raw, message):
    with pytest.raises(ValueError, match=message):
        validate_candidates(raw, "candidates")


def test_an_exec_of_a_removal_gives_its_choice():
    assert _choices(with_choices(_bash_rc("exec pip uninstall -y scikit-learn-intelex", rc=0), SKLEARN, [])) == [
        ("sklearn", "remove", None)]


INSTALL_SCIPY = {"scipy": {"install": ["scipy"]}}


@pytest.mark.parametrize("command", [
    "exec >install.log && pip install scipy",  # a commandless exec only redirects: the shell goes on
    "exec && pip install scipy",
    "uv --directory /tmp/project add scipy",  # uv's global options take a value: it is not the project's verb
    "uv --project=/tmp/project --quiet add scipy",
    "pip install >$(printf /tmp/install.log) scipy",  # a substitution is the redirection's target, not the package
])
def test_a_command_the_shell_runs_before_or_around_its_redirects_gives_its_install(command):
    assert _choices(_run(INSTALL_SCIPY, _bash("a", command, "2026-01-01T00:00:05Z"))) == [("scipy", "install", None)]


@pytest.mark.parametrize("command", [
    "pip install scipy 2>logs\\\\|cat",  # ``\\`` is a backslash: the pipe after it runs cat, whose code is the result
    "exec true && pip install scipy",  # exec with a program replaces the shell: nothing after it runs
])
def test_a_pipeline_or_an_exec_with_a_program_gives_no_install(command):
    assert _choices(_run(INSTALL_SCIPY, _bash("a", command, "2026-01-01T00:00:05Z"))) == []
