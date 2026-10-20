# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The no-growth ratchet on file length and function complexity (scripts/check_growth.py).

The first two tests are the gate on this tree. The rest prove it binds: each builds a
small project in a temporary directory, with the caps lowered, and checks that growth,
new over-cap code, slack and stale entries fail.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
_SCRIPT = PROJECT / "scripts" / "check_growth.py"
_spec = importlib.util.spec_from_file_location("check_growth", _SCRIPT)
assert _spec and _spec.loader
cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cg)


# --- the gate on this tree -------------------------------------------------------------


def test_no_file_or_function_grew_past_its_baseline():
    found = cg.violations(cg.measure(), cg.load_baseline())
    assert not found, (
        "file length / complexity ratchet (scripts/check_growth.py):\n  "
        + "\n  ".join(found)
        + "\nSplit the code that grew. When code shrank, run `python scripts/check_growth.py "
        "--update` to lower the baseline."
    )


def test_a_raised_or_added_baseline_entry_says_why():
    ref = cg.base_ref()
    base = cg.load_base_baseline(ref)
    if base is None:
        pytest.skip(f"no {cg.BASELINE.name} at {ref} to compare with")
    found = cg.raised_without_rationale(cg.load_baseline(), base)
    assert not found, "\n".join(found)


def test_baseline_file_is_in_canonical_form():
    # --update rewrites it this way; a hand edit in another layout would churn on the next run.
    text = cg.BASELINE.read_text(encoding="utf-8")
    assert text == cg.dump_baseline(cg.load_baseline())


# --- the complexity count -----------------------------------------------------------


def _cx(src: str) -> dict[str, int]:
    return cg.function_complexities(ast.parse(textwrap.dedent(src)))


@pytest.mark.parametrize("body, expected", [
    ("return 1", 1),
    ("if a:\n    pass\nelif b:\n    pass\nelse:\n    pass", 3),
    ("for x in y:\n    pass\nelse:\n    pass", 2),
    ("while a:\n    pass", 2),
    ("try:\n    pass\nexcept A:\n    pass\nexcept B:\n    pass\nelse:\n    pass", 4),
    ("try:\n    pass\nexcept* A:\n    pass\nexcept* B:\n    pass", 3),   # try-star counts like try
    ("with a:\n    if b:\n        pass", 2),
    ("for x in y:\n    pass\nelse:\n    if a:\n        pass", 3),   # a decision in a loop else
    ("while a:\n    pass\nelse:\n    if b:\n        pass", 3),
    ("try:\n    pass\nexcept A:\n    pass\nelse:\n    if a:\n        pass", 4),
    ("match a:\n    case 1:\n        pass\n    case _ if g:\n        pass", 3),  # guarded: counts
    ("match a:\n    case 1:\n        pass\n    case y:\n        pass", 2),       # capture = else
    ("match a:\n    case 1:\n        pass\n    case 2 | _:\n        pass", 2),   # or-pattern with _
    ("return a and b or c", 1),                        # boolean operators score nothing
    ("return [x for x in y if x]", 1),                 # nor do comprehensions
    ("match a:\n    case 1:\n        pass\n    case 2:\n        pass", 3),
    ("match a:\n    case 1:\n        pass\n    case _:\n        pass", 2),  # wildcard = else
    ("def inner():\n    if a:\n        pass", 3),        # a nested def counts in its parent
])
def test_complexity_counts_like_ruff_c901(body, expected):
    src = "def f():\n" + textwrap.indent(body, "    ") + "\n"
    assert _cx(src)["f"] == expected


def test_functions_are_keyed_by_qualified_name():
    found = _cx("""
        class A:
            def m(self):
                def inner():
                    pass
        if X:
            def g():
                pass
    """)
    assert found == {"A.m": 2, "A.m.inner": 1, "g": 1}


# --- the gate binds -----------------------------------------------------------------


