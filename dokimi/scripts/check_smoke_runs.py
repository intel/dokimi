#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pass/fail gate for the external-catalog smoke test's ``task_runs.v1`` artifact.

``tasks run`` is a reporting command: its exit code does not say whether the
task passed. This gate does, for ``docker-oracle`` and ``docker-probe``:

- every cell ran and passed: ``n > 0``, ``passed == n`` and no errored cell.
  ``pass_rate`` alone is not enough, because an errored cell is left out of its
  denominator (one pass + one errored cell reads ``pass_rate == 1.0``);
- every planned cell (each harness on :data:`TASKS`, once) has a measured
  status, computed by :func:`dokimi.runner.plan.completeness`: a cell missing
  from the artifact fails the gate however well the others did;
- each ``docker-probe`` row shows the egress proxy denying ``example.com``
  both as a CONNECT (``:443``) and as a plain-HTTP GET (``:80``, a separate
  code path): "closed" alone also fits a sidecar nobody reached.

Usage:
    python scripts/check_smoke_runs.py logs/external-catalog-smoke/runs.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # run as a script from CI, not installed
    sys.path.insert(0, str(PROJECT_ROOT))

from dokimi.harnesses.runner import is_errored
from dokimi.runner.plan import cell_id, completeness, plan_cells, plan_digest

HARNESSES = ("docker-oracle", "docker-probe")
#: The task the workflow runs (``tasks run --tasks`` in agent-quality.yml).
TASKS = ("onetbb-histogram-local-aggregation",)


def planned() -> dict[str, Any]:
    """The cells this gate expects; the oracle and probe call no model."""
    return {"cells": plan_cells(HARNESSES, [None], TASKS)}


def result_for(plan: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """The ``task_runs.v1`` rows as result cells of ``plan``."""
    cells = [{"cell_id": cell_id(row["harness"], None, row.get("task", ""), row.get("repeat", 1)),
              "status": "infra_error" if is_errored(row) else "pass" if row.get("passed") else "fail"}
             for row in data["results"]]
    return {"plan_digest": plan_digest(plan), "cells": cells}


def harness_ok(stats: dict[str, Any]) -> bool:
    return stats["n"] > 0 and stats["passed"] == stats["n"] and not stats.get("errored", 0)


def smoke_ok(data: dict[str, Any], out=sys.stdout) -> bool:
    ok = True
    per_harness = data["summary"]["per_harness"]
    for harness in HARNESSES:
        stats = per_harness.get(harness) or {"n": 0, "passed": 0}
        print(f"{harness}: {stats['passed']}/{stats['n']} pass, "
              f"errored={stats.get('errored', 0)} (rate={stats.get('pass_rate')})", file=out)
        ok = harness_ok(stats) and ok
    plan = planned()
    found = completeness(plan, result_for(plan, data))
    print(f"cells: {found.measured}/{found.planned} measured ({found.state})", file=out)
    names = {c["cell_id"]: f"{c['arm']} on {c['task']}" for c in plan["cells"]}
    for missing in found.missing:
        print(f"  not measured: {names[missing]}", file=out)
    for problem in found.problems:
        print(f"  {problem}", file=out)
    ok = found.complete and ok
    for row in data["results"]:
        metrics = row.get("metrics") or {}
        egress = metrics.get("egress") or {}
        if egress.get("status") == "unknown":  # an unreadable sidecar log is not "no violation"
            print(f"{row['harness']}: egress unknown (sidecar log could not be read)", file=out)
            ok = False
        if row["harness"] == "docker-probe":
            print("isolation_probe:", json.dumps(metrics.get("isolation_probe")), file=out)
            print("egress:", json.dumps(metrics.get("egress")), file=out)
            denied = set(egress.get("denied_entries", []))
            ok = ok and {"example.com:443", "example.com:80"} <= denied
    return ok


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        data = json.load(fh)
    return 0 if smoke_ok(data) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
