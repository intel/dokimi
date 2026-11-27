# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The CLI fault contract: every path parameter of every command, fed a hostile input, refuses it cleanly.

The cases come from the parser tree (tests/cli_faults.py), so a new command or option is covered
without a new test. For each case the command runs in-process as `cli.main` runs it and must:

- exit with the code docs/quickstart.md ("Exit codes") gives a bad input: 2 for "a missing,
  unreadable or malformed input file ... an output file that cannot be written", or 1 too for
  `repo run` and `eval check`, which that section says still exit 1 for some input refusals;
- print no ``Traceback`` (the table's exit 5, a crash);
- leave nothing behind and change nothing, in the working directory or outside it;
- make no model call before it refuses a path it cannot read.

A readable file gets four more values: JSON cut short, which is a malformed input (exit 2), and a JSON list,
``null`` and a JSON string, which a command may accept and may refuse but must not crash on. A command that
answers one of the four as it answers a good file did not parse it as JSON, and the case is skipped.

The run is offline and spends nothing: provider keys are unset, every model call goes to a fake
that records it, and PATH holds only git, so no agent CLI or docker can start.

A case counts only when it reaches its parameter. Every other required argument gets a filler, and
a command may refuse the filler first; so each parameter is also run once with a good value (the
control), and a case the command answers exactly as it answers the control is skipped as not
reached, never passed. A command none of whose path parameters is reached fails
``test_the_harness_reaches_a_path_parameter_of_every_command`` until the harness gets a filler
for it (tests/cli_faults.py) or the command gets an ``unreached`` row.

A known failure is a row of tests/data/cli_fault_allowlist.txt saying what fails. A row whose case
now passes, is not reached, or names no case fails the test, so the list only shrinks. A failure that
depends on the interpreter says so (``python <3.14: ...``) and is a row on those versions only:
``Path.exists()`` and its kin raise PermissionError under an unreadable directory before 3.14 and
answer False from 3.14 on.
"""

from __future__ import annotations

import functools
import importlib.util
import io
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from dokimi import cli
from tests import cli_faults

ALLOWLIST = Path(__file__).parent / "data" / "cli_fault_allowlist.txt"
REFUSAL = frozenset({2})
# The names kept by hand are in tests/cli_faults.py: CI reruns a changed test file 20 times, a changed helper not at all.
DOCUMENTED, NEEDS_MODULE, UNREACHABLE, CENTRAL = (
    cli_faults.DOCUMENTED, cli_faults.NEEDS_MODULE, cli_faults.UNREACHABLE, cli_faults.CENTRAL)
# Faults no command can read past: a model call before the refusal spends money on a bad invocation.
UNREADABLE = frozenset({"missing", "directory", "not-a-directory", "unreadable", "dangling-link",
                        "unwritable-parent"})
NOT_REACHED = "unreached"
_SECRET_ENV = re.compile(r"(_API_KEY|_TOKEN|_SECRET)$|^DOKIMI_")
UNSCRIPTED = "FakeAdapter: unscripted call"  # what the fake adapter raises for a model call no test scripted
_USAGE_ARG = re.compile(r"error: argument ([^:]+):")

PARSER = cli.build_parser()
LEAVES = dict(cli_faults.leaves(PARSER))
CASES = cli_faults.cases(PARSER)


_ON_PYTHON = re.compile(r"^python (<|>=) ?(\d+)\.(\d+): *")


def _on_this_python(reason: str, version: tuple[int, int]) -> bool:
    """Whether a row whose reason starts ``python <3.14:`` or ``python >=3.14:`` describes this interpreter."""
    tag = _ON_PYTHON.match(reason)
    if tag is None:
        return True
    bound = (int(tag[2]), int(tag[3]))
    return version < bound if tag[1] == "<" else version >= bound


@functools.lru_cache(maxsize=None)
def _parsed(path: Path) -> tuple[tuple[str, str, str, str], ...]:
    """Every row of the allowlist file, read and checked once per process."""
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = tuple(part.strip() for part in line.split("|", 3))
        if len(parts) != 4:
            raise ValueError(f"{path.name}:{number}: expected `command | parameter | fault | reason`, got {line!r}")
        rows.append(parts)
    return tuple(rows)  # type: ignore[arg-type]


def _rows(path: Path, version: tuple[int, int] | None = sys.version_info[:2]) -> list[tuple[str, str, str, str]]:
    """The rows that describe Python ``version``; every row when it is None."""
    return [row for row in _parsed(path) if version is None or _on_this_python(row[3], version)]


def load_allowlist(path: Path = ALLOWLIST, version: tuple[int, int] | None = sys.version_info[:2]) -> dict[str, str]:
    """``command | parameter | fault`` -> what fails, one row per known failure on Python ``version``."""
    return {f"{command} | {key} | {fault}": reason
            for command, key, fault, reason in _rows(path, version) if fault != NOT_REACHED}


def load_unreached(path: Path = ALLOWLIST) -> dict[str, str]:
    """Command -> why the harness reaches none of its path parameters (rows ``command | * | unreached | why``)."""
    return {command: reason for command, _, fault, reason in _rows(path) if fault == NOT_REACHED}


@dataclass
class Result:
    code: int
    err: str
    left: list[str]
    model_calls: int


def _exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, str):  # sys.exit("message") prints it and exits 1
        return 1
    return exc.code or 0


def _git_only_path(bin_dir: Path, windows: bool = os.name == "nt") -> str:
    """A PATH that holds git and nothing else: a link to it in ``bin_dir``, or on Windows the directory of ``git.exe``."""
    git = shutil.which("git")
    if git and windows:
        return str(Path(git).parent)
    bin_dir.mkdir()
    if git:
        (bin_dir / "git").symlink_to(git)
    return str(bin_dir)


def _hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path) -> None:
    for name in list(os.environ):
        if _SECRET_ENV.search(name):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PATH", _git_only_path(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "home" / ".cache"))
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


def run_case(case: cli_faults.Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
             capsys: pytest.CaptureFixture[str], fake_llm) -> Result:
    """Build the fault, run the command in-process and see what it exited with and left behind."""
    root, outside = tmp_path / "root", tmp_path / "outside"
    for directory in (root, outside, tmp_path / "home"):
        directory.mkdir(parents=True)
    capsys.readouterr()
    del fake_llm.requests[:]
    try:
        value = cli_faults.build(case, root, outside)
    except cli_faults.Unbuildable as exc:
        pytest.skip(str(exc))
    args = cli_faults.argv(case, LEAVES[case.param.command], value, root)
    before = (cli_faults.tree(root), cli_faults.tree(outside))
    _hermetic(monkeypatch, tmp_path, root)
    monkeypatch.setattr(sys, "argv", ["dokimi", *args])
    try:
        cli.main()
        code = 0
    except SystemExit as exc:
        if isinstance(exc.code, str):  # the interpreter prints it on the way out; this run never gets there
            print(exc.code, file=sys.stderr)
        code = _exit_code(exc)
    finally:
        cli_faults.unlock(tmp_path)
    err = capsys.readouterr().err
    left = cli_faults.changes(before[0], cli_faults.tree(root)) + [
        f"../outside/{p}" for p in cli_faults.changes(before[1], cli_faults.tree(outside))]
    return Result(code, err, left, len(fake_llm.requests))


def problems(case: cli_faults.Case, result: Result) -> list[str]:
    """What the case breaks of the contract; empty when it holds."""
    allowed = DOCUMENTED.get(case.param.command, REFUSAL)
    found = []
    lines = [line.strip() for line in result.err.splitlines() if line.strip()]
    if case.fault in cli_faults.SHAPES:  # the command may accept the shape; what it may not do is crash on it
        crashed = result.code == 5 or "Traceback" in result.err
        return [f"exit {result.code} with a traceback ({lines[-1][:160] if lines else 'empty'})"] if crashed else []
    if result.model_calls and case.fault in UNREADABLE:
        found.append(f"{result.model_calls} model call(s) before the refusal")
    if result.model_calls and case.fault == "truncated-json":  # cut-short JSON is no input: a call made on it is not a refusal
        return [f"{result.model_calls} model call(s) on JSON cut short, which is not refused"]
    if result.code not in allowed:
        found.append(f"exit {result.code}, not {sorted(allowed)} (stderr: {lines[-1][:160] if lines else 'empty'})")
    if "Traceback" in result.err:
        found.append(f"traceback ({lines[-1][:160]})")
    if result.left:
        found.append(f"left behind or changed {', '.join(result.left[:4])}")
    return found


def _answer(result: Result, base: Path) -> tuple[int, str]:
    """What the command answered, without the temporary directory and the numbers that differ from run to run."""
    return result.code, re.sub(r"\d+", "N", result.err.replace(str(base), "<tmp>"))


_CONTROLS: dict[cli_faults.Param, tuple[int, str]] = {}


def control(param: cli_faults.Param, tmp_path: Path, monkeypatch, capsys, fake_llm) -> tuple[int, str]:
    """The command's answer to a good value for ``param``, every other argument as in its cases; once per process."""
    if param not in _CONTROLS:
        base = tmp_path / f"control{len(_CONTROLS)}"
        with monkeypatch.context() as patch:
            _CONTROLS[param] = _answer(run_case(cli_faults.Case(param, cli_faults.GOOD), base, patch, capsys, fake_llm), base)
    return _CONTROLS[param]


def reached(case: cli_faults.Case, result: Result, base: Path, answer: tuple[int, str]) -> bool:
    """Whether the hostile value changed anything: not when the control was refused and this run was refused in the same words.

    A model call with a path no command can read past, or with JSON cut short, is a break whatever came next, so that run counts as reached.
    """
    if result.model_calls and (case.fault in UNREADABLE or case.fault == "truncated-json"):
        return True
    if result.left:  # a run that changed files must reach the filesystem checks, whatever it answered
        return True
    if case.fault in cli_faults.CONTENT:  # read as text, or not read at all: the file's JSON was never parsed
        return _answer(result, base) != answer
    return answer[0] == 0 or _answer(result, base) != answer


def _crashed_on_shape(case: cli_faults.Case, result: Result) -> bool:
    """A JSON-shape run that crashed in the product: not the fake adapter's refusal of a call nobody scripted."""
    crashed = result.code == 5 or "Traceback" in result.err
    return case.fault in cli_faults.SHAPES and crashed and UNSCRIPTED not in result.err


def _usage_gap(case: cli_faults.Case, result: Result) -> str | None:
    """An argparse refusal of another argument: the harness's filler is wrong, the case never ran."""
    match = _USAGE_ARG.search(result.err)
    if result.code != 2 or "usage:" not in result.err:
        return None
    if match and case.param.key in match.group(1).split("/"):
        return None  # argparse refused the hostile value itself: a clean refusal
    return f"argparse refused the argv before the command ran: {result.err.strip().splitlines()[-1]}"


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_a_hostile_path_is_refused_cleanly(case, tmp_path, monkeypatch, capsys, fake_llm):
    module = NEEDS_MODULE.get(case.param.command)
    if module and importlib.util.find_spec(module) is None:
        pytest.skip(f"{case.param.command} exits before its inputs without the {module} extra")
    if case.id in UNREACHABLE:
        pytest.skip(UNREACHABLE[case.id])
    answer = control(case.param, tmp_path, monkeypatch, capsys, fake_llm)
    base = tmp_path / "case"
    result = run_case(case, base, monkeypatch, capsys, fake_llm)
    gap = _usage_gap(case, result)
    assert gap is None, gap
    known = load_allowlist().get(case.id)
    if not reached(case, result, base, answer):
        assert known is None, f"{case.id} is not reached: delete its row from {ALLOWLIST.name} ({known})"
        pytest.skip(f"not reached: a good value is refused in the same words ({_last(answer[1])})")
    if result.model_calls and case.fault not in UNREADABLE and case.fault != "truncated-json" and not _crashed_on_shape(case, result):
        pytest.skip("the file reads as valid input here: the command went on to a model call")
    found = problems(case, result)
    if known is None:
        assert not found, f"{case.id}: {'; '.join(found)}"
    else:
        assert found, f"{case.id} now passes: delete its row from {ALLOWLIST.name} ({known})"


PARAMS = sorted({c.param for c in CASES}, key=lambda p: (p.command, p.key))
COMMANDS = sorted({p.command for p in PARAMS})


def _good_id(command: str) -> str:
    return f"{command} | * | {cli_faults.GOOD}"


@pytest.mark.parametrize("command", COMMANDS, ids=[_good_id(c) for c in COMMANDS])
def test_well_formed_values_get_no_traceback(command, tmp_path, monkeypatch, capsys, fake_llm):
    """The controls of a command: an empty JSON object for a file, a directory holding one skill, are never a crash."""
    if NEEDS_MODULE.get(command) and importlib.util.find_spec(NEEDS_MODULE[command]) is None:
        pytest.skip(f"{command} exits before its inputs without the {NEEDS_MODULE[command]} extra")
    answers = [control(p, tmp_path, monkeypatch, capsys, fake_llm) for p in PARAMS if p.command == command]
    crashes = sorted({_last(err) for code, err in answers if code == 5 or "Traceback" in err})
    known = load_allowlist().get(_good_id(command))
    if known is None:
        assert not crashes, f"{_good_id(command)}: {'; '.join(crashes)}"
    else:
        assert crashes, f"{_good_id(command)} no longer crashes: delete its row from {ALLOWLIST.name} ({known})"


def _last(err: str) -> str:
    lines = [line.strip() for line in err.splitlines() if line.strip()]
    return lines[-1][:160] if lines else "nothing on stderr"


def test_every_allowlist_row_names_a_case_and_says_what_fails():
    ids = {c.id for c in CASES} | {_good_id(c) for c in COMMANDS}
    rows = load_allowlist(version=None)
    assert not [k for k in rows if k not in ids], "allowlist rows that name no generated case"
    assert all(rows.values()), "an allowlist row without its reason"
    unreached = load_unreached()
    assert not [c for c in unreached if c not in {case.param.command for case in CASES}], "unreached rows that name no command with a path parameter"
    assert all(unreached.values()), "an unreached row without its reason"


def test_the_harness_reaches_a_path_parameter_of_every_command(tmp_path, monkeypatch, capsys, fake_llm):
    """A command whose every case is refused before its path is read is tested by nothing here.

    A new command lands in this state when another required argument needs a real value: give the
    harness one (``FILLERS`` or ``FILE_FILLERS`` in tests/cli_faults.py), or add the row
    ``<command> | * | unreached | <what refuses first>``. A row whose command is reached fails too.
    """
    listed = load_unreached()
    by_command: dict[str, list[cli_faults.Case]] = {}
    for case in CASES:
        by_command.setdefault(case.param.command, []).append(case)
    blind, stale = {}, []
    for number, (command, cases) in enumerate(sorted(by_command.items())):
        if NEEDS_MODULE.get(command) and importlib.util.find_spec(NEEDS_MODULE[command]) is None:
            continue
        hit, answer = False, (0, "")
        for index, case in enumerate(cases):
            if case.id in UNREACHABLE:
                continue
            with monkeypatch.context() as patch:
                answer = control(case.param, tmp_path, patch, capsys, fake_llm)
                base = tmp_path / f"c{number}_{index}"
                try:
                    result = run_case(case, base, patch, capsys, fake_llm)
                except pytest.skip.Exception:
                    continue
            if reached(case, result, base, answer):
                hit = True
                break
        if not hit and command not in listed:
            blind[command] = _last(answer[1])
        if hit and command in listed:
            stale.append(command)
    assert not blind, "no path parameter of these commands is reached; a good value is refused with: " + "; ".join(
        f"{c}: {why}" for c, why in blind.items())
    assert not stale, f"reached now: delete the unreached row of {', '.join(stale)} from {ALLOWLIST.name}"


def test_every_hand_kept_name_is_still_in_the_parser():
    """A renamed command or option leaves its row here naming nothing; say which row, on the change that renamed it."""
    commands = set(LEAVES)
    options = {cli_faults._key(a) for leaf in LEAVES.values() for a in leaf._actions}
    case_ids = {f"{c.param.command} | {c.param.key} | {f}" for c in CASES for f in cli_faults.FAULTS[c.param.role] + cli_faults.ESCAPES}
    gone = [name for name in (*CENTRAL, *DOCUMENTED, *NEEDS_MODULE) if name not in commands]
    gone += [name for name in (*cli_faults.FILLERS, *cli_faults.FILE_FILLERS) if name not in options]
    gone += [name for name in cli_faults.NOT_PATHS
             if name not in options and not any(name == f"{c} {a.dest}" for c, leaf in LEAVES.items() for a in leaf._actions)]
    gone += [name for name in (*UNREACHABLE, *cli_faults.INAPPLICABLE) if name not in case_ids]
    assert not gone, f"named in tests/cli_faults.py or this file, but not in the CLI: {gone}"


def test_the_central_commands_have_their_path_parameters_covered():
    covered = {c.param.command for c in CASES}
    assert [c for c in CENTRAL if c not in covered] == []
    assert {"input-file", "input-dir", "output-file", "output-dir"} <= {c.param.role for c in CASES}
