# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`evaluate`: a bad --custom-questions file, a bad --questions-from file or a bad --out-dir ends with an `Error:` message and no traceback.

A missing, directory, unreadable, empty, non-JSON or non-UTF-8 --custom-questions file and an unwritable --out-dir exit 2
(all but the missing file are also shown to leave no output directory). An unparseable --questions-from file raises `InputError`
naming that flag at construction, and `load_questions_payload` names a flag only when it is given one.
A named pipe given to either flag is not parsed up front.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from tests.cli_refusal import refusal

from dokimi.cli import build_parser
from dokimi.orchestrator.pipeline import EvaluationPipeline, load_questions_payload
from dokimi.utils import InputError


def _evaluate(*extra: str):
    args = build_parser().parse_args(["evaluate", "--product", "p", "--description", "d", *extra])
    args.func(args)


def test_a_missing_custom_questions_file_exits_2_with_a_message(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    with refusal() as out:
        _evaluate("--out-dir", str(tmp_path / "out"), "--custom-questions", str(missing))
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions path does not exist: {missing}" in err
    assert "Traceback" not in err


def test_a_directory_as_custom_questions_exits_2_before_any_output_directory(tmp_path, capsys):
    out_dir = tmp_path / "out dir"
    with refusal() as out:
        _evaluate("--out-dir", str(out_dir), "--custom-questions", str(tmp_path))
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions {tmp_path}: not a file" in err
    assert "Traceback" not in err
    assert not out_dir.exists()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions")
def test_an_unreadable_custom_questions_file_exits_2_before_any_output_directory(tmp_path, capsys):
    locked, out_dir = tmp_path / "q.json", tmp_path / "out"
    locked.write_text("[]", encoding="utf-8")
    locked.chmod(0)
    try:
        with refusal() as out:
            _evaluate("--out-dir", str(out_dir), "--custom-questions", str(locked))
    finally:
        locked.chmod(0o644)
    assert out.code == 2
    err = capsys.readouterr().err
    assert f"Error: --custom-questions {locked}: Permission denied" in err
    assert "Traceback" not in err
    assert not out_dir.exists()


@pytest.mark.parametrize("content", [b"", b"not json", b"\xff\xfe\x00{"], ids=["empty", "not-json", "not-utf8"])
def test_an_unparseable_custom_questions_file_exits_2_before_any_output_directory(tmp_path, capsys, content):
    bad, out_dir = tmp_path / "q.md", tmp_path / "out"
    bad.write_bytes(content)
    with refusal() as out:
        _evaluate("--out-dir", str(out_dir), "--custom-questions", str(bad))
    assert out.code == 2
    err = capsys.readouterr().err
    assert err.startswith(f"Error: --custom-questions {bad}: ")
    assert "Traceback" not in err
    assert not out_dir.exists()


def _pipeline(tmp_path, bad, out="out", **kw):
    return EvaluationPipeline(product="Demo", product_key="demo", output_dir=tmp_path / out,
                              description="A demo product", custom_questions_path=bad, **kw)


def test_a_custom_questions_file_the_run_never_reads_does_not_stop_it(tmp_path):
    """A cached re-run and a questions_from run reuse a set without reading --custom-questions."""
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"")
    good = tmp_path / "good.json"
    good.write_text("[]", encoding="utf-8")
    cached = tmp_path / "out" / "questions" / "demo.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("[]", encoding="utf-8")

    _pipeline(tmp_path, bad)  # cached, no force_regen
    _pipeline(tmp_path, bad, out="fresh", questions_from=good)  # no cache here
    with pytest.raises(InputError, match=rf"^--custom-questions {re.escape(str(bad))}: "):
        _pipeline(tmp_path, bad, force_regen=True)  # the run regenerates, so it reads the file


def test_a_missing_or_directory_custom_questions_path_stops_a_run_that_never_reads_it(tmp_path):
    """Only the parse is skipped for a cached or questions_from run: the file-type checks run every time."""
    good = tmp_path / "good.json"
    good.write_text("[]", encoding="utf-8")
    cached = tmp_path / "out" / "questions" / "demo.json"
    cached.parent.mkdir(parents=True)
    cached.write_text("[]", encoding="utf-8")
    (tmp_path / "adir").mkdir()

    with pytest.raises(FileNotFoundError, match="does not exist"):
        _pipeline(tmp_path, tmp_path / "nope.json")  # cached
    with pytest.raises(InputError, match="not a file"):
        _pipeline(tmp_path, tmp_path / "adir")  # cached
    with pytest.raises(InputError, match="not a file"):
        _pipeline(tmp_path, tmp_path / "adir", out="fresh", questions_from=good)


def test_an_unparseable_questions_from_file_names_the_flag(tmp_path):
    source = tmp_path / "earlier (v1).json"
    source.write_text("not json", encoding="utf-8")

    with pytest.raises(InputError, match=rf"^--questions-from {re.escape(str(source))}: "):
        EvaluationPipeline(product="Demo", product_key="demo", output_dir=tmp_path / "out",
                           description="A demo product", questions_from=source)
    assert not (tmp_path / "out").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs named pipes")
def test_a_named_pipe_as_custom_questions_or_questions_from_is_not_read_up_front(tmp_path, monkeypatch):
    """The early parse is for regular files: reading a pipe there would take the one read its writer gets."""
    from dokimi.orchestrator import pipeline

    read = []
    monkeypatch.setattr(pipeline, "read_json_input", lambda path, **kw: read.append(path))
    fifo = tmp_path / "q.json"
    os.mkfifo(fifo)

    pipeline._check_custom_questions(fifo)
    EvaluationPipeline(product="Demo", product_key="demo", output_dir=tmp_path / "out",
                       description="A demo product", questions_from=fifo)

    assert read == []


def test_load_questions_payload_names_the_flag_only_when_given_one(tmp_path):
    empty = tmp_path / "q (v1).json"
    empty.write_bytes(b"")

    with pytest.raises(json.JSONDecodeError):
        load_questions_payload(empty)
    with pytest.raises(InputError, match=rf"^--custom-questions {re.escape(str(empty))}: "):
        load_questions_payload(empty, "--custom-questions")


def test_an_unwritable_output_dir_exits_2_with_a_message(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with refusal() as out:
        _evaluate("--out-dir", str(blocker))
    assert out.code == 2
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and str(blocker) in err
    assert "Traceback" not in err
