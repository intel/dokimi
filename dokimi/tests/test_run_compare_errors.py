# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`run` and `compare` name a bad input file and exit 2 instead of ending in a traceback."""
import json

import pytest

from dokimi import cli

SNAPSHOT = {"summary": {"docs": 1, "score": 0.5, "coverage": 0.5, "freshness_lite": 0.5, "readability": 0.5}}


def _main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def _snapshot(path):
    path.write_text(json.dumps(SNAPSHOT), encoding="utf-8")
    return str(path)


def _write(path, content):
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("content", [None, "weights: [", "- a\n- b\n", "weights: {}\n", b"\xff\xfe", "version: 1\n"],
                         ids=["missing", "malformed", "not-a-mapping", "missing-fields", "not-utf8", "schema-invalid"])
def test_run_exits_2_on_a_bad_spec(tmp_path, monkeypatch, capsys, content):
    spec = tmp_path / "spec.yaml"
    _write(spec, content)
    code = _main(monkeypatch, "run", "--root", str(tmp_path), "--spec", str(spec),
                 "--out-json", str(tmp_path / "o.json"), "--out-md", str(tmp_path / "o.md"))
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(spec) in err
    assert not (tmp_path / "o.json").exists()


@pytest.mark.parametrize("which", ["--base", "--candidate"])
@pytest.mark.parametrize("content", [None, "{not json", b"\xff\xfe", "[1]", "{}"],
                         ids=["missing", "malformed", "not-utf8", "not-an-object", "no-summary"])
def test_compare_exits_2_on_a_bad_snapshot(tmp_path, monkeypatch, capsys, which, content):
    bad = tmp_path / "bad.json"
    _write(bad, content)
    good = _snapshot(tmp_path / "good.json")
    pair = {"--base": good, "--candidate": good, which: str(bad)}
    code = _main(monkeypatch, "compare", "--base", pair["--base"], "--candidate", pair["--candidate"],
                 "--out-json", str(tmp_path / "c.json"), "--out-md", str(tmp_path / "c.md"))
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err
    assert not (tmp_path / "c.json").exists()


@pytest.mark.parametrize("content", [None, "a: [", "- a\n"], ids=["missing", "malformed", "not-a-mapping"])
def test_compare_exits_2_on_a_bad_spec(tmp_path, monkeypatch, capsys, content):
    spec = tmp_path / "spec.yaml"
    _write(spec, content)
    good = _snapshot(tmp_path / "good.json")
    code = _main(monkeypatch, "compare", "--base", good, "--candidate", good, "--spec", str(spec),
                 "--out-json", str(tmp_path / "c.json"), "--out-md", str(tmp_path / "c.md"))
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(spec) in err


_SPEC_MIN_DOCS = (
    "weights:\n  coverage: 0.4\n  freshness_lite: 0.3\n  readability: 0.3\n"
    "metrics:\n  freshness_lite:\n    max_age_days: 365\n  readability:\n    grade_max: 12\n"
    "golden_manifest:\n  include: ['docs/**/*.md']\n  exclude: []\n  min_docs: 3\n  max_docs: 10\n"
)


def test_run_exits_2_when_the_root_has_fewer_docs_than_min_docs(tmp_path, monkeypatch, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\ntext\n", encoding="utf-8")
    spec = tmp_path / "spec.yaml"
    spec.write_text(_SPEC_MIN_DOCS, encoding="utf-8")
    code = _main(monkeypatch, "run", "--root", str(tmp_path), "--spec", str(spec),
                 "--out-json", str(tmp_path / "o.json"), "--out-md", str(tmp_path / "o.md"))
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and "min_docs" in err and "Traceback" not in err


@pytest.mark.parametrize("which", ["--base", "--candidate"])
@pytest.mark.parametrize("field,value", [("docs", "1"), ("score", None), ("coverage", True)])
def test_compare_exits_2_on_a_non_numeric_summary_value(tmp_path, monkeypatch, capsys, which, field, value):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"summary": {**SNAPSHOT["summary"], field: value}}), encoding="utf-8")
    good = _snapshot(tmp_path / "good.json")
    pair = {"--base": good, "--candidate": good, which: str(bad)}
    code = _main(monkeypatch, "compare", "--base", pair["--base"], "--candidate", pair["--candidate"],
                 "--out-json", str(tmp_path / "c.json"), "--out-md", str(tmp_path / "c.md"))
    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err and f"`{field}`" in err


def _blocked(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a regular file: nothing can be created below it
    return blocker


def test_compare_exits_1_when_an_output_cannot_be_written(tmp_path, monkeypatch, capsys):
    good = _snapshot(tmp_path / "good.json")
    target = _blocked(tmp_path) / "c.json"
    code = _main(monkeypatch, "compare", "--base", good, "--candidate", good,
                 "--out-json", str(target), "--out-md", str(tmp_path / "c.md"))
    assert code == 1
    err = capsys.readouterr().err
    assert f"Error: cannot write {target}" in err and "Traceback" not in err


@pytest.mark.parametrize("which", ["json", "md"])
def test_run_exits_1_when_an_output_cannot_be_written(tmp_path, monkeypatch, capsys, which):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\ntext\n", encoding="utf-8")
    spec = tmp_path / "spec.yaml"
    spec.write_text(_SPEC_MIN_DOCS.replace("min_docs: 3", "min_docs: 1"), encoding="utf-8")
    blocked = _blocked(tmp_path) / "out"
    outs = {"json": str(tmp_path / "o.json"), "md": str(tmp_path / "o.md")}
    outs[which] = str(blocked)
    code = _main(monkeypatch, "run", "--root", str(tmp_path), "--spec", str(spec),
                 "--out-json", outs["json"], "--out-md", outs["md"])
    assert code == 1
    err = capsys.readouterr().err
    assert f"Error: cannot write {blocked}" in err and "Traceback" not in err
