# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/mutate_changed.py — mutation of a PR's changed lines."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mutate_changed.py"
_spec = importlib.util.spec_from_file_location("mutate_changed", _SCRIPT)
assert _spec and _spec.loader
mc = importlib.util.module_from_spec(_spec)
sys.modules["mutate_changed"] = mc
_spec.loader.exec_module(mc)

SRC = '''def f(a, b):
    """Doc: a == b."""
    if a == b and a > 0:
        return a + 1
    return not b
'''


def _ops(lines: set[int]) -> list[tuple[int, str, str]]:
    return [(m.line, m.op, mc.apply(SRC, m)) for m in mc.mutants_for("x.py", SRC, lines)]


def test_mutants_only_on_changed_lines_and_each_parses() -> None:
    got = _ops({3})
    assert {op for _, op, _ in got} == {"flip-compare", "swap-boolop", "negate-condition", "bump-int"}
    assert all(line == 3 for line, _, _ in got)
    for _, _, text in got:
        ast.parse(text)
        assert text.splitlines()[3:] == SRC.splitlines()[3:]  # lines after the mutant untouched
    texts = {(op, t.splitlines()[2]) for _, op, t in got}
    assert texts == {
        ("flip-compare", "    if a != b and a > 0:"),
        ("flip-compare", "    if a == b and a <= 0:"),
        ("swap-boolop", "    if a == b or a > 0:"),
        ("negate-condition", "    if not (a == b and a > 0):"),
        ("bump-int", "    if a == b and a > 1:"),
    }


def test_return_addsub_and_not_mutants() -> None:
    got = {(line, op, t.splitlines()[line - 1].strip()) for line, op, t in _ops({4, 5})}
    assert got == {
        (4, "return-none", "return None"),
        (4, "swap-addsub", "return a - 1"),
        (4, "bump-int", "return a + 2"),
        (5, "return-none", "return None"),
        (5, "drop-not", "return (b)"),
    }


def test_docstring_lines_are_not_mutated() -> None:
    assert _ops({2}) == []


def test_string_concatenation_is_not_swapped() -> None:
    assert mc.mutants_for("x.py", "y = 'a' + b\n", {1}) == []


def test_apply_uses_utf8_byte_columns() -> None:
    src = "x = 'é' if a < b else 0\n"
    (m,) = [m for m in mc.mutants_for("x.py", src, {1}) if m.op == "flip-compare"]
    assert mc.apply(src, m) == "x = 'é' if a >= b else 0\n"


def test_pick_spreads_the_cap_over_lines() -> None:
    ms = mc.mutants_for("x.py", SRC, {3, 4, 5})
    chosen = mc.pick(ms, 3)
    assert [m.line for m in chosen] == [3, 4, 5]
    assert len(mc.pick(ms, 100)) == len(ms)


