# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Trace journey acceptance criteria to the CLI and to the tests that assert them.

The catalog is ``docs/user-journeys/journeys.md``: it defines the journeys
(``## J4 ...``), their use cases (``#### UC-J04-001 ...``) and the acceptance
criteria under each use case (``- **AC-J04-001** ...``). The text lives there
and only there. ``tests/outcomes/journeys.yaml`` holds the trace links for the
criteria that have any: delivery state, the CLI surfaces the criterion names,
and the evidence ids of the tests that assert it, each with its kind and its
stubs. The plugin calls :func:`check` when ``pytest --outcomes`` finishes.

What fails the session: a broken catalog or registry (unknown or duplicate id,
a reference to an id that is not defined, a surface ``build_parser()`` does not
have, a path that does not resolve), and an ``enforce: true`` criterion whose
evidence is missing, failed, all skipped, or did not invoke the CLI surface it
declares. A criterion that is documented and not enforced is reported as
unverified, even when its tests pass, and gates nothing at run time; a broken
link in its entry is still an error.
"""

from __future__ import annotations

import argparse
import datetime
import os
import platform
import re
import shlex
import subprocess
from pathlib import Path
from typing import Callable

import yaml

CATALOG = "docs/user-journeys/journeys.md"
JOURNEYS = [f"J{n}" for n in range(1, 16)]
DELIVERY = ("proposed", "partial", "available", "retired")
KINDS = ("static_reference", "unit", "integration", "cli_scenario", "live_agent", "recorded_manual")
# A pytest session can only produce these kinds; the others are recorded elsewhere.
SESSION_KINDS = ("static_reference", "unit", "integration", "cli_scenario")
RANK = ["pass", "not_run", "inconclusive", "fail", "error"]
GATE = {"pass": "pass", "not_run": "pass", "inconclusive": "inconclusive", "fail": "fail", "error": "error"}

JOURNEY_HEAD = re.compile(r"^##\s+(J\d+)\b\s*[—:-]?\s*(.*)$")
USE_CASE_HEAD = re.compile(r"^#{3,4}\s+(UC-J(\d\d)-\d{3})\b\s*[—:-]?\s*(.*)$")
CRITERION = re.compile(r"^\s*[-*]\s+\*\*(AC-J(\d\d)-\d{3})\*\*\s*[—:-]?\s*(.*)$")
ANY_ID = re.compile(r"\b(?:UC|AC)-J\d\d-\d{3}\b")


def _worst(verdicts, rank=RANK):
    return max(verdicts, key=rank.index, default="pass")


# ------------------------------------------------------------------ catalog


def parse_catalog(path: Path) -> tuple[dict, list[str]]:
    """Journeys, use cases and criteria defined in the catalog, and its problems."""
    journeys: dict[str, dict] = {}
    use_cases: dict[str, dict] = {}
    criteria: dict[str, dict] = {}
    problems: list[str] = []
    journey = use_case = None
    fenced = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        if m := JOURNEY_HEAD.match(line):
            journey, use_case = m.group(1), None
            if journey in journeys:
                problems.append(f"{path.name}:{n}: {journey} defined twice")
            journeys[journey] = {"title": m.group(2).strip(), "use_cases": []}
        elif m := USE_CASE_HEAD.match(line):
            uc, number = m.group(1), int(m.group(2))
            if journey is None or journey != f"J{number}":
                problems.append(f"{path.name}:{n}: {uc} is not under the ## J{number} heading")
            if uc in use_cases:
                problems.append(f"{path.name}:{n}: {uc} defined twice")
            use_case = uc
            use_cases[uc] = {"journey": f"J{number}", "title": m.group(3).strip()}
            if journey in journeys:
                journeys[journey]["use_cases"].append(uc)
        elif m := CRITERION.match(line):
            ac, number = m.group(1), int(m.group(2))
            if use_case is None or use_cases[use_case]["journey"] != f"J{number}":
                problems.append(f"{path.name}:{n}: {ac} is not under a use case of J{number}")
            if ac in criteria:
                problems.append(f"{path.name}:{n}: {ac} defined twice")
            criteria[ac] = {"journey": f"J{number}", "use_case": use_case, "text": m.group(3).strip()}
    missing = [j for j in JOURNEYS if j not in journeys]
    extra = [j for j in journeys if j not in JOURNEYS]
    if missing:
        problems.append(f"{path.name}: journeys not defined: {', '.join(missing)}")
    if extra:
        problems.append(f"{path.name}: journeys outside J1-J15: {', '.join(extra)}")
    return {"journeys": journeys, "use_cases": use_cases, "criteria": criteria}, problems


def dangling_ids(docs: list[Path], catalog: dict) -> list[str]:
    """UC/AC ids a page mentions outside fenced code that the catalog does not define."""
    known = set(catalog["use_cases"]) | set(catalog["criteria"])
    found = []
    for doc in docs:
        fenced = False
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            found.append(f"{doc.name}: cannot read: {exc}")
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            if fenced:
                continue
            found += [f"{doc.name}:{n}: {i} is not defined in the catalog"
                      for i in ANY_ID.findall(line) if i not in known]
    return found


# ------------------------------------------------------------------ CLI surfaces


def _subparsers(parser: argparse.ArgumentParser) -> dict:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _option(parser: argparse.ArgumentParser, token: str) -> str:
    """The option ``token`` names in ``parser``, as one identity per action.

    ``-o`` and ``--out`` of one action are the same option, and so is an
    abbreviation argparse accepts (``--ta`` for ``--task``). The identity is the
    action's first option string, not its ``dest``: ``--x`` and ``--no-x`` that
    share a dest stay two options. A token no action owns is returned as is, so
    it is reported as unknown.
    """
    name = token.split("=", 1)[0]
    actions = parser._option_string_actions
    action = actions.get(name)
    if action is None and len(name) > 2 and name[0] == "-" and name[1] != "-":
        action = actions.get(name[:2])  # a short option with its value attached: -j2
    if action is None and parser.allow_abbrev and name.startswith("--"):
        owners = {id(a): a for opt, a in actions.items() if opt.startswith(name)}
        if len(owners) == 1:
            action = next(iter(owners.values()))
    return action.option_strings[0] if action is not None else name


def _flags(parser: argparse.ArgumentParser) -> set[str]:
    return {action.option_strings[0] for action in parser._actions if action.option_strings}


def resolve(root: argparse.ArgumentParser, tokens: list[str]) -> tuple[argparse.ArgumentParser, list[str], list[str]]:
    """The parser ``tokens`` lead to, the command path taken, and the options passed.

    Each option is resolved by the parser it was passed to, and nothing after
    ``--`` is an option: there argparse reads ``--fail-on-fail`` as a positional.
    """
    parser, path, flags = root, [], []
    walking = True
    for token in tokens:
        if token == "--":
            break
        subs = _subparsers(parser)
        if walking and subs and token in subs:
            parser = subs[token]
            path.append(token)
            continue
        walking = walking and token.startswith("-")
        if token.startswith("-") and token != "-":
            flags.append(_option(parser, token))
    return parser, path, flags


def surface_problem(root: argparse.ArgumentParser, surface: str) -> str | None:
    """Why ``surface`` (``"repo tasks check --warm"``) is not in the parser, or None."""
    tokens = shlex.split(surface)
    if "--" in tokens:
        return f"a surface names options, not '--': {surface}"
    parser, path, flags = resolve(root, tokens)
    rest = [t for t in tokens[len(path):] if not t.startswith("-")]
    if not path or rest:
        return f"unknown command: {surface}"
    if _subparsers(parser):
        return f"not a leaf command: {surface}"
    unknown = [f for f in flags if f not in _flags(parser)]
    return f"unknown flag: {' '.join(path)} {' '.join(unknown)}" if unknown else None


def _covers(root: argparse.ArgumentParser, invoke: str, surface: str) -> bool:
    """Whether the declared *invoke* reaches *surface*'s command with all of its flags."""
    target, _, want = resolve(root, shlex.split(surface))
    parser, _, flags = resolve(root, shlex.split(invoke))
    return parser is target and set(want) <= set(flags)


def invoked(root: argparse.ArgumentParser, surface: str, calls: list[list[str]]) -> bool:
    """Whether one captured argv reached ``surface``'s command with all of its flags."""
    target, _, want = resolve(root, shlex.split(surface))
    for argv in calls:
        parser, _, flags = resolve(root, argv)
        if parser is target and set(want) <= set(flags):
            return True
    return False


# ------------------------------------------------------------------ registry


def _ref_problem(repo_root: Path, ref: str, anchors: Callable[[Path], set[str]]) -> str | None:
    name, _, anchor = ref.partition("#")
    target = repo_root / name
    try:
        exists = target.exists()
    except OSError:  # a name the file system refuses (too long, NUL) is a missing path
        exists = False
    if not exists:
        return f"missing path: {ref}"
    if anchor and (target.suffix != ".md" or anchor not in anchors(target)):
        return f"missing anchor: {ref}"
    return None


def _refers_to_itself(value) -> bool:
    """Whether a loaded YAML value contains itself through an anchor (a cycle)."""
    state: dict[int, int] = {}  # 1: on the walk now, 2: finished without a cycle

    def visit(node) -> bool:
        if not isinstance(node, (dict, list)):
            return False
        if state.get(id(node)) == 1:
            return True
        if state.get(id(node)) == 2:
            return False
        state[id(node)] = 1
        found = any(visit(child) for child in (node.values() if isinstance(node, dict) else node))
        state[id(node)] = 2
        return found

    return visit(value)


def _repeated_keys(text: str, name: str) -> list[str]:
    """Keys given twice in one mapping: safe_load keeps the last copy without a word."""
    problems: list[str] = []
    todo = [yaml.compose(text, Loader=yaml.SafeLoader)]
    while todo:
        node = todo.pop()
        if isinstance(node, yaml.MappingNode):
            keys = set()
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode):
                    if key.value in keys:
                        problems.append(f"{name}:{key.start_mark.line + 1}: {key.value} given twice")
                    keys.add(key.value)
                todo.append(value)
        elif isinstance(node, yaml.SequenceNode):
            todo += node.value
    return problems


