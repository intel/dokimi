# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`arms plugin-delta`, `arms run` and `arms matrix-run`: an unwritable output exits 2 with a message."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser

from test_plugin_delta import _arms_artifact


def _plugin_delta(tmp_path: Path, *out: str) -> None:
    baseline = tmp_path / "baseline.json"
    plugin = tmp_path / "plugin.json"
    baseline.write_text(json.dumps(_arms_artifact()), encoding="utf-8")
    plugin.write_text(json.dumps(_arms_artifact("caveman:full")), encoding="utf-8")
    args = build_parser().parse_args([
        "arms", "plugin-delta", "--baseline-json", str(baseline), "--plugin-json", str(plugin), *out,
    ])
    args.func(args)


def test_unwritable_out_json_exits_2_naming_the_path(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    target = blocker / "delta.json"
    with pytest.raises(SystemExit) as exc:
        _plugin_delta(tmp_path, "--out-json", str(target))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"Error: cannot write {target}" in err
    assert "Traceback" not in err


def test_unwritable_out_md_exits_2_naming_the_path(tmp_path, capsys):
    out_md = tmp_path / "is-a-dir"
    out_md.mkdir()
    with pytest.raises(SystemExit) as exc:
        _plugin_delta(tmp_path, "--out-json", str(tmp_path / "delta.json"), "--out-md", str(out_md))
    assert exc.value.code == 2
    assert f"Error: cannot write {out_md}" in capsys.readouterr().err


def _args(*argv: str):
    return build_parser().parse_args(["arms", *argv])


def _blocker(tmp_path: Path) -> Path:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    return blocker


def test_save_arms_output_exits_2_naming_an_unwritable_json(tmp_path, capsys):
    from dokimi.commands.arms import _save_arms_output

    target = _blocker(tmp_path) / "arms.json"
    with pytest.raises(SystemExit) as exc:
        _save_arms_output({}, target, tmp_path / "arms.md")
    assert exc.value.code == 2
    assert f"Error: cannot write {target}" in capsys.readouterr().err


def test_save_arms_output_exits_2_naming_an_unwritable_report(tmp_path, capsys):
    from dokimi.commands.arms import _save_arms_output

    out_md = tmp_path / "is-a-dir"
    out_md.mkdir()
    output = _arms_artifact()
    with pytest.raises(SystemExit) as exc:
        _save_arms_output(output, tmp_path / "arms.json", out_md)
    assert exc.value.code == 2
    assert f"Error: cannot write {out_md}" in capsys.readouterr().err


def test_matrix_run_exits_2_naming_an_uncreatable_out_dir(tmp_path, capsys):
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [{"name": "c1", "model": "m", "provider": "anthropic", "harness": "arms-runner"}]}), encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps({"questions": [{"id": "q1", "question": "What is x?"}]}), encoding="utf-8")
    out_dir = _blocker(tmp_path) / "out"
    args = _args("matrix-run", "--product", "p", "--questions", str(questions), "--arms", "baseline",
                 "--matrix-cells", str(cells), "--out-dir", str(out_dir))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"Error: cannot write {out_dir / 'cells'}" in err and "Traceback" not in err


def test_plugin_delta_exits_2_on_a_deeply_nested_artifact(tmp_path, capsys):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000, encoding="utf-8")
    plugin = tmp_path / "plugin.json"
    plugin.write_text(json.dumps(_arms_artifact("caveman:full")), encoding="utf-8")
    args = _args("plugin-delta", "--baseline-json", str(deep), "--plugin-json", str(plugin))
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Error computing plugin delta" in err and "Traceback" not in err


def _matrix_run(tmp_path: Path, out_dir: Path, *extra: str):
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [{"name": "c1", "model": "m", "provider": "anthropic", "harness": "arms-runner"}]}), encoding="utf-8")
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps({"questions": [{"id": "q1", "question": "What is x?"}]}), encoding="utf-8")
    return _args("matrix-run", "--product", "p", "--questions", str(questions), "--arms", "baseline",
                 "--matrix-cells", str(cells), "--out-dir", str(out_dir), *extra)