def _complex_fn(name: str, branches: int) -> str:
    return f"def {name}(x):\n" + "".join(
        f"    if x == {i}:\n        return {i}\n" for i in range(branches)) + "    return -1\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project in tmp_path with caps of 20 lines (prod), 30 (tests), complexity 3,
    one file over its cap and one function over the complexity cap, both baselined."""
    monkeypatch.setattr(cg, "PROJECT", tmp_path)
    monkeypatch.setattr(cg, "BASELINE", tmp_path / "scripts" / "growth_baseline.json")
    monkeypatch.setattr(cg, "PROD_LINE_CAP", 20)
    monkeypatch.setattr(cg, "TEST_LINE_CAP", 30)
    monkeypatch.setattr(cg, "COMPLEXITY_CAP", 3)
    (tmp_path / "dokimi").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "dokimi" / "big.py").write_text("x = 1\n" * 22, encoding="utf-8")
    (tmp_path / "dokimi" / "cx.py").write_text(_complex_fn("f", 4), encoding="utf-8")
    (tmp_path / "tests" / "test_ok.py").write_text("x = 1\n" * 25, encoding="utf-8")
    cg.BASELINE.write_text(cg.dump_baseline({
        "files": {"dokimi/big.py": {"lines": 22}},
        "functions": {"dokimi/cx.py::f": {"complexity": 5}},
    }), encoding="utf-8")
    return tmp_path


def _found(root: Path) -> list[str]:
    return cg.violations(cg.measure(root), cg.load_baseline())


def test_the_fixture_project_passes(project):
    assert _found(project) == []
    assert cg.main([]) == 0


def test_a_baselined_file_that_grows_fails(project):
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 23, encoding="utf-8")
    assert _found(project) == ["dokimi/big.py: 23 lines grew past its baseline 22; "
                               "split it instead (see growth_baseline.json)"]
    assert cg.main([]) == 1


def test_a_new_file_over_the_cap_fails(project):
    (project / "scripts" / "new.py").write_text("x = 1\n" * 21, encoding="utf-8")
    (project / "tests" / "test_ok.py").write_text("x = 1\n" * 31, encoding="utf-8")
    found = _found(project)
    assert [f.split(":")[0] for f in found] == ["scripts/new.py", "tests/test_ok.py"]
    assert all("no baseline entry" in f for f in found)


def test_a_new_complex_function_fails(project):
    (project / "dokimi" / "more.py").write_text(_complex_fn("g", 3), encoding="utf-8")
    assert _found(project) == ["dokimi/more.py::g: 4 complexity is over the 3 cap and has no "
                               "baseline entry; split the function into helpers"]


def test_a_baselined_function_that_grows_fails(project):
    (project / "dokimi" / "cx.py").write_text(_complex_fn("f", 5), encoding="utf-8")
    assert _found(project) == ["dokimi/cx.py::f: 6 complexity grew past its baseline 5; "
                               "split it instead (see growth_baseline.json)"]


def test_slack_and_stale_entries_fail(project):
    # Shrunk but still over the cap: the entry must come down with it, or the slack
    # would let the file grow back unseen.
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 21, encoding="utf-8")
    # The function is gone.
    (project / "dokimi" / "cx.py").write_text("def h():\n    pass\n", encoding="utf-8")
    found = _found(project)
    assert len(found) == 2
    assert found[0].startswith("dokimi/big.py: 21 lines, below its baseline 22")
    assert found[1].startswith("dokimi/cx.py::f: function no longer exists")


def test_an_entry_back_under_the_cap_must_go(project):
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 20, encoding="utf-8")
    assert _found(project)[0].startswith("dokimi/big.py: 20 lines, back under the cap")


def test_update_lowers_and_removes_but_never_raises_or_adds(project):
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 21, encoding="utf-8")
    (project / "dokimi" / "cx.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (project / "dokimi" / "new.py").write_text("x = 1\n" * 40, encoding="utf-8")
    assert cg.main(["--update"]) == 1           # new.py is still a violation
    assert cg.load_baseline() == {"files": {"dokimi/big.py": {"lines": 21}}, "functions": {}}
    # Growth is never written back.
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 24, encoding="utf-8")
    cg.main(["--update"])
    assert cg.load_baseline()["files"] == {"dokimi/big.py": {"lines": 21}}


def test_an_overlong_rationale_fails(project):
    cg.BASELINE.write_text(cg.dump_baseline({
        "files": {"dokimi/big.py": {"lines": 22, "rationale": "x" * (cg.RATIONALE_MAX + 1)}},
        "functions": {"dokimi/cx.py::f": {"complexity": 5}},
    }), encoding="utf-8")
    assert _found(project) == [f"dokimi/big.py: rationale must be a string of at most "
                               f"{cg.RATIONALE_MAX} characters"]


def test_raised_or_added_entries_need_a_rationale():
    base = {"files": {"a.py": {"lines": 900}}, "functions": {"a.py::f": {"complexity": 12}}}
    head = {
        "files": {"a.py": {"lines": 901}, "b.py": {"lines": 850, "rationale": "moved here"}},
        "functions": {"a.py::f": {"complexity": 11}, "a.py::g": {"complexity": 14}},
    }
    assert cg.raised_without_rationale(head, base) == [
        'a.py: raised from 900 to 901 without a "rationale" (at most 300 characters: '
        "why it cannot be split now)",
        'a.py::g: added without a "rationale" (at most 300 characters: '
        "why it cannot be split now)",
    ]
    head["files"]["a.py"]["rationale"] = "  "  # blank does not count
    assert len(cg.raised_without_rationale(head, base)) == 2
    head["files"]["a.py"]["rationale"] = "one release; split tracked in #N"
    head["functions"]["a.py::g"]["rationale"] = "dispatch table, split next"
    assert cg.raised_without_rationale(head, base) == []


def test_a_raised_entry_keeping_its_old_rationale_still_fails():
    base = {"files": {"a.py": {"lines": 900, "rationale": "legacy"}}, "functions": {}}
    head = {"files": {"a.py": {"lines": 1500, "rationale": "legacy"}}, "functions": {}}
    assert len(cg.raised_without_rationale(head, base)) == 1
    head["files"]["a.py"]["rationale"] = "grew for the new probe; split in #N"
    assert cg.raised_without_rationale(head, base) == []
    head["files"]["a.py"] = {"lines": 900, "rationale": "legacy"}  # unchanged: no new rationale needed
    assert cg.raised_without_rationale(head, base) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_base_baseline_is_read_from_git(project, monkeypatch):
    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=project, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("branch", "base")
    raised = cg.load_baseline()
    raised["files"]["dokimi/big.py"]["lines"] = 23
    cg.BASELINE.write_text(cg.dump_baseline(raised), encoding="utf-8")
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 23, encoding="utf-8")

    assert cg.load_base_baseline("no-such-ref") is None
    base = cg.load_base_baseline("base")
    assert base is not None and base["files"]["dokimi/big.py"] == {"lines": 22}
    assert cg.main(["--base", "base"]) == 1     # raised from 22 to 23 with no rationale
    monkeypatch.setenv("DOKIMI_GROWTH_BASE", "base")
    assert cg.base_ref() == "base"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_an_entry_the_base_lowered_after_the_fork_is_not_blamed_on_the_branch(project):
    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=project, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "fork point")
    git("tag", "fork")
    git("checkout", "-q", "-b", "feature")
    (project / "README.md").write_text("unrelated\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "feature")
    git("checkout", "-q", "main")
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 21, encoding="utf-8")
    assert cg.main(["--update"]) == 0           # main lowers big.py from 22 to 21
    git("commit", "-q", "-am", "shrink")

    git("checkout", "-q", "feature")            # a branch behind main
    assert cg.main(["--base", "main"]) == 0
    git("checkout", "-q", "--detach", "fork")   # CI's merge commit, built before the shrink
    git("merge", "-q", "--no-ff", "-m", "merge", "feature")
    assert cg.main(["--base", "main"]) == 0
    raised = cg.load_baseline()                 # a raise the branch makes is still caught
    raised["files"]["dokimi/big.py"]["lines"] = 23
    cg.BASELINE.write_text(cg.dump_baseline(raised), encoding="utf-8")
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 23, encoding="utf-8")
    assert cg.main(["--base", "main"]) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_non_origin_remote_baseline_via_dokimi_growth_base(project, monkeypatch):
    """Regression: ci_local.py resolves the base ref to a non-origin remote, but did not
    pass it to the pytest environment, causing the growth check to use the wrong baseline."""
    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=project, check=True, capture_output=True)

    # Set up git repo with a remote (simulating a fork setup).
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    # Simulate a non-origin remote by creating a local bare repo.
    remote_dir = project.parent / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote_dir)], check=True)
    git("remote", "add", "fork", str(remote_dir))
    git("push", "-q", "fork", "main")

    # The test: baseline is at the remote ref; ci_local.py resolves fork/main as the base
    # and must pass it to the check_growth script via DOKIMI_GROWTH_BASE env var.
    # Without the fix, load_base_baseline would fail or use the wrong ref.
    raised = cg.load_baseline()
    raised["files"]["dokimi/big.py"]["lines"] = 23
    cg.BASELINE.write_text(cg.dump_baseline(raised), encoding="utf-8")
    (project / "dokimi" / "big.py").write_text("x = 1\n" * 23, encoding="utf-8")

    # Simulate ci_local.py's behavior: it resolves the base to fork/main.
    monkeypatch.setenv("DOKIMI_GROWTH_BASE", "fork/main")
    # Now the check should pass because it compares against fork/main (the baseline).
    base = cg.load_base_baseline("fork/main")
    assert base is not None and base["files"]["dokimi/big.py"] == {"lines": 22}
    # The gate should catch the raised entry because it has no rationale.
    assert cg.main([]) == 1
    found = cg.raised_without_rationale(cg.load_baseline(), base)
    assert len(found) == 1
    assert "raised from 22 to 23 without a \"rationale\"" in found[0]


def test_report_totals(project, tmp_path, capsys):
    out = tmp_path / "r.json"
    assert cg.main(["--report", "--markdown", "--json", str(out)]) == 0
    assert "| 5 | `dokimi/cx.py::f` |" in capsys.readouterr().out
    r = json.loads(out.read_text(encoding="utf-8"))
    assert (r["files_over_cap"], r["lines_over_cap"]) == (1, 2)
    assert (r["functions_over_cap"], r["complexity_over_cap"]) == (1, 2)
