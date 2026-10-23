# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Answer and eval records are read through ``dokimi.records``, not by their persisted keys.

``dokimi/records.py`` is the one reader of the ``with_docs`` and ``without_docs``
conditions of an answers, eval or arms record (see ``AGENTS.md``). This test scans
``dokimi/`` with ``ast`` for a read of either key outside it: a ``x["with_docs"]``
subscript, or a ``x.get("with_docs")``, ``.pop`` or ``.setdefault`` call. A store
(``x["with_docs"] = ...``), a delete and a dict-literal key are writes and are not reads.

Each read that may stay is in :data:`READ_BASELINE`, keyed by module and enclosing
function, with the number of reads there and the reason they are not reads of a persisted
record or why they have not moved. A read beyond that number fails, in a function the
baseline lists as anywhere else; an entry that counts more reads than are left fails as
stale, so the baseline only shrinks. Never add an entry to make a new read pass: read the record
through ``dokimi.records`` (``records()``/``record()``/``load()``, then ``Record.score``,
``Record.answer`` and the ``Score``/``Answer`` fields).

Not covered: a read through the constants (``rec[records.CONTEXT]``), a ``"with_docs" in x``
test, and ``.raw`` reads of record fields (``rec.raw.get("question_text")``). The last cannot
be told from the other ``.raw`` attributes by syntax: ``Answer.raw`` and ``Score.raw`` are
condition payloads, and ``Task.raw`` of ``dokimi.repos`` is a task file, not a record.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / "dokimi"
READER = "dokimi.records"

#: The persisted condition keys of a binary answers or eval record.
KEYS = frozenset({"with_docs", "without_docs"})

#: Dict methods that read the value of the key they are given.
READ_METHODS = frozenset({"get", "pop", "setdefault"})

ReadSite = tuple[str, str]

#: ``(module, enclosing function) -> (reads, reason)`` for the reads that may stay. Only shrinks.
READ_BASELINE: dict[ReadSite, tuple[int, str]] = {
    ("dokimi.eval.answerer", "Answerer.generate_answers"): (2,
        "a writer counting the answers it just built, before they are persisted"),
    ("dokimi.eval.question_text", "has_answer_to_judge"): (2,
        "takes the persisted record dict its judges pass as Record.raw; moving it onto Record "
        "changes every judge's call"),
    ("dokimi.report.generator", "ReportGenerator._format_markdown"): (6,
        "reads the statistics dict the generator computed from the evaluations, not a record"),
    ("dokimi.report.model_compare", "pool_replicate_scores"): (2,
        "reads the per-question score rows extract_scores built, which use the binary score names"),
    ("dokimi.report.model_compare", "generate_section_report"): (15,
        "reads the per-question score rows extract_scores built, which use the binary score names"),
}


def _enclosing_names(tree: ast.AST) -> dict[ast.AST, str]:
    """Each node's innermost enclosing function or class qualname (``<module>`` at top level)."""
    names: dict[ast.AST, str] = {}

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope + (child.name,) if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else scope
            names[child] = ".".join(scope) or "<module>"
            visit(child, inner)

    visit(tree, ())
    return names


def _is_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value in KEYS


def key_reads(source: str) -> list[tuple[ReadSite, int]]:
    """``(<scope>, line)`` of each ``x["with_docs"]`` load and ``x.get/pop/setdefault("with_docs")``."""
    tree = ast.parse(source)
    scopes = _enclosing_names(tree)
    found = []
    for node in ast.walk(tree):
        subscript_load = (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                          and _is_key(node.slice))
        get_call = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in READ_METHODS and bool(node.args) and _is_key(node.args[0]))
        if subscript_load or get_call:
            found.append((scopes.get(node, "<module>"), node.lineno))
    return found


def package_reads(package: Path = PACKAGE, reader: str = READER) -> dict[ReadSite, list[int]]:
    """Every read of a condition key outside ``reader``: ``(module, scope) -> lines``."""
    found: dict[ReadSite, list[int]] = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).with_suffix("")
        module = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
        if module == reader:
            continue
        for scope, line in key_reads(path.read_text(encoding="utf-8")):
            found.setdefault((module, scope), []).append(line)
    return {site: sorted(lines) for site, lines in found.items()}


def stale_entries(baseline: Mapping[ReadSite, tuple[int, str]],
                  found: Mapping[ReadSite, list[int]]) -> list[str]:
    """Baseline entries that count more reads than are left: lower the count, or delete the entry."""
    stale = []
    for (module, scope), (allowed, _) in sorted(baseline.items()):
        left = len(found.get((module, scope), []))
        if left < allowed:
            fix = f"lower its count to {left}" if left else "delete the entry"
            stale.append(f"{module}:{scope} has {left} of its {allowed} baselined reads left; {fix}")
    return stale


