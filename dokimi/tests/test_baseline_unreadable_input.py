# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`baseline save` and `baseline compare` exit with a message, not a traceback, on a bad eval file."""

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


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
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


@pytest.mark.parametrize("content", ["{truncated", '{"baselines": null}', '{"baselines": "x"}'])
def test_a_corrupt_manifest_exits_with_a_message_in_every_subcommand(run, tmp_path, capsys, content):
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
        assert str(manifest) in capsys.readouterr().err
    assert sorted(p.name for p in manifest.parent.iterdir()) == ["manifest.json"]


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
