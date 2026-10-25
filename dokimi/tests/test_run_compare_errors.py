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
