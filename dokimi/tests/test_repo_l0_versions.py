# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The Python version runs a guidance line gives, and the versions a workflow matrix runs (``l0.versions``)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from dokimi.repos.l0.core import Scan
from dokimi.repos.l0.versions import _RUN, judge, matrix_versions, parse, setup_versions
from dokimi.repos.source import SourceError, WorkTree


def _runs(text: str) -> list[tuple[str, object]]:
    return [(m.group(0).strip(), parse(m.group(0).strip())) for m in _RUN.finditer(text)]


@pytest.mark.parametrize("text, expected", [
    ("conda tests, py3.12-3.14t (`Test (...)`)", [("py3.12-3.14t", ([((12, False), (14, True))], []))]),
    ("py310–py313", [("py310–py313", ([((10, False), (13, False))], []))]),
    ("Python 3.12, 3.13 and 3.14", [("Python 3.12, 3.13 and 3.14", ([], [(12, False), (13, False), (14, False)]))]),
    ("cp312, cp313 and cp314t wheels", [("cp312, cp313 and cp314t", ([], [(12, False), (13, False), (14, True)]))]),
    ("py3.12-3.14 and 3.14t", [("py3.12-3.14 and 3.14t", ([((12, False), (14, False))], [(14, True)]))]),
    ("Python 3.13 only.", [("Python 3.13", None)]),                      # one version: no claim
    ("Python 3.12 and CMake 3.27", [("Python 3.12", None)]),             # 3.27 has no Python marker
    ("moved from Python 3.11 to 3.13", [("Python 3.11", None)]),         # `to` is no range
    ("Python 3.12.4 and 3.12.5", []),                                    # patch releases are not matrix versions
    ("numpy3.1-3.2 and dpctl 0.22", []),                                 # no marker at a word start
    ("`pkg-1.0-cp313-cp313-linux_x86_64.whl`", [("cp313", None), ("cp313", None)]),  # a wheel's tags, no range
    ("cp312-cp314 wheels", [("cp312", None), ("cp314", None)]),         # a `cp` tag starts no range
    ("Python 3.12, 3.13, 30 min timeout", [("Python 3.12, 3.13", ([], [(12, False), (13, False)]))]),
    ("Python 3.11 / 3.12 on 32 cores", [("Python 3.11 / 3.12", ([], [(11, False), (12, False)]))]),  # no dot: no version
    ("py3.12-32 cores", [("py3.12", None)]),                             # nor as a range end
])
def test_runs_of_versions(text, expected):
    assert _runs(text) == expected


@pytest.mark.parametrize("matrix, expected", [
    ({"python-version": ["3.12", "3.13t", "3.14-dev", "3.15.0"]}, {(12, False), (13, True), (14, False), (15, False)}),
    ({"python": [3.10, "3.11"]}, {(1, False), (11, False)}),             # unquoted 3.10 is the float 3.1, as in GitHub
    ({"py": ["pypy3.10", "graalpy-24.1", "x64", "3.12"]}, {(12, False)}),  # not a CPython version: not read
    ({"python": ["3.14.0-rc.1", "3.15.0-alpha.1", "3.13.0b2", "3.11.0rc1", "3.14t-dev", "3.12.x", "3.13.1t"]},
     {(14, False), (15, False), (13, False), (11, False), (14, True), (12, False), (13, True)}),  # a pre-release: its minor
    ({"version": ["3.12"], "os": ["3.13"]}, {(12, False)}),               # a setup step names `version`
    ({"python": ["cp312", "cp313t", "py314", "pypy310"]}, {(12, False), (13, True), (14, False)}),
    ({"python": [["3.12"], {"v": "3.11"}, "3.13"]}, {(13, False)}),  # a nested list or mapping is not read
    ({"python": ["3.12", "3.13"], "os": ["a"], "exclude": [{"python": "3.13", "os": "a"}, {"os": "b"}, "x"]},
     {(12, False)}),                                                     # 3.13 is out of every combination
    ({"python": ["3.12", "3.13"], "os": ["a", "b"], "exclude": [{"os": "a"}, {"python": "3.13", "os": "b"}]},
     {(12, False)}),                                                     # by two entries, one with no Python key
    ({"os": ["a"], "include": [{"python": "3.14", "spec": "3.14.* *_cp314t"}]}, {(14, False), (14, True)}),
    ({"python": ["3.12"], "exclude": [{"python": "3.11"}]}, {(12, False)}),
    ({"cibw": ["cp313t-*", "cp314t-*", ["cp315t"]], "os": ["a"], "exclude": [{"cibw": "cp314t-*"}]}, {(13, True)}),
])
def test_matrix_versions(matrix, expected):
    unknown: list[str] = []
    assert matrix_versions(matrix, "ci.yml", unknown, {"version"}) == expected
    assert unknown == []


