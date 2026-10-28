# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Python versions a guidance line gives for a GitHub Actions workflow, against that workflow's matrices.

A table row such as ``| conda tests, py3.12-3.14t | `conda-package.yml` |`` goes stale when the workflow's
matrix moves on, and every path and name on the line still resolves. Only a line that names a workflow the
tree holds (a code span or a link ending in ``.yml``/``.yaml`` under ``.github/workflows/``, a blob or tree
link to this repository too) is checked, and only a run of versions that a ``py``, ``Python`` or ``cp``
marker starts: a range written with a dash (``Python 3.12-3.14``, ``py310–py313``) or a list of two or more
(``Python 3.12, 3.13 and 3.14``). A single version is often one job of many and gets no claim. A ``cp`` tag
starts no range: ``cp313-cp313`` is the two tags of a wheel's name. Without a marker in front, a version
needs its dot (``30 min`` after a list is no version), and the workflow's own name is no version.

What a workflow runs is the values of every matrix key whose name holds ``python`` (``python``,
``python-version``, the keys of ``include`` entries; ``3.12``, ``cp312`` or ``py312``), less a value that
``exclude`` takes out of every combination, and the ``python-version`` a step sets up
(``actions/setup-python``, or another action's input of that name; ``${{ matrix.<key> }}`` there makes
``<key>`` a Python key), over all its jobs and the reusable workflows of this tree its jobs call
(``uses: ./.github/workflows/x.yml``). A pre-release (``3.14.0-rc.1``, ``3.14.0a1``) runs its minor
version. A free-threaded build is a ``3.14t`` value or a ``cp314t`` tag in any matrix string. A range is
``ok`` when its ends are the lowest and highest version the workflow runs (both ends written with ``t``: the
lowest and highest free-threaded one), a list when each version it names runs; a version written with ``t``
must run free-threaded. A matrix, ``include``, ``exclude`` or step version computed from an expression
(``${{ fromJSON(...) }}``), one that names no one version (``3.x``, ``>=3.12``), a ``python-version-file``,
or a call to another repository's workflow makes the claim ``unresolved``.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection
from itertools import product
from math import prod
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .core import Scan
    from .markdown import Pieces

WORKFLOWS = ".github/workflows/"
Version = tuple[int, bool]  # (minor, free-threaded) of a CPython 3 version

_PY = r"(?:[Cc][Pp]ython ?|[Pp]ython ?|[Pp]y)"
_DOTTED = r"3\.\d{1,2}t?(?![\d.]*\d)"  # `3.12`, `3.14t`; not a patch release
_VER = r"3(?:\.\d{1,2}|\d{1,2})t?(?![\d.]*\d)"  # also `312` and `314t`, only right after a `py` or `cp`
_DASH = r"\s*[-–—]\s*"  # not `to`: "moved from Python 3.11 to 3.13" is no range
_SEP = r"\s*(?:,|/|&|\band\b|\bor\b)\s*"
_TO = rf"(?:{_DASH}(?:(?:{_PY}|cp){_VER}|{_DOTTED}))?"  # the end of a range written with a dash
# a `cp` tag is one version and starts no range: `cp313-cp313` is the two tags of a wheel's name
_ITEM = rf"(?<![\w.])(?:cp{_VER}|{_PY}{_VER}{_TO})"
_RUN = re.compile(rf"{_ITEM}(?:{_SEP}(?:{_ITEM}|{_DOTTED}{_TO}))*")  # `30 min` after a list is no version
_ONE = re.compile(rf"(?:cp|{_PY})?(?P<a>{_VER})(?:{_DASH}(?:cp|{_PY})?(?P<b>{_VER}))?")
# `3.14`, `3.14t`, `3.14.2`, `3.12.x`, and a pre-release: `3.14-dev`, `3.14.0-rc.1`, `3.15.0-alpha.1`, `3.14.0a1`
_MATRIX_VALUE = re.compile(r"^3\.(\d{1,2})(t)?(?:\.[\dx*]+)?(?:-?(?:dev|alpha|beta|rc|a|b)[.\d]*)?(t)?$")
# a CPython version spec that names no one version: the latest (`3.x`) or a range (`>=3.12`, `3.12 - 3.14`)
_OPEN = re.compile(r"^(?:3(?:\.[x*])?|[<>=~^!].*|3[\d.]*\s+-\s+3[\d.]*)$")
_TAG_VALUE = re.compile(r"^(?:cp|py)3(\d{1,2})(t)?$")  # `cp312`, `cp313t`, `py312` under a Python key
_MATRIX_REF = re.compile(r"^\$\{\{\s*matrix(?:\.([\w-]+)|\[\s*['\"]([\w-]+)['\"]\s*\])\s*\}\}$")
_CP_TAG = re.compile(r"(?<![A-Za-z0-9])cp3(\d{1,2})t(?![A-Za-z0-9])")
_MAX_JOBS = 256  # the most jobs GitHub makes from one matrix


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
        for m in _RUN.finditer(_unnamed(ctx, spellings, flows)):
            run = m.group(0).strip()
            claim = parse(run)
            if claim is not None:
                status, why = judge(scan, claim, sorted(set(flows)))
                scan.add(file, n, "python_versions", run, status, why, ctx)


