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
           "paired": {"lenient": {"degraded": 2, "redundant": 0, "still_missing": 0, "n_paired": 4}}}


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
    with pytest.raises(InputError, match="not a novelty artifact"):
        cmd.build([str(other)])


def test_json_output_is_a_valid_opportunities_v1_file(inputs, tmp_path):
    out = tmp_path / "opp.json"
    subprocess.run([sys.executable, "cli.py", "report", "opportunities", *inputs,
                    "--format", "json", "--out", str(out)], check=True)
    doc = json.loads(out.read_text(encoding="utf-8"))
    validate_artifact("opportunities", doc)
    assert doc["schema_version"] == "opportunities.v1"
    assert doc["items"][0]["rank"] == 1 and doc["items"][0]["quadrant"] == "act_now"


def test_markdown_honours_top(inputs):
    proc = subprocess.run([sys.executable, "cli.py", "report", "opportunities", *inputs, "--top", "2"],
                          check=True, capture_output=True, text=True)
    rows = [line for line in proc.stdout.splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert [r.split("|")[1].strip() for r in rows] == ["1", "2"]


def test_malformed_json_is_an_input_error(tmp_path):
    bad = tmp_path / "n.json"
    bad.write_text('{"paired": ', encoding="utf-8")
    with pytest.raises(InputError, match="not valid JSON"):
        cmd.build([str(bad)])