def test_candidate_tests_match_dotted_and_from_imports(tmp_path: Path) -> None:
    (tmp_path / "test_a.py").write_text("from dokimi.subjects import scorecard\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("import dokimi.subjects.scorecard as s\n", encoding="utf-8")
    (tmp_path / "test_c.py").write_text("from dokimi.subjects import scorecards\n", encoding="utf-8")
    assert mc.candidate_tests(["dokimi/dokimi/subjects/scorecard.py"], tmp_path) == ["tests/test_a.py", "tests/test_b.py"]
    (tmp_path / "test_scorecard_x.py").write_text("from dokimi.subjects import scorecard\n", encoding="utf-8")
    assert mc.candidate_tests(["dokimi/dokimi/subjects/scorecard.py"], tmp_path)[0] == "tests/test_scorecard_x.py"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_changed_lines_reads_added_and_edited_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (pkg / "m.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    (pkg / "m.py").write_text("a = 1\nb = 5\nc = 3\nd = 4\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("x = 2\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "head")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    assert mc.changed_lines("HEAD~1") == {"dokimi/dokimi/m.py": {2, 4}}
    with pytest.raises(SystemExit, match="git merge-base"):
        mc.changed_lines("no-such-ref")


def test_markdown_lists_survivors_and_uncovered() -> None:
    m = mc.Mutant("dokimi/dokimi/x.py", 3, "flip-compare", (3, 4), (3, 10), "a != b", "a == b", status="survived", tests=["t"])
    k = mc.Mutant("dokimi/dokimi/x.py", 4, "return-none", (4, 8), (4, 9), "None", "a", status="killed")
    k2 = mc.Mutant("dokimi/dokimi/x.py", 5, "return-none", (5, 8), (5, 9), "None", "a", status="killed")
    e = mc.Mutant("dokimi/dokimi/x.py", 6, "return-none", (6, 8), (6, 9), "None", "a", status="error")
    text = mc.markdown({"mutants": [vars(m), vars(k), vars(k2), vars(e)], "mutants_covered": 4, "max_mutants": 40,
                        "uncovered_lines": {"dokimi/dokimi/x.py": [7]}, "seconds": 1.0})
    assert "2 killed" in text and "**1 survived**" in text and "1 errors" in text
    assert "| `dokimi/dokimi/x.py:3` | flip-compare | `a == b` | `a != b` | 1 |" in text
    assert "`dokimi/dokimi/x.py`: 7" in text


def _mutation_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, src: str, tests: str, cover: dict[int, list[str]], *flags: str,
    skipped: tuple[str, ...] = (), broken: tuple[str, ...] = (),
) -> tuple[int, dict]:
    """Run the script on a repo whose head adds ``dokimi/zz.py`` and its tests; returns (exit code, JSON report).

    ``cover`` (zz.py line -> covering test ids) stands in for the coverage pass: the suite's
    test environments have no coverage package, and covering_tests has its own test below.
    ``skipped`` (left for lack of time) and ``broken`` (failing to collect) name candidate test
    files that import zz and did not run; they are written into the repo as well.
    """
    if not (tmp_path / ".git").exists():
        pkg = tmp_path / "dokimi" / "dokimi"
        pkg.mkdir(parents=True)
        (tmp_path / "dokimi" / "tests").mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "t@example.com")
        _git(tmp_path, "config", "user.name", "t")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", "base")
        (pkg / "zz.py").write_text(src, encoding="utf-8")
        (tmp_path / "dokimi" / "tests" / "test_zz.py").write_text(tests, encoding="utf-8")
        for name in (*skipped, *broken):
            (tmp_path / "dokimi" / name).write_text("from dokimi.zz import b\n", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-qm", "head")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    monkeypatch.setattr(mc, "PROJECT", tmp_path / "dokimi")
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    # ci/test.py sets PYTHONSAFEPATH=1: pytest in the copy would then import the installed dokimi, not its zz.py.
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)

    def covering(work: Path, files: list[str], paths: list[str], budget: float) -> tuple[dict, list[str], list[str]]:
        mc._pytest(work / "dokimi", files, budget)  # imports the unmutated zz.py first, as the coverage pass does
        return {"dokimi/dokimi/zz.py": cover}, list(broken), list(skipped)

    monkeypatch.setattr(mc, "covering_tests", covering)
    out = tmp_path / "report.json"
    rc = mc.main(["--base", "HEAD~1", "--budget", "100", "--mutant-timeout", "30", "--json", str(out), *flags])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _statuses(report: dict) -> dict[tuple[int, str], str]:
    return {(m["line"], m["replacement"]): m["status"] for m in report["mutants"]}


def test_a_same_size_mutant_in_the_same_second_runs_its_own_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = mc._pytest

    def same_second(cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
        os.utime(cwd / "dokimi" / "zz.py", (1_700_000_000, 1_700_000_000))
        return real(cwd, args, timeout, env)

    monkeypatch.setattr(mc, "_pytest", same_second)
    src = "def a(x):\n    return x == 1\n"
    tests = "from dokimi.zz import a\n\n\ndef test_a():\n    assert a(1) is True\n    assert a(2) is False\n"
    _, report = _mutation_run(tmp_path, monkeypatch, src, tests, {2: ["tests/test_zz.py::test_a"]})
    assert _statuses(report) == {(2, "x != 1"): "killed", (2, "2"): "killed", (2, "None"): "killed"}


def test_a_mutant_only_the_last_of_many_covering_tests_catches_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = "def a(x):\n    return x > 3\n"
    tests = (
        "import pytest\n\nfrom dokimi.zz import a\n\n\n"
        "@pytest.mark.parametrize('n', range(30))\ndef test_a_weak(n):\n    assert a(10 + n)\n\n\n"
        "def test_z_strict():\n    assert a(4) and not a(3)\n"
    )
    weak = [f"tests/test_zz.py::test_a_weak[{n}]" for n in range(30)]
    _, report = _mutation_run(tmp_path, monkeypatch, src, tests, {2: sorted([*weak, "tests/test_zz.py::test_z_strict"])})
    assert _statuses(report) == {(2, "x <= 3"): "killed", (2, "4"): "killed", (2, "None"): "killed"}
    assert {len(m["tests"]) for m in report["mutants"]} == {31}


def test_survivors_kills_and_the_fail_on_survivor_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = "def a(x):\n    return x == 1\n\n\ndef b(x):\n    return x == 2\n"
    tests = (
        "from dokimi.zz import a, b\n\n\n"
        "def test_a():\n    assert a(1) is True\n    assert a(2) is False\n\n\n"
        "def test_b_is_only_called():\n    b(2)\n"
    )
    cover = {2: ["tests/test_zz.py::test_a"], 6: ["tests/test_zz.py::test_b_is_only_called"]}
    rc, report = _mutation_run(tmp_path, monkeypatch, src, tests, cover, "--fail-on-survivor")
    assert _statuses(report) == {
        (2, "x != 1"): "killed", (2, "2"): "killed", (2, "None"): "killed",
        (6, "x != 2"): "survived", (6, "3"): "survived", (6, "None"): "survived",
    }
    assert rc == 1
    assert _mutation_run(tmp_path, monkeypatch, src, tests, cover)[0] == 0


def test_covering_tests_leaves_out_a_test_that_fails_unmutated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zz = tmp_path / "dokimi" / "dokimi" / "zz.py"
    zz.parent.mkdir(parents=True)
    contexts = {2: {"tests/test_zz.py::test_a|run"},
                6: {"tests/test_zz.py::test_b|run", "tests/test_zz.py::test_broken|run"}}

    class Data:
        def __init__(self, basename: str) -> None:
            assert basename == str(tmp_path / "dokimi" / ".coverage")

        def read(self) -> None:
            pass

        def measured_files(self) -> list[str]:
            return [str(zz)]

        def contexts_by_lineno(self, f: str) -> dict[int, set[str]]:
            return contexts

    def pytest_run(cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
        (cwd / ".coverage").write_text("", encoding="utf-8")
        log = "FAILED tests/test_zz.py::test_broken - assert False\n"
        return (1, log) if args[-1] == "tests/test_zz.py" else (2, "ERROR collecting")

    monkeypatch.setitem(sys.modules, "coverage", types.SimpleNamespace(CoverageData=Data))
    monkeypatch.setattr(mc, "_pytest", pytest_run)
    by_file, failing, not_run = mc.covering_tests(
        tmp_path, ["tests/test_zz.py", "tests/test_bad.py"], ["dokimi/dokimi/zz.py"], 60
    )
    assert by_file == {"dokimi/dokimi/zz.py": {2: ["tests/test_zz.py::test_a"], 6: ["tests/test_zz.py::test_b"]}}
    assert failing == ["tests/test_zz.py::test_broken", "tests/test_bad.py"]
    assert not_run == []


def _alive(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_a_mutant_that_times_out_leaves_no_process_of_its_tests_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("ZZ_PID_FILE", str(pid_file))
    src = "def a(x):\n    return x == 1\n"
    tests = (
        "import os\nimport subprocess\nimport sys\nimport time\n\nfrom dokimi.zz import a\n\n\n"
        "def test_a():\n    if a(1) is not True:\n"
        "        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "        with open(os.environ['ZZ_PID_FILE'], 'w') as f:\n            f.write(str(child.pid))\n"
        "        time.sleep(120)\n"
    )
    _, report = _mutation_run(tmp_path, monkeypatch, src, tests, {2: ["tests/test_zz.py::test_a"]}, "--max-mutants", "1", "--mutant-timeout", "5")
    pid = int(pid_file.read_text(encoding="utf-8"))
    try:
        assert [m["status"] for m in report["mutants"]] == ["timeout"]
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(pid)
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


def test_candidate_tests_match_a_wrapped_from_import(tmp_path: Path) -> None:
    (tmp_path / "test_a.py").write_text("from dokimi.subjects import (\n    other,\n    scorecard,\n)\n", encoding="utf-8")
    (tmp_path / "test_b.py").write_text("from dokimi.subjects import \\\n    scorecard\n", encoding="utf-8")
    assert mc.candidate_tests(["dokimi/dokimi/subjects/scorecard.py"], tmp_path) == ["tests/test_a.py", "tests/test_b.py"]


def test_changed_lines_counts_an_uncommitted_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (pkg / "m.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    (pkg / "m.py").write_text("a = 1\nb = 5\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "head")
    (pkg / "m.py").write_text("a = 1\nb = 5\nc = 6\n", encoding="utf-8")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    assert mc.changed_lines("HEAD~1") == {"dokimi/dokimi/m.py": {2, 3}}


def test_covering_tests_maps_an_import_time_line_to_the_file_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pytest_cov")
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "zz.py").write_text("FLAG = True\n\n\ndef f(x=3):\n    return FLAG\n", encoding="utf-8")
    (tmp_path / "dokimi" / "tests").mkdir()
    (tmp_path / "dokimi" / "tests" / "test_zz.py").write_text(
        "from dokimi.zz import f\n\n\ndef test_f():\n    assert f()\n", encoding="utf-8")
    by_file, failing, not_run = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 120)
    assert (failing, not_run) == ([], [])
    assert by_file == {"dokimi/dokimi/zz.py": {1: ["tests/test_zz.py"], 4: ["tests/test_zz.py"], 5: ["tests/test_zz.py::test_f"]}}


@pytest.mark.parametrize("tests, cover", [
    ("from dokimi.zz import a\n\n\ndef test_a():\n    assert a(1) is True\n", {2: ["tests/test_zz.py::test_missing"]}),
    ("from dokimi.zz import a\n", {2: ["tests/test_zz.py"]}),
], ids=["node-id-not-found", "nothing-collected"])
def test_a_mutant_no_test_runs_for_is_an_error_not_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tests: str, cover: dict[int, list[str]]
) -> None:
    rc, report = _mutation_run(tmp_path, monkeypatch, "def a(x):\n    return x == 1\n", tests, cover, "--fail-on-survivor")
    assert rc == 0
    assert report["mutants"] and {m["status"] for m in report["mutants"]} == {"error"}


def test_a_mutant_is_restored_to_the_source_after_its_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[str, str]] = []
    real = mc._write_source

    def spy(target: Path, text: str) -> None:
        writes.append((target.name, text))
        real(target, text)

    monkeypatch.setattr(mc, "_write_source", spy)
    src = "def a(x):\n    return x == 1\n"
    tests = "from dokimi.zz import a\n\n\ndef test_a():\n    assert a(1) is True\n"
    _mutation_run(tmp_path, monkeypatch, src, tests, {2: ["tests/test_zz.py::test_a"]}, "--max-mutants", "1")
    assert len(writes) == 2 and writes[-1] == ("zz.py", src)