def _unnamed(text: str, spellings: list[str], flows: list[str]) -> str:
    """The line with its workflows' names blanked: ``py312-wheels.yml`` is no version, in a code span, a link
    target or a link text (``[py310-py313](...)`` too)."""
    for s in spellings:
        text = text.replace(s, " ")
    for f in flows:
        stem = posixpath.splitext(posixpath.basename(f))[0]
        if _RUN.search(stem):
            text = re.sub(rf"(?<![\w.-]){re.escape(stem)}(?![\w-])", " ", text)
    return text


def workflow(scan: Scan, name: str) -> str | None:
    """The workflow file a code span or link names, by its path or its bare file name.

    A blob or tree link to this repository names the file at its path. A bare name is the file in
    ``.github/workflows/`` itself, the directory GitHub runs workflows from; else the one workflow path the
    name ends. A name that several of them end, and none there, names no workflow.
    """
    s = name.strip().strip("<>")
    s = scan.own_path(s) or s.split("#")[0]
    if not s.endswith((".yml", ".yaml")) or " " in s:
        return None
    s = re.sub(r"^(?:\.{0,2}/)+", "", s)  # `/`, `./` and `../` in front
    flows = scan.cached("workflows", lambda: [f for f in scan.files if f.startswith(WORKFLOWS)])
    for exact in (s, WORKFLOWS + s):
        if exact in flows:
            return exact
    hits = [f for f in flows if f.endswith("/" + s)]
    return hits[0] if len(hits) == 1 else None


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
        got, why = scan.cached(f"python_versions:{wf}", lambda wf=wf: _read(scan, wf))
        runs |= got
        unknown += why
    where = ", ".join(flows)
    if unknown:
        return "unresolved", "; ".join(dict.fromkeys(unknown))
    if not runs:
        return "unresolved", f"no Python version in the matrices or setup steps of {where}"
    ranges, singles = claim
    wrong = [f"3.{a[0]}{'t' if a[1] else ''} to 3.{b[0]}{'t' if b[1] else ''}" for a, b in ranges
             if not _range_holds(a, b, runs)]
    named = [v for a, b in ranges for v in (a, b) if v[1]] + singles
    wrong += [show([v]) for v in named if v not in runs]
    if wrong:
        return "fail", f"the jobs of {where} run {show(runs)}, not {', '.join(wrong)}"
    return "ok", f"the jobs of {where} run {show(runs)}"


def _range_holds(a: Version, b: Version, runs: set[Version]) -> bool:
    ft = a[1] and b[1]  # both ends free-threaded: a range of free-threaded builds
    minors = [minor for minor, t in runs if t == ft]
    return bool(minors) and min(minors) == a[0] and max(minors) == b[0]


def _read(scan: Scan, path: str) -> tuple[frozenset[Version], tuple[str, ...]]:
    """What ``path`` runs and why that is not known, read once per scan: many lines can name one workflow."""
    unknown: list[str] = []
    return frozenset(workflow_versions(scan, path, unknown, set())), tuple(unknown)


def workflow_versions(scan: Scan, path: str, unknown: list[str], seen: set[str]) -> set[Version]:
    """What the jobs of ``path`` and the local workflows it calls run; why that is not known goes to ``unknown``."""
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
        if isinstance(job, dict):
            out |= _job(scan, path, job, unknown, seen)
    return out


def _job(scan: Scan, path: str, job: dict[Any, Any], unknown: list[str], seen: set[str]) -> set[Version]:
    """What one job runs: a local workflow it calls, the Python its steps set up, and its matrix."""
    keys: set[str] = set()  # the matrix keys a setup step takes its version from
    out = _called(scan, path, job.get("uses"), unknown, seen) | setup_versions(job.get("steps"), path, unknown, keys)
    strategy = job.get("strategy")
    if isinstance(strategy, dict) and "matrix" in strategy:
        out |= matrix_versions(strategy["matrix"], path, unknown, keys)
    return out


def setup_versions(steps: Any, path: str, unknown: list[str], keys: set[str]) -> set[Version]:
    """The versions a job's steps set up with ``with: python-version``; ``${{ matrix.<key> }}`` goes to ``keys``."""
    out: set[Version] = set()
    where = f"a setup step of {path}"
    for step in steps if isinstance(steps, list) else []:
        given = step.get("with") if isinstance(step, dict) else None
        if not isinstance(given, dict):
            continue
        if "python-version-file" in given:
            unknown.append(f"{where} reads its Python version from {given['python-version-file']}")
        value = given.get("python-version")
        ref = _MATRIX_REF.match(value.strip()) if isinstance(value, str) else None
        if ref:
            keys.add(ref.group(1) or ref.group(2))
        elif isinstance(value, (str, int, float)):
            for item in str(value).strip().splitlines():  # setup-python takes one version per line
                out |= _value("python-version", item, where, unknown)
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


