# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Reading trigger sets back: skill-reach, skill-creator / agentskills.io, dokimi questions.

Each reader is held to the writer of the same format in ``dokimi.external.trigger_set``:
what dokimi writes, it reads back to the same labels.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external import trigger_set as ts
from dokimi.questions.artifact import load_questions
from dokimi.skills import discover_skills
from dokimi.task_formats import detect


def _write_skill(root: Path, name: str, description: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: \"{description}\"\n---\n# {name}\n",
                                encoding="utf-8")


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    _write_skill(root, "linux-perf", "Profile Linux programs with perf: hotspots, IPC, cache misses.")
    _write_skill(root, "dpnp-io", "Read and write dpnp arrays to files on Intel GPUs.")
    _write_skill(root, "dpnp-memory", "Place dpnp arrays on devices and size memory.")
    return root


@pytest.fixture
def queries(catalog):
    return ts.build_trigger_set([
        ([{"id": "p", "text": "my program is slow, where is the time going"}], "linux-perf"),
        ([{"id": "f", "text": "save this array to disk", "skills": ["dpnp-io", "dpnp-memory"]}],
         "@skills"),
        ([{"id": "n", "text": "write a haiku"}], "none"),
        ([{"id": "u", "text": "unlabelled", "metadata": {}}], "@metadata.skill"),
    ], discover_skills(catalog))


def _labels(qs):
    return [(q.id, q.text, q.expected_skill, q.labelled) for q in qs]


def test_reach_round_trip_keeps_ids_texts_and_acceptable_sets(queries):
    back = ts.from_reach(ts.to_reach(queries, catalog_id="c"))
    assert _labels(back) == _labels(ts.labelled(queries))
    assert ts.detect_trigger_format(ts.to_reach(queries)) == ts.FORMAT_REACH


def test_skill_creator_round_trip_is_identity_on_its_own_rows(queries):
    rows = ts.to_skill_creator(queries, "linux-perf")
    back = ts.from_skill_creator(rows, "linux-perf")
    assert ts.to_skill_creator(back, "linux-perf") == rows
    assert [q.expected_skill for q in back] == ["linux-perf", None, None]
    assert [q.id for q in back] == ["sc-001", "sc-002", "sc-003"]
    assert ts.detect_trigger_format(rows) == ts.FORMAT_SKILL_CREATOR


def test_questions_round_trip_keeps_unlabelled_and_lint(queries, tmp_path):
    payload = ts.to_questions(queries)
    back = ts.from_questions(payload)
    assert _labels(back) == _labels(queries)
    assert back[0].lint == queries[0].lint
    # Through the stamped artifact the arms command loads, too.
    from dokimi.questions.artifact import save_questions_artifact

    path = tmp_path / "q.json"
    save_questions_artifact(payload, str(path))
    stamped = json.loads(path.read_text(encoding="utf-8"))
    assert _labels(ts.from_questions(stamped)) == _labels(queries)
    assert len(load_questions(path).questions) == 4


def test_reach_reader_matches_skill_reach_defaults_and_refuses_bad_rows():
    qs = ts.from_reach({"queries": [
        {"text": "a", "expected_skill": "x", "acceptable_skills": ["y", "x"]},
        {"query_id": "k", "text": "b", "expected_skill": None, "kind": "out_of_scope"},
    ]})
    assert [(q.id, q.expected_skill) for q in qs] == [("q-001", ["x", "y"]), ("k", None)]
    for bad, msg in [({"text": "a", "expected_skill": None, "acceptable_skills": ["y"]}, "without"),
                     ({"text": "", "expected_skill": "x"}, "no query text"),
                     ({"text": "a", "expected_skill": 3}, "skill name"),
                     ({"text": "a", "expected_skill": ["x"]}, "one name")]:
        with pytest.raises(ValueError, match=msg):
            ts.from_reach({"queries": [bad]})


def test_skill_creator_reader_needs_a_target_and_booleans():
    with pytest.raises(ValueError, match="target"):
        ts.read_trigger_set([{"query": "a", "should_trigger": True}])
    with pytest.raises(ValueError, match="true or false"):
        ts.from_skill_creator([{"query": "a", "should_trigger": "yes"}], "x")


def test_detect_refuses_what_is_not_a_trigger_set():
    for data in ({"evals": []}, [], [{"prompt": "a"}], "text", {"queries": "x"}):
        assert ts.detect_trigger_format(data) is None
    with pytest.raises(ValueError, match="not a trigger set"):
        ts.read_trigger_set({"evals": []})


def _run(argv):
    args = build_parser().parse_args(argv)
    args.func(args)