def test_fail_on_survivor_passes_when_every_mutant_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tests = "from dokimi.zz import a\n\n\ndef test_a():\n    assert a(1) is True\n    assert a(2) is False\n"
    rc, report = _mutation_run(tmp_path, monkeypatch, "def a(x):\n    return x == 1\n", tests,
                               {2: ["tests/test_zz.py::test_a"]}, "--fail-on-survivor")
    assert rc == 0
    assert report["mutants"] and {m["status"] for m in report["mutants"]} == {"killed"}


def test_a_survivor_is_a_warning_unless_fail_on_survivor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    src = "def b(x):\n    return x == 2\n"
    tests = "from dokimi.zz import b\n\n\ndef test_b_is_only_called():\n    b(2)\n"
    cover = {2: ["tests/test_zz.py::test_b_is_only_called"]}
    _mutation_run(tmp_path, monkeypatch, src, tests, cover)
    out = capsys.readouterr().out
    assert "::warning file=dokimi/dokimi/zz.py,line=2::mutant survived" in out and "::error" not in out
    _mutation_run(tmp_path, monkeypatch, src, tests, cover, "--fail-on-survivor")
    out = capsys.readouterr().out
    assert "::error file=dokimi/dokimi/zz.py,line=2::mutant survived" in out


def test_no_mutable_changed_line_exits_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mc, "changed_lines", lambda base: {})
    assert mc.main(["--base", "HEAD", "--fail-on-survivor"]) == 0


