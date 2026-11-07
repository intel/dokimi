# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Map actual deterministic catalog checks onto the shared plan/result contract."""

from __future__ import annotations

from typing import Any

from dokimi.runner.plan import completeness, plan_cells, plan_digest

from .catalog_eval import shared_problems


def receipts(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One always-run integrity cell plus one check per selected skill; no runtime cells."""
    selected = set(report["selection"]["selected"])
    by_name: dict[str, dict[str, Any]] = {}
    for row in report["skills"]:
        if row["name"] in selected:
            by_name.setdefault(row["name"], row)
    rows = list(by_name.values())
    names = ["catalog-integrity", *(f"skill/{row['name']}" for row in rows)]
    plan: dict[str, Any] = {
        "subjects": [{"id": f"skill/{row['name']}", "kind": "skill",
                      "sha": report["source"]["commit"], "fingerprint": row["bundle_digest"]}
                     for row in rows],
        "suites": [{"id": "catalog-static", "hash": report["catalog_digest"],
                    "reasons": ["deterministic catalog integrity and selected skill checks"]}],
        "models": [None], "harnesses": ["catalog-static"],
        "grader": {"id": "catalog-static", "digest": report["checker"]["package_sha256"]},
        "varied_variable": "none-static-checks", "cells": plan_cells(["catalog-static"], [None], names),
        "budget": {"cells": len(names), "agent_calls": 0, "cost_usd": 0},
    }
    integrity = any(item["level"] == "error" for item in shared_problems(report["problems"], report["skills"]))
    statuses = {"catalog-integrity": "fail" if integrity else "pass",
                **{f"skill/{row['name']}": _skill_status(report, row["name"]) for row in rows}}
    result = {
        "plan_digest": plan_digest(plan),
        "cells": [{"cell_id": cell["cell_id"], "status": statuses[cell["task"]], "model": None,
                   "detail": "Deterministic checks only; task outcomes and skill utility were not measured."}
                  for cell in plan["cells"]],
    }
    if not completeness(plan, result).complete:
        raise ValueError("internal error: the static check result is incomplete")
    return plan, result


def _skill_status(report: dict[str, Any], name: str) -> str:
    return "fail" if any(row["checks"]["status"] == "fail" for row in report["skills"]
                         if row["name"] == name) else "pass"


def _selection_lines(report: dict[str, Any]) -> list[str]:
    """The selection count, its rule and the rows excluded, counted from the same rows as the table."""
    rows = report["skills"]
    selected = set(report["selection"]["selected"])
    chosen = sum(1 for row in rows if row["name"] in selected)
    shared = len(rows) - len({row["name"] for row in rows})
    if report["selection"]["mode"] == "all":
        excluded = "Excluded rows: 0 (a full run selects every row)."
    else:
        excluded = (f"Excluded rows: {len(rows) - chosen}, because no changed path is owned by "
                    "their skill.")
    plural = "" if len(selected) == 1 else "s"
    return [
        f"Selected skills: {chosen} of {len(rows)} rows ({len(selected)} distinct name{plural}).",
        "Selection is by skill name. Every SKILL.md the catalog lists is a row, nested and "
        f"load-error rows included; {shared} row(s) share a name with an earlier row and are "
        "selected with it, as the duplicate check reports.",
        excluded,
        "",
    ]


def render(report: dict[str, Any]) -> str:
    """A human receipt whose pass is explicitly scoped to deterministic checks."""
    lines = ["# Catalog static evaluation", "", f"Static checks: **{report['status']}**", "",
             "No model, Docker, skill script, oracle or task verifier was run. "
             "This result does not validate skill utility or hardware performance.", ""]
    lines.extend(_selection_lines(report))
    note = report.get("selection_note")
    if note:
        lines.extend([str(note), ""])
    lines.extend(["| Skill | Selected | Static checks | Runtime evaluation | Actual tasks |",
                  "|---|---|---|---|---|"])
    selected = set(report["selection"]["selected"])
    for row in report["skills"]:
        lines.append(f"| {_text(row['name'])} | {row['name'] in selected} | {row['checks']['status']} | "
                     f"{row['evaluation']} | {len(row['tasks'])} |")
    per_skill = [item for row in report["skills"] for item in row["checks"]["findings"]]
    for finding in report["problems"]:
        if finding not in per_skill:
            lines.extend(["", f"- {_text(finding['code'])}: {_text(finding['message'])}"])
    for row in report["skills"]:
        for finding in row["checks"]["findings"]:
            lines.extend(["", f"- {_text(row['name'])}: {_text(finding['code'])}: {_text(finding['message'])}"])
    return "\n".join(lines) + "\n"


def _text(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")
