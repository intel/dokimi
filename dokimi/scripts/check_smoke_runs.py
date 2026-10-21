#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pass/fail gate for the external-catalog smoke test's ``task_runs.v1`` artifact.

``tasks run`` is a reporting command: its exit code does not say whether the
task passed. This gate does, for ``docker-oracle`` and ``docker-probe``:

- every cell ran and passed: ``n > 0``, ``passed == n`` and no errored cell.
  ``pass_rate`` alone is not enough, because an errored cell is left out of its
  denominator (one pass + one errored cell reads ``pass_rate == 1.0``);
- each ``docker-probe`` row shows the egress proxy denying ``example.com``
  both as a CONNECT (``:443``) and as a plain-HTTP GET (``:80``, a separate
  code path): "closed" alone also fits a sidecar nobody reached.

Usage:
    python scripts/check_smoke_runs.py logs/external-catalog-smoke/runs.json
"""

from __future__ import annotations

import json
import sys
from typing import Any

HARNESSES = ("docker-oracle", "docker-probe")


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
