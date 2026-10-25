# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Probes for dataset files: one file, many items.

Field names follow each upstream's own definition:

- SWE-bench: ``swebench/types.py`` ``SWEbenchInstance`` (SWE-bench/SWE-bench).
- Inspect AI: ``Sample`` / ``FieldSpec`` defaults in ``inspect_ai/dataset/_dataset.py``.
- OpenAI evals: ``docs/build-eval.md`` (``input`` as chat messages, ``ideal``).
- anthropics/skills skill-creator: ``references/schemas.md`` (``evals.json``) and
  the trigger eval set of its ``SKILL.md`` (``[{query, should_trigger}]``).
- google/skill-reach: ``Query`` / ``QuerySet`` in ``src/reach/models.py`` and
  ``src/reach/queries.py`` (``id`` is the legacy spelling of ``query_id``).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from dokimi.task_formats.model import (
    BY_QUESTIONS, BY_TRIGGER_SET, KIND_ANSWER_ONLY, KIND_EXECUTABLE, KIND_TRIGGER, Fingerprint, Report,
)
from dokimi.task_formats.source import FileSource, first_rows, key_share

_ROW_SUFFIXES = (".json", ".jsonl", ".yaml", ".yml")


def _records(source: FileSource, marker: str) -> list[dict[str, Any]] | None:
    """The file's rows; a single object holding *marker* is a one-row file."""
    if source.suffix not in _ROW_SUFFIXES:
        return None
    if source.rows is not None:
        return source.rows
    data = source.data
    return [data] if isinstance(data, dict) and marker in data else None


def _without(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for r in rows if not r.get(key))


