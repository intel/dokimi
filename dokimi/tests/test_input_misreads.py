# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Three readers refuse an input they would otherwise misread (the Low items of #687).

- products.yaml: a null ``products:`` no longer hides the legacy ``libraries:`` mapping.
- A question set whose records share an id is refused, read or written: questions.v2 declares
  ``id`` "Identifier, unique within the set" (``dokimi/schemas/questions.v2.json``).
- A ``DOKIMI_OP`` marker or operations.jsonl line that is JSON but not the object
  docs/coding-harnesses.md "Operation Telemetry" asks for is a ``log_parse_error``, not a crash.

https://github.com/intel/dokimi/issues/687
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pytest
import yaml

import dokimi.eval
from dokimi import cli
from dokimi.artifacts import ArtifactValidationError
from dokimi.commands import answers
from dokimi.harnesses.operations import load_operations
from dokimi.questions.artifact import load_questions, save_questions_artifact
from dokimi.registry import ProductRegistry

from tests.cli_refusal import refusal


def _registry(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "products.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_a_null_products_key_falls_through_to_libraries(tmp_path):
    path = _registry(tmp_path, {"products": None, "libraries": {"onetbb": {"name": "oneTBB"}}})

    assert ProductRegistry(path=path).keys() == ["onetbb"]


def test_a_registry_with_both_keys_names_the_file_and_the_ignored_one(tmp_path, caplog):
    path = _registry(tmp_path, {"products": {"onemkl": {"name": "oneMKL"}},
                                "libraries": {"onetbb": {"name": "oneTBB"}}})

    with caplog.at_level(logging.WARNING, logger="dokimi.registry"):
        keys = ProductRegistry(path=path).keys()

    assert keys == ["onemkl"]
    assert f"Registry {path} has both 'products' and 'libraries': 'libraries' is ignored" in caplog.text


@pytest.mark.parametrize("data,warning", [
    ({"products": None}, "'products' must be a mapping, got NoneType"),
    ({"libraries": ["onetbb"]}, "'libraries' must be a mapping, got list"),
    ({"products": None, "libraries": "onetbb"}, "'libraries' must be a mapping, got str"),
], ids=["null-products", "list-libraries", "str-libraries-under-null-products"])
def test_a_registry_key_that_is_not_a_mapping_is_named_with_the_file(tmp_path, caplog, data, warning):
    path = _registry(tmp_path, data)

    with caplog.at_level(logging.WARNING, logger="dokimi.registry"):
        keys = ProductRegistry(path=path).keys()

    assert keys == []
    assert f"Registry {path}: {warning}" in caplog.text


@pytest.mark.parametrize("records,ids", [
    ([{"question_id": "a", "text": "x"}, {"question_id": "a", "text": "y"},
      {"question_id": "b", "text": "z"}], "'a'"),
    ([{"id": "b", "question": "x"}, {"id": "a", "question": "y"}, {"id": "b", "question": "z"},
      {"id": "a", "question": "w"}], "'a', 'b'"),
    ([{"id": 1, "question": "x"}, {"id": "1", "question": "y"}], "'1'"),
    ([{"id": f"q{i}", "question": "x"} for i in range(7)] * 2, "'q0', 'q1', 'q2', 'q3', 'q4' and 2 more;"),
], ids=["v1-question_id", "v2-id", "int-and-str", "seven-shown-as-five"])
def test_a_set_whose_records_share_an_id_is_refused_naming_the_ids(tmp_path, records, ids):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": records}), encoding="utf-8")

    with pytest.raises(ArtifactValidationError) as exc_info:
        load_questions(path)

    assert str(exc_info.value).startswith(f"duplicate question ids in {path}: {ids}")


def test_refine_still_reads_a_set_with_shared_ids(tmp_path):
    """``questions refine`` repairs sets, so ``validate=False`` reads one as written."""
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "a", "question": "x"}, {"id": "a", "question": "y"}]),
                    encoding="utf-8")

    assert [q["question"] for q in load_questions(path, validate=False).questions] == ["x", "y"]


SHARED = [{"id": "a", "question": "How do I configure the parallel reduction in oneTBB for floats?"},
          {"id": "a", "question": "What is the difference between task_arena and task_group in oneTBB?"}]