def new_reads(baseline: Mapping[ReadSite, tuple[int, str]], found: Mapping[ReadSite, list[int]]) -> list[str]:
    """Sites with more reads than the baseline allows (none, for a site it does not list)."""
    problems = []
    for (module, scope), lines in sorted(found.items()):
        allowed = baseline.get((module, scope), (0, ""))[0]
        if len(lines) > allowed:
            over = f" ({len(lines)} reads, the baseline allows {allowed})" if allowed else ""
            problems.append(f"{module}:{lines} reads a record's with_docs/without_docs key in {scope}{over}; "
                            f"read the record through {READER} (records()/record(), Record.score)")
    return problems


# --- the package ----------------------------------------------------------------


def test_records_are_read_through_the_records_module():
    found = package_reads()
    assert found, f"the scan found no read under {PACKAGE}: it would pass every check"
    problems = new_reads(READ_BASELINE, found)
    assert not problems, "\n".join(problems)


def test_a_read_that_moved_leaves_the_baseline():
    stale = stale_entries(READ_BASELINE, package_reads())
    assert not stale, "READ_BASELINE counts reads that are gone:\n" + "\n".join(stale)


def test_every_baseline_entry_has_a_reason_and_names_a_real_module():
    assert all(count >= 1 and reason.strip() for count, reason in READ_BASELINE.values()), (
        "every entry counts at least one read and carries its reason")
    for module, _ in READ_BASELINE:
        assert (PACKAGE.parent / (module.replace(".", "/") + ".py")).is_file(), f"{module} names no module"


# --- the scanner finds what it should -------------------------------------------


def test_a_subscript_load_get_pop_and_setdefault_are_reads_in_their_function():
    source = (
        "def total(rows):\n"
        "    return [r['with_docs'] for r in rows if r.get('without_docs')]\n"
        "class Report:\n"
        "    def line(self, r):\n"
        "        return r.get(\"with_docs\", {})\n"
        "def take(r):\n"
        "    return r.pop('with_docs'), r.setdefault('without_docs', None)\n"
    )
    assert sorted(key_reads(source)) == [("Report.line", 5), ("take", 7), ("take", 7), ("total", 2), ("total", 2)]


def test_a_store_a_delete_and_a_dict_literal_are_writes_not_reads():
    source = (
        "def write(r):\n"
        "    r['with_docs'] = {'answer': 'a'}\n"
        "    del r['without_docs']\n"
        "    return {'with_docs': None, 'without_docs': None}\n"
    )
    assert key_reads(source) == []


def test_other_keys_and_non_literal_keys_are_not_reads():
    source = "def read(r, key):\n    return r['aggregate'], r.get('delta'), r[key], r.get(key)\n"
    assert key_reads(source) == []


def test_the_reader_module_is_exempt_and_a_package_read_is_found(tmp_path):
    package = tmp_path / "pkg"
    (package / "sub").mkdir(parents=True)
    (package / "records.py").write_text("def f(r):\n    return r.get('with_docs')\n")
    (package / "sub" / "__init__.py").write_text("def g(r):\n    return r['without_docs']\n")
    (package / "sub" / "mod.py").write_text("X = 1\n")
    assert package_reads(package, reader="pkg.records") == {("pkg.sub", "g"): [2]}


def test_a_read_missing_from_the_baseline_is_reported_with_the_fix():
    found = {("pkg.a", "f"): [3], ("pkg.b", "g"): [9]}
    problems = new_reads({("pkg.a", "f"): (1, "a writer")}, found)
    assert len(problems) == 1 and problems[0].startswith("pkg.b:[9] ")
    assert "read the record through dokimi.records" in problems[0]


def test_a_new_read_in_a_function_the_baseline_lists_is_reported():
    problems = new_reads({("pkg.a", "f"): (1, "a writer")}, {("pkg.a", "f"): [3, 7]})
    assert len(problems) == 1 and "pkg.a:[3, 7] " in problems[0]
    assert "(2 reads, the baseline allows 1)" in problems[0]


def test_a_baseline_entry_counting_reads_that_are_gone_is_reported():
    baseline = {("a", "f"): (1, "x"), ("b", "g"): (2, "y"), ("c", "h"): (1, "z")}
    assert stale_entries(baseline, {("b", "g"): [1], ("c", "h"): [4]}) == [
        "a:f has 0 of its 1 baselined reads left; delete the entry",
        "b:g has 1 of its 2 baselined reads left; lower its count to 1",
    ]
    assert stale_entries({}, {("b", "g"): [1]}) == []