def test_a_mutant_that_breaks_the_import_is_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = "ITEMS = [0, 1]\nFIRST = ITEMS[1]\n\n\ndef a(x):\n    return x == 1\n"
    tests = "from dokimi.zz import a\n\n\ndef test_a():\n    assert a(1) is True\n"
    _, report = _mutation_run(tmp_path, monkeypatch, src, tests, {2: ["tests/test_zz.py::test_a"]})
    assert report["mutants"] and {m["status"] for m in report["mutants"]} == {"killed"}


@pytest.mark.parametrize("summary, attributed", [("1 skipped in 0.01s", False), ("1 passed in 0.01s", True)])
def test_an_import_time_line_is_attributed_only_when_a_test_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, summary: str, attributed: bool
) -> None:
    zz = tmp_path / "dokimi" / "dokimi" / "zz.py"
    zz.parent.mkdir(parents=True)
    contexts = {1: {""}, 4: {"tests/test_zz.py::test_a|run"}}

    class Data:
        def __init__(self, basename: str) -> None:
            pass

        def read(self) -> None:
            pass

        def measured_files(self) -> list[str]:
            return [str(zz)]

        def contexts_by_lineno(self, f: str) -> dict[int, set[str]]:
            return contexts

    def pytest_run(cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
        (cwd / ".coverage").write_text("", encoding="utf-8")
        return 0, f"=== {summary} ==="

    monkeypatch.setitem(sys.modules, "coverage", types.SimpleNamespace(CoverageData=Data))
    monkeypatch.setattr(mc, "_pytest", pytest_run)
    by_file, failing, not_run = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 60)
    assert (failing, not_run) == ([], [])
    expected = {1: ["tests/test_zz.py"], 4: ["tests/test_zz.py::test_a"]} if attributed else {4: ["tests/test_zz.py::test_a"]}
    assert by_file == {"dokimi/dokimi/zz.py": expected}


