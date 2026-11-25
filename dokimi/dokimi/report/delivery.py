# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Markdown from a persisted delivery run. Rendering reads no corpus or model."""

from html import escape


def cell(value: object) -> str:
    return escape(str(value)).replace("|", r"\|").replace("\n", " ")


def scan_lines(data: dict) -> list[str]:
    summary = data["summary"]
    attribution = summary["attribution"]
    lines = [
        f"Model `{cell(data['identity'].get('model') or 'fixture')}` · {attribution['evidence_quality']}",
        "",
        "| Arm | Score / 10 | Measured / planned | False successes / claims |",
        "|---|---:|---:|---:|",
    ]
    for arm in ("A", "B"):
        stats = summary[arm]
        scored = stats["score"]
        value = f"{scored:.2f}" if scored is not None else "partial"
        measured, planned = stats["coverage"]
        rejected, claims = stats["false_success"]
        lines.append(f"| {arm} | {value} | {measured}/{planned} | {rejected}/{claims} |")
    lines += [
        "",
        "| Arm | Group | Weight | Scenario | Successful / measured |",
        "|---|---|---:|---|---:|",
    ]
    for arm in ("A", "B"):
        for group, group_stats in summary[arm]["groups"].items():
            for name, counts in group_stats["scenarios"].items():
                lines.append(
                    f"| {arm} | {cell(group)} | {group_stats['weight']:.3f} | {cell(name)} | "
                    f"{counts['successes']}/{counts['measured']} |"
                )
    lines += [
        "",
        "### Source attribution",
        "",
        "Snapshot injection. Read telemetry unavailable.",
        "",
    ]
    for name, pair in summary["paired"].items():
        lines.append(
            f"- {cell(name)}. {pair['measured_pairs']}/{pair['planned_pairs']} pairs measured, "
            f"{pair['improvements']} improved and {pair['regressions']} regressed in B."
        )
    for source in attribution["sources"]:
        lines.append(
            f"- {cell(source.get('finding_id', 'Source'))}. {cell(source['url'])} · {cell(source['captured_at'])}. "
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
        lines += [
            f"**{summary['verdict']}** · Policy {cell(data['identity']['policy_version'])}",
            "",
        ]
        lines += [f"- {cell(name)} · {outcome}" for name, outcome in summary["checks"].items()]
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
