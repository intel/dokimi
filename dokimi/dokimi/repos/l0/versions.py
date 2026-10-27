# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Python versions a guidance line gives for a GitHub Actions workflow, against that workflow's matrices.

A table row such as ``| conda tests, py3.12-3.14t | `conda-package.yml` |`` goes stale when the workflow's
matrix moves on, and every path and name on the line still resolves. Only a line that names a workflow the
tree holds (a code span or a link ending in ``.yml``/``.yaml`` under ``.github/workflows/``) is checked, and
only a run of versions that a ``py``, ``Python`` or ``cp`` marker starts: a range written with a dash
(``Python 3.12-3.14``, ``py310–py313``) or a list of two or more (``Python 3.12, 3.13 and 3.14``). A single version is often one
job of many and gets no claim.

What a workflow runs is the values of every matrix key whose name holds ``python`` (``python``,
``python-version``, the keys of ``include`` entries; ``exclude`` is not read), over all its jobs and the
reusable workflows of this tree its jobs call (``uses: ./.github/workflows/x.yml``). A free-threaded build is
a ``3.14t`` value or a ``cp314t`` tag in any matrix string. A range is ``ok`` when its ends are the lowest and
highest version the matrices run (both ends written with ``t``: the lowest and highest free-threaded one), a
list when each version it names runs; a version written with ``t`` must run free-threaded. A matrix computed
from an expression (``${{ fromJSON(...) }}``) or a call to another repository's workflow makes the claim
``unresolved``.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .core import Scan
    from .markdown import Pieces

WORKFLOWS = ".github/workflows/"
Version = tuple[int, bool]  # (minor, free-threaded) of a CPython 3 version

_MARK = r"(?<![\w.])(?:[Cc][Pp]ython ?|[Pp]ython ?|[Pp]y|cp)"
_VER = r"3(?:\.\d{1,2}|\d{1,2})t?(?![\d.]*\d)"
_DASH = r"\s*[-–—]\s*"  # not `to`: "moved from Python 3.11 to 3.13" is no range
_SEP = r"\s*(?:,|/|&|\band\b|\bor\b)\s*"
_ITEM = rf"{_VER}(?:{_DASH}(?:{_MARK})?{_VER})?"
_RUN = re.compile(rf"{_MARK}{_ITEM}(?:{_SEP}(?:{_MARK})?{_ITEM})*")
_ONE = re.compile(rf"(?:{_MARK})?(?P<a>{_VER})(?:{_DASH}(?:{_MARK})?(?P<b>{_VER}))?")
_MATRIX_VALUE = re.compile(r"^3\.(\d{1,2})(t)?(?:\.[\dx*]+)?(?:-dev)?$")
_CP_TAG = re.compile(r"(?<![A-Za-z0-9])cp3(\d{1,2})t(?![A-Za-z0-9])")


def check(scan: Scan, file: str, pieces: Pieces) -> None:
    """A ``python_versions`` claim for each run of versions on a line that names a workflow."""
    lines: dict[int, tuple[str, list[str], list[str]]] = {}  # line -> (text, workflows, their spellings)
    for n, s, ctx in [*pieces.spans, *pieces.links]:
        wf = workflow(scan, s)
        if wf:
            entry = lines.setdefault(n, (ctx, [], []))
            entry[1].append(wf)
            entry[2].append(s)
    for n, (ctx, flows, spellings) in sorted(lines.items()):
        text = ctx
        for s in spellings:  # a file name such as `py312-wheels.yml` is not a version
            text = text.replace(s, " ")
        for m in _RUN.finditer(text):
            run = m.group(0).strip()
            claim = parse(run)
            if claim is not None:
                status, why = judge(scan, claim, sorted(set(flows)))
                scan.add(file, n, "python_versions", run, status, why, ctx)


def workflow(scan: Scan, name: str) -> str | None:
    """The workflow file a code span or link names, by its path or its bare file name."""
    s = name.strip().strip("<>").split("#")[0]
    if not s.endswith((".yml", ".yaml")) or " " in s:
        return None
    s = re.sub(r"^(?:\.{1,2}/)+", "", s)
    flows = scan.cached("workflows", lambda: [f for f in scan.files if f.startswith(WORKFLOWS)])
    return next((f for f in flows if f == s or f.endswith("/" + s)), None)


def _version(text: str) -> Version:
    digits = re.sub(r"\D", "", text.removeprefix("3").removesuffix("t"))
    return int(digits), text.endswith("t")