def test_a_set_with_shared_ids_is_not_written_but_kept_as_rejected(tmp_path):
    out = tmp_path / "q.json"

    with pytest.raises(ArtifactValidationError, match="duplicate question ids 'a'"):
        save_questions_artifact({"questions": SHARED}, out)

    assert not out.exists()
    assert [q["question"] for q in json.loads((tmp_path / "q.json.rejected").read_text())["questions"]] == [
        q["question"] for q in SHARED]


@pytest.mark.parametrize("extra,code", [((), 1), (("--fill-gaps",), 2)], ids=["refine", "fill-gaps"])
def test_refine_writes_no_set_with_shared_ids(tmp_path, monkeypatch, capsys, extra, code):
    """With ``--fill-gaps`` the set is refused before the first paid call."""
    path, out = tmp_path / "q.json", tmp_path / "out.json"
    path.write_text(json.dumps(SHARED), encoding="utf-8")
    calls = []
    monkeypatch.setattr("dokimi.questions.refiner.llm_call", lambda *a, **k: calls.append(a) or "[]")
    monkeypatch.setattr("sys.argv", ["dokimi", "eval", "questions", "refine", "--product", "oneTBB",
                                     "--questions", str(path), "--output", str(out), *extra])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == code
    assert "duplicate question ids 'a'" in capsys.readouterr().err
    assert not out.exists() and calls == []


def test_a_second_gap_fill_does_not_reuse_the_first_ones_ids(monkeypatch):
    from dokimi.questions.refiner import GapFiller

    monkeypatch.setattr("dokimi.questions.refiner.llm_call", lambda *a, **k: json.dumps(["Q1?", "Q2?"]))
    first = [{"id": "gap_advanced_000", "question": "Q0?", "difficulty": "advanced"},
             {"id": "gap_advanced_002", "question": "Q9?", "difficulty": "advanced"}]

    new, _ = GapFiller("oneTBB").fill(first, gaps={"advanced": 2})

    assert [q["id"] for q in new] == ["gap_advanced_001", "gap_advanced_003"]


def test_a_merge_renumbers_a_generated_id_a_kept_record_has():
    from dokimi.questions.normalizer import renumber_taken

    kept = [{"id": "q_001", "question": "Old?"}, {"id": "q_000", "question": "Golden?"}]
    new = [{"id": "q_001", "question": "New?"}, {"id": "q_002", "question": "Other?"}]

    assert [q["id"] for q in renumber_taken(new, kept)] == ["q_003", "q_002"]
    assert new[0]["id"] == "q_001"


@pytest.mark.parametrize("text,reason", [
    (json.dumps({"questions": [{"id": "q_001", "question": "A?"}, {"id": "q_001", "question": "B?"}]}),
     "duplicate question ids in {cache}: 'q_001'"),
    (json.dumps({"questions": [{"id": "q_001", "question": ""}]}), "{cache} is not a usable question set"),
    ('{"questions": [', "Expecting value"),
], ids=["shared-ids", "schema", "truncated"])
def test_benchmark_run_on_an_unusable_cache_exits_2_naming_force_regen(tmp_path, monkeypatch, capsys, text, reason):
    """A cache main's ``--force-regen`` wrote with two ``q_001``, or one cut short: an input error, not a traceback."""
    cache = tmp_path / "questions" / "onetbb.json"
    cache.parent.mkdir()
    cache.write_text(text, encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "benchmark", "run", "--library", "onetbb",
                                     "--out-dir", str(tmp_path)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    err = capsys.readouterr().err
    named = "" if "{cache}" in reason else f" {cache}"
    assert exc_info.value.code == 2 and "Traceback" not in err
    assert f"Error: the cached question set of an earlier run{named} cannot be used: " in err
    assert reason.format(cache=cache) in err
    assert "(fix or delete it to generate a new set; benchmark run: --force-regen)" in err


def test_benchmark_run_on_a_golden_set_with_shared_ids_exits_2(tmp_path, monkeypatch, capsys):
    """``benchmark preflight`` refuses this set (``_check_golden_set``); ``run`` refuses it too."""
    root = tmp_path / "root"
    root.mkdir()
    golden = root / "onetbb_golden.json"
    golden.write_text(json.dumps([{"id": "q_001", "question": "A?"}, {"id": "q_001", "question": "B?"}]),
                      encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["dokimi", "benchmark", "run", "--library", "onetbb",
                                     "--out-dir", str(tmp_path / "out"), "--questions-root", str(root)])

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    err = capsys.readouterr().err
    assert exc_info.value.code == 2 and "Traceback" not in err
    assert f"Error: the golden set cannot be used: duplicate question ids in {golden}: 'q_001'" in err


def test_answers_generate_exits_2_on_shared_ids_before_any_answer(tmp_path, monkeypatch):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"question_id": "a", "text": "x"}, {"question_id": "a", "text": "y"}]),
                    encoding="utf-8")
    monkeypatch.setattr("dokimi.config.runtime_config_path", lambda: tmp_path / "absent.yaml")
    built = []
    monkeypatch.setattr(dokimi.eval, "Answerer", lambda **kwargs: built.append(kwargs))
    args = argparse.Namespace(product="Lib", questions=str(path), top_k=None, model="m", provider="p",
                              rerank_threshold=0.5, debug_retrieval=False, max_tokens=100, concurrency=1,
                              output=str(tmp_path / "out.json"), run_id=None, doc_source="none",
                              context7_id=None)

    with refusal() as exc:
        answers.cmd_answers_generate(args)

    assert exc.code == 2
    assert f"Error: duplicate question ids in {path}: 'a'" in exc.err
    assert built == []


