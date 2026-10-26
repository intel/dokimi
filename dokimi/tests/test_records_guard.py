# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Answer and eval records are read through ``dokimi.records``, not by their persisted keys.

``dokimi/records.py`` is the one reader of the ``with_docs`` and ``without_docs`` conditions of
an answers, eval or arms record (see ``AGENTS.md``). This test counts, in each module of
``dokimi/`` but that one, the reads of either key: an ``x["with_docs"]`` subscript that is not
assigned to, and an ``x.get("with_docs")``, ``.pop`` or ``.setdefault`` call. A dict-literal key
and an assignment are writes and do not match.

:data:`READ_BASELINE` holds the modules whose reads may stay, with their count and the reason
they are not reads of a persisted record. More reads than the count fail, so a new read fails
wherever it is; fewer fail as stale, so the baseline only shrinks. Never raise a count to make a
new read pass: read the record through ``dokimi.records``.

Not covered: a read through the constants (``rec[records.CONTEXT]``), a ``"with_docs" in x``
test, and ``.raw`` reads of record fields, which a pattern cannot tell from other ``.raw``
attributes (``Task.raw`` of ``dokimi.repos`` is a task file, not a record).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "dokimi"
READER = "dokimi.records"

_KEY = r"""["'](?:with|without)_docs["']"""
#: A subscript of a condition key not followed by ``=`` (``==`` is a read), or a dict method call on one.
READ = re.compile(rf"\[\s*{_KEY}\s*\](?!\s*=[^=])|\.(?:get|pop|setdefault)\(\s*{_KEY}")

#: ``module -> (reads, reason)`` for the reads that may stay. Only shrinks.
READ_BASELINE: dict[str, tuple[int, str]] = {
    "dokimi.eval.answerer": (2, "a writer counting the answers it just built, before they are persisted"),
    "dokimi.eval.question_text": (
        2, "takes the persisted record dict its judges pass as Record.raw; moving it onto Record changes "
           "every judge's call"),
    "dokimi.report.generator": (6, "reads the statistics dict it computed from the evaluations, not a record"),
    "dokimi.report.model_compare": (
        17, "reads the per-question score rows extract_scores built, which use the binary score names"),
}


def reads(source: str) -> list[int]:
    """The line of each read of a condition key in ``source``."""
    return [source.count("\n", 0, m.start()) + 1 for m in READ.finditer(source)]


def package_reads(package: Path = PACKAGE, reader: str = READER) -> dict[str, list[int]]:
    """``module -> lines`` of every read of a condition key outside ``reader``."""
    found: dict[str, list[int]] = {}
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).with_suffix("")
        module = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
        if module != reader and (lines := reads(path.read_text(encoding="utf-8"))):
            found[module] = lines
    return found


def problems(baseline: Mapping[str, tuple[int, str]], found: Mapping[str, list[int]]) -> list[str]:
    """A module with more reads than its baseline allows (0 if not listed), or fewer than it counts."""
    out = []
    for module in sorted(set(baseline) | set(found)):
        lines, allowed = found.get(module, []), baseline.get(module, (0, ""))[0]
        if len(lines) > allowed:
            out.append(f"{module}:{lines} reads a record's with_docs/without_docs key ({len(lines)} reads, "
                       f"{allowed} allowed); read the record through {READER} (records()/record(), Record.score)")
        elif len(lines) < allowed:
            fix = f"lower its count to {len(lines)}" if lines else "delete its entry"
            out.append(f"{module} has {len(lines)} of its {allowed} baselined reads left; {fix} in READ_BASELINE")
    return out


def test_records_are_read_through_the_records_module():
    found = package_reads()
    assert found, f"the scan found no read under {PACKAGE}: it would pass every check"
    found_problems = problems(READ_BASELINE, found)
    assert not found_problems, "\n".join(found_problems)


def test_every_baseline_entry_has_a_reason_and_names_a_real_module():
    for module, (count, reason) in READ_BASELINE.items():
        assert count >= 1 and reason.strip(), module
        assert (PACKAGE.parent / (module.replace(".", "/") + ".py")).is_file(), f"{module} names no module"


def test_subscript_loads_and_dict_method_calls_are_reads():
    source = ('a = r["with_docs"]["aggregate"]\nb = r.get("without_docs")\nc = r.pop(\'with_docs\')\n'
              'd = r.setdefault("without_docs", {})\nif r[\n    "with_docs"\n] == x:\n    pass\n')
    assert reads(source) == [1, 2, 3, 4, 5]


def test_assignments_dict_literals_and_other_keys_are_not_reads():
    source = ('r["with_docs"] = {"answer": "a"}\nrow = {"with_docs": None, "without_docs": None}\n'
              'r["aggregate"], r.get("delta"), r.get(key), r[key]\n')
    assert reads(source) == []


def test_the_reader_module_is_exempt_and_a_package_read_is_found(tmp_path):
    package = tmp_path / "pkg"
    (package / "sub").mkdir(parents=True)
    (package / "records.py").write_text("def f(r):\n    return r.get('with_docs')\n")
    (package / "sub" / "__init__.py").write_text("def g(r):\n    return r['without_docs']\n")
    (package / "sub" / "mod.py").write_text("X = 1\n")
    assert package_reads(package, reader="pkg.records") == {"pkg.sub": [2]}


def test_a_new_read_an_extra_read_and_a_stale_count_are_reported():
    baseline = {"pkg.a": (1, "x"), "pkg.b": (2, "y"), "pkg.c": (1, "z")}
    found = {"pkg.a": [3, 7], "pkg.b": [1], "pkg.new": [9]}
    assert problems(baseline, found) == [
        "pkg.a:[3, 7] reads a record's with_docs/without_docs key (2 reads, 1 allowed); "
        "read the record through dokimi.records (records()/record(), Record.score)",
        "pkg.b has 1 of its 2 baselined reads left; lower its count to 1 in READ_BASELINE",
        "pkg.c has 0 of its 1 baselined reads left; delete its entry in READ_BASELINE",
        "pkg.new:[9] reads a record's with_docs/without_docs key (1 reads, 0 allowed); "
        "read the record through dokimi.records (records()/record(), Record.score)",
    ]