def _stub_matrix(monkeypatch, tmp_path):
    from dokimi.commands import arms

    monkeypatch.setattr(arms, "_prepare_arms", lambda *a, **k: None)
    monkeypatch.setattr(arms, "_probe_setup", lambda *a, **k: None)
    monkeypatch.setattr(arms, "_run_arms", lambda *a, **k: {})
    monkeypatch.setattr(arms, "_save_arms_output", lambda *a, **k: None)
    delta = tmp_path / "out" / "delta.json"
    rollup = {"plugin_deltas": [{"artifact": str(delta), "_artifact_payload": {"d": 1}}]}
    monkeypatch.setattr("dokimi.eval.matrix_rollup.build_matrix_rollup", lambda **k: rollup)
    monkeypatch.setattr("dokimi.eval.matrix_rollup.strip_internal_payloads", lambda r: {"r": 1})
    monkeypatch.setattr("dokimi.report.plugin_delta_report.render_plugin_delta_report", lambda p: "delta")
    monkeypatch.setattr("dokimi.report.matrix_rollup_report.render_matrix_rollup_report", lambda p: "rollup")
    return delta


def _fake_save_artifact(monkeypatch, bad: Path):
    def save(kind, payload, path, *a, **k):
        if Path(path) == bad:
            raise OSError("denied")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("{}", encoding="utf-8")

    monkeypatch.setattr("dokimi.artifacts.save_artifact", save)


@pytest.mark.parametrize("which", ["delta_json", "delta_md", "rollup_json", "rollup_md"])
def test_matrix_run_exits_2_naming_an_unwritable_delta_or_rollup(tmp_path, monkeypatch, capsys, which):
    delta = _stub_matrix(monkeypatch, tmp_path)
    out_dir = tmp_path / "out"
    rollup_json = out_dir / "matrix-rollup.json"
    bad = {"delta_json": delta, "delta_md": delta.with_suffix(".md"),
           "rollup_json": rollup_json, "rollup_md": rollup_json.with_suffix(".md")}[which]
    _fake_save_artifact(monkeypatch, bad)
    if which.endswith("_md"):
        bad.mkdir(parents=True)  # a directory where the report file belongs
    args = _matrix_run(tmp_path, out_dir)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"Error: cannot write {bad}" in err and "Traceback" not in err


@pytest.mark.parametrize("kind", ["directory", "deep"])
def test_arms_run_exits_2_on_an_unreadable_questions_file(tmp_path, capsys, kind):
    questions = tmp_path / "q.json"
    if kind == "directory":
        questions.mkdir()
    else:
        questions.write_text("[" * 100000, encoding="utf-8")
    args = build_parser().parse_args(["arms", "run", "--product", "p", "--arms", "a:spec.yaml",
                                      "--questions", str(questions)])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("Error building arms/plugins:")


@pytest.mark.parametrize("kind", ["directory", "deep"])
def test_matrix_run_exits_2_on_an_unreadable_questions_file(tmp_path, capsys, kind):
    args = _matrix_run(tmp_path, tmp_path / "out")
    questions = Path(args.questions)
    questions.unlink()
    if kind == "directory":
        questions.mkdir()
    else:
        questions.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("Error loading matrix run inputs:")


@pytest.mark.parametrize("error", [PermissionError("denied"), RecursionError("deep")], ids=lambda e: type(e).__name__)
def test_matrix_run_exits_2_when_a_cell_cannot_read_its_inputs(tmp_path, monkeypatch, capsys, error):
    from dokimi.commands import arms

    def build(*a, **k):
        raise error

    monkeypatch.setattr(arms, "_prepare_arms", build)
    args = _matrix_run(tmp_path, tmp_path / "out")
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("Error building matrix cell 'c1':")
