# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One summary over any number of ``cli_output.v1`` documents (issue #749, layer 1).

It reads envelope keys only (``kind``, ``status``, ``verdict``, ``reason``, ``error``,
``subjects``, ``cost``), so a new leaf needs no change here. It computes no verdict: each row
shows the verdict its document carries, and ``verdict: null`` is report-only. A document that is
missing, unreadable or of another schema stays a row (``not_run`` or ``invalid``); it is never
dropped and never shown as a pass. Stdlib only, and no ``GITHUB_*`` variable is read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "cli_output.v1"
SUMMARY_SCHEMA = "report_summary.v1"
_VERDICTS = ("pass", "fail", "inconclusive", "invalid", "infra_error")
_STATUSES = ("complete", "partial", "budget_stopped", "cancelled", "failed")
FORMATS = ("markdown", "json", "annotations")
#: Document verdicts that count against the overall state, worst first.
_BAD = ("infra_error", "invalid", "fail", "inconclusive")
#: Document statuses that end short of complete; a row in one is not a pass.
_STOPPED = ("budget_stopped", "partial", "cancelled")
#: Findings listed under the subject table of the Markdown; the rest stay in the JSON and the annotations.
MAX_LISTED = 100


def _row(source: str, state: str, *, kind: str | None = None, status: str | None = None,
         verdict: str | None = None, reason: str | None = None, cost: dict[str, Any] | None = None,
         subjects: int = 0) -> dict[str, Any]:
    return {"source": source, "state": state, "kind": kind, "status": status, "verdict": verdict,
            "reason": reason, "subjects": subjects, "summary": {},
            "spent_usd": (cost or {}).get("spent_usd"), "cap_usd": (cost or {}).get("cap_usd"),
            "priced": (cost or {}).get("priced")}


def expand_inputs(paths: list[str]) -> list[Path]:
    """Each file as given; a directory becomes its ``*.json`` files, sorted. A missing path, and a directory with no
    ``*.json`` file, stay as given, to become a ``not_run`` row."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        out.extend((sorted(p.glob("*.json")) or [p]) if p.is_dir() else [p])
    return out


def _str_or_none(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _finding_ok(f: Any) -> bool:
    """A finding holds the types ``report_summary.v1`` states: string level, code and message, and an optional
    string file and integer line."""
    return (isinstance(f, dict) and all(isinstance(f.get(k), str) for k in ("level", "code", "message"))
            and _str_or_none(f.get("file")) and (f.get("line") is None or (
                isinstance(f["line"], int) and not isinstance(f["line"], bool))))


def _malformed(doc: dict[str, Any]) -> str | None:
    """What is wrong with the envelope fields the summary consumes, or None when they hold their schema's types."""
    if not isinstance(doc.get("kind"), str):
        return "kind"
    if doc.get("status") not in _STATUSES:
        return "status"
    if doc.get("verdict") is not None and doc["verdict"] not in _VERDICTS:
        return "verdict"
    if not isinstance(doc.get("subjects", []), list):
        return "subjects"
    for s in doc.get("subjects") or []:
        if not isinstance(s, dict) or not isinstance(s.get("subject"), str) or not s["subject"] \
                or (s.get("verdict") is not None and s["verdict"] not in _VERDICTS) \
                or not isinstance(s.get("findings", []), list) \
                or not all(_finding_ok(f) for f in s.get("findings") or []):
            return "subjects"
    error = doc.get("error")
    if not _str_or_none(doc.get("reason")) or (isinstance(error, dict) and not _str_or_none(error.get("message"))):
        return "reason"
    cost = doc.get("cost")
    if cost is not None and (not isinstance(cost, dict) or any(
            v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)))
            for v in (cost.get("spent_usd"), cost.get("cap_usd")))):
        return "cost"
    return None


