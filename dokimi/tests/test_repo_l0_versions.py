# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The Python version runs a guidance line gives, and the versions a workflow matrix runs (``l0.versions``)."""

from __future__ import annotations

import pytest

from dokimi.repos.l0.versions import _RUN, matrix_versions, parse


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
])
def test_runs_of_versions(text, expected):
    assert _runs(text) == expected


@pytest.mark.parametrize("matrix, expected", [
    ({"python-version": ["3.12", "3.13t", "3.14-dev", "3.15.0"]}, {(12, False), (13, True), (14, False), (15, False)}),
    ({"python": [3.10, "3.11"]}, {(1, False), (11, False)}),             # unquoted 3.10 is the float 3.1, as in GitHub
    ({"py": ["pypy3.10", "3.x", "3.12"]}, {(12, False)}),                # not a CPython 3.N version: not read
    ({"os": ["a"], "include": [{"python": "3.14", "spec": "3.14.* *_cp314t"}]}, {(14, False), (14, True)}),
    ({"python": ["3.12"], "exclude": [{"python": "3.11"}]}, {(12, False)}),
])
def test_matrix_versions(matrix, expected):
    unknown: list[str] = []
    assert matrix_versions(matrix, "ci.yml", unknown) == expected
    assert unknown == []


@pytest.mark.timeout(5)  # str() of the bomb never returns
def test_nested_matrix_values_are_not_read():
    bomb: list = ["3.12"]
    for _ in range(40):  # what YAML aliases build: 2**40 leaves through shared lists
        bomb = [bomb, bomb]
    assert matrix_versions({"python": ["3.13", bomb], "include": [{"python": bomb}]}, "ci.yml", []) == {(13, False)}


def test_a_computed_matrix_is_unknown():
    unknown: list[str] = []
    assert matrix_versions("${{ fromJSON(needs.plan.outputs.matrix) }}", "ci.yml", unknown) == set()
    assert matrix_versions({"python": ["${{ inputs.python }}"]}, "ci.yml", unknown) == set()
    assert unknown == ["a matrix of ci.yml is computed (${{ fromJSON(needs.plan.outputs.matrix) }})",
                       "a matrix of ci.yml is computed (${{ inputs.python }})"]
