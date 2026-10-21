# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Check user-outcome contracts against the tests that ran.

Each ``tests/outcomes/<name>.json`` names a promise to the user, the docs that
state it, the code that keeps it, and the evidence ids of the tests that assert
it. A test claims an id with ``@pytest.mark.outcome("<id>")``.

``pytest --outcomes`` checks every contract once the session ends and writes
``--outcomes-report=<path>`` (default ``outcomes-result.json``; pass it
with ``=``: as a separate argument an existing path is taken for a test path
and moves the rootdir away from this conftest). An id no test claimed
is an error, never a pass; an id whose tests were all skipped is inconclusive.
Exit codes: 0 pass, 1 a claimed test failed, 2 a contract or reference is
broken (a missing file, or a ``#anchor`` that is no heading of a markdown file),
3 inconclusive. Without ``--outcomes`` only the marker is registered.

The same session checks the journey trace in ``journeys.yaml`` (see
``journeys.py``) and writes ``--journeys-report`` (JSON, plus a ``.md``
beside it). For that, a marked test records every argv the ``dokimi`` root
parser parsed in its setup or call, from any thread, and a test that did not pass
contributes none: a criterion whose evidence declares a CLI invocation passes
only if a passing claimed test made one. Tests in one process run one at a
time, so a call is credited to the test that was running when it was made.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pytest

CONTRACT_DIR = Path(__file__).parent
REPO_ROOT = CONTRACT_DIR.parent.parent
EXIT = {"pass": 0, "fail": 1, "error": 2, "inconclusive": 3}
# The worst verdict wins when one contract or one session combines several.
RANK = ["pass", "inconclusive", "fail", "error"]
# The journey registry. A test of the contracts alone sets it to None; a missing
# file at this path is an error in the journey report, never a skipped check.
REGISTRY: Path | None = CONTRACT_DIR / "journeys.yaml"

_results: dict[str, list[str]] = {}
_invocations: dict[str, list[list[str]]] = {}
_CALLS = pytest.StashKey[list]()
# The argv list of the marked test now running, or None. A module global, not a
# ContextVar: a worker thread the test starts must still record into it.
_active_calls: list[list[str]] | None = None
_root_prog: str | None = None


def _root() -> str:
    """build_parser()'s prog, read once: subcommand parsers are "<prog> <cmd>"."""
    global _root_prog
    if _root_prog is None:
        from dokimi.cli import build_parser

        _root_prog = build_parser().prog
    return _root_prog


def pytest_addoption(parser):
    group = parser.getgroup("outcomes")
    group.addoption("--outcomes", action="store_true", help="check tests/outcomes contracts after the run")
    group.addoption("--outcomes-report", default="outcomes-result.json", help="where --outcomes writes its result")
    group.addoption("--journeys-report", default="journeys-result.json",
                    help="where --outcomes writes the journey coverage (JSON, and .md beside it)")


def pytest_configure(config):
    config.addinivalue_line("markers", "outcome(*ids): the test is evidence for these user-outcome ids")
    out = Path(config.getoption("journeys_report"))
    if config.getoption("outcomes") and out.suffix != ".json":
        # Checked before the run, not after it: the markdown goes beside it with ".md".
        raise pytest.UsageError(f"--journeys-report must be a .json path, got {out}")


def pytest_runtest_logreport(report):
    # A failed setup or teardown counts as a failure of the evidence; otherwise only the call phase.
    # A skip in setup (skipif, or a fixture that skips) is a skipped test.
    if report.when != "call" and not (report.failed or report.skipped):
        return
    ids = [i for key, value in report.user_properties if key == "outcome_ids" for i in value]
    for evidence_id in ids:
        _results.setdefault(evidence_id, []).append(report.outcome)
    # A skipped or failed test proves no invocation, even if it parsed one first.
    calls = [c for key, value in report.user_properties if key == "cli_invocations" for c in value] \
        if report.passed else []
    for evidence_id in ids:
        _invocations.setdefault(evidence_id, []).extend(calls)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    # Around setup and call: a fixture that runs the CLI before its yield counts too.
    # The argv list is attached to the call report, so a parse in teardown is never credited.
    if not any(item.iter_markers("outcome")):
        yield
        return
    global _active_calls
    calls: list[list[str]] = []
    item.stash[_CALLS] = calls
    original = argparse.ArgumentParser.parse_known_args

    def capture(self, args=None, namespace=None):
        parsed, leftover = original(self, args, namespace)
        # Leftovers mean parse_args() is about to reject the argv: nothing was invoked.
        if _active_calls is not None and self.prog == _root() and not leftover:
            _active_calls.append([str(a) for a in (sys.argv[1:] if args is None else args)])
        return parsed, leftover

    argparse.ArgumentParser.parse_known_args = capture  # type: ignore[method-assign]
    _active_calls = calls
    try:
        yield
    finally:
        _active_calls = None
        argparse.ArgumentParser.parse_known_args = original  # type: ignore[method-assign]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    # user_properties, not a new attribute: pytest-xdist serializes it from worker to controller.
    ids = [i for mark in item.iter_markers("outcome") for i in mark.args]
    if ids:
        report.user_properties.append(("outcome_ids", ids))
        if call.when == "call":
            report.user_properties.append(("cli_invocations", item.stash.get(_CALLS, [])))


def _worst(verdicts):
    return max(verdicts, key=RANK.index, default="error")


def _evidence(evidence_id: str) -> dict:
    outcomes = _results.get(evidence_id, [])
    if not outcomes:
        return {"id": evidence_id, "verdict": "error", "reason": "no test claimed this id"}
    if "failed" in outcomes:
        return {"id": evidence_id, "verdict": "fail", "reason": f"{outcomes.count('failed')} claimed test(s) failed"}
    if all(o == "skipped" for o in outcomes):
        return {"id": evidence_id, "verdict": "inconclusive", "reason": "every claimed test was skipped"}
    return {"id": evidence_id, "verdict": "pass", "tests": len(outcomes)}


def _anchors(doc: Path) -> set[str]:
    """GitHub-style heading slugs of a markdown file, ignoring fenced code."""
    slugs: set[str] = set()
    fenced = False
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and (heading := re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)):
            slug = re.sub(r"[^\w\- ]", "", heading.group(1).lower()).replace(" ", "-")
            n, unique = 0, slug
            while unique in slugs:
                n += 1
                unique = f"{slug}-{n}"
            slugs.add(unique)
    return slugs


