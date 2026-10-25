# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""questions generate: an unreadable --personas or --topics file is a clean exit, not a traceback."""

import json

import pytest

from dokimi import cli

GOOD_PERSONAS = {"personas": [{"name": "dev"}]}


def _generate(monkeypatch, *extra):
    monkeypatch.setattr("sys.argv", ["dokimi", "questions", "generate", "--product", "p", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", '{"personas": "dev"}', "{}"],
                         ids=["missing", "malformed", "not-an-object", "personas-not-a-list", "no-personas"])
def test_generate_exits_1_on_an_unusable_personas_file(tmp_path, monkeypatch, capsys, content):
    bad = tmp_path / "personas.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(bad)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err


@pytest.mark.parametrize("content", [None, "{not json"], ids=["missing", "malformed"])
def test_generate_exits_1_on_an_unreadable_topics_file(tmp_path, monkeypatch, capsys, content):
    personas = tmp_path / "personas.json"
    personas.write_text(json.dumps(GOOD_PERSONAS), encoding="utf-8")
    bad = tmp_path / "topics.json"
    if content is not None:
        bad.write_text(content, encoding="utf-8")
    assert _generate(monkeypatch, "--personas", str(personas), "--topics", str(bad)) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(bad) in err