def _stub_covering(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contexts: dict[int, set[str]], rc: int, log: str) -> None:
    zz = tmp_path / "dokimi" / "dokimi" / "zz.py"
    zz.parent.mkdir(parents=True, exist_ok=True)

    class Data:
        def __init__(self, basename: str) -> None:
            pass

        def read(self) -> None:
            pass

        def measured_files(self) -> list[str]:
            return [str(zz)]

        def contexts_by_lineno(self, f: str) -> dict[int, set[str]]:
            return contexts

    def pytest_run(cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
        (cwd / ".coverage").write_text("", encoding="utf-8")
        return rc, log

    monkeypatch.setitem(sys.modules, "coverage", types.SimpleNamespace(CoverageData=Data))
    monkeypatch.setattr(mc, "_pytest", pytest_run)


def test_a_failing_id_with_a_dash_in_its_parameter_is_matched_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    node = "tests/test_zz.py::test_f[a - b]"
    _stub_covering(monkeypatch, tmp_path, {2: {f"{node}|run"}}, 1, f"FAILED {node} - AssertionError\n")
    by_file, failing, _ = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 60)
    assert by_file == {"dokimi/dokimi/zz.py": {}}
    assert failing == [node]


def test_an_import_time_line_in_a_mixed_file_is_credited_to_its_passing_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contexts = {1: {""}, 4: {"tests/test_zz.py::test_ok|run", "tests/test_zz.py::test_bad|run"}}
    log = "FAILED tests/test_zz.py::test_bad - assert False\nPASSED tests/test_zz.py::test_ok\n=== 1 failed, 1 passed in 0.01s ===\n"
    _stub_covering(monkeypatch, tmp_path, contexts, 1, log)
    by_file, failing, _ = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 60)
    assert by_file == {"dokimi/dokimi/zz.py": {1: ["tests/test_zz.py::test_ok"], 4: ["tests/test_zz.py::test_ok"]}}
    assert failing == ["tests/test_zz.py::test_bad"]