def _normalize(registry: dict) -> list[str]:
    """Drop the nested shapes the checks cannot read, in place, and say what was dropped."""
    problems = []
    if not isinstance(registry.get("catalog", CATALOG), str):
        problems.append("journeys.yaml: catalog must be a path")
        registry["catalog"] = CATALOG
    if not isinstance(registry.get("criteria") or {}, dict):
        problems.append("journeys.yaml: criteria must be a mapping")
        registry["criteria"] = {}
    criteria = registry.get("criteria") or {}
    for ac, entry in list(criteria.items()):
        if not isinstance(entry, dict):
            problems.append(f"journeys.yaml {ac}: must be a mapping")
            del criteria[ac]
            continue
        evidence = entry.get("evidence") or []
        if not isinstance(evidence, list):
            problems.append(f"journeys.yaml {ac}: evidence must be a list")
            evidence = []
        if any(not isinstance(e, dict) for e in evidence):
            problems.append(f"journeys.yaml {ac}: every evidence entry must be a mapping")
        entry["evidence"] = [e for e in evidence if isinstance(e, dict)]
        problems += _strings(entry, ("surfaces", "refs", "gaps"), ("decision_ref", "outcome"), f"journeys.yaml {ac}")
        problems += _unsplittable(entry, "surfaces", f"journeys.yaml {ac}")
        for e in entry["evidence"]:
            problems += _strings(e, ("invokes", "stubs"), ("id", "kind"), f"journeys.yaml {ac}: evidence")
            problems += _unsplittable(e, "invokes", f"journeys.yaml {ac}: evidence")
    return problems


