# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The no-new-clones ratchet (scripts/check_duplicates.py).

The gate on this tree is the "Duplicate code ratchet" step of ci.yml's static job
(one interpreter, ~8s). These tests plant clones in a temporary project and check
what is and is not a clone group, and what the ratchet lets through.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import textwrap
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
_SCRIPT = PROJECT / "scripts" / "check_duplicates.py"
_spec = importlib.util.spec_from_file_location("check_duplicates", _SCRIPT)
assert _spec and _spec.loader
cd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cd   # dataclasses resolves postponed annotations through it
_spec.loader.exec_module(cd)


# --- the baseline file -------------------------------------------------------------------


def test_baseline_file_is_in_canonical_form():
    text = cd.BASELINE.read_text(encoding="utf-8")
    assert text == cd.dump_baseline(cd.load_baseline())


# --- planted clones ----------------------------------------------------------------------

_LOADER = '''
def load_{name}(path):
    """Docstrings differ and do not count."""
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("evals", [])
    except (OSError, ValueError) as exc:
        raise ValueError(f"{{path}}: not an evals file ({{exc}})") from exc
    if not isinstance(rows, list):
        raise ValueError(f"{{path}}: evals is not a list")
    return [row for row in rows if isinstance(row, dict) and row.get("id")]
'''


def _groups(tmp_path: Path, **files: str) -> dict[str, list[str]]:
    for name, src in files.items():
        path = tmp_path / "dokimi" / f"{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import json\n" + textwrap.dedent(src), encoding="utf-8")
    return cd.clone_groups(tmp_path)


def test_a_copied_function_is_a_clone_group(tmp_path):
    groups = _groups(tmp_path, a=_LOADER.format(name="a"), b=_LOADER.format(name="b"))
    assert list(groups.values()) == [["dokimi/a.py:3-11", "dokimi/b.py:3-11"]]


def test_renamed_locals_still_match(tmp_path):
    renamed = _LOADER.format(name="b").replace("rows", "items").replace("row ", "it ").replace("row.", "it.").replace("(row", "(it")
    groups = _groups(tmp_path, a=_LOADER.format(name="a"), b=renamed)
    assert len(groups) == 1


def test_a_copied_block_inside_different_functions_matches(tmp_path):
    block = _LOADER.format(name="x").split('count."""\n', 1)[1]
    a = "def first(path, flag):\n    if flag:\n        return None\n" + block
    b = "def second(path):\n    print(path)\n" + block + "\n"
    groups = _groups(tmp_path, a=a, b=b)
    assert len(groups) == 1 and len(next(iter(groups.values()))) == 2


def test_renamed_exception_alias_still_matches(tmp_path):
    renamed = re.sub(r"\bexc\b", "err", _LOADER.format(name="b"))
    assert len(_groups(tmp_path, a=_LOADER.format(name="a"), b=renamed)) == 1


def test_renamed_argument_still_matches(tmp_path):
    renamed = _LOADER.format(name="b").replace("path", "source")
    assert len(_groups(tmp_path, a=_LOADER.format(name="a"), b=renamed)) == 1


def test_a_renamed_argument_matches_in_a_window_of_the_function(tmp_path):
    block = _LOADER.format(name="x").split('count."""\n', 1)[1]
    a = "def first(path):\n    print(1)\n" + block
    b = "def second(source):\n    print(2)\n" + block.replace("path", "source")
    groups = _groups(tmp_path, a=a, b=b)
    assert len(groups) == 1 and all(w.split(":")[0] != "x" for w in next(iter(groups.values())))


def test_statements_in_one_branch_differ_from_one_in_each(tmp_path):
    first, second = (f"{n} = [{', '.join(str(i) for i in range(lo, lo + 20))}]" for n, lo in (("a", 0), ("b", 20)))
    together = f"def f(flag):\n    if flag:\n        {first}\n        {second}\n"
    split = f"def f(flag):\n    if flag:\n        {first}\n    else:\n        {second}\n"
    assert not _groups(tmp_path, a=together, b=split)