def test_a_python_value_that_names_no_one_version_is_unknown():
    unknown: list[str] = []
    assert matrix_versions({"python": ["3.x", ">=3.12", "3.12 - 3.14", 3, "3.13"]}, "ci.yml", unknown) == {(13, False)}
    assert unknown == [f"a matrix of ci.yml gives Python {v}, not one CPython 3 version"
                       for v in ("3.x", ">=3.12", "3.12 - 3.14", "3")]


def test_setup_versions():
    keys: set[str] = set()
    unknown: list[str] = []
    steps = [{"uses": "actions/checkout@v6"}, "run: x", {"with": "x"},
             {"with": {"python-version": "3.12\n3.13t"}}, {"with": {"python-version": 3.14}},
             {"with": {"python-version": "${{ matrix.py }}"}}, {"with": {"python-version": "${{ matrix['ver'] }}"}},
             {"with": {"python-version": ["3.9"]}}]
    assert setup_versions(steps, "ci.yml", unknown, keys) == {(12, False), (13, True), (14, False)}
    assert keys == {"py", "ver"} and unknown == []
    assert setup_versions({"with": {"python-version": "3.12"}}, "ci.yml", unknown, keys) == set()  # not a list


def test_setup_versions_reads_a_version_file_only_without_a_version():
    unknown: list[str] = []
    steps = [{"with": {"python-version": "3.12", "python-version-file": "a"}},
             {"with": {"python-version": "", "python-version-file": "b"}}, {"with": {"python-version-file": "c"}}]
    assert setup_versions(steps, "ci.yml", unknown, set()) == {(12, False)}
    assert unknown == [f"a setup step of ci.yml reads its Python version from {f}" for f in "bc"]


def test_setup_versions_reads_inputs_only_in_a_called_workflow():
    steps = [{"with": {"python-version": "${{ inputs.py }}"}}, {"with": {"python-version": "${{ inputs['v'] }}"}}]
    keys: set[str] = set()
    inputs: set[str] = set()
    assert setup_versions(steps, "t.yml", [], keys, inputs) == set()
    assert inputs == {"py", "v"} and keys == set()
    unknown: list[str] = []  # in a workflow GitHub runs itself, an input is not known
    assert setup_versions(steps, "t.yml", unknown, keys) == set()
    assert unknown == ["a setup step of t.yml gives a computed Python version (${{ inputs.py }})",
                       "a setup step of t.yml gives a computed Python version (${{ inputs['v'] }})"]


_BOMB = """
from dokimi.repos.l0.versions import matrix_versions

def bomb():
    b = ["3.12"]
    for _ in range(40):  # what YAML aliases build: 2**40 leaves through shared lists
        b = [b, b]
    return b

one, other = bomb(), bomb()
matrix = {"python": ["3.13", one], "include": [{"python": one}], "exclude": [{"python": other}]}
print(sorted(matrix_versions(matrix, "ci.yml", [])))
"""


def test_nested_matrix_values_are_not_read():
    # str() of a bomb, or == between two of them, runs in C and never returns. No timeout inside the process
    # can stop that (SIGALRM waits for the C call, a timer thread for the GIL), so the call runs in a child
    # that the timeout kills.
    out = subprocess.run([sys.executable, "-c", _BOMB], capture_output=True, text=True, timeout=20, check=True)
    assert out.stdout.strip() == "[(13, False)]"


_FILE_BOMB = """
from dokimi.repos.l0.versions import setup_versions

b = ["3.12"]
for _ in range(40):
    b = [b, b]
unknown = []
setup_versions([{"with": {"python-version-file": b}}], "ci.yml", unknown, set())
print(unknown)
"""