def test_an_import_time_line_in_a_mixed_file_is_credited_to_every_passing_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # test_const passes and reads the constant without executing a line of the file: it has no context there
    contexts = {1: {""}, 4: {"tests/test_zz.py::test_fn|run"}}
    log = ("FAILED tests/test_zz.py::test_bad - assert False\nPASSED tests/test_zz.py::test_const\n"
           "PASSED tests/test_zz.py::test_fn\n=== 1 failed, 2 passed in 0.01s ===\n")
    _stub_covering(monkeypatch, tmp_path, contexts, 1, log)
    by_file, _, _ = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 60)
    assert by_file["dokimi/dokimi/zz.py"][1] == ["tests/test_zz.py::test_const", "tests/test_zz.py::test_fn"]


def test_a_passing_test_that_errors_in_teardown_is_not_credited_to_an_import_time_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contexts = {1: {""}, 4: {"tests/test_zz.py::test_fn|run"}}
    log = ("FAILED tests/test_zz.py::test_bad - assert False\nPASSED tests/test_zz.py::test_const\n"
           "PASSED tests/test_zz.py::test_fn\nERROR tests/test_zz.py::test_const - teardown boom\n"
           "=== 1 failed, 2 passed, 1 error in 0.01s ===\n")
    _stub_covering(monkeypatch, tmp_path, contexts, 1, log)
    by_file, _, _ = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 60)
    assert by_file["dokimi/dokimi/zz.py"][1] == ["tests/test_zz.py::test_fn"]


def test_a_passing_id_that_prefixes_a_failing_id_is_not_failed() -> None:
    passing, failing = "tests/test_zz.py::test_f[a]", "tests/test_zz.py::test_f[a] - b]"
    bad, unmatched = mc._failed(f"FAILED {failing} - AssertionError\n", {passing, failing})
    assert (bad, unmatched) == ({failing}, [])
    bad, _ = mc._failed(f"FAILED {passing} - AssertionError\n", {passing, failing})
    assert bad == {passing}
    bad, unmatched = mc._failed("FAILED tests/test_zz.py::test_f[a]b - msg\n", {passing})
    assert (bad, unmatched) == (set(), ["tests/test_zz.py::test_f[a]b"])


def test_changed_lines_diffs_against_the_merge_base_not_the_base_tip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (pkg / "m.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    (pkg / "m.py").write_text("a = 1\nb = 2\nc = 30\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "feature")
    _git(tmp_path, "checkout", "-q", "main")
    (pkg / "m.py").write_text("a = 10\nb = 2\nc = 3\n", encoding="utf-8")
    _git(tmp_path, "commit", "-qam", "main moves on")
    _git(tmp_path, "checkout", "-q", "feature")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    assert mc.changed_lines("main") == {"dokimi/dokimi/m.py": {3}}


def test_changed_lines_reads_a_non_ascii_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "core.quotePath", "true")
    (tmp_path / "other.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    (pkg / "café.py").write_text("FLAG = True\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    assert mc.changed_lines("HEAD~1") == {"dokimi/dokimi/café.py": {1}}


@pytest.mark.parametrize("rc, log, status", [
    (0, "", "survived"),
    (-1, "timeout", "timeout"),
    (1, "", "killed"),
    (2, "ERROR collecting x", "killed"),
    (4, "ERROR collecting x", "killed"),
    (3, "ERROR collecting x", "error"),
    (2, "", "error"),
    (4, "", "error"),
    (5, "", "error"),
])
def test_mutant_status_by_exit_code(rc: int, log: str, status: str) -> None:
    assert mc._mutant_status(rc, log) == status


def test_the_coverage_pass_asks_for_passed_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pytest_cov")
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    pkg = tmp_path / "dokimi" / "dokimi"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "zz.py").write_text("FLAG = True\n\n\ndef f():\n    return FLAG\n", encoding="utf-8")
    (tmp_path / "dokimi" / "tests").mkdir()
    (tmp_path / "dokimi" / "tests" / "test_zz.py").write_text(
        "from dokimi.zz import FLAG\n\n\ndef test_flag():\n    assert FLAG\n\n\ndef test_bad():\n    assert False\n",
        encoding="utf-8")
    by_file, failing, _ = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 120)
    assert failing == ["tests/test_zz.py::test_bad"]
    assert by_file["dokimi/dokimi/zz.py"][1] == ["tests/test_zz.py::test_flag"]