def matrix_versions(matrix: Any, path: str, unknown: list[str], keys: Collection[str] = ()) -> set[Version]:
    """The versions one ``strategy.matrix`` runs; ``keys`` hold Python versions too, whatever their names.

    A value that ``exclude`` takes out of every combination does not run; an ``include`` entry runs anyway.
    """
    if isinstance(matrix, str):
        unknown.append(f"a matrix of {path} is computed ({matrix.strip()})")
        return set()
    out: set[Version] = set()
    if not isinstance(matrix, dict):
        return out
    where = f"a matrix of {path}"
    gone = _excluded(matrix, keys, where, unknown)
    for key, values in matrix.items():
        items = values if isinstance(values, list) else [values]
        if key == "include":
            out |= _included(items, keys, where, unknown)
        elif key != "exclude":
            for i, value in enumerate(items):
                if (key, i) not in gone:
                    out |= _value(str(key), value, where, unknown, str(key) in keys)
    return out


def _included(entries: list[Any], keys: Collection[str], where: str, unknown: list[str]) -> set[Version]:
    """What the ``include`` entries of a matrix run; a computed ``include`` adds what is not known."""
    out: set[Version] = set()
    for entry in entries:
        if isinstance(entry, str) and "${{" in entry:
            unknown.append(f"{where} computes its include ({entry.strip()})")
        for k, v in entry.items() if isinstance(entry, dict) else []:
            out |= _value(str(k), v, where, unknown, str(k) in keys)
    return out


def _excluded(matrix: dict[Any, Any], keys: Collection[str], where: str, unknown: list[str]) -> set[tuple[Any, int]]:
    """``(key, index)`` of each Python value that ``exclude`` takes out of every combination of the matrix."""
    given = matrix.get("exclude")
    if isinstance(given, str) and "${{" in given:
        unknown.append(f"{where} computes its exclude ({given.strip()})")
    rules = [r for r in given if isinstance(r, dict)] if isinstance(given, list) else []
    base = {k: v if isinstance(v, list) else [v] for k, v in matrix.items() if k not in ("include", "exclude")}
    gone: set[tuple[Any, int]] = set()
    for key, values in base.items():
        if not _python_key(str(key), keys) or not any(key in r for r in rules):
            continue  # an entry with no Python key takes as much out of every Python version's combinations
        names = [k for k in base if k != key]
        if prod(len(base[k]) for k in names) > _MAX_JOBS:
            unknown.append(f"{where} excludes Python versions from more than {_MAX_JOBS} combinations")
            continue
        for i, value in enumerate(values):
            combos = ({**dict(zip(names, c, strict=True)), key: value} for c in product(*(base[k] for k in names)))
            if all(any(_matches(r, combo) for r in rules) for combo in combos):
                gone.add((key, i))
    return gone


def _matches(rule: dict[Any, Any], combo: dict[Any, Any]) -> bool:
    """An ``exclude`` entry matches a combination that holds each of its keys with the same value."""
    return all(k in combo and _same(v, combo[k]) for k, v in rule.items())


def _same(a: Any, b: Any) -> bool:
    """Scalars compare as text (``3.14`` and ``'3.14'``); a list or mapping only as the same YAML node."""
    scalar = (str, int, float)
    return str(a).strip() == str(b).strip() if isinstance(a, scalar) and isinstance(b, scalar) else a is b


def _python_key(key: str, keys: Collection[str]) -> bool:
    return "python" in key.lower() or key.lower() == "py" or key in keys


def _value(key: str, value: Any, where: str, unknown: list[str], python: bool = False) -> set[Version]:
    """What one value runs: a ``cp3XYt`` tag in it, and its version under a Python key (or with ``python``)."""
    if not isinstance(value, (str, int, float)):  # a nested list or mapping: str() of YAML aliases can explode
        return set()
    text = str(value).strip()
    out = {(int(m), True) for m in _CP_TAG.findall(text)}
    if not python and not _python_key(key, ()):
        return out
    m, tag = _MATRIX_VALUE.match(text), _TAG_VALUE.match(text)
    if m:
        out.add((int(m.group(1)), bool(m.group(2) or m.group(3))))
    elif tag:
        out.add((int(tag.group(1)), bool(tag.group(2))))
    elif "${{" in text:
        unknown.append(f"{where} gives a computed Python version ({text})")
    elif _OPEN.match(text):
        unknown.append(f"{where} gives Python {text}, not one CPython 3 version")
    return out