def _unsplittable(mapping: dict, key: str, where: str) -> list[str]:
    """Drop the commands under ``key`` that shlex cannot split (an unbalanced quote); say which."""
    problems, kept = [], []
    for command in mapping.get(key) or []:
        try:
            shlex.split(command)
        except ValueError as exc:
            problems.append(f"{where}: {key} {command!r}: {exc}")
        else:
            kept.append(command)
    if problems:
        mapping[key] = kept
    return problems


def _strings(mapping: dict, lists: tuple[str, ...], scalars: tuple[str, ...], where: str) -> list[str]:
    """Replace a key of the wrong shape: a list of strings, or one string; say which."""
    problems = []
    for key in lists:
        value = mapping.get(key)
        if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            problems.append(f"{where}: {key} must be a list of strings")
            mapping[key] = []
    for key in scalars:
        value = mapping.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{where}: {key} must be a string")
            mapping[key] = None
    return problems


def static_problems(registry: dict, catalog: dict, repo_root: Path, contracts: dict[str, dict],
                    anchors: Callable[[Path], set[str]], root: argparse.ArgumentParser) -> list[str]:
    """Everything wrong with the registry that no test run can change."""
    problems = []
    if registry.get("schema") != 1:
        problems.append("journeys.yaml: schema must be 1")
    criteria = registry.get("criteria") or {}
    for ac, entry in criteria.items():
        where = f"journeys.yaml {ac}"
        if ac not in catalog["criteria"]:
            problems.append(f"{where}: not defined in {CATALOG}")
        if entry.get("delivery") not in DELIVERY:
            problems.append(f"{where}: delivery must be one of {', '.join(DELIVERY)}")
        evidence = entry.get("evidence") or []
        if entry.get("enforce"):
            if entry.get("delivery") not in ("partial", "available"):
                problems.append(f"{where}: an enforced criterion is partial or available")
            if not any(e.get("kind") in SESSION_KINDS for e in evidence):
                problems.append(f"{where}: an enforced criterion needs evidence a pytest session produces")
        if outcome := entry.get("outcome"):
            if outcome not in contracts:
                problems.append(f"{where}: no outcome contract {outcome}")
        for e in evidence:
            if not e.get("id"):
                problems.append(f"{where}: evidence entry must have a non-empty id")
            if e.get("kind") not in KINDS:
                problems.append(f"{where}: evidence {e.get('id')}: kind must be one of {', '.join(KINDS)}")
            if outcome in contracts and e.get("kind") in SESSION_KINDS \
                    and e.get("id") not in contracts[outcome].get("evidence_ids", []):
                problems.append(f"{where}: evidence {e.get('id')} is not an evidence id of {outcome}")
            if e.get("invokes") and e.get("kind") != "cli_scenario":
                problems.append(f"{where}: evidence {e.get('id')}: only a cli_scenario declares invokes")
            if e.get("kind") == "cli_scenario" and not e.get("invokes"):
                problems.append(f"{where}: evidence {e.get('id')}: a cli_scenario names the surface it invokes")
            if e.get("kind") in ("unit", "integration", "cli_scenario") and "stubs" not in e:
                problems.append(f"{where}: evidence {e.get('id')}: state its stubs ([] for none)")
            for surface in e.get("invokes") or []:
                if problem := surface_problem(root, surface):
                    problems.append(f"{where}: evidence {e.get('id')}: {problem}")
        scenario_invokes = [i for e in evidence if e.get("kind") == "cli_scenario"
                            for i in e.get("invokes") or []]
        for surface in entry.get("surfaces") or []:
            if problem := surface_problem(root, surface):
                problems.append(f"{where}: {problem}")
            elif entry.get("enforce") and scenario_invokes and not any(
                    _covers(root, i, surface) for i in scenario_invokes):
                # Otherwise a surface is "verified" by tests that never pass its flags.
                problems.append(f"{where}: no cli_scenario invokes the surface {surface!r}")
        for ref in entry.get("refs") or []:
            if problem := _ref_problem(repo_root, ref, anchors):
                problems.append(f"{where}: {problem}")
        decision = entry.get("decision_ref")
        if decision and not re.match(r"https://github\.com/\S+/(issues|pull)/\d+", decision):
            problems.append(f"{where}: decision_ref is an issue or PR URL")
    return problems