def load(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The check row of one input and its document, or None when it is not a ``cli_output.v1`` document."""
    source = str(path)
    if path.is_dir():
        return _row(source, "not_run", reason="no *.json file in the directory"), None
    if not path.exists():
        return _row(source, "not_run", reason="file not found"), None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return _row(source, "invalid", reason=f"unreadable: {type(exc).__name__}"), None
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        got = doc.get("schema_version") if isinstance(doc, dict) else type(doc).__name__
        return _row(source, "invalid", reason=f"not {SCHEMA_VERSION} (got {got!r})"), None
    bad = _malformed(doc)
    if bad:
        return _row(source, "invalid", reason=f"{bad} malformed"), None
    error = doc.get("error") if isinstance(doc.get("error"), dict) else None
    reason = doc.get("reason") or (error or {}).get("message")
    verdict = doc.get("verdict")
    status = doc.get("status")
    if status == "failed":
        state = "failed"
    elif verdict in _BAD:
        state = verdict
    elif status in _STOPPED:  # a run the cap or an error cut short is not a pass, whatever verdict it carries
        state = status
    else:
        state = verdict or "report_only"
    cost = doc.get("cost") if isinstance(doc.get("cost"), dict) else None
    row = _row(source, state, kind=doc["kind"], status=doc.get("status"), verdict=verdict, reason=reason,
               cost=cost, subjects=len(doc.get("subjects") or []))
    row["summary"] = _flat(doc.get("summary"))
    return row, doc


def _flat(summary: Any, prefix: str = "") -> dict[str, Any]:
    """The scalar values of a document's ``summary``, nested keys joined by ``.``; ``dokimi_version`` left out."""
    out: dict[str, Any] = {}
    for key, value in (summary.items() if isinstance(summary, dict) else ()):
        name = f"{prefix}{key}"
        if name == "dokimi_version":
            continue
        if isinstance(value, dict):
            out.update(_flat(value, name + "."))
        elif value is None or isinstance(value, (str, int, float, bool)):
            out[name] = value
    return out


def _overall(checks: list[dict[str, Any]]) -> str:
    states = {c["state"] for c in checks}
    for bad in ("failed", "invalid", "not_run", *_BAD, *_STOPPED):
        if bad in states:
            return bad
    if "pass" in states:
        return "pass"
    return "report_only" if checks else "not_run"


def summarize(paths: list[str]) -> dict[str, Any]:
    """The merged document: overall state, one row per input, one row per subject id across inputs."""
    checks: list[dict[str, Any]] = []
    subjects: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for path in expand_inputs(paths):
        row, doc = load(path)
        checks.append(row)
        # One column key per document: its kind, or the kind and source when an earlier document took the kind.
        check = row["kind"]
        if (doc or {}).get("subjects"):
            n = 1
            while check in used:
                n += 1
                check = f"{row['kind']} ({row['source']})" + (f" #{n}" if n > 2 else "")
            used.add(check)
        for s in (doc or {}).get("subjects") or []:
            if isinstance(s, dict) and isinstance(s.get("subject"), str):
                entry = subjects.setdefault(s["subject"], {"subject": s["subject"], "verdicts": {}, "findings": []})
                entry["verdicts"][check] = s.get("verdict")
                entry["findings"] += [{**f, "check": check} for f in s.get("findings") or []
                                      if isinstance(f, dict)]
    spent = [c["spent_usd"] for c in checks if isinstance(c["spent_usd"], (int, float))]
    unpriced = any(c["priced"] is False for c in checks)
    return {"schema_version": SUMMARY_SCHEMA, "overall": _overall(checks), "checks": checks,
            "subjects": [subjects[k] for k in sorted(subjects)],
            "spent_usd": round(sum(spent), 6) if spent and not unpriced else None}


def _cell(value: Any) -> str:
    """A table cell: ``|`` and line breaks cannot split it, ``<`` and ``>`` cannot open HTML."""
    if value is None:
        return "—"
    return (str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
            .replace("<", "&lt;").replace(">", "&gt;"))


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [f"## dokimi summary: {summary['overall']}", "",
             "| Check | Source | State | Verdict | Subjects | Spent / cap USD | Summary | Reason |",
             "|---|---|---|---|---|---|---|---|"]
    for c in summary["checks"]:
        money = "—" if c["spent_usd"] is None else (
            f"unpriced / {_cell(c['cap_usd'])}" if c["priced"] is False else f"{c['spent_usd']} / {_cell(c['cap_usd'])}")
        facts = ", ".join(f"{k} {_cell(v)}" for k, v in c["summary"].items()) or None
        lines.append(f"| {_cell(c['kind'])} | `{_cell(c['source'])}` | {c['state']} | {_cell(c['verdict'])} "
                     f"| {c['subjects']} | {money} | {_cell(facts)} | {_cell(c['reason'])} |")
    if summary["subjects"]:
        kinds = sorted({k for s in summary["subjects"] for k in s["verdicts"]})
        lines += ["", "| Subject | " + " | ".join(_cell(k) for k in kinds) + " |", "|---|" + "---|" * len(kinds)]
        for s in summary["subjects"]:
            counts = {k: sum(f["check"] == k for f in s["findings"]) for k in kinds}
            lines.append(f"| {_cell(s['subject'])} | " + " | ".join(
                (_cell(s["verdicts"].get(k)) + (f" ({counts[k]} finding{'s' * (counts[k] != 1)})" if counts[k] else ""))
                if k in s["verdicts"] else "not in run" for k in kinds) + " |")
        listed = [(s["subject"], f) for s in summary["subjects"] for f in s["findings"]]
        if listed:
            lines += ["", "| Subject | Check | Level | Location | Code | Message |", "|---|---|---|---|---|---|"]
            for subject, f in listed[:MAX_LISTED]:
                where = f.get("file") and f"{f['file']}:{f['line']}" if f.get("line") else f.get("file")
                lines.append(f"| {_cell(subject)} | {_cell(f['check'])} | {_cell(f.get('level'))} | {_cell(where)} "
                             f"| {_cell(f.get('code'))} | {_cell(f.get('message'))} |")
            if len(listed) > MAX_LISTED:
                lines += ["", f"{len(listed) - MAX_LISTED} more findings in the JSON summary and the annotations"]
    return "\n".join(lines) + "\n"


def _escape(text: str) -> str:
    """Workflow-command escaping of a message, so a newline or ``%`` cannot end or forge the command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _prop(text: str) -> str:
    """Workflow-command escaping of a property value (``title``, ``file``)."""
    return _escape(text).replace(",", "%2C").replace(":", "%3A")


def render_annotations(summary: dict[str, Any]) -> str:
    """One ``::error`` per check that failed or could not be read, one ``::warning`` per bad verdict, and one
    line per subject finding (``::error`` for level error, ``::warning`` else) with ``file=`` and ``line=`` when known.
    """
    out = []
    for c in summary["checks"]:
        name = _prop(c["kind"] or c["source"])
        msg = _escape(f"{c['state']}: {c['reason'] or c['source']}")
        if c["state"] in ("failed", "invalid", "not_run", "infra_error"):
            out.append(f"::error title={name}::{msg}")
        elif c["state"] in _BAD or c["state"] in _STOPPED:
            out.append(f"::warning title={name}::{msg}")
    for s in summary["subjects"]:
        for kind, verdict in s["verdicts"].items():
            if verdict in _BAD and not any(f["check"] == kind for f in s["findings"]):
                out.append(f"::warning title={_prop(str(kind))}::{_escape(s['subject'])}: {verdict}")
        for f in s["findings"]:
            props = [f"title={_prop(str(f['check']) + ' ' + str(f.get('code')))}"]
            if f.get("file"):
                props.insert(0, f"file={_prop(str(f['file']))}")
                if isinstance(f.get("line"), int):
                    props.insert(1, f"line={f['line']}")
            level = "error" if f.get("level") == "error" else "warning"
            out.append(f"::{level} {','.join(props)}::{_escape(s['subject'] + ': ' + str(f.get('message')))}")
    return "".join(line + "\n" for line in out)


def render(summary: dict[str, Any], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(summary, indent=2) + "\n"
    if fmt == "annotations":
        return render_annotations(summary)
    return render_markdown(summary)