def test_a_nested_version_file_is_not_read():
    out = subprocess.run([sys.executable, "-c", _FILE_BOMB], capture_output=True, text=True, timeout=20, check=True)
    assert out.stdout.strip() == "['a setup step of ci.yml reads its Python version from a file']"


def test_an_exclude_over_too_many_combinations_is_unknown():
    unknown: list[str] = []
    matrix = {"python": ["3.12", "3.13"], "a": list(range(20)), "b": list(range(20)), "exclude": [{"python": "3.13"}]}
    assert matrix_versions(matrix, "ci.yml", unknown) == {(12, False), (13, False)}
    assert unknown == ["a matrix of ci.yml excludes Python versions from more than 256 combinations"]
    other: list[str] = []  # an exclude that names no Python key is not counted out
    assert matrix_versions({**matrix, "exclude": [{"a": 0}]}, "ci.yml", other) == {(12, False), (13, False)}
    assert other == []


def test_the_exclude_cap_counts_every_combination_and_comparison():
    # the Python key's own values count toward the 256 jobs, and so do the entries an exclude is read with
    wide = {"python": [f"3.{i}" for i in range(100)], "os": list(range(256)),
            "exclude": [{"python": f"3.{i}"} for i in range(100)]}
    unknown: list[str] = []
    assert len(matrix_versions(wide, "ci.yml", unknown)) == 100
    assert unknown == ["a matrix of ci.yml excludes Python versions from more than 256 combinations"]
    many = {f"python{i}": ["3.12"] for i in range(1000)} | {"exclude": [{f"python{i}": "3.12"} for i in range(1000)]}
    unknown = []
    assert matrix_versions(many, "ci.yml", unknown) == {(12, False)}
    assert unknown == ["a matrix of ci.yml has an exclude too large to read"]


def test_a_computed_matrix_is_unknown():
    unknown: list[str] = []
    assert matrix_versions("${{ fromJSON(needs.plan.outputs.matrix) }}", "ci.yml", unknown) == set()
    assert matrix_versions({"python": ["${{ inputs.python }}"]}, "ci.yml", unknown) == set()
    assert matrix_versions({"python": ["3.12"], "include": ["${{ fromJSON(a) }}"], "exclude": "${{ fromJSON(b) }}"},
                           "ci.yml", unknown) == {(12, False)}
    assert unknown == ["a matrix of ci.yml is computed (${{ fromJSON(needs.plan.outputs.matrix) }})",
                       "a matrix of ci.yml gives a computed Python version (${{ inputs.python }})",
                       "a matrix of ci.yml computes its exclude (${{ fromJSON(b) }})",
                       "a matrix of ci.yml computes its include (${{ fromJSON(a) }})"]


@pytest.mark.parametrize("bad", ["env: {DATE: 2026-02-30}\njobs: {}\n",  # ValueError: no such day
                                 "jobs: " + "[" * 5000 + "\n",  # RecursionError
                                 "env: {X: !!bool maybe}\njobs: {}\n",  # KeyError
                                 "env: {X: !!timestamp foo}\njobs: {}\n"],  # AttributeError
                         ids=["bad-date", "deep", "bad-bool", "bad-timestamp"])
def test_a_workflow_yaml_cannot_build_is_unresolved(tmp_path, bad):
    """A line `py3.12-3.13 (bad.yml)` naming a workflow PyYAML cannot build is unresolved, not a crash."""
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/bad.yml").write_text(bad, encoding="utf-8")
    scan = Scan(WorkTree(tmp_path), frozenset())
    assert judge(scan, parse("py3.12-3.13"), [".github/workflows/bad.yml"]) == (
        "unresolved", ".github/workflows/bad.yml is not valid YAML")


def test_a_git_failure_reading_a_named_workflow_stops_the_scan(tmp_path, monkeypatch):
    """A `git cat-file` that fails (a SourceError, which is a ValueError) is an error exit, not invalid YAML."""
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text("jobs: {}\n", encoding="utf-8")
    scan = Scan(WorkTree(tmp_path), frozenset())

    def fail(*args, **kwargs):
        raise SourceError("git cat-file failed: timed out after 60 seconds")
    monkeypatch.setattr(scan.tree, "read_many", fail)
    with pytest.raises(SourceError, match="timed out"):
        judge(scan, parse("py3.12-3.13"), [".github/workflows/ci.yml"])
