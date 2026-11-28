# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Markdown from a persisted delivery run. Rendering reads no corpus or model."""

import math
from html import escape


def cell(value: object) -> str:
    text = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return escape(text).replace("|", r"\|")


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _count(value: object) -> str:
    return str(value) if type(value) is int and value >= 0 else "—"


def _ratio(value: object) -> str:
    if isinstance(value, list) and len(value) == 2:
        return f"{_count(value[0])}/{_count(value[1])}"
    return "—"


def _measure(value: object, digits: int) -> str:
    if type(value) is int:
        return f"{value}.{digits * '0'}"
    return f"{value:.{digits}f}" if isinstance(value, float) and math.isfinite(value) else "—"


def scan_lines(data: dict) -> list[str]:
    summary = data["summary"]
    attribution = _object(summary.get("attribution"))
    quality = attribution.get("evidence_quality") or "attribution unavailable"
    lines = [
        f"Model `{cell(data['identity'].get('model') or 'fixture')}` · {cell(quality)}",
        "",
        "| Arm | Score / 10 | Measured / planned | False successes / claims |",
        "|---|---:|---:|---:|",
    ]
    for arm in ("A", "B"):
        stats = _object(summary.get(arm))
        value = "partial" if "score" in stats and stats["score"] is None else _measure(stats.get("score"), 2)
        lines.append(f"| {arm} | {value} | {_ratio(stats.get('coverage'))} | {_ratio(stats.get('false_success'))} |")
    lines += [
        "",
        "| Arm | Group | Weight | Scenario | Successful / measured |",
        "|---|---|---:|---|---:|",
    ]
    for arm in ("A", "B"):
        stats = _object(summary.get(arm))
        for group, group_data in _object(stats.get("groups")).items():
            group_stats = _object(group_data)
            for name, scenario in _object(group_stats.get("scenarios")).items():
                counts = _object(scenario)
                lines.append(
                    f"| {arm} | {cell(group)} | {_measure(group_stats.get('weight'), 3)} | {cell(name)} | "
                    f"{_count(counts.get('successes'))}/{_count(counts.get('measured'))} |"
                )
    lines += [
        "",
        "### Source attribution",
        "",
        "Snapshot injection. Read telemetry unavailable." if attribution else "Source attribution unavailable.",
        "",
    ]
    pairs = _object(summary.get("paired"))
    if not pairs:
        lines.append("Paired evidence unavailable.")
    for name, pair_data in pairs.items():
        pair = _object(pair_data)
        lines.append(
            f"- {cell(name)}. {_count(pair.get('measured_pairs'))}/{_count(pair.get('planned_pairs'))} pairs measured, "
            f"{_count(pair.get('improvements'))} improved and {_count(pair.get('regressions'))} regressed in B."
        )
    sources = attribution.get("sources")
    for source in sources if isinstance(sources, list) else ():
        if not isinstance(source, dict):
            continue
        lines.append(
            f"- {cell(source.get('finding_id', 'Source'))}. {cell(source.get('url') or 'URL unavailable')} · "
            f"{cell(source.get('captured_at') or 'Capture date unavailable')}. "
            f"{cell(source.get('unavailable') or source.get('conflict', 'Version conflict unknown'))}. "
            f"Owner: {cell(source.get('owner', 'unassigned'))}. "
            f"{cell(source.get('recommendation', 'Review the source and its version'))}."
        )
    return lines


def render(data: dict) -> str:
    summary = data["summary"]
    lines = [
        f"# {data['mode'].title()} · {cell(data['identity']['profile'])}",
        "",
        f"{cell(data['date'])} · Artifact `{data['identity']['artifact']}`",
        "",
    ]
    if data["mode"] == "release":
        policy = data["identity"].get("policy_version")
        lines += [
            f"**{summary['verdict']}**" + (f" · Policy {cell(policy)}" if policy is not None else ""),
            "",
        ]
        lines += [f"- {cell(name)} · {cell(outcome)}" for name, outcome in _object(summary.get("checks")).items()]
    else:
        lines += scan_lines(data)
    lines += [
        "",
        "| Scenario | Arm | Attempt | Result | Component | Evidence in run JSON |",
        "|---|---|---:|---|---|---|",
    ]
    for i, row in enumerate(data["observations"]):
        detail = row.get("reason") or row.get("agent_error") or row.get("finding_id") or ""
        lines.append(
            f"| {cell(row['task'])} | {row['arm']} | {row['repeat']} | {row['outcome']} | "
            f"{cell(row.get('owner', ''))} | `#/observations/{i}` {cell(detail)} |"
        )
    return "\n".join([*lines, "", "### Reproduce", "", "```sh", data["reproduce"], "```", ""])