def test_pytest_output_is_not_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def run(argv: object, **kw: object) -> object:
        seen.update(kw)
        return types.SimpleNamespace(timed_out=False, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mc.process, "run", run)
    mc._pytest(tmp_path, [], 5)
    assert seen["capture_limit"] is None


def test_a_failure_message_that_completes_a_longer_passing_id_is_not_read_as_its_failure() -> None:
    failing, passing = "tests/test_x.py::test_f[a]", "tests/test_x.py::test_f[a] - Failed: b]"
    log = f"PASSED {passing}\nFAILED {failing} - Failed: b]\n"
    bad, unmatched = mc._failed(log, {failing, passing})
    assert (bad, unmatched) == ({failing}, [])


@pytest.mark.parametrize("gap", ["skipped", "broken"])
def test_a_survivor_is_inconclusive_when_a_candidate_test_file_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], gap: str
) -> None:
    src = "def b(x):\n    return x == 2\n"
    tests = "from dokimi.zz import b\n\n\ndef test_b_is_only_called():\n    b(2)\n"
    cover = {2: ["tests/test_zz.py::test_b_is_only_called"]}
    rc, report = _mutation_run(tmp_path, monkeypatch, src, tests, cover, "--fail-on-survivor",
                               **{gap: ("tests/test_zz_late.py",)})
    assert _statuses(report) == {(2, "x != 2"): "inconclusive", (2, "3"): "inconclusive", (2, "None"): "inconclusive"}
    assert rc == 0
    out = capsys.readouterr().out
    assert "3 inconclusive, 0 not run" in out
    assert "3 inconclusive: the tests covering the line pass" in out
    assert "::warning file=dokimi/dokimi/zz.py,line=2::mutant inconclusive" in out