# ------------------------------------------------------------------ verdicts


def _evidence_row(e: dict, evidence: Callable[[str], dict], calls: list[list[str]],
                  root: argparse.ArgumentParser) -> dict:
    row = {"id": e["id"], "kind": e["kind"], "stubs": e.get("stubs", [])}
    if e["kind"] not in SESSION_KINDS:
        return {**row, "verdict": "not_run", "reason": f"{e['kind']} evidence is not produced by pytest"}
    result = evidence(e["id"])
    row.update({k: v for k, v in result.items() if k != "id"})
    if e.get("invokes"):
        seen = {s: invoked(root, s, calls) for s in e["invokes"]}
        row["invokes"] = seen
        if row["verdict"] == "pass" and not all(seen.values()):
            missing = [s for s, ok in seen.items() if not ok]
            row.update(verdict="error", reason=f"no claimed test invoked: {'; '.join(missing)}")
    return row


def check(registry_path: Path, repo_root: Path, contracts: dict[str, dict],
          evidence: Callable[[str], dict], invocations: dict[str, list[list[str]]],
          anchors: Callable[[Path], set[str]]) -> dict:
    """The coverage report: one row per documented criterion, and the session verdict."""
    from dokimi.cli import build_parser

    root = build_parser()
    try:
        text = registry_path.read_text(encoding="utf-8")
        registry = yaml.safe_load(text) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:
        registry, problems = {}, [f"{registry_path.name}: cannot read: {exc}"]
    else:
        problems = [] if isinstance(registry, dict) else [f"{registry_path.name}: not a mapping"]
        if _refers_to_itself(registry):
            # Checks and normalizing would mutate the cycle; the registry is not usable.
            problems.append(f"{registry_path.name}: refers to itself through a YAML anchor")
            registry = {}
        else:
            problems += _repeated_keys(text, registry_path.name)
        registry = registry if isinstance(registry, dict) else {}
    problems += _normalize(registry)
    catalog_path = repo_root / registry.get("catalog", CATALOG)
    try:
        catalog, catalog_problems = parse_catalog(catalog_path)
        # Inside the guard: from Python 3.13 a NUL in the directory part raises here too.
        pages = sorted(catalog_path.parent.glob("*.md"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # Reported like any other broken link, so the report is still written.
        catalog = {"journeys": {}, "use_cases": {}, "criteria": {}}
        catalog_problems = [f"{CATALOG}: cannot read: {exc}"]
        pages = []
    problems += catalog_problems
    problems += dangling_ids(pages, catalog)
    problems += static_problems(registry, catalog, repo_root, contracts, anchors, root)
    entries = registry.get("criteria") or {}

    rows = []
    for ac, defined in catalog["criteria"].items():
        entry = entries.get(ac)
        row = {"id": ac, **defined, "tracked": entry is not None}
        if entry is None:
            rows.append({**row, "delivery": None, "enforce": False, "verdict": "not_run",
                         "reason": "no trace entry in journeys.yaml", "evidence": []})
            continue
        ev = [_evidence_row(e, evidence, invocations.get(e.get("id"), []), root)
              for e in entry.get("evidence") or [] if e.get("kind") in KINDS and e.get("id")]
        # What a pytest session produced decides the row; evidence it cannot produce
        # (live_agent, recorded_manual) is listed in the row, never mixed into the verdict.
        verdicts = [e["verdict"] for e in ev if e["kind"] in SESSION_KINDS]
        if not entry.get("enforce"):
            # Planned work is reported, never gated: an unclaimed id of an unenforced
            # criterion means its test is not written yet.
            verdicts = ["not_run" if v == "error" else v for v in verdicts]
            for e in ev:
                if e["verdict"] == "error":
                    e["verdict"] = "not_run"
        verdict = _worst(verdicts) if verdicts else "not_run"
        if entry.get("enforce") and all(v == "not_run" for v in verdicts):
            verdict = "error"
        rows.append({**row, "delivery": entry.get("delivery"), "enforce": bool(entry.get("enforce")),
                     "capability": entry.get("capability"), "surfaces": entry.get("surfaces", []),
                     "outcome": entry.get("outcome"), "decision_ref": entry.get("decision_ref"),
                     "scope": entry.get("scope"), "gaps": entry.get("gaps", []),
                     "verdict": verdict, "evidence": ev})

    gated = [GATE[r["verdict"]] for r in rows if r["enforce"]]
    verdict = "error" if problems else _worst(gated, ["pass", "inconclusive", "fail", "error"])
    # Verified means enforced and passing: a passing test of an unenforced criterion
    # is reported in its row, but nothing would fail if it stopped proving the criterion.
    verified = {r["id"] for r in rows if r["enforce"] and r["verdict"] == "pass"}
    unverified = [r["id"] for r in rows if r["id"] not in verified]
    journeys = [{
        "id": j, "title": catalog["journeys"][j]["title"],
        "use_cases": len(catalog["journeys"][j]["use_cases"]),
        "criteria": sum(1 for r in rows if r["journey"] == j),
        "enforced": sum(1 for r in rows if r["journey"] == j and r["enforce"]),
        "passed": sum(1 for r in rows if r["journey"] == j and r["id"] in verified),
    } for j in JOURNEYS if j in catalog["journeys"]]
    return {"schema": "journey-coverage/1", "verdict": verdict, "provenance": provenance(repo_root),
            "problems": problems, "journeys": journeys, "criteria": rows, "unverified": unverified}


def provenance(repo_root: Path) -> dict:
    """What this report is evidence about: the commit, the run, and what it did not run."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
                                text=True, timeout=10, check=False).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {
        "commit": os.environ.get("GITHUB_SHA") or commit,
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "scope": "offline pytest session: no model, harness, Docker daemon or paid run; "
                 "each evidence row names its stubs",
    }


def markdown(report: dict) -> str:
    """The report for people: what is verified, under which stubs, and what is not."""
    p = report["provenance"]
    lines = [
        "# Journey coverage", "",
        f"Verdict: **{report['verdict']}**. Commit `{p['commit']}`, run `{p['run_id'] or 'local'}`, {p['date']}.", "",
        f"Scope: {p['scope']}. A pass is evidence for the criterion within that scope, not a "
        "completed real-user journey.", "",
    ]
    if report["problems"]:
        lines += ["## Problems", "", *[f"- {x}" for x in report["problems"]], ""]
    lines += ["## Journeys", "", "| Journey | Use cases | Criteria | Enforced | Passed |", "|---|---|---|---|---|"]
    lines += [f"| {j['id']} {j['title']} | {j['use_cases']} | {j['criteria']} | {j['enforced']} | {j['passed']} |"
              for j in report["journeys"]]
    lines += ["", "## Traced criteria", "", "| Criterion | Delivery | Enforced | Verdict | Evidence (kind, verdict, stubs) |",
              "|---|---|---|---|---|"]
    for r in report["criteria"]:
        if not r["tracked"]:
            continue
        ev = "<br>".join(f"`{e['id']}` {e['kind']} {e['verdict']}"
                         + (f" (stubs: {', '.join(e['stubs'])})" if e["stubs"] else "")
                         + (f": {e['reason']}" if e.get("reason") else "") for e in r["evidence"]) or "none"
        lines.append(f"| {r['id']} | {r['delivery']} | {'yes' if r['enforce'] else 'no'} | {r['verdict']} | {ev} |")
    gaps = [(r["id"], g) for r in report["criteria"] for g in r.get("gaps", [])]
    if gaps:
        lines += ["", "## Stated gaps", "", *[f"- {i}: {g}" for i, g in gaps]]
    untracked = [r["id"] for r in report["criteria"] if not r["tracked"]]
    lines += ["", "## Unverified", "",
              f"{len(report['unverified'])} of {len(report['criteria'])} documented criteria are not enforced "
              f"or have no passing evidence in this run; {len(untracked)} of them have no trace entry yet.", "",
              ", ".join(report["unverified"]) or "none", ""]
    return "\n".join(lines)
