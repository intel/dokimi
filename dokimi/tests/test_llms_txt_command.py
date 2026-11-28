# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``dokimi docs llms-txt``: the CLI over :mod:`dokimi.llms_txt`."""

from __future__ import annotations

import errno
import json
import sys
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.artifacts import schema_errors

GOOD = """# Tool

> Does one thing for agents.

## When to use

- [Guide](https://x/guide): how to start

## When not to use

- [Other](https://x/other): use this instead
"""


def _main(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["dokimi", "docs", "llms-txt", *argv])
    try:
        cli.main()
    except SystemExit as exc:
        return exc.code or 0
    return 0


def test_a_clean_file_passes_with_exit_0(tmp_path, monkeypatch, capsys):
    (tmp_path / "llms.txt").write_text(GOOD, encoding="utf-8")
    assert _main(monkeypatch, [str(tmp_path)]) == 0  # a directory resolves to its llms.txt
    out = capsys.readouterr().out
    assert out.strip().endswith("pass (0 fail, 0 warn)")


def test_a_fail_finding_exits_1_and_prints_its_row(tmp_path, monkeypatch, capsys):
    f = tmp_path / "llms.txt"
    f.write_text("no title here\n", encoding="utf-8")
    assert _main(monkeypatch, [str(f)]) == 1
    out = capsys.readouterr().out
    assert "fail\ttitle\t" in out and "fail\tlinks\t" in out
    assert out.strip().splitlines()[-1].endswith("fail (2 fail, 2 warn)")


def test_warnings_alone_exit_0(tmp_path, monkeypatch, capsys):
    f = tmp_path / "llms.txt"
    f.write_text(GOOD, encoding="utf-8")
    assert _main(monkeypatch, [str(f), "--token-budget", "1"]) == 0
    assert "warn\tsize\t" in capsys.readouterr().out


def test_json_is_a_cli_output_document_with_the_findings(tmp_path, monkeypatch, capsys):
    f = tmp_path / "llms.txt"
    f.write_text("# T\n", encoding="utf-8")
    assert _main(monkeypatch, [str(f), "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "docs llms-txt" and doc["verdict"] == "fail"
    assert (doc["summary"]["fail"], doc["summary"]["warn"]) == (2, 2)
    assert doc["data"]["token_budget"] == 2000 and doc["data"]["tokens"] == 1
    checks = {x["check"] for x in doc["data"]["findings"]}
    assert {"summary", "links", "when_to_use", "when_not_to_use"} <= checks
    assert {x["code"] for x in doc["subjects"][0]["findings"]} == checks
    assert schema_errors("cli_output.v1", doc) == []


def test_a_missing_path_is_an_input_error(tmp_path, monkeypatch, capsys):
    assert _main(monkeypatch, [str(tmp_path)]) == 2
    assert "no such file" in capsys.readouterr().err


def test_a_file_that_is_not_utf8_is_an_input_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "llms.txt").write_bytes(b"\xff\xfe# T\n")
    assert _main(monkeypatch, [str(tmp_path)]) == 2
    message = capsys.readouterr().err
    assert "decode" in message and "Traceback" not in message


def test_a_path_without_search_permission_is_an_input_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "restricted" / "llms.txt"
    stat = Path.stat

    def guarded_stat(target, **kwargs):
        if target == path:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return stat(target, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    assert _main(monkeypatch, [str(path)]) == 2
    message = capsys.readouterr().err
    assert str(path) in message and "Traceback" not in message


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_token_budget_must_be_positive(tmp_path, monkeypatch, bad):
    (tmp_path / "llms.txt").write_text(GOOD, encoding="utf-8")
    assert _main(monkeypatch, [str(tmp_path), "--token-budget", bad]) == 2


def test_json_input_error_uses_the_command_envelope(tmp_path, monkeypatch, capsys):
    assert _main(monkeypatch, [str(tmp_path), "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "docs llms-txt"
    assert schema_errors("cli_output.v1", doc) == []


def test_utf8_bom_does_not_hide_the_title(tmp_path, monkeypatch, capsys):
    path = tmp_path / "llms.txt"
    path.write_text(GOOD, encoding="utf-8-sig")
    assert _main(monkeypatch, [str(path)]) == 0
    assert "(0 fail, 0 warn)" in capsys.readouterr().out