def test_the_covering_pass_asks_pytest_for_the_passed_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zz = tmp_path / "dokimi" / "dokimi" / "zz.py"
    zz.parent.mkdir(parents=True)

    class Data:
        def __init__(self, basename: str) -> None:
            pass

        def read(self) -> None:
            pass

        def measured_files(self) -> list[str]:
            return [str(zz)]

        def contexts_by_lineno(self, f: str) -> dict[int, set[str]]:
            return {1: {""}}  # FLAG = True, run at collection

    asked: list[list[str]] = []

    def pytest_run(cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
        asked.append(args)
        (cwd / ".coverage").write_text("", encoding="utf-8")
        return 1, ("FAILED tests/test_zz.py::test_bad - assert False\nPASSED tests/test_zz.py::test_const\n"
                   "=== 1 failed, 1 passed in 0.01s ===\n")

    monkeypatch.setitem(sys.modules, "coverage", types.SimpleNamespace(CoverageData=Data))
    monkeypatch.setattr(mc, "_pytest", pytest_run)
    by_file, _, _ = mc.covering_tests(tmp_path, ["tests/test_zz.py"], ["dokimi/dokimi/zz.py"], 60)
    assert "-rfEp" in asked[0]
    assert by_file["dokimi/dokimi/zz.py"][1] == ["tests/test_zz.py::test_const"]


def test_apply_splices_a_mutant_on_its_own_line_past_a_unicode_line_separator() -> None:
    src = 'S = "\u2028"\ndef f(x):\n    return x == 1\n'
    (m,) = [m for m in mc.mutants_for("x.py", src, {3}) if m.op == "flip-compare"]
    assert mc.apply(src, m) == 'S = "\u2028"\ndef f(x):\n    return x != 1\n'


def test_a_survivor_is_inconclusive_only_in_the_module_whose_test_file_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg, tests_dir = tmp_path / "dokimi" / "dokimi", tmp_path / "dokimi" / "tests"
    pkg.mkdir(parents=True)
    tests_dir.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "commit", "--allow-empty", "-qm", "base")
    (pkg / "zz.py").write_text("def b(x):\n    return x == 2\n", encoding="utf-8")
    (pkg / "yy.py").write_text("def c(x):\n    return x == 5\n", encoding="utf-8")
    (tests_dir / "test_zz.py").write_text(
        "from dokimi.zz import b\nfrom dokimi.yy import c\n\n\ndef test_both_are_called():\n    b(2)\n    c(5)\n",
        encoding="utf-8")
    (tests_dir / "test_yy_late.py").write_text("from dokimi.yy import c\n\n\ndef test_late():\n    c(5)\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "head")
    monkeypatch.setattr(mc, "REPO", tmp_path)
    monkeypatch.setattr(mc, "PROJECT", tmp_path / "dokimi")
    monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    covered = {2: ["tests/test_zz.py::test_both_are_called"]}

    def covering(work: Path, files: list[str], paths: list[str], budget: float) -> tuple[dict, list[str], list[str]]:
        return {"dokimi/dokimi/zz.py": covered, "dokimi/dokimi/yy.py": covered}, [], ["tests/test_yy_late.py"]

    monkeypatch.setattr(mc, "covering_tests", covering)
    out = tmp_path / "report.json"
    mc.main(["--base", "HEAD~1", "--budget", "100", "--mutant-timeout", "30", "--json", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    status = {m["path"]: set() for m in report["mutants"]}
    for m in report["mutants"]:
        status[m["path"]].add(m["status"])
    assert status == {"dokimi/dokimi/zz.py": {"survived"}, "dokimi/dokimi/yy.py": {"inconclusive"}}


def test_covering_tests_lists_a_timed_out_file_as_not_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "coverage", types.SimpleNamespace())

    def pytest_run(cwd: Path, args: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str]:
        return -1, "timeout"

    monkeypatch.setattr(mc, "_pytest", pytest_run)
    tests = ["tests/test_slow.py", "tests/test_after.py"]
    assert mc.covering_tests(tmp_path, tests, [], 60) == ({}, [], tests)


def test_a_changed_continuation_line_with_no_mutant_is_listed_uncovered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Line 3 (`+ a)`) continues the BinOp that starts on line 2: no mutant starts there, so only the uncovered list can show it.
    src = "def f(a):\n    x = (a\n         + a)\n    return x\n"
    tests = "from dokimi.zz import f\n\n\ndef test_f():\n    assert f(1) == 2\n"
    _, report = _mutation_run(tmp_path, monkeypatch, src, tests, {})
    assert 3 in report["uncovered_lines"]["dokimi/dokimi/zz.py"]


def test_a_changed_signature_line_of_a_multi_line_def_is_listed_but_not_the_def_line() -> None:
    # Line 1 starts the def; lines 2 and 3 are its parameters, which no mutant covers: listed.
    src = "def f(\n    a,\n    b=3,\n):\n    return a + b\n"
    assert mc.continuation_lines(src, {1, 2, 3}, set()) == {2, 3}


def test_a_changed_comment_line_in_a_multi_line_expression_is_not_listed() -> None:
    # Line 3 is a comment between `x = (a` and `+ a)`: it holds no code, so it is not listed.
    src = "def f(a):\n    x = (a  # note\n         # the reason\n         + a)\n    return x\n"
    assert mc.continuation_lines(src, {3}, set()) == set()


def test_the_text_of_a_multi_line_string_is_not_listed_but_code_beside_it_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Line 7 is the string's text: not listed. Line 3 (`+ a)`), line 13 (`u""" + b)`) and line 17 (`"b")`, a single-line string
    # continuing an implicit concatenation) are code: listed.
    src = (
        'def f(a):\n    x = (a\n         + a)\n    return x\n'
        'def g():\n    y = dedent("""\n    hello\n    """)\n    return y\n'
        'def h(a, b):\n    z = (a\n         + """t\n    u""" + b)\n    return z\n'
        'def k():\n    w = ("a"\n         "b")\n    return w\n'
    )
    tests = "from dokimi.zz import f\n\n\ndef test_f():\n    assert f(1) == 2\n"
    _, report = _mutation_run(tmp_path, monkeypatch, src, tests, {})
    listed = report["uncovered_lines"]["dokimi/dokimi/zz.py"]
    assert 3 in listed and 7 not in listed and 13 in listed and 17 in listed


def test_the_script_starts_without_the_package_installed() -> None:
    # Under -S site-packages is off sys.path, so the script must put its project directory on it.
    run = subprocess.run([sys.executable, "-S", str(_SCRIPT), "--help"], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
