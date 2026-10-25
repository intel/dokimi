# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo report` and `repo compare` exit 1 with a message, not a traceback, on a corrupt cell file."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser


def _batch(tmp_path, name: str, files: dict[str, bytes]):
    batch = tmp_path / name
    for rel, content in files.items():
        path = batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return batch


def _run(*argv: str) -> None:
    args = build_parser().parse_args(["repo", *argv])
    args.func(args)


_CORRUPT = [
    pytest.param(b"{not json", id="not-json"),
    pytest.param(b"\xff\xfe", id="not-utf8"),
    pytest.param(b"[1, 2]", id="not-an-object"),
]


@pytest.mark.parametrize("content", _CORRUPT)
def test_report_exits_1_naming_a_corrupt_result_file(tmp_path, capsys, content):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": content})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(batch / "t__a__m__r1" / "result.json") in err
    assert "Traceback" not in err


@pytest.mark.parametrize("content", _CORRUPT)
def test_compare_exits_1_naming_a_corrupt_result_file(tmp_path, capsys, content):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": content})
    with pytest.raises(SystemExit) as exc:
        _run("compare", str(batch), "--baseline", "a")
    assert exc.value.code == 1
    assert str(batch / "t__a__m__r1" / "result.json") in capsys.readouterr().err


def test_report_exits_1_naming_a_corrupt_unsupported_file(tmp_path, capsys):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/unsupported.json": b"{not json"})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert str(batch / "t__a__m__r1" / "unsupported.json") in capsys.readouterr().err


@pytest.mark.parametrize("content", [b"{not json", b"{}", b'{"cells": 3}', b'{"cells": [1]}'])
def test_report_exits_1_naming_a_corrupt_plan_file(tmp_path, capsys, content):
    batch = _batch(tmp_path, "b1", {"_eval/plan.json": content})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert str(batch / "_eval" / "plan.json") in capsys.readouterr().err


def test_report_still_reads_an_empty_batch(tmp_path, capsys):
    batch = _batch(tmp_path, "b1", {"_eval/plan.json": json.dumps({"cells": []}).encode()})
    _run("report", str(batch), "--json")
    assert json.loads(capsys.readouterr().out)["rows"] == []