def _lint_missing(report: Report, rows: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
    for key in keys:
        n = _without(rows, key)
        if n:
            report.problems.append(f"{n} of {len(rows)} items have no {key}")


def _lint_duplicate_ids(report: Report, rows: list[dict[str, Any]], *keys: str) -> None:
    ids = Counter(str(next((r[k] for k in keys if k in r), "")) for r in rows)
    dupes = sorted(i for i, n in ids.items() if i and n > 1)
    if dupes:
        report.problems.append(f"duplicate ids: {', '.join(dupes[:5])}")


def _base(source: FileSource, name: str, kind: str, rows: list[dict[str, Any]]) -> Report:
    report = Report(source.path, name, kind, items=len(rows))
    report.problems.extend(source.problems)
    return report


# --- SWE-bench ---------------------------------------------------------------

_SWE_REQUIRED = ("instance_id", "repo", "base_commit", "problem_statement")


def _test_list(value: Any) -> list[Any] | None:
    """FAIL_TO_PASS / PASS_TO_PASS: a list, or (as the HF datasets ship it) its JSON string."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


class SweBenchProbe:
    name = "swe-bench"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        rows = first_rows(_records(source, "instance_id"))
        share = key_share(rows, *_SWE_REQUIRED)
        if share < 0.5:
            return None
        tests = key_share(rows, "FAIL_TO_PASS", "PASS_TO_PASS")
        return Fingerprint(0.6 * share + 0.4 * tests, (*_SWE_REQUIRED, "FAIL_TO_PASS/PASS_TO_PASS" if tests else ""))

    def describe(self, source: FileSource) -> Report:
        rows = _records(source, "instance_id") or []
        report = _base(source, self.name, KIND_EXECUTABLE, rows)
        report.set_parts({
            "instruction": key_share(rows, "problem_statement") == 1,
            "environment": key_share(rows, "repo", "base_commit") == 1,
            "oracle": key_share(rows, "patch") == 1,
            "verifier": key_share(rows, "FAIL_TO_PASS") == 1,
            "metadata": key_share(rows, "version") == 1,
        })
        _lint_missing(report, rows, (*_SWE_REQUIRED, "FAIL_TO_PASS"))
        bad = sum(1 for r in rows if "FAIL_TO_PASS" in r and _test_list(r["FAIL_TO_PASS"]) is None)
        if bad:
            report.problems.append(f"{bad} items have a FAIL_TO_PASS that is not a list of test ids")
        _lint_duplicate_ids(report, rows, "instance_id")
        report.details["repos"] = sorted({str(r.get("repo")) for r in rows if r.get("repo")})[:20]
        return report


# --- Inspect AI samples and OpenAI evals --------------------------------------


def _is_chat(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(m, dict) and "role" in m and "content" in m for m in value)


class InspectSamplesProbe:
    name = "inspect-samples"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        rows = first_rows(_records(source, "input"))
        with_target = key_share(rows, "input", "target")
        if with_target >= 0.5:
            return Fingerprint(0.85 * with_target, ("input", "target"))
        if key_share(rows, "input") >= 0.8 and not key_share(rows, "ideal"):
            return Fingerprint(0.55, ("input (no target)",))
        return None

    def describe(self, source: FileSource) -> Report:
        rows = _records(source, "input") or []
        report = _base(source, self.name, KIND_ANSWER_ONLY, rows)
        report.set_parts({
            "instruction": key_share(rows, "input") == 1,
            "environment": any(k in r for r in rows for k in ("sandbox", "files", "setup")),
            "oracle": key_share(rows, "target") == 1,
            # The scorer is code in the @task file, not in the samples.
            "verifier": False,
            "metadata": key_share(rows, "metadata") == 1,
        })
        _lint_missing(report, rows, ("input",))
        bad = sum(1 for r in rows if "input" in r and not isinstance(r["input"], str) and not _is_chat(r["input"]))
        if bad:
            report.problems.append(f"{bad} items have an input that is neither text nor chat messages")
        _lint_duplicate_ids(report, rows, "id")
        report.details["with_choices"] = sum(1 for r in rows if "choices" in r)
        return report


class OpenAIEvalsProbe:
    name = "openai-evals"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        if source.suffix not in (".jsonl", ".json"):
            return None
        rows = first_rows(_records(source, "ideal"))
        share = key_share(rows, "input", "ideal")
        if share < 0.5:
            return None
        chat = sum(_is_chat(r.get("input")) for r in rows) / len(rows)
        return Fingerprint(0.7 * share + 0.25 * chat, ("input", "ideal", "chat messages" if chat else ""))

    def describe(self, source: FileSource) -> Report:
        rows = _records(source, "ideal") or []
        report = _base(source, self.name, KIND_ANSWER_ONLY, rows)
        report.set_parts({
            "instruction": key_share(rows, "input") == 1,
            "oracle": key_share(rows, "ideal") == 1,
        })
        _lint_missing(report, rows, ("input", "ideal"))
        chat = sum(1 for r in rows if _is_chat(r.get("input")))
        report.details["chat_inputs"] = chat
        return report


# --- skill-creator -----------------------------------------------------------


class SkillCreatorEvalsProbe:
    name = "skill-creator-evals"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        data = source.data if source.suffix == ".json" else None
        if not isinstance(data, dict) or not isinstance(data.get("evals"), list):
            return None
        rows = [r for r in data["evals"] if isinstance(r, dict)]
        score = 0.5 + 0.2 * ("skill_name" in data) + 0.3 * key_share(first_rows(rows), "prompt")
        return Fingerprint(score, ("evals[]", "skill_name" if "skill_name" in data else "", "prompt"))

    def describe(self, source: FileSource) -> Report:
        data = source.data if isinstance(source.data, dict) else {}
        rows = [r for r in data.get("evals", []) if isinstance(r, dict)]
        report = _base(source, self.name, KIND_ANSWER_ONLY, rows)
        report.set_parts({
            "instruction": key_share(rows, "prompt") == 1 and bool(rows),
            "environment": any(r.get("files") for r in rows),
            "oracle": key_share(rows, "expected_output") == 1 and bool(rows),
            "verifier": any(r.get("expectations") for r in rows),
            "metadata": bool(data.get("skill_name")),
        })
        _lint_missing(report, rows, ("prompt",))
        bad = sum(1 for r in rows if not isinstance(r.get("expectations", []), list))
        if bad:
            report.problems.append(f"{bad} items have expectations that are not a list")
        _lint_duplicate_ids(report, rows, "id")
        report.details["skill_name"] = data.get("skill_name")
        return report


class SkillCreatorTriggerProbe:
    name = "skill-creator-trigger"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        rows = first_rows(source.rows if source.suffix in (".json", ".jsonl") else None)
        share = key_share(rows, "query", "should_trigger")
        return Fingerprint(0.95 * share, ("query", "should_trigger")) if share >= 0.5 else None

    def describe(self, source: FileSource) -> Report:
        rows = source.rows or []
        report = _base(source, self.name, KIND_TRIGGER, rows)
        report.set_parts({"instruction": key_share(rows, "query") == 1,
                          "oracle": key_share(rows, "should_trigger") == 1})
        _lint_missing(report, rows, ("query",))
        bad = sum(1 for r in rows if not isinstance(r.get("should_trigger"), bool))
        if bad:
            report.problems.append(f"{bad} items have a should_trigger that is not true/false")
        report.details["should_trigger"] = sum(1 for r in rows if r.get("should_trigger") is True)
        report.details["should_not_trigger"] = sum(1 for r in rows if r.get("should_trigger") is False)
        if not report.problems:
            report.consumers.append(BY_TRIGGER_SET)
        return report


# --- skill-reach -------------------------------------------------------------

_REACH_QUERY_KEYS = frozenset({"query_id", "id", "text", "kind", "expected_skill", "acceptable_skills", "notes"})
_REACH_SET_KEYS = frozenset({"catalog_id", "notes", "provenance", "queries"})


def _reach_rows(source: FileSource) -> list[dict[str, Any]] | None:
    data = source.data
    if isinstance(data, dict) and isinstance(data.get("queries"), list):
        return [q for q in data["queries"] if isinstance(q, dict)]
    return source.rows


def _lint_reach_query(q: dict[str, Any]) -> str | None:
    qid = q.get("query_id", q.get("id", "?"))
    unknown = sorted(set(q) - _REACH_QUERY_KEYS)
    if unknown:
        return f"{qid}: keys skill-reach refuses: {', '.join(unknown)}"
    if (q.get("kind") == "out_of_scope") != (q.get("expected_skill") is None):
        return f"{qid}: kind={q.get('kind')!r} disagrees with expected_skill={q.get('expected_skill')!r}"
    return None


class SkillReachProbe:
    name = "skill-reach"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        if source.suffix not in _ROW_SUFFIXES:
            return None
        rows = first_rows(_reach_rows(source))
        texts = key_share(rows, "text", "expected_skill")
        if texts < 0.5:
            return None
        ids = max(key_share(rows, "query_id"), key_share(rows, "id"))
        wrapped = isinstance(source.data, dict) and "queries" in source.data
        evidence = ("queries[]" if wrapped else "rows", "text", "expected_skill", "query_id/id" if ids else "")
        return Fingerprint(0.5 * texts + 0.3 * ids + 0.2 * wrapped, evidence)

    def describe(self, source: FileSource) -> Report:
        rows = _reach_rows(source) or []
        report = _base(source, self.name, KIND_TRIGGER, rows)
        data = source.data if isinstance(source.data, dict) else {}
        report.set_parts({"instruction": key_share(rows, "text") == 1,
                          "oracle": key_share(rows, "expected_skill") == 1,
                          "metadata": bool(data.get("catalog_id") or data.get("provenance"))})
        _lint_missing(report, rows, ("text",))
        extra = sorted(set(data) - _REACH_SET_KEYS)
        if extra:
            report.problems.append(f"QuerySet keys skill-reach refuses: {', '.join(extra)}")
        report.problems.extend(p for p in map(_lint_reach_query, rows) if p)
        _lint_duplicate_ids(report, rows, "query_id", "id")
        report.details["out_of_scope"] = sum(1 for r in rows if r.get("expected_skill") is None)
        if not report.problems:
            report.consumers.append(BY_TRIGGER_SET)
        return report


# --- dokimi questions --------------------------------------------------------

_QUESTIONS_STAMPS = ("questions.v1", "questions.v2")


class QuestionsProbe:
    name = "dokimi-questions"
    target = "file"

    def fingerprint(self, source: FileSource) -> Fingerprint | None:
        data = source.data if source.suffix == ".json" else None
        if isinstance(data, dict) and data.get("schema_version") in _QUESTIONS_STAMPS:
            return Fingerprint(1.0, (f"schema_version {data['schema_version']}",))
        if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
            return None
        rows = first_rows([q for q in data["questions"] if isinstance(q, dict)])
        share = max(key_share(rows, "question"), key_share(rows, "text"))
        return Fingerprint(0.4 + 0.4 * share, ("questions[]", "question/text")) if share else None

    def describe(self, source: FileSource) -> Report:
        from dokimi.questions.artifact import load_questions

        data = source.data if isinstance(source.data, dict) else {}
        rows = [q for q in data.get("questions", []) if isinstance(q, dict)]
        kind = KIND_TRIGGER if any("expected_skill" in q for q in rows) else KIND_ANSWER_ONLY
        report = _base(source, self.name, kind, rows)
        has_text = all(q.get("question") or q.get("text") for q in rows) and bool(rows)
        report.set_parts({
            "instruction": has_text,
            "oracle": any(q.get("ground_truth_chunk") or q.get("expected_skill") for q in rows),
            "verifier": any(q.get("expected_topics") for q in rows),
            "metadata": bool(data.get("product") or data.get("library")),
        })
        try:
            load_questions(source.path)
        except Exception as exc:  # noqa: BLE001 -- any refusal is a lint finding, the scan goes on
            report.problems.append(" ".join(str(exc).split())[:300])
        else:
            report.consumers.append(BY_QUESTIONS)
        report.details["schema_version"] = data.get("schema_version")
        return report
