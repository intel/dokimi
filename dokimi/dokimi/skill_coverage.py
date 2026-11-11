# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill coverage: which checks of an eval set exercise each requirement of a SKILL.md (issue #220, A5).

Two model calls, both through :class:`dokimi.novelty.client.LLMClient` so usage is recorded and
a test can swap the transport for a stub:

1. :func:`extract_requirements` reads the SKILL.md and lists its checkable requirements in five
   sections: ``triggers``, ``out_of_scope``, ``must_hold``, ``success_criteria``, ``failure_modes``.
2. :func:`map_requirements` reads those requirements and the eval set's checks, and names the
   checks that would fail if an answer or run violated each requirement.

A check is one ``evals.json`` row (its id) or one test of a task's ``tests/``: a ``test_*`` function (named by
the function) or a ``test_*`` method of a ``Test*`` class (named ``Class::method``). A task check's text is the
module-level code of the whole ``tests/`` folder (conftest, helpers, every test file), then the test; a test whose
text differs between tasks gets a number.
A requirement that no check names is uncovered: it is a *candidate check* to write.

The map is LLM-estimated and unverified beyond a prototype's labelled calibration set (88 items, not shipped:
item precision about .92, recall 1.0, link precision about .70, not re-measured on the current check text).
The report labels the map as LLM-estimated, and no score or gate uses it.
:func:`coverage_report` is a pure function of its inputs: the model is called only in the two
functions above.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dokimi.eval_coverage import _evals_files, _is_task
from dokimi.novelty.client import LLMClient

SCHEMA_VERSION = "skill_coverage.v1"
METHOD = "llm_static_map"
LABEL = "candidate checks: LLM-estimated, not a verified coverage score"
SECTIONS = ("triggers", "out_of_scope", "must_hold", "success_criteria", "failure_modes")
PREFIX = {"triggers": "trig", "out_of_scope": "oos", "must_hold": "mh",
          "success_criteria": "sc", "failure_modes": "fm"}
MAX_REQUIREMENTS = 80
MAX_SKILL_CHARS = 60_000
MAX_CATALOGUE_CHARS = 120_000
_SKIPPED_ROW_FIELDS = ("id", "needs")


class SkillCoverageError(RuntimeError):
    """The model's reply was not usable after the client's own retries: a run failure, not bad input."""


@dataclass(frozen=True)
class SkillDoc:
    """The SKILL.md read for one run: where it is, what it is called, and its content digest."""

    path: str
    name: str
    text: str
    sha256: str


@dataclass(frozen=True)
class Requirement:
    id: str
    section: str
    text: str


@dataclass(frozen=True)
class Check:
    """One check of the eval set: its name, the units (eval ids or task names) it belongs to, and its text."""

    name: str
    units: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Links:
    """The requirement -> check names the model named, and how many names it gave that match no check (those
    are dropped and counted in ``dropped_names``)."""

    by_requirement: dict[str, tuple[str, ...]]
    dropped_names: int