def test_cli_reads_each_format_back_and_relints_against_the_catalog(tmp_path, catalog, queries,
                                                                   capsys):
    reach = tmp_path / "reach.json"
    reach.write_text(json.dumps(ts.to_reach(queries)), encoding="utf-8")
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps([{"query": "use linux-perf on it", "should_trigger": True}]),
                  encoding="utf-8")
    out = tmp_path / "q.json"
    _run(["catalog", "trigger-set", "--catalog", str(catalog), "--format", "questions",
          "--trigger-set", str(reach), "--trigger-set", f"{sc}=linux-perf", "--out", str(out)])
    rows = load_questions(out).questions
    assert [r.get("expected_skill") for r in rows] == [
        "linux-perf", ["dpnp-io", "dpnp-memory"], None, "linux-perf"]
    assert rows[3]["trigger_lint"]["names_skill"] == ["linux-perf"]  # linted on import
    err = capsys.readouterr().err
    assert "as reach (3 queries)" in err and "rival load counts as a false trigger" in err


def test_cli_read_back_keeps_source_text(tmp_path, catalog):
    src = tmp_path / "rw.json"
    src.write_text(json.dumps({"questions": [{"id": "r1", "question": "rewritten text",
                                              "source_text": "Scenario: original text",
                                              "expected_skill": "linux-perf"}]}), encoding="utf-8")
    out = tmp_path / "q.json"
    _run(["catalog", "trigger-set", "--catalog", str(catalog), "--format", "questions",
          "--trigger-set", str(src), "--out", str(out)])
    assert load_questions(out).questions[0]["source_text"] == "Scenario: original text"
    queries = ts.build_trigger_set([(ts.to_questions(ts.from_questions(json.loads(
        src.read_text(encoding="utf-8"))))["questions"], "@expected_skill")],
        discover_skills(catalog), rewrite=lambda t: "again")
    assert (queries[0].source_text, queries[0].text) == ("Scenario: original text", "again")


def test_cli_reach_read_back_keeps_kind_and_notes(tmp_path, catalog):
    src = tmp_path / "nn.json"
    src.write_text(json.dumps({"queries": [{"id": "nn", "text": "save a numpy array to csv",
                                            "expected_skill": "linux-perf", "kind": "neighbor_negative",
                                            "notes": "near dpnp-io"}]}), encoding="utf-8")
    out, q_out = tmp_path / "r.json", tmp_path / "q.json"
    base = ["catalog", "trigger-set", "--catalog", str(catalog), "--trigger-set", str(src)]
    _run(base + ["--out", str(out)])
    row = json.loads(out.read_text(encoding="utf-8"))["queries"][0]
    assert (row["kind"], row["notes"]) == ("neighbor_negative", "near dpnp-io")
    _run(base + ["--format", "questions", "--out", str(q_out)])
    assert ts.REACH_FIELDS not in load_questions(q_out).questions[0]


def test_cli_refuses_bad_trigger_set_specs(tmp_path, catalog):
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps([{"query": "a", "should_trigger": True}]), encoding="utf-8")
    reach = tmp_path / "r.json"
    reach.write_text(json.dumps({"queries": [{"id": "1", "text": "a", "expected_skill": None}]}),
                     encoding="utf-8")
    base = ["catalog", "trigger-set", "--catalog", str(catalog), "--out", str(tmp_path / "o.json")]
    with pytest.raises(SystemExit, match="not a skill in --catalog"):
        _run(base + ["--trigger-set", f"{sc}=nope"])
    with pytest.raises(SystemExit, match="needs the target"):
        _run(base + ["--trigger-set", str(sc)])
    with pytest.raises(SystemExit, match="skill-creator sets only"):
        _run(base + ["--trigger-set", f"{reach}=linux-perf"])
    with pytest.raises(SystemExit, match="--questions PATH=LABEL or --trigger-set"):
        _run(base)


def test_arms_points_an_external_trigger_set_at_the_converter(tmp_path):
    from dokimi.commands.arms import _load_questions

    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps([{"query": "a", "should_trigger": True}]), encoding="utf-8")
    with pytest.raises(ValueError, match=r"skill-creator trigger set.*--trigger-set .*=<skill>"):
        _load_questions(str(sc))


def test_task_formats_name_the_reader_as_consumer(tmp_path, queries):
    reach = tmp_path / "reach.json"
    reach.write_text(json.dumps(ts.to_reach(queries)), encoding="utf-8")
    sc = tmp_path / "sc.json"
    sc.write_text(json.dumps(ts.to_skill_creator(queries, "linux-perf")), encoding="utf-8")
    for path in (reach, sc):
        report = detect(path)
        assert report.consumers == ["catalog trigger-set"], report