def test_try_bodies_split_differently_do_not_match(tmp_path):
    first, second = (f"{n} = [{', '.join(str(i) for i in range(lo, lo + 20))}]" for n, lo in (("a", 0), ("b", 20)))
    together = f"def f():\n    try:\n        {first}\n        {second}\n    except OSError:\n        pass\n"
    split = f"def f():\n    try:\n        {first}\n    except OSError:\n        pass\n    else:\n        {second}\n"
    assert not _groups(tmp_path, a=together, b=split)


def test_a_free_name_is_not_a_renamed_local(tmp_path):
    terms = " + ".join(["{x}"] * 25)
    local_a = "def f():\n    a = 1\n    return a + " + terms.format(x="v0") + "\n"
    local_b = "def f():\n    b = 1\n    return b + " + terms.format(x="b") + "\n"
    assert not _groups(tmp_path, a=local_a, b=local_b)


def test_a_comprehension_variable_is_not_bound_outside_it(tmp_path):
    src = "{g} = 1\ndef f():\n    values = [{g} for {g} in range(20)]\n    return " + " + ".join(["{g}"] * 20) + "\n"
    groups = _groups(tmp_path, a=src.format(g="x"), b=src.format(g="y"))
    assert not any(w.startswith("dokimi/a.py:3-") for ws in groups.values() for w in ws)


def test_a_comprehension_variable_still_renames_inside_it(tmp_path):
    body = "\n".join(f"    total = total + data[{i}] * weights.get({i}, 1)" for i in range(12))
    big = "def g{n}(data, weights):\n    total = 0\n" + body + "\n    return [{g} * 2 for {g} in data]\n"
    groups = _groups(tmp_path, a=big.format(n=1, g="x"), b=big.format(n=2, g="y"))
    assert len(groups) == 1


def test_a_lambda_argument_in_a_default_does_not_bind_the_function_body(tmp_path):
    src = "{g} = 1\ndef f{g}(cb=lambda {g}: {g}):\n    value = " + "+".join(["{g}"] * 25) + "\n    return value\n"
    assert not _groups(tmp_path, a=src.format(g="x"), b=src.format(g="y"))


def test_a_dense_first_statement_still_makes_a_two_statement_window(tmp_path):
    dense = "    values = [" + ", ".join(str(i) for i in range(50)) + "]\n    return values\n"
    groups = _groups(tmp_path, a="def f():\n    print('a')\n" + dense,
                     b="def g():\n    print('b')\n" + dense)
    assert len(groups) == 1


def test_different_literals_or_callees_do_not_match(tmp_path):
    other_literal = _LOADER.format(name="b").replace('"evals"', '"cases"')
    other_callee = _LOADER.format(name="c").replace("json.loads", "yaml.safe_load")
    assert not _groups(tmp_path, a=_LOADER.format(name="a"), b=other_literal, c=other_callee)


def test_a_block_below_the_size_floor_is_ignored(tmp_path):
    small = "def f{n}(x):\n    y = x + 1\n    return y * 2\n"
    assert not _groups(tmp_path, a=small.format(n=1), b=small.format(n=2))


def test_a_long_copy_is_one_group_not_one_per_window(tmp_path):
    body = "".join(f"    total = total + data[{i}] * weights.get({i}, 1)\n" for i in range(12))
    src = "def f{n}(data, weights):\n    total = 0\n" + body + "    return total\n"
    groups = _groups(tmp_path, a=src.format(n=1), b=src.format(n=2))
    assert len(groups) == 1


def test_tests_are_not_scanned(tmp_path):
    for name in ("a", "b"):
        path = tmp_path / "tests" / f"test_{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import json\n" + _LOADER.format(name=name), encoding="utf-8")
    assert not cd.clone_groups(tmp_path)


# --- the ratchet -------------------------------------------------------------------------