def parse(run: str) -> tuple[list[tuple[Version, Version]], list[Version]] | None:
    """The ranges and the single versions of a run; None for a run of one version."""
    ranges: list[tuple[Version, Version]] = []
    singles: list[Version] = []
    for m in _ONE.finditer(run):
        a = _version(m.group("a"))
        if m.group("b"):
            ranges.append((a, _version(m.group("b"))))
        else:
            singles.append(a)
    if not ranges and len(singles) < 2:
        return None
    return ranges, singles


def show(versions: set[Version] | list[Version]) -> str:
    return ", ".join(f"3.{minor}{'t' if ft else ''}" for minor, ft in sorted(versions))


def judge(scan: Scan, claim: tuple[list[tuple[Version, Version]], list[Version]], flows: list[str]) -> tuple[str, str]:
    """(status, why) for the versions a line gives, against the union of what its workflows run."""
    runs: set[Version] = set()
    unknown: list[str] = []
    for wf in flows:
        runs |= workflow_versions(scan, wf, unknown, set())
    where = ", ".join(flows)
    if unknown:
        return "unresolved", "; ".join(dict.fromkeys(unknown))
    if not runs:
        return "unresolved", f"no Python version in the matrices of {where}"
    ranges, singles = claim
    wrong = [f"3.{a[0]}{'t' if a[1] else ''} to 3.{b[0]}{'t' if b[1] else ''}" for a, b in ranges
             if not _range_holds(a, b, runs)]
    named = [v for a, b in ranges for v in (a, b) if v[1]] + singles
    wrong += [show([v]) for v in named if v not in runs]
    if wrong:
        return "fail", f"the matrices of {where} run {show(runs)}, not {', '.join(wrong)}"
    return "ok", f"the matrices of {where} run {show(runs)}"


def _range_holds(a: Version, b: Version, runs: set[Version]) -> bool:
    ft = a[1] and b[1]  # both ends free-threaded: a range of free-threaded builds
    minors = [minor for minor, t in runs if t == ft]
    return bool(minors) and min(minors) == a[0] and max(minors) == b[0]


def workflow_versions(scan: Scan, path: str, unknown: list[str], seen: set[str]) -> set[Version]:
    """What the matrices of ``path`` and the local workflows it calls run; why that is not known goes to ``unknown``."""
    if path in seen:
        return set()
    seen.add(path)
    try:
        doc = yaml.safe_load(scan.text(path))
    except yaml.YAMLError:
        unknown.append(f"{path} is not valid YAML")
        return set()
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    out: set[Version] = set()
    for job in (jobs.values() if isinstance(jobs, dict) else []):
        if not isinstance(job, dict):
            continue
        out |= _called(scan, path, job.get("uses"), unknown, seen)
        strategy = job.get("strategy")
        if isinstance(strategy, dict) and "matrix" in strategy:
            out |= matrix_versions(strategy["matrix"], path, unknown)
    return out


def _called(scan: Scan, path: str, uses: Any, unknown: list[str], seen: set[str]) -> set[Version]:
    """The versions of a reusable workflow a job of ``path`` calls with ``uses:``."""
    if not isinstance(uses, str):
        return set()
    if not uses.startswith("./"):
        unknown.append(f"a job of {path} calls {uses}, a workflow outside this tree")
        return set()
    target = posixpath.normpath(uses[2:])
    if target not in scan.file_set:
        unknown.append(f"a job of {path} calls {target}, which the tree does not hold")
        return set()
    return workflow_versions(scan, target, unknown, seen)


def matrix_versions(matrix: Any, path: str, unknown: list[str]) -> set[Version]:
    """The versions one ``strategy.matrix`` runs."""
    if isinstance(matrix, str):
        unknown.append(f"a matrix of {path} is computed ({matrix.strip()})")
        return set()
    out: set[Version] = set()
    if not isinstance(matrix, dict):
        return out
    for key, values in matrix.items():
        if key == "exclude":
            continue
        for value in values if isinstance(values, list) else [values]:
            pairs = value.items() if key == "include" and isinstance(value, dict) else [(key, value)]
            for k, v in pairs:
                out |= _value(str(k), v, path, unknown)
    return out


def _value(key: str, value: Any, path: str, unknown: list[str]) -> set[Version]:
    if not isinstance(value, (str, int, float)):  # a nested list or mapping: str() of YAML aliases can explode
        return set()
    text = str(value).strip()
    out = {(int(m), True) for m in _CP_TAG.findall(text)}
    if "python" not in key.lower() and key.lower() != "py":
        return out
    if "${{" in text:
        unknown.append(f"a matrix of {path} is computed ({text})")
        return out
    m = _MATRIX_VALUE.match(text)
    if m:
        out.add((int(m.group(1)), bool(m.group(2))))
    return out
