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
    proc = subprocess.run([sys.executable, "cli.py", "report", "opportunities", *inputs, "--top-n", "2"],
                          check=True, capture_output=True, text=True)
    rows = [line for line in proc.stdout.splitlines() if line.startswith("| ") and line[2].isdigit()]
    assert [r.split("|")[1].strip() for r in rows] == ["1", "2"]


def test_malformed_json_is_an_input_error(tmp_path):
    bad = tmp_path / "n.json"
    bad.write_text('{"paired": ', encoding="utf-8")
    with pytest.raises(InputError, match="not valid JSON"):
        cmd.build([str(bad)])


@pytest.mark.parametrize("link", ["[Start](start.md)", "[Start][guide]\n\n[guide]: https://example.test"])
def test_markdown_link_at_start_is_not_json(tmp_path, link):
    path = tmp_path / "llms.txt"
    path.write_text(link + "\n", encoding="utf-8-sig")
    assert cmd.build([str(path)])["inputs"][0]["kind"] == "llms_txt"


@pytest.mark.parametrize("data", [b"\xff",
                                  br'{"schema":"repo_static.v1","claims":[{"file":"\ud800.md","line":1,"status":"fail_missing_path"}]}', b'{"schema":"repo_static.v1","claims":null}',
                                  b'{"paired":{"lenient":{"degraded":-0.5,"n_paired":4}}}'])
def test_invalid_input_names_the_path(tmp_path, data):
    path = tmp_path / "evidence.json"
    path.write_bytes(data)
    with pytest.raises(InputError, match="evidence.json"):
        cmd.build([str(path)])


def test_repeated_input_is_read_once(inputs):
    assert cmd.build([*inputs, inputs[-1]]) == cmd.build(inputs)


def test_llms_identity_survives_staging_in_another_directory(tmp_path):
    first, second = (tmp_path / name / "llms.txt" for name in ("run-1", "run-2"))
    for path in (first, second):
        path.parent.mkdir()
        path.write_text("prose only\n", encoding="utf-8")
    a, b = (cmd.build([str(path)]) for path in (first, second))
    assert a["inputs"] != b["inputs"]
    assert a["items"] == b["items"]


def test_repo_static_keys_distinguish_inputs_and_follow_explicit_repository(tmp_path):
    paths = [tmp_path / name / 'static.json' for name in ('a', 'b')]
    for path in paths:
        path.parent.mkdir()
        path.write_text(json.dumps(STATIC), encoding='utf-8')
    keys = [item['source_key'] for item in cmd.build(list(map(str, paths)))['items']]
    assert len(set(keys)) == 2
    named = []
    for path in paths:
        output = subprocess.check_output([sys.executable, 'cli.py', 'report', 'opportunities', str(path),
                                          '--format', 'json', '--repository', 'acme/example'], text=True)
        named.append(json.loads(output)['items'][0]['source_key'])
    assert named[0] == named[1]
