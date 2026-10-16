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
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CONTRACT_DIR = Path(__file__).parent
REPO_ROOT = CONTRACT_DIR.parent.parent
EXIT = {"pass": 0, "fail": 1, "error": 2, "inconclusive": 3}
# The worst verdict wins when one contract or one session combines several.
RANK = ["pass", "inconclusive", "fail", "error"]

_results: dict[str, list[str]] = {}


def pytest_addoption(parser):
    group = parser.getgroup("outcomes")
    group.addoption("--outcomes", action="store_true", help="check tests/outcomes contracts after the run")
    group.addoption("--outcomes-report", default="outcomes-result.json", help="where --outcomes writes its result")


def pytest_configure(config):
    config.addinivalue_line("markers", "outcome(*ids): the test is evidence for these user-outcome ids")


def pytest_runtest_logreport(report):
    # A failed setup counts as a failure of the evidence; otherwise only the call phase.
    if report.when != "call" and not (report.when == "setup" and not report.passed):
        return
    for evidence_id in getattr(report, "outcome_ids", ()):
        _results.setdefault(evidence_id, []).append(report.outcome)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    report.outcome_ids = [i for mark in item.iter_markers("outcome") for i in mark.args]


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
    if not config.getoption("outcomes"):
        return
    checked = [_check(p) for p in sorted(CONTRACT_DIR.glob("*.json"))]
    verdict = _worst(c["verdict"] for c in checked)
    report = {"verdict": verdict, "outcomes": checked}
    Path(config.getoption("outcomes_report")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # Set exit status before printing (in case reporter is None)
    session.exitstatus = max(int(exitstatus), EXIT[verdict]) if verdict != "pass" else exitstatus
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        for c in checked:
            bad = [f"{e['id']}={e['verdict']}" for e in c["evidence"] if e["verdict"] != "pass"]
            reporter.write_line(f"outcome {c['id']}: {c['verdict']}" + (f" ({', '.join(bad + c['missing_refs'])})" if bad or c["missing_refs"] else ""))
