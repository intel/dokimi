# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill coverage: which checks of an eval set exercise each requirement of a SKILL.md (issue #220, A5).

Two model calls, both through :class:`dokimi.novelty.client.LLMClient` so usage is recorded and
a test can swap the transport for a stub:

1. :func:`extract_requirements` reads the SKILL.md and lists its checkable requirements in five
   sections: ``triggers``, ``out_of_scope``, ``must_hold``, ``success_criteria``, ``failure_modes``.
2. :func:`map_requirements` reads those requirements and the eval set's checks, and names the
   checks that would fail if an answer or run violated each requirement.

A check is one ``evals.json`` row (its id) or one top-level ``test_*`` function under a task's
``tests/`` (named by the function; a function whose body differs between tasks gets a number).
A requirement that no check names is uncovered: it is a *candidate check* to write.

The map is LLM-estimated and unverified beyond a labelled calibration set (88 items: item precision
about .92, recall 1.0, link precision about .70). The report says so, and no score or gate uses it.
:func:`coverage_report` is a pure function of its inputs: the model is called only in the two
functions above.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dokimi.eval_coverage import _evals_files, _is_task
from dokimi.novelty.client import LLMClient

SCHEMA_VERSION = "skill_coverage.v1"
METHOD = "llm_static_map"
LABEL = "candidate checks: LLM-estimated, not a verified coverage score; links ~.70 precision"
SECTIONS = ("triggers", "out_of_scope", "must_hold", "success_criteria", "failure_modes")
PREFIX = {"triggers": "trig", "out_of_scope": "oos", "must_hold": "mh",
          "success_criteria": "sc", "failure_modes": "fm"}
MAX_REQUIREMENTS = 80
MAX_SKILL_CHARS = 60_000
CHECK_CHARS = 1_500
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
    """The requirement -> check names the model named, with the names it invented and the requirements it
    cut because they were past :data:`MAX_REQUIREMENTS`."""

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
    name = target.parent.name if target.name == "SKILL.md" else target.stem
    return SkillDoc(path=str(target), name=name, text=text,
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def _clip(text: str) -> str:
    return text if len(text) <= CHECK_CHARS else text[:CHECK_CHARS] + " [clipped]"


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
            out.append((eid, eid, _clip(json.dumps(body, ensure_ascii=False, indent=1))))
    return out


def _task_checks(root: Path) -> list[tuple[str, str, str]]:
    """(task, function name, source) for each top-level ``test_*`` function under a task's ``tests/``."""
    out: list[tuple[str, str, str]] = []
    for task_dir in sorted(p for p in root.iterdir() if _is_task(p)):
        for path in sorted((task_dir / "tests").glob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text, filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
                raise ValueError(f"{path}: unreadable test file ({exc})") from exc
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                    source = ast.get_source_segment(text, node) or node.name
                    out.append((task_dir.name, node.name, _clip(source)))
    return out


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
    return isinstance(row, dict) and row.get("section") in SECTIONS and bool(_flat(row.get("text", "")))


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


def extract_requirements(skill: SkillDoc, client: LLMClient) -> tuple[list[Requirement], int]:
    """The skill's requirements (ids like ``mh-03``) and how many the model listed past the cap.

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
    for row in reply.get("requirements") or []:
        if not isinstance(row, dict) or row.get("section") not in SECTIONS or not _flat(row.get("text", "")):
            continue
        if len(out) >= MAX_REQUIREMENTS:
            dropped += 1
            continue
        section = row["section"]
        counters[section] += 1
        out.append(Requirement(id=f"{PREFIX[section]}-{counters[section]:02d}", section=section,
                               text=_flat(row["text"])))
    return out, dropped


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


def _has_items(reply: dict[str, Any]) -> bool:
    return isinstance(reply.get("items"), dict)


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
    try:
        reply = client.json_object(prompt, system=_MAP_SYSTEM, usable=_has_items)
    except ValueError as exc:
        raise SkillCoverageError(f"no usable mapping from the model: {exc}") from exc
    if not _has_items(reply):  # ``usable`` guards the cache only; an injected transport skips it
        raise SkillCoverageError("no usable mapping from the model")
    known = {c.name for c in checks}
    by_requirement: dict[str, tuple[str, ...]] = {}
    dropped = 0
    items = reply["items"]
    for requirement in requirements:
        named = items.get(requirement.id) or []
        if not isinstance(named, list):
            named = []
        hits = {str(n) for n in named if str(n) in known}
        dropped += sum(1 for n in named if str(n) not in known)
        by_requirement[requirement.id] = tuple(sorted(hits))
    return Links(by_requirement=by_requirement, dropped_names=dropped)


def coverage_report(skill: SkillDoc, requirements: list[Requirement], checks: list[Check], links: Links, *,
                    evals_root: str, model: str, provider: str, cost: dict[str, Any],
                    requirements_dropped: int) -> dict[str, Any]:
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
    if s["check_names_dropped"]:
        notes.append(f"{s['check_names_dropped']} check name(s) the eval set does not have")
    if notes:
        lines += ["", "Dropped: " + "; ".join(notes) + "."]
    return "\n".join(lines) + "\n"