def _ref_resolves(ref: str) -> bool:
    name, _, anchor = ref.partition("#")
    target = REPO_ROOT / name
    if not target.exists():
        return False
    return not anchor or (target.suffix == ".md" and anchor in _anchors(target))


def _check(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    refs = contract.get("intent_refs", []) + contract.get("implementation_refs", [])
    missing = [r for r in refs if not _ref_resolves(r)]
    evidence = [_evidence(i) for i in contract.get("evidence_ids", [])]
    if missing or not evidence:
        verdict = "error"
    else:
        verdict = _worst(e["verdict"] for e in evidence)
    return {"id": contract.get("id", path.stem), "verdict": verdict, "missing_refs": missing, "evidence": evidence}


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    # Under pytest-xdist only the controller sees every report.
    if not config.getoption("outcomes") or hasattr(config, "workerinput"):
        return
    paths = sorted(CONTRACT_DIR.glob("*.json"))
    checked = [_check(p) for p in paths]
    verdict = _worst(c["verdict"] for c in checked)
    report = {"verdict": verdict, "outcomes": checked}
    Path(config.getoption("outcomes_report")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    journeys = None
    if (registry := REGISTRY) is not None:
        from tests.outcomes import journeys as trace

        contracts = {c.get("id", p.stem): c for p in paths
                     if isinstance(c := json.loads(p.read_text(encoding="utf-8")), dict)}
        journeys = trace.check(registry, REPO_ROOT, contracts, _evidence, _invocations, _anchors)
        out = Path(config.getoption("journeys_report"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(journeys, indent=2) + "\n", encoding="utf-8")
        out.with_suffix(".md").write_text(trace.markdown(journeys), encoding="utf-8")
        verdict = _worst([verdict, journeys["verdict"]])
    # Set exit status before printing (in case reporter is None)
    session.exitstatus = max(int(exitstatus), EXIT[verdict]) if verdict != "pass" else exitstatus
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        for c in checked:
            bad = [f"{e['id']}={e['verdict']}" for e in c["evidence"] if e["verdict"] != "pass"]
            reporter.write_line(f"outcome {c['id']}: {c['verdict']}" + (f" ({', '.join(bad + c['missing_refs'])})" if bad or c["missing_refs"] else ""))
        if journeys is not None:
            for problem in journeys["problems"]:
                reporter.write_line(f"journeys: {problem}")
            for row in journeys["criteria"]:
                if row["enforce"] and row["verdict"] != "pass":
                    reasons = [f"{e['id']}={e['verdict']}" for e in row["evidence"] if e["verdict"] != "pass"]
                    reporter.write_line(f"journey criterion {row['id']}: {row['verdict']} ({', '.join(reasons)})")
            enforced = sum(1 for r in journeys["criteria"] if r["enforce"])
            reporter.write_line(f"journeys: {journeys['verdict']}, {enforced} enforced criteria, "
                                f"{len(journeys['unverified'])} of {len(journeys['criteria'])} unverified")
