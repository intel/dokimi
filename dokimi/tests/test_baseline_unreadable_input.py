# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`baseline save`, `list` and `compare` exit with a message, not a traceback.

Covers an unreadable or malformed eval file, saved baseline or manifest.
"""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.commands import baseline as baseline_mod

_EVAL = {"evaluations": [{"question_id": "q1", "with_docs": {"aggregate": 7.0},
                          "without_docs": {"aggregate": 5.0}, "delta": 2.0}]}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(baseline_mod, "BASELINES_DIR", tmp_path / "baselines")

    def _run(*argv: str) -> None:
        args = build_parser().parse_args(["baseline", *argv])
        args.func(args)

    return _run


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "null"])
def test_save_exits_1_on_an_invalid_eval_file(run, tmp_path, capsys, content):
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run("save", "--from-eval", str(bad), "--name", "b")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(bad) in err
    assert not (tmp_path / "baselines" / "b.json").exists()


@pytest.mark.parametrize("content, type_name", [
    ("null", "null"),
    ("[1, 2]", "array"),
    ('"x"', "string"),
    ("5", "number"),
    ("true", "boolean"),
])
def test_a_non_object_eval_file_is_reported_by_its_json_type(run, tmp_path, capsys, content, type_name):
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit):
        run("save", "--from-eval", str(bad), "--name", "b")
    assert f"got {type_name}" in capsys.readouterr().err


def test_save_exits_1_on_an_eval_file_that_is_not_utf8(run, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(SystemExit) as exc:
        run("save", "--from-eval", str(bad), "--name", "b")
    assert exc.value.code == 1
    assert str(bad) in capsys.readouterr().err


def test_save_exits_1_on_a_directory_given_as_the_eval_file(run, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        run("save", "--from-eval", str(tmp_path), "--name", "b")
    assert exc.value.code == 1
    assert str(tmp_path) in capsys.readouterr().err


def test_compare_exits_2_on_a_missing_eval_file(run, tmp_path, capsys):
    missing = tmp_path / "missing.json"
    with pytest.raises(SystemExit) as exc:
        run("compare", "--eval", str(missing))
    assert exc.value.code == 2  # 1 is the --strict verdict
    assert str(missing) in capsys.readouterr().err


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "null"])
def test_compare_exits_2_on_an_invalid_eval_file(run, tmp_path, capsys, content):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_EVAL), encoding="utf-8")
    run("save", "--from-eval", str(good), "--name", "b")
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run("compare", "--baseline", "b", "--eval", str(bad))
    assert exc.value.code == 2  # 1 is the --strict verdict
    assert str(bad) in capsys.readouterr().err


@pytest.mark.parametrize("content, type_name", [("{truncated", None), ('{"baselines": null}', "null"),
                                                 ('{"baselines": "x"}', "string")])
def test_a_corrupt_manifest_exits_with_a_message_in_every_subcommand(run, tmp_path, capsys, content, type_name):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_EVAL), encoding="utf-8")
    manifest = tmp_path / "baselines" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(content, encoding="utf-8")
    for argv, code in ((("save", "--from-eval", str(good)), 1), (("list",), 1),
                       (("compare", "--eval", str(good)), 2)):
        with pytest.raises(SystemExit) as exc:
            run(*argv)
        assert exc.value.code == code
        err = capsys.readouterr().err
        assert str(manifest) in err
        if type_name:
            assert f"got {type_name}" in err
    assert sorted(p.name for p in manifest.parent.iterdir()) == ["manifest.json"]


def test_save_reports_a_corrupt_manifest_before_an_invalid_eval_file(run, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    manifest = tmp_path / "baselines" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{truncated", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run("save", "--from-eval", str(bad), "--name", "b")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(manifest) in err
    assert str(bad) not in err


_GOOD_ENTRY = '{"name": "ok", "path": "ok.json"}'


@pytest.mark.parametrize("entry", [
    '"x"',
    "5",
    "null",
    '{"path": "p.json"}',
    '{"name": "n"}',
    '{"name": "n", "path": 5}',
    '{"name": "n", "path": "p.json", "summary": 5}',
])
def test_a_manifest_entry_of_the_wrong_shape_exits_with_a_message_in_every_subcommand(run, tmp_path, capsys, entry):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_EVAL), encoding="utf-8")
    manifest = tmp_path / "baselines" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"baselines": [%s, %s]}' % (_GOOD_ENTRY, entry), encoding="utf-8")
    for argv, code in ((("save", "--from-eval", str(good)), 1), (("list",), 1),
                       (("compare", "--eval", str(good)), 2)):
        with pytest.raises(SystemExit) as exc:
            run(*argv)
        assert exc.value.code == code
        err = capsys.readouterr().err
        assert str(manifest) in err
        assert "baselines[1]" in err
    assert sorted(p.name for p in manifest.parent.iterdir()) == ["manifest.json"]


def test_list_accepts_an_entry_without_a_summary(run, tmp_path, capsys):
    manifest = tmp_path / "baselines" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"baselines": [%s]}' % _GOOD_ENTRY, encoding="utf-8")
    run("list")
    assert "ok" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [
    '"saved_at": null',
    '"product": ["a"]',
    '"summary": {"n": null}',
    '"summary": {"avg_with": null, "avg_without": null, "avg_delta": null}',
])
def test_list_prints_an_entry_whose_optional_values_are_not_scalars(run, tmp_path, capsys, extra):
    manifest = tmp_path / "baselines" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"baselines": [{"name": "n", "path": "p.json", %s}]}' % extra, encoding="utf-8")
    run("list")
    assert capsys.readouterr().out.splitlines()[-1].startswith("n ")


_MODELS = {"run_metadata": {"answer_provider": "p", "answer_model": "m"},
           "judge_provider": "p", "judge_model": "m"}


@pytest.mark.parametrize("field, role", [
    ({"run_metadata": "x"}, "answer"),
    ({"run_metadata": 5}, "answer"),
    ({"run_metadata": [1]}, "answer"),
    ({"judge_model": "panel(2)", "panel_config": {"models": 5}}, "judge"),
    ({"judge_model": "panel(2)", "panel_config": {"models": [1]}}, "judge"),
    ({"judge_model": "panel(2)", "panel_config": {"models": ["a/x", 5]}}, "judge"),
    ({"judge_model": "panel(2)", "panel_config": "x"}, "judge"),
])
def test_compare_treats_malformed_model_metadata_as_not_recorded(run, tmp_path, capsys, field, role):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({**_EVAL, **_MODELS}), encoding="utf-8")
    run("save", "--from-eval", str(good), "--name", "b")
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({**_EVAL, **_MODELS, **field}), encoding="utf-8")
    capsys.readouterr()
    run("compare", "--baseline", "b", "--eval", str(odd))
    err = capsys.readouterr().err
    assert f"{role} model not recorded in current eval; not compared" in err
    assert err.count("not recorded in") == 1


def test_save_into_a_manifest_without_a_baselines_key_records_the_baseline(run, tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_EVAL), encoding="utf-8")
    manifest = tmp_path / "baselines" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text("{}", encoding="utf-8")
    run("save", "--from-eval", str(good), "--name", "b")
    saved = json.loads(manifest.read_text(encoding="utf-8"))["baselines"]
    assert [b["name"] for b in saved] == ["b"]


def test_compare_exits_2_when_the_saved_baseline_file_is_corrupt(run, tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_EVAL), encoding="utf-8")
    run("save", "--from-eval", str(good), "--name", "b")
    saved = tmp_path / "baselines" / "b.json"
    saved.write_text("{truncated", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run("compare", "--baseline", "b", "--eval", str(good))
    assert exc.value.code == 2
    assert str(saved) in capsys.readouterr().err