@pytest.mark.parametrize("payload,kind", [
    ("[1, 2]", "list"), ("5", "int"), ("null", "NoneType"), ('"edit"', "str"),
])
def test_a_marker_that_is_not_an_object_is_a_parse_error(payload, kind):
    ops = load_operations(None, f'DOKIMI_OP {payload}\nDOKIMI_OP {{"type":"tool","name":"edit"}}', "")

    assert [(op.type, op.name, op.status) for op in ops] == [
        ("log_parse_error", "marker", "error"), ("tool", "edit", "ok")]
    assert ops[0].metadata == {"error": f"expected a JSON object, got {kind}"}


@pytest.mark.parametrize("payload,prefix,reason", [
    ("1" * 5000, "JSON this reader cannot hold: ", "4300"),
    ("{", "not JSON: ", "Expecting"), ("", "not JSON: ", "Expecting value"),
], ids=["int-over-4300-digits", "unparsable", "empty"])
def test_a_marker_json_cannot_hold_is_a_parse_error_that_says_why(payload, prefix, reason):
    ops = load_operations(None, f"DOKIMI_OP {payload}", "")

    assert [(op.type, op.name, op.status) for op in ops] == [("log_parse_error", "marker", "error")]
    assert ops[0].metadata["error"].startswith(prefix) and reason in ops[0].metadata["error"]


def test_a_marker_nested_past_the_decoder_s_limit_is_a_parse_error():
    """Where the limit falls, and what the decoder then says, depends on the Python and its stack (#820)."""
    ops = load_operations(None, "DOKIMI_OP " + "[" * 100000 + "]" * 100000, "")

    assert [(op.type, op.name, op.status) for op in ops] == [("log_parse_error", "marker", "error")]


def test_a_recursion_error_in_the_decoder_is_json_this_reader_cannot_hold(monkeypatch):
    def too_deep(text):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr("dokimi.harnesses.operations.json.loads", too_deep)
    ops = load_operations(None, 'DOKIMI_OP {"type":"tool"}', "")

    assert [op.metadata for op in ops] == [
        {"error": "JSON this reader cannot hold: maximum recursion depth exceeded while decoding a JSON array"}]


def test_an_elapsed_too_large_for_a_float_is_zero():
    ops = load_operations(None, 'DOKIMI_OP {"type":"tool","name":"edit","elapsed_sec":1' + "0" * 400 + "}", "")

    assert [(op.type, op.name, op.elapsed_sec) for op in ops] == [("tool", "edit", 0.0)]


def test_an_operations_line_that_is_not_an_object_names_the_file(tmp_path):
    (tmp_path / "operations.jsonl").write_text('["edit"]\n{"type":"subagent","name":"reviewer"}\n',
                                               encoding="utf-8")

    ops = load_operations(tmp_path, "", "")

    assert [(op.type, op.name, op.status) for op in ops] == [
        ("log_parse_error", "operations.jsonl", "error"), ("subagent", "reviewer", "ok")]
    assert ops[0].metadata == {"error": "expected a JSON object, got list"}