def test_new_grown_and_stale_groups_fail():
    groups = {"aaa": ["x.py:1-9", "y.py:1-9"], "bbb": ["x.py:20-29", "y.py:20-29", "z.py:1-9"]}
    baseline = {"bbb": {"count": 2, "where": []}, "ccc": {"count": 2, "where": []}}
    found = cd.violations(groups, baseline)
    assert [f.split(" (")[0] for f in found] == [
        "new clone group", "clone group grew past 2", "stale entry ccc: 0 copies now, baseline says 2"]


def test_a_recorded_group_at_its_count_passes():
    groups = {"aaa": ["x.py:1-9", "y.py:1-9"]}
    assert not cd.violations(groups, {"aaa": {"count": 2, "where": ["x.py", "y.py"]}})


def test_update_lowers_and_drops_but_never_raises():
    groups = {"aaa": ["x.py:1-9", "y.py:1-9"], "bbb": ["x.py:20-29", "y.py:20-29"], "new": ["p:1", "q:1"]}
    baseline = {"aaa": {"count": 3, "where": []}, "bbb": {"count": 2, "where": []},
                "ccc": {"count": 2, "where": []}}
    assert cd.tightened(groups, baseline) == {
        "aaa": {"count": 2, "where": ["x.py", "y.py"]}, "bbb": {"count": 2, "where": ["x.py", "y.py"]}}


def test_seed_keeps_a_reviewed_rationale_and_marks_only_new_or_raised_groups(tmp_path, monkeypatch):
    # Property: --seed never overwrites the stored rationale of a group the code did not outgrow.
    monkeypatch.setattr(cd, "PROJECT", tmp_path)
    monkeypatch.setattr(cd, "BASELINE", tmp_path / "duplicates_baseline.json")
    groups = {"kept": ["x.py:1-9", "y.py:1-9"], "raised": ["x.py:20-29", "y.py:20-29", "z.py:1-9"],
              "new": ["p.py:1-9", "q.py:1-9"]}
    monkeypatch.setattr(cd, "clone_groups", lambda project=None: groups)
    assert cd.main(["--seed"]) == 0   # no baseline file yet: every group is new
    assert {e["rationale"] for e in cd.load_baseline().values()} == {cd.SEED_RATIONALE}
    cd.BASELINE.write_text(cd.dump_baseline({
        "kept": {"count": 3, "rationale": "two CLIs, no shared module", "where": []},
        "raised": {"count": 2, "rationale": "old reason", "where": []},
        "gone": {"count": 2, "rationale": "removed since", "where": []}}), encoding="utf-8")
    assert cd.main(["--seed"]) == 0
    assert cd.load_baseline() == {
        "kept": {"count": 2, "rationale": "two CLIs, no shared module", "where": ["x.py", "y.py"]},
        "raised": {"count": 3, "rationale": cd.SEED_RATIONALE, "where": ["x.py", "y.py", "z.py"]},
        "new": {"count": 2, "rationale": cd.SEED_RATIONALE, "where": ["p.py", "q.py"]}}


def test_an_added_or_raised_entry_needs_a_new_rationale():
    base = {"kept": {"count": 2, "rationale": "two CLIs, no shared module"},
            "raised": {"count": 2, "rationale": "old reason"}}
    baseline = {"kept": {"count": 2, "rationale": "two CLIs, no shared module"},
                "raised": {"count": 3, "rationale": "old reason"},
                "seeded": {"count": 2, "rationale": cd.SEED_RATIONALE},
                "explained": {"count": 2, "rationale": "generated per provider"}}
    found = cd.raised_without_rationale(baseline, base)
    assert [f.split(" without")[0] for f in found] == ["raised: raised from 2 to 3", "seeded: added"]


def test_a_rationale_must_be_a_string_within_the_limit():
    bad = [None, 5, True, ["why"], " ", "x" * (cd.RATIONALE_MAX + 1)]
    baseline = {f"e{i}": {"count": 2, "rationale": value} for i, value in enumerate(bad)}
    baseline["ok"] = {"count": 2, "rationale": "x" * cd.RATIONALE_MAX}
    found = cd.raised_without_rationale(baseline, {})
    assert [f.split(":")[0] for f in found] == sorted(f"e{i}" for i in range(len(bad)))
