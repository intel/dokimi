# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.commands import opportunities as cmd
from dokimi.utils import InputError

STATIC = {"schema": "repo_static.v1", "claims": [
    {"file": "AGENTS.md", "line": 9, "status": "fail_missing_path"},
    {"file": "AGENTS.md", "line": 3, "status": "ok"},
]}
NOVELTY = {"artifact": {"ref": "skill:acme"}, "model": "m1",
           "paired": {"lenient": {"degraded": 0.5, "redundant": 0, "still_missing": 0, "n_paired": 4}}}


@pytest.fixture
def inputs(tmp_path):
    static = tmp_path / "static.json"
    static.write_text(json.dumps(STATIC), encoding="utf-8")
    novelty = tmp_path / "novelty.json"
    novelty.write_text(json.dumps(NOVELTY), encoding="utf-8")
    llms = tmp_path / "llms.txt"
    llms.write_text("prose only\n", encoding="utf-8")
    return [str(static), str(novelty), str(llms)]


def test_build_detects_each_input_kind(inputs):
    doc = cmd.build(inputs)
    assert [i["kind"] for i in doc["inputs"]] == ["repo_static", "novelty", "llms_txt"]
    kinds = {item["kind"] for item in doc["items"]}
    assert kinds == {"repo-claim", "high-severity", "discovery"}


def test_unknown_json_is_an_input_error(tmp_path):
    other = tmp_path / "x.json"
    other.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(InputError, match="not a novelty"):
        cmd.build([str(other)])


def test_json_output_is_a_valid_opportunities_v1_file(inputs, tmp_path):
    out = tmp_path / "opp.json"
    subprocess.run([sys.executable, "cli.py", "report", "opportunities", *inputs,
                    "--format", "json", "--out", str(out)], check=True)
    doc = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("opportunities", doc)
    assert doc["schema_version"] == "opportunities.v1"
    assert doc["items"][0]["rank"] == 1 and doc["items"][0]["kind"] == "high-severity"


def test_markdown_honours_top(inputs):
    proc = subprocess.run([sys.executable, "cli.py", "report", "opportunities", *inputs, "--top-n", "2"],
                          check=True, capture_output=True, text=True)
    rows = [line for line in proc.stdout.splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert [r.split("|")[1].strip() for r in rows] == ["1", "2"]


def test_malformed_json_is_an_input_error(tmp_path):
    bad = tmp_path / "n.json"
    bad.write_text('{"paired": ', encoding="utf-8")
    with pytest.raises(InputError, match="not valid JSON"):
        cmd.build([str(bad)])


def test_build_reads_catalog_and_judge_validity_files(tmp_path):
    cat = tmp_path / "cat.json"
    cat.write_text(json.dumps({"schema_version": "catalog_eval.v1", "skills": [{"path": "s", "checks": {
        "findings": [{"level": "error", "code": "c", "message": "m", "path": "s"}]}}]}), encoding="utf-8")
    jv = tmp_path / "jv.json"
    jv.write_text(json.dumps({"schema_version": "judge_validity.v1",
                              "summary": {"judge": "j", "passed": False}}), encoding="utf-8")
    doc = cmd.build([str(cat), str(jv)])
    assert [i["kind"] for i in doc["inputs"]] == ["catalog_eval", "judge_validity"]
    assert {i["kind"] for i in doc["items"]} == {"catalog-check", "judge-validity"}


def test_markdown_link_at_start_is_not_json(tmp_path):
    path = tmp_path / "llms.txt"
    path.write_text("[Start](start.md)\n", encoding="utf-8-sig")
    assert cmd.build([str(path)])["inputs"][0]["kind"] == "llms_txt"


@pytest.mark.parametrize("data", [b"\xff", b'{"schema":"repo_static.v1","claims":null}',
                                  b'{"paired":{}}', b'{"paired":{"lenient":{"n_paired":0}}}',
                                  b'{"paired":{"lenient":{"degraded":-0.5,"n_paired":4}}}'])
def test_invalid_input_names_the_path(tmp_path, data):
    path = tmp_path / "evidence.json"
    path.write_bytes(data)
    with pytest.raises(InputError, match="evidence.json"):
        cmd.build([str(path)])


def test_markdown_file_matches_stdout(inputs, tmp_path):
    command = [sys.executable, "cli.py", "report", "opportunities", *inputs]
    expected = subprocess.check_output(command)
    out = tmp_path / "report.md"
    subprocess.run([*command, "--out", str(out)], check=True)
    assert out.read_bytes() == expected


def test_repo_claim_targets_the_claim_file(inputs):
    [item] = cmd.build(inputs[:1])["items"]
    assert item["target"] == "AGENTS.md"


def test_build_identifies_repo_static_inputs_by_path(inputs, tmp_path):
    other = tmp_path / "other.json"
    other.write_text(json.dumps(STATIC), encoding="utf-8")
    paths = [inputs[0], str(other), inputs[0]]
    report = cmd.build(paths)
    assert [row["path"] for row in report["inputs"]] == paths[:2]
    assert len({row["source_key"] for row in report["items"]}) == 2
    scoped = cmd.build(paths, repository="owner/repo")
    assert len(scoped["items"]) == 2
    assert len({row["source_key"] for row in scoped["items"]}) == 1


def test_shortcut_reference_at_start_is_markdown(tmp_path):
    path = tmp_path / "llms.txt"
    path.write_text("[Guide]\n\n[Guide]: guide.md\n", encoding="utf-8")
    assert cmd.build([str(path)])["inputs"][0]["kind"] == "llms_txt"
    json_path = path.with_suffix(".json")
    json_path.write_text('["incomplete"', encoding="utf-8")
    with pytest.raises(InputError, match="not valid JSON"):
        cmd.build([str(json_path)])


def test_build_keeps_llms_txt_path_in_finding_identity(tmp_path):
    paths = [tmp_path / part / "llms.txt" for part in ("api", "sdk")]
    for path in paths:
        path.parent.mkdir()
        path.write_text("prose only\n", encoding="utf-8")
    items = cmd.build(list(map(str, paths)))["items"]
    assert {item["target"] for item in items} == set(map(str, paths))
    assert len({item["source_key"] for item in items}) == len(items)


@pytest.mark.parametrize("paired", [{"strict": NOVELTY["paired"]["lenient"]},
                                     {"lenient": {"n_paired": 4, "degraded": 0}}])
def test_incomplete_novelty_is_an_input_error(tmp_path, paired):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({**NOVELTY, "paired": paired}), encoding="utf-8")
    with pytest.raises(InputError, match=r"evidence\.json: novelty.*(object|required)"):
        cmd.build([str(path)])


def test_input_with_unpaired_unicode_surrogate_names_the_path(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({**NOVELTY, "model": "\ud800"}), encoding="utf-8")
    with pytest.raises(InputError, match="evidence.json: JSON contains an unpaired Unicode surrogate"):
        cmd.build([str(path)])
