# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Probes for eval definitions that are config or code: promptfoo, Inspect AI tasks.

An Inspect ``@task`` file is Python: it is scanned as text with regular
expressions and never imported or run.
"""

from __future__ import annotations

import re
from typing import Any

from dokimi.task_formats.model import KIND_ANSWER_ONLY, KIND_EXECUTABLE, Fingerprint, Report
from dokimi.task_formats.source import FileSource

# --- promptfoo (promptfoo.dev/docs/configuration/reference) -------------------

_PROMPTFOO_NAMES = ("promptfooconfig.yaml", "promptfooconfig.yml", "promptfooconfig.json")


class PromptfooProbe:
    name = "promptfoo"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        if source.suffix not in (".yaml", ".yml", ".json"):
            return None
        named = source.path.name in _PROMPTFOO_NAMES
        data = source.data
        if not isinstance(data, dict):
            return Fingerprint(0.6, ("file name",)) if named else None
        keys = [k for k in ("prompts", "providers", "targets", "tests", "scenarios") if k in data]
        has_target = "providers" in data or "targets" in data
        if not named and not ("prompts" in data and has_target and ("tests" in data or "scenarios" in data)):
            return None
        return Fingerprint(min(1.0, 0.5 + 0.1 * named + 0.1 * len(keys)), (("file name",) if named else ()) + tuple(keys))

    def describe(self, source: FileSource) -> Report:
        data: dict[str, Any] = source.data if isinstance(source.data, dict) else {}
        tests = data.get("tests")
        report = Report(source.path, self.name, KIND_ANSWER_ONLY,
                        items=len(tests) if isinstance(tests, list) else None)
        report.problems.extend(source.problems)
        if source.data is not None and not data:
            report.problems.append("not a mapping")
        asserts = isinstance(data.get("defaultTest"), dict) and "assert" in data["defaultTest"]
        asserts = asserts or any(isinstance(t, dict) and t.get("assert") for t in tests or [] if isinstance(tests, list))
        report.set_parts({
            "instruction": bool(data.get("prompts")),
            "environment": bool(data.get("providers") or data.get("targets")),
            "verifier": asserts,
            "metadata": bool(data.get("description")),
        })
        if isinstance(tests, str):
            report.details["tests_from"] = tests
        elif data and tests is None and "scenarios" not in data:
            report.problems.append("no tests")
        if data and not data.get("prompts"):
            report.problems.append("no prompts")
        return report


# --- Inspect AI @task files ----------------------------------------------------

_INSPECT_IMPORT = re.compile(r"^\s*(?:from\s+inspect_ai(?:\.\w+)*\s+import|import\s+inspect_ai)\b", re.M)
_TASK_DECORATOR = re.compile(r"^\s*@(?:inspect_ai\.)?task\b", re.M)
_SANDBOX = re.compile(r"\bsandbox\s*=")
_SCORER = re.compile(r"\bscorer\s*=")
_DATASET = re.compile(r"\bdataset\s*=|\bSample\s*\(|\b\w+_dataset\s*\(")
_SOLVER = re.compile(r"\b(?:solver|plan)\s*=")


class InspectTaskProbe:
    name = "inspect-task"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        if source.suffix != ".py" or not source.text:
            return None
        if not _INSPECT_IMPORT.search(source.text) or not _TASK_DECORATOR.search(source.text):
            return None
        return Fingerprint(0.9, ("imports inspect_ai", "@task"))

    def describe(self, source: FileSource) -> Report:
        text = source.text or ""
        sandboxed = bool(_SANDBOX.search(text))
        report = Report(source.path, self.name, KIND_EXECUTABLE if sandboxed else KIND_ANSWER_ONLY,
                        items=len(_TASK_DECORATOR.findall(text)))
        report.problems.extend(source.problems)
        report.set_parts({
            "instruction": bool(_DATASET.search(text)),
            "environment": sandboxed,
            "verifier": bool(_SCORER.search(text)),
            "metadata": False,
        })
        report.details["solver"] = bool(_SOLVER.search(text))
        report.details["scan"] = "static text scan; the file was not imported"
        return report
