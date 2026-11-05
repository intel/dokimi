# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`eval inspect` over real files of every format (tests/fixtures/task_formats/).

MANIFEST.yaml says what each file must be reported as; the whole corpus is one
mixed tree, inspected once. Every report must validate as task_inspect.v1, and the
files dokimi's own writers produce must read back as the format they claim to write.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from dokimi.artifacts import stamp, validate_artifact
from dokimi.external.trigger_set import TriggerQuery, to_questions, to_reach, to_skill_creator
from dokimi.questions.artifact import save_questions_artifact
from dokimi.task_formats import inspect_path

CORPUS = Path(__file__).parent / "fixtures" / "task_formats"
MANIFEST = yaml.safe_load((CORPUS / "MANIFEST.yaml").read_text(encoding="utf-8"))
ITEMS = MANIFEST["items"]
MAX_CORPUS_BYTES = 200 * 1024


@pytest.fixture(scope="module")
def reports() -> dict[str, dict[str, Any]]:
    found = [r.as_dict() for r in inspect_path(CORPUS)]
    return {Path(r["path"]).relative_to(CORPUS).as_posix(): r for r in found}


def _payload(path: Path) -> dict[str, Any]:
    return stamp("task_inspect", {"root": str(path), "items": [r.as_dict() for r in inspect_path(path)]})


def test_corpus_is_small_and_every_file_is_in_the_manifest() -> None:
    files = [p for p in CORPUS.rglob("*") if p.is_file()]
    assert sum(p.stat().st_size for p in files) < MAX_CORPUS_BYTES
    listed = {CORPUS / i["path"] for i in ITEMS}
    for path in files:
        if path.name == "MANIFEST.yaml" or path.parent.name == "LICENSES":
            continue
        # A file is listed itself or lies inside a listed task directory.
        assert any(path == p or p in path.parents for p in listed), path


@pytest.mark.parametrize("entry", ITEMS, ids=[i["path"] for i in ITEMS])
def test_manifest_entry_has_its_provenance(entry: dict[str, Any]) -> None:
    source = MANIFEST["sources"][entry["source"]]
    assert source["license"]
    if entry["source"] not in ("generated", "dokimi"):
        assert source["url"] and len(source["commit"]) == 40 and entry["upstream"]
    else:
        assert entry.get("note") or entry.get("upstream")


@pytest.mark.parametrize("entry", ITEMS, ids=[i["path"] for i in ITEMS])
def test_corpus_item_is_reported_as_the_manifest_says(entry: dict[str, Any], reports: dict) -> None:
    report = reports.get(entry["path"])
    if entry["format"] is None:
        assert report is None, f"{entry['path']} should be unclaimed, got {report and report['format']}"
        return
    assert report is not None, f"{entry['path']} not reported"
    got = {k: report[k] for k in ("format", "kind", "consumers", "items")}
    assert got == {k: entry[k] for k in ("format", "kind", "consumers", "items")}
    if "expected_format" in entry:
        assert report["details"]["expected_format"] == entry["expected_format"]
    if "evidence" in entry:
        assert entry["evidence"] in report["evidence"]


def test_mixed_tree_reports_nothing_the_manifest_does_not_list(reports: dict) -> None:
    claimed = {i["path"] for i in ITEMS if i["format"] is not None}
    assert set(reports) == claimed


def test_every_corpus_format_is_covered() -> None:
    from dokimi.task_formats.detect import PROBES

    covered = {i["format"] for i in ITEMS}
    assert {p.name for p in PROBES} <= covered


@pytest.mark.parametrize("sub", ["", "catalog", "openai-evals", "inspect/examples/biology_qa.py",
                                 "openai-evals/registry/data/GOL/samples.jsonl", "negative"])
def test_every_report_validates_as_task_inspect_v1(sub: str) -> None:
    payload = _payload(CORPUS / sub)
    validate_artifact("task_inspect", json.loads(json.dumps(payload, default=str)))


def test_nested_catalog_alone_reports_its_task_and_its_evals() -> None:
    found = {Path(r.path).relative_to(CORPUS / "catalog").as_posix(): r.format
             for r in inspect_path(CORPUS / "catalog")}
    assert found == {"evaluation/harbor/tasks/onetbb-nstream": "harbor",
                     "skills/dpnp-io/evals/evals.json": "skill-creator-evals"}


def test_a_named_file_inspected_alone_has_no_config_to_follow() -> None:
    # Without its @task file, biology_qa.jsonl's question/answer keys say nothing.
    alone = inspect_path(CORPUS / "inspect/_examples/biology_qa.jsonl")
    assert [r.format for r in alone] == ["unknown"]
    # Without its model-graded spec, input-only rows are ambiguous.
    joke = inspect_path(CORPUS / "openai-evals/registry/data/test_modelgraded/joke_fruits.jsonl")[0]
    assert "openai-evals" in {joke.format, *(a["format"] for a in joke.alternatives)}


# --- writer -> probe round trip ------------------------------------------------

QUERIES = [TriggerQuery("q1", "Fill in this PDF form for me", "pdf-forms"),
           TriggerQuery("q2", "Plot this CSV as a bar chart", ["charts", "pdf-forms"]),
           TriggerQuery("q3", "What is the capital of France?", None)]


def _write(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("writer, fmt, committed", [
    (lambda d: _write(d / "t.json", to_skill_creator(QUERIES, "pdf-forms")),
     "skill-creator-trigger", "writers/pdf-forms.trigger.json"),
    (lambda d: _write(d / "r.json", to_reach(QUERIES, catalog_id="demo")),
     "skill-reach", "writers/reach-queries.json"),
    (lambda d: (save_questions_artifact(to_questions(QUERIES), d / "q.json"), d / "q.json")[1],
     "dokimi-questions", "writers/questions.json"),
], ids=["to_skill_creator", "to_reach", "to_questions"])
def test_writer_output_reads_back_as_its_format(tmp_path: Path, writer, fmt: str, committed: str) -> None:
    path = writer(tmp_path)
    [report] = inspect_path(path)
    assert (report.format, report.items, report.problems) == (fmt, len(QUERIES), [])
    # The corpus copy is what the writer writes today.
    assert json.loads(path.read_text()) == json.loads((CORPUS / committed).read_text())