def read_skill(path: Path) -> SkillDoc:
    """Read a SKILL.md, or the SKILL.md inside a skill directory. Raises ``ValueError`` on a bad path or size."""
    target = path / "SKILL.md" if path.is_dir() else path
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{target}: cannot read the skill ({exc})") from exc
    if len(text) > MAX_SKILL_CHARS:
        raise ValueError(f"{target}: {len(text)} characters, over the {MAX_SKILL_CHARS} one mapping call takes")
    name = target.resolve().parent.name if target.name == "SKILL.md" else target.stem
    return SkillDoc(path=str(target), name=name, text=text,
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def _evals_checks(root: Path) -> list[tuple[str, str, str]]:
    """(unit, name, source) for each evals.json row: the row's id, its body without ``needs``."""
    out: list[tuple[str, str, str]] = []
    for path in _evals_files(root):
        try:
            rows = json.loads(path.read_text(encoding="utf-8")).get("evals", [])
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError(f"{path}: not a skill-creator evals.json ({exc})") from exc
        if not isinstance(rows, list):
            raise ValueError(f"{path}: \"evals\" is not a list")
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            eid = str(row.get("id", index))
            body = {k: v for k, v in row.items() if k not in _SKIPPED_ROW_FIELDS}
            out.append((eid, eid, json.dumps(body, ensure_ascii=False, indent=1)))
    return out


def check_files(root: Path) -> list[Path]:
    """The task test files :func:`load_checks` reads under a tasks root, resolved (an ``--out`` must not name one)."""
    if not root.is_dir():
        return []
    return [p.resolve() for task in root.iterdir() if _is_task(task) for p in (task / "tests").rglob("*.py")]


def _lines(text: str) -> list[str]:
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z", text)  # the line breaks ast counts, not splitlines's


def _source(text: str, node: ast.stmt) -> str:
    """The function's source from its first decorator to its last line (``ast.get_source_segment`` starts at ``def``,
    so a parametrize decorator would be lost and two differently parametrized checks would read as one)."""
    start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    return "".join(_lines(text)[start - 1:node.end_lineno]).rstrip("\n")


def _header(text: str, node: ast.ClassDef) -> str:
    """A class's decorators and its header (the ``class`` line, bases and keywords included, however many lines
    they take): what its methods' checks inherit."""
    start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
    first = node.body[0]  # the header ends before the first statement, and before that statement's decorators
    end = max(node.lineno, min([first.lineno, *(d.lineno for d in getattr(first, "decorator_list", []))]) - 1)
    return "".join(_lines(text)[start - 1:end]).rstrip("\n")


def _is_test(node: ast.stmt) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")


def _is_test_class(node: ast.stmt) -> bool:
    """A class pytest collects (``Test*``): its test methods are checks, and the rest of its body is their context."""
    return isinstance(node, ast.ClassDef) and node.name.startswith("Test")


def _is_test_file(path: Path) -> bool:
    """A file pytest collects by its default pattern (``test_*.py`` or ``*_test.py``), in any folder under ``tests/``."""
    return path.name.startswith("test_") or path.name.endswith("_test.py")


def _module_code(tests: Path) -> list[str]:
    """The module-level code of every Python file under a task's ``tests/``, in path order, minus the tests (a
    ``test_*`` function, a ``Test*`` class). It is the context of every check of the folder, read or not: a constant,
    helper, fixture or list that one statement fills can decide an assertion in another file."""
    out: list[str] = []
    for path in sorted(tests.rglob("*.py")):
        text, tree = _parse(path)
        out += [_source(text, node) for node in tree.body if not _is_test(node) and not _is_test_class(node)]
    return out


def _task_checks(root: Path) -> list[tuple[str, str, str]]:
    """(task, check name, source) for each test: a ``test_*`` function of a test file under a task's ``tests/``, and
    each ``test_*`` method of a ``Test*`` class in one (named ``Class::method``). The source is the module code of the
    folder (see :func:`_module_code`), then the class body's other statements for a method, then the test itself."""
    out: list[tuple[str, str, str]] = []
    for task_dir in sorted(p for p in root.iterdir() if _is_task(p)):
        tests = task_dir / "tests"
        module = _module_code(tests)
        for path in sorted(tests.rglob("*.py")):
            if not _is_test_file(path):
                continue
            text, tree = _parse(path)
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_test(node):
                    out.append((task_dir.name, node.name, "\n\n".join([*module, _source(text, node) or node.name])))
                elif isinstance(node, ast.ClassDef) and _is_test_class(node):
                    context = [_header(text, node), *(_source(text, s) for s in node.body if not _is_test(s))]
                    for method in node.body:
                        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_test(method):
                            out.append((task_dir.name, f"{node.name}::{method.name}",
                                        "\n\n".join([*module, *context, _source(text, method) or method.name])))
    return out


def _parse(path: Path) -> tuple[str, ast.Module]:
    try:
        text = path.read_text(encoding="utf-8-sig")  # pytest reads a BOM; ast.parse refuses it
        return text, ast.parse(text, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
        raise ValueError(f"{path}: unreadable test file ({exc})") from exc


def _named(found: list[tuple[str, str, str]]) -> list[Check]:
    """Group task functions by name: one check per distinct body, named by the function when it has one body."""
    by_name: dict[str, dict[str, list[str]]] = {}
    for unit, fn, source in found:
        by_name.setdefault(fn, {}).setdefault(source, []).append(unit)
    out: list[Check] = []
    for fn, variants in by_name.items():
        for number, (source, units) in enumerate(variants.items(), start=1):
            name = fn if len(variants) == 1 else f"{fn}#{number}"
            out.append(Check(name=name, units=tuple(sorted(set(units))), source=source))
    return out


def load_checks(root: Path) -> list[Check]:
    """The checks of an eval set: ``root`` is an evals.json, a skill directory or a tasks root (as in
    ``eval coverage``). Raises ``ValueError`` on an unreadable input, a malformed file or duplicate names."""
    checks = [Check(name=name, units=(unit,), source=source) for unit, name, source in _evals_checks(root)]
    if root.is_dir():
        checks += _named(_task_checks(root))
    seen: set[str] = set()
    for check in checks:
        if check.name in seen:
            raise ValueError(f"{root}: two checks are named {check.name!r}")
        seen.add(check.name)
    return checks


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _usable_row(row: Any) -> bool:
    return (isinstance(row, dict) and row.get("section") in SECTIONS and isinstance(row.get("text"), str)
            and bool(_flat(row["text"])))


def _has_requirements(reply: dict[str, Any]) -> bool:
    rows = reply.get("requirements")
    return isinstance(rows, list) and any(_usable_row(r) for r in rows)


_EXTRACT_SYSTEM = ("You list the checkable requirements of an AI agent skill. You work only from the skill's "
                   "text; you do not assume what the skill's tools or documents contain.")

_EXTRACT_PROMPT = """\
Each requirement is one thing a run of the agent must do, must not do, or must activate or decline on, that
a test could check. Sections:
- triggers: when the skill should activate (its description's trigger conditions)
- out_of_scope: requests the skill must decline or leave to others
- must_hold: instructions and constraints the agent must follow in its answer or actions
- success_criteria: what a correct outcome looks like
- failure_modes: mistakes the skill warns against

One requirement per item, self-contained, in your own words, at most 25 words. Skip background, examples and
text that states no requirement.

Reply with JSON only: {{"requirements": [{{"section": "<section>", "text": "<requirement>"}}]}}

SKILL.md:
<<<
{skill}
>>>"""


def extract_requirements(skill: SkillDoc, client: LLMClient) -> tuple[list[Requirement], int, int]:
    """The skill's requirements (ids like ``mh-03``), how many the model listed past the cap, and how many rows
    it listed that are not usable (not an object, an unknown section, or no text).

    Raises :class:`SkillCoverageError` when no usable reply comes back.
    """
    try:
        reply = client.json_object(_EXTRACT_PROMPT.format(skill=skill.text), system=_EXTRACT_SYSTEM,
                                   usable=_has_requirements)
    except ValueError as exc:
        raise SkillCoverageError(f"no usable requirement list from the model: {exc}") from exc
    if not _has_requirements(reply):  # ``usable`` guards the cache only; an injected transport skips it
        raise SkillCoverageError("no usable requirement list from the model")
    counters = {section: 0 for section in SECTIONS}
    out: list[Requirement] = []
    dropped = 0
    unusable = 0
    for row in reply.get("requirements") or []:
        if not _usable_row(row):
            unusable += 1
            continue
        if len(out) >= MAX_REQUIREMENTS:
            dropped += 1
            continue
        section = row["section"]
        counters[section] += 1
        out.append(Requirement(id=f"{PREFIX[section]}-{counters[section]:02d}", section=section,
                               text=_flat(row["text"])))
    return out, dropped, unusable


_MAP_SYSTEM = "You map requirements of an AI agent skill to the eval checks that would detect a violation of them."

_MAP_PROMPT = """\
Requirements (each has an id):
{requirements}

Eval checks (each has a name and its definition):
{checks}

For each requirement id, list the names of the checks that would FAIL if an answer or run violated that
requirement. A check covers a requirement only if a violation would make that check fail; topical overlap is
not coverage. A trigger or out_of_scope requirement counts only for a check that asserts the activation or the
declining. Be strict: most requirements have 0 or 1 checks. Use [] when none would detect it.

Reply with JSON only: {{"items": {{"<requirement id>": ["<check name>", ...]}}}}"""


def _usable_items(reply: dict[str, Any], ids: set[str]) -> bool:
    """A mapping the run can use: ``items`` is an object and every requirement id holds a list of check names
    (an explicit empty list is a valid answer). A name is a string or an integer, the id of an evals.json row
    (``bool`` is not one). An omitted id is not usable: it would read as uncovered, when the model never mapped
    it. The cache keeps only what this accepts, and the run refuses what it rejects, so an identical rerun asks
    the model again."""
    items = reply.get("items")
    if not isinstance(items, dict) or not ids:
        return False
    return all(isinstance(items.get(rid), list) and all(_check_name(name) for name in items[rid])
               for rid in ids)


def _check_name(name: Any) -> bool:
    return isinstance(name, str) or (isinstance(name, int) and not isinstance(name, bool))


def catalogue_text(checks: list[Check]) -> str:
    return "\n\n".join(f"### check: {c.name}\n{c.source}" for c in checks)


def check_budget(checks: list[Check]) -> None:
    """Raise ``ValueError`` when the checks would not fit one mapping call. Run before any model call."""
    size = len(catalogue_text(checks))
    if size > MAX_CATALOGUE_CHARS:
        raise ValueError(f"the eval set's checks are {size} characters, over the {MAX_CATALOGUE_CHARS} "
                         "one mapping call takes; map a smaller set")


def map_requirements(requirements: list[Requirement], checks: list[Check], client: LLMClient) -> Links:
    """Ask the model which checks cover each requirement. Names that match no check are dropped and counted.

    Raises ``ValueError`` when the checks are past the budget (:func:`check_budget`), and
    :class:`SkillCoverageError` when no usable reply comes back.
    """
    check_budget(checks)
    catalogue = catalogue_text(checks)
    listing = "\n".join(f"[{r.id}] ({r.section}) {r.text}" for r in requirements)
    prompt = _MAP_PROMPT.format(requirements=listing, checks=catalogue)
    ids = {r.id for r in requirements}

    def usable(reply: dict[str, Any]) -> bool:
        return _usable_items(reply, ids)

    try:
        reply = client.json_object(prompt, system=_MAP_SYSTEM, usable=usable)
    except ValueError as exc:
        raise SkillCoverageError(f"no usable mapping from the model: {exc}") from exc
    if not usable(reply):  # ``usable`` guards the cache only; an injected transport skips it
        raise SkillCoverageError("no usable mapping from the model: every requirement id must hold a list of check "
                                 "names (an empty list when none fits)")
    known = {c.name for c in checks}
    by_requirement: dict[str, tuple[str, ...]] = {}
    dropped = 0
    items = reply["items"]
    for requirement in requirements:
        named = items.get(requirement.id, [])
        hits = {str(n) for n in named if str(n) in known}
        dropped += sum(1 for n in named if str(n) not in known)
        by_requirement[requirement.id] = tuple(sorted(hits))
    for rid, named in items.items():  # names filed under an id that is no requirement are dropped too
        if rid not in ids and isinstance(named, list):
            dropped += len(named)
    return Links(by_requirement=by_requirement, dropped_names=dropped)


def coverage_report(skill: SkillDoc, requirements: list[Requirement], checks: list[Check], links: Links, *,
                    evals_root: str, model: str, provider: str, cost: dict[str, Any],
                    requirements_dropped: int, requirements_unusable: int = 0) -> dict[str, Any]:
    """The ``skill_coverage.v1`` payload (unstamped) for one skill, its requirements and the model's links."""
    by_id = {c.name: c for c in checks}
    rows = []
    for req in requirements:
        names = links.by_requirement.get(req.id, ())
        units = sorted({u for n in names for u in by_id[n].units})
        rows.append({"id": req.id, "section": req.section, "text": req.text,
                     "covered": bool(names), "checks": list(names), "units": units})
    sections: dict[str, dict[str, int]] = {}
    for section in SECTIONS:
        in_section = [r for r in rows if r["section"] == section]
        if in_section:
            sections[section] = {"covered": sum(1 for r in in_section if r["covered"]), "total": len(in_section)}
    candidates = [{"id": r["id"], "section": r["section"], "text": r["text"]} for r in rows if not r["covered"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": {"path": skill.path, "name": skill.name, "sha256": skill.sha256},
        "evals_root": evals_root,
        "method": METHOD,
        "label": LABEL,
        "model": {"model": model, "provider": provider},
        "requirements": rows,
        "candidate_checks": candidates,
        "summary": {
            "requirements": len(rows),
            "covered": sum(1 for r in rows if r["covered"]),
            "candidate_checks": len(candidates),
            "by_section": sections,
            "checks": len(checks),
            "requirements_dropped": requirements_dropped,
            "requirements_unusable": requirements_unusable,
            "check_names_dropped": links.dropped_names,
        },
        "cost": cost,
    }


def _cell(text: str) -> str:
    """A Markdown table cell: pipes escaped, line breaks flattened so the row stays one line."""
    return " ".join(str(text).replace("|", "\\|").splitlines())


def render_markdown(report: dict[str, Any]) -> str:
    """The report as Markdown: the labelled summary, every requirement, then the candidate checks."""
    s = report["summary"]
    lines = [
        f"## Skill coverage: {report['skill']['name']} (candidate checks)",
        "",
        f"{s['covered']} of {s['requirements']} requirements named by a check; {s['candidate_checks']} "
        f"candidate checks (uncovered). {report['label']}.",
        "",
        "| Id | Section | Requirement | Checks |",
        "|---|---|---|---|",
    ]
    lines += [f"| {r['id']} | {r['section']} | {_cell(r['text'])} | {_cell(', '.join(r['checks']) or '-')} |"
              for r in report["requirements"]]
    lines += ["", "Candidate checks (no check names the requirement):"]
    lines += [f"- {c['id']} [{c['section']}] {c['text']}" for c in report["candidate_checks"]] or ["- none"]
    notes = []
    if s["requirements_dropped"]:
        notes.append(f"{s['requirements_dropped']} requirement(s) past the cap of {MAX_REQUIREMENTS}")
    if s["requirements_unusable"]:
        notes.append(f"{s['requirements_unusable']} requirement row(s) not usable (not an object, an unknown section, or no text)")
    if s["check_names_dropped"]:
        notes.append(f"{s['check_names_dropped']} check name(s) the eval set does not have")
    if notes:
        lines += ["", "Dropped: " + "; ".join(notes) + "."]
    return "\n".join(lines) + "\n"
