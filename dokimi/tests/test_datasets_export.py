# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for artifact -> dataset export."""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from dokimi.datasets_export import dataset_card, export_artifact, flatten, to_jsonl

from tests.cli_refusal import refusal

HAS_DATASETS = importlib.util.find_spec("datasets") is not None


def test_flatten_attaches_metadata_and_jsonifies():
    data = {
        "schema_version": "answers.v1",
        "model": "gpt-4o",
        "answers": [
            {"question_id": "q1", "with_docs": {"answer": "a", "model": "gpt-4o"}},
        ],
    }
    rows = flatten("answers", data)
    assert rows[0]["model"] == "gpt-4o"
    assert rows[0]["question_id"] == "q1"
    # nested dict is JSON-encoded
    assert json.loads(rows[0]["with_docs"])["answer"] == "a"


def test_exported_rows_carry_the_run_envelope(tmp_path):
    """Rows of two runs exported together still tell the runs apart, by the run_id save_artifact wrote."""
    from dokimi.artifacts import ENVELOPE_KEYS, save_artifact

    fixture = Path(__file__).resolve().parent / "fixtures" / "artifacts" / "answers.v1.json"
    saved = save_artifact("answers", json.loads(fixture.read_text(encoding="utf-8")), tmp_path / "a.json")
    primary = export_artifact("answers", tmp_path / "a.json", tmp_path / "out", fmt="jsonl")
    rows = [json.loads(line) for line in primary.read_text(encoding="utf-8").strip().splitlines()]
    assert rows and all({k: row[k] for k in ENVELOPE_KEYS} == {k: saved[k] for k in ENVELOPE_KEYS}
                        for row in rows)


def test_export_jsonl(tmp_path):
    src = tmp_path / "q.json"
    src.write_text(json.dumps({
        "schema_version": "questions.v1",
        "library": "oneTBB",
        "questions": [{"id": "q1", "text": "What is X?"}, {"id": "q2", "text": "How Y?"}],
    }))
    out = tmp_path / "out"
    primary = export_artifact("questions", src, out, fmt="jsonl")
    assert primary.exists()
    lines = primary.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["library"] == "oneTBB"
    assert (out / "README.md").exists()


@pytest.mark.parametrize(("value", "written", "loaded"), [
    ("n\ud800", "n\\ud800", "n\ud800"),
    (chr(0xD83D) + chr(0xDE00), "\U0001F600", "\U0001F600"),
    ("café", "café", "café"),
])
def test_to_jsonl_writes_a_surrogate_as_utf8(tmp_path, value, written, loaded):
    """One JSON object per line; a lone surrogate stays its escape, a pair is joined, ``é`` is not escaped (#990)."""
    path = tmp_path / "out" / "rows.jsonl"

    to_jsonl([{"a": value}, {"a": "x"}], path)

    raw = path.read_bytes()
    assert raw == ('{"a": "' + written + '"}\n{"a": "x"}\n').encode("utf-8")
    assert [json.loads(line)["a"] for line in raw.decode("utf-8").splitlines()] == [loaded, "x"]


def test_to_jsonl_keeps_a_lone_surrogate_key_apart_from_a_question_mark_key(tmp_path):
    path = tmp_path / "rows.jsonl"

    to_jsonl([{"\ud800": 1, "?": 2}], path)

    assert json.loads(path.read_bytes().decode("utf-8")) == {"\ud800": 1, "?": 2}


def test_to_jsonl_refuses_a_pair_key_beside_its_character_key(tmp_path):
    with pytest.raises(ValueError, match="two object keys are the same"):
        to_jsonl([{chr(0xD83D) + chr(0xDE00): 1, "\U0001F600": 2}], tmp_path / "rows.jsonl")


def test_a_refused_to_jsonl_leaves_the_existing_file_and_creates_none(tmp_path):
    """The rows are serialised before the destination is opened, so a refusal in a later row cuts nothing (#1020)."""
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"kept": 1}\n')
    bad = {chr(0xD83D) + chr(0xDE00): 1, "\U0001F600": 2}

    with pytest.raises(ValueError, match="two object keys are the same"):
        to_jsonl([{"a": 1}, bad], path)
    with pytest.raises(TypeError):
        to_jsonl([{"a": 1}, {"a": object()}], path)
    fresh = tmp_path / "new" / "rows.jsonl"
    with pytest.raises(ValueError, match="two object keys are the same"):
        to_jsonl([bad], fresh)

    assert path.read_bytes() == b'{"kept": 1}\n'
    assert not fresh.exists()


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
@pytest.mark.parametrize("target_exists", [True, False])
def test_to_jsonl_writes_through_a_symlink_and_keeps_it(tmp_path, target_exists):
    """``open(path, "w")`` followed a link at the destination; the atomic publish replaces the target, not the link (#1020)."""
    shared = tmp_path / "shared" / "shared.jsonl"
    shared.parent.mkdir()
    if target_exists:
        shared.write_text("stale\n")
    link = tmp_path / "rows.jsonl"
    try:
        link.symlink_to(Path("shared") / "shared.jsonl")
    except OSError:
        pytest.skip("symlinks need a privilege here")

    to_jsonl([{"a": 1}], link)

    assert link.is_symlink()
    assert shared.read_text() == '{"a": 1}\n'
    assert sorted(p.name for p in shared.parent.iterdir()) == ["shared.jsonl"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rows.jsonl", "shared"]


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no symlinks")
def test_to_jsonl_refuses_a_link_into_a_missing_directory(tmp_path):
    """``open(path, "w")`` raised on it; the missing directory of the link's target is not made (#1020)."""
    link = tmp_path / "rows.jsonl"
    try:
        link.symlink_to(Path("missing") / "shared.jsonl")
    except OSError:
        pytest.skip("symlinks need a privilege here")

    with pytest.raises(FileNotFoundError):
        to_jsonl([{"a": 1}], link)

    assert not (tmp_path / "missing").exists()
    assert link.is_symlink()


def test_to_jsonl_rewrites_a_hard_linked_file_in_place(tmp_path):
    """Like ``open(path, "w")``, the other name sees the rows; a refused row still leaves both names as they were (#1020)."""
    path = tmp_path / "rows.jsonl"
    path.write_text("stale\n")
    other = tmp_path / "other.jsonl"
    try:
        os.link(path, other)
    except OSError:
        pytest.skip("no hard links here")

    with pytest.raises(TypeError):
        to_jsonl([{"a": 1}, {"a": object()}], path)
    assert other.read_text() == "stale\n"

    to_jsonl([{"a": 1}], path)

    assert other.read_text() == '{"a": 1}\n'
    assert os.path.samefile(path, other)


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="no extended attributes")
def test_to_jsonl_keeps_a_user_xattr_of_the_destination(tmp_path):
    """A new inode would drop ``user.dataset``; the file is rewritten in place instead (#1020)."""
    path = tmp_path / "rows.jsonl"
    path.write_text("stale\n")
    try:
        os.setxattr(path, "user.dataset", b"sample")
    except OSError:
        pytest.skip("user xattrs are not supported here")

    to_jsonl([{"a": 1}], path)

    assert path.read_text() == '{"a": 1}\n'
    assert os.getxattr(path, "user.dataset") == b"sample"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs")
def test_to_jsonl_writes_into_a_fifo_and_keeps_it(tmp_path):
    """``open(path, "w")`` wrote into a FIFO; replacing it with a regular file would lose the reader's rows (#1020)."""
    path = tmp_path / "rows.jsonl"
    os.mkfifo(path)
    reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        to_jsonl([{"a": 1}], path)
        assert os.read(reader, 100) == b'{"a": 1}\n'
    finally:
        os.close(reader)

    assert stat.S_ISFIFO(path.lstat().st_mode)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root writes into a read-only directory")
def test_to_jsonl_rewrites_a_writable_file_in_a_read_only_directory(tmp_path):
    """A tmp sibling cannot be made there, but ``open(path, "w")`` needs only the file's write bit (#1020)."""
    out = tmp_path / "out"
    out.mkdir()
    path = out / "rows.jsonl"
    path.write_text("stale\n")
    out.chmod(0o555)
    try:
        to_jsonl([{"a": 1}], path)
    finally:
        out.chmod(0o755)

    assert path.read_text() == '{"a": 1}\n'


def test_to_jsonl_holds_one_row_of_output_at_a_time(tmp_path):
    """The output is encoded row by row into a tmp file, so memory is bounded by a row, not the export (#1020)."""
    import tracemalloc

    rows = [{"a": "x" * 4096, "i": i} for i in range(2048)]
    path = tmp_path / "rows.jsonl"

    tracemalloc.start()
    try:
        to_jsonl(rows, path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert path.stat().st_size > 8_000_000
    assert peak < 1_000_000
    assert [p.name for p in tmp_path.iterdir()] == ["rows.jsonl"]


def test_to_jsonl_into_a_hard_linked_file_holds_one_row_of_output_at_a_time(tmp_path):
    """The in-place fallback stages the rows on disk, not in a list (#1020)."""
    import tracemalloc

    rows = [{"a": "x" * 4096, "i": i} for i in range(2048)]
    path = tmp_path / "rows.jsonl"
    path.write_text("stale\n")
    try:
        os.link(path, tmp_path / "other.jsonl")
    except OSError:
        pytest.skip("no hard links here")

    tracemalloc.start()
    try:
        to_jsonl(rows, path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert (tmp_path / "other.jsonl").stat().st_size > 8_000_000
    assert peak < 1_000_000


def test_to_jsonl_into_a_mount_point_copies_the_staged_rows_in_place(tmp_path, monkeypatch):
    """``os.replace`` onto a mount point fails with EBUSY; the rows are already consumed, so they are copied in place (#1020)."""
    path = tmp_path / "rows.jsonl"
    path.write_text("stale\n")
    inode = path.stat().st_ino

    def busy(src, dst):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr(os, "replace", busy)
    to_jsonl([{"a": 1}, {"a": 2}], path)

    assert (path.read_text(), path.stat().st_ino) == ('{"a": 1}\n{"a": 2}\n', inode)
    assert [p.name for p in tmp_path.iterdir()] == ["rows.jsonl"]


def test_to_jsonl_writes_equal_plain_keys_beside_a_surrogate_pair(tmp_path):
    """Only keys holding a character above U+FFFF can be made equal by joining a pair: ``1`` and ``"1"`` are not refused (#1020)."""
    path = tmp_path / "rows.jsonl"

    to_jsonl([{1: "a", "1": "b", "x": chr(0xD83D) + chr(0xDE00)}], path)

    assert path.read_bytes() == '{"1": "a", "1": "b", "x": "\U0001F600"}\n'.encode()


def test_export_answers_artifact_jsonl(tmp_path):
    """The answers kind flattens a nested per-arm record, which the questions kind does not
    exercise. This used to point at a captured product run under a path that never existed,
    so it skipped on every run; the payload is written here instead.
    """
    src = tmp_path / "a.json"
    src.write_text(json.dumps({
        "schema_version": "answers.v1",
        "model": "gpt-6.1-sol",
        "provider": "openai",
        "total_questions": 1,
        "answers": [
            {
                "question_id": "q1",
                "question_text": "What is X?",
                "library_name": "demo",
                "with_docs": {"answer": "a", "tokens": 3},
                "without_docs": {"answer": "b", "tokens": 2},
            }
        ],
    }))
    primary = export_artifact("answers", src, tmp_path / "out", fmt="jsonl")
    assert primary.exists()
    rows = [json.loads(line) for line in primary.read_text().strip().splitlines()]
    assert len(rows) == 1
    assert rows[0]["question_id"] == "q1"
    assert rows[0]["model"] == "gpt-6.1-sol"
    assert json.loads(rows[0]["with_docs"])["answer"] == "a"


def test_dataset_card_contents():
    card = dataset_card("questions", Path("my_questions.json"), 5, ["id", "question"])
    assert "Schema version:** questions.v2" in card
    assert "Source schema version" not in card
    assert "Rows:** 5" in card


@pytest.mark.parametrize("linesep", ["\n", "\r\n"])
def test_export_card_for_an_ordinary_name_is_the_dataset_card_text(tmp_path, monkeypatch, linesep):
    monkeypatch.setattr(os, "linesep", linesep)
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"schema_version": "questions.v1", "questions": [{"id": "q1", "text": "x"}]}))
    primary = export_artifact("questions", src, tmp_path / "out", fmt="jsonl")
    columns = sorted(json.loads(primary.read_text(encoding="utf-8")))
    expected = dataset_card("questions", src, 1, columns, source_version="questions.v1")
    assert "Exported from `q.json` (questions.v1)." in expected
    assert (tmp_path / "out" / "README.md").read_bytes() == expected.replace("\n", linesep).encode("utf-8")


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="the file system takes only UTF-8 names")
def test_export_card_names_a_non_utf8_input_with_a_question_mark(tmp_path):
    """#1100: a lone surrogate (a non-UTF-8 byte of the input's name) is a ``?`` in the card, not a UnicodeEncodeError."""
    src = tmp_path / os.fsdecode(b"q\xff.json")
    src.write_text(json.dumps({"questions": []}))
    out = tmp_path / "out"
    primary = export_artifact("questions", src, out, fmt="jsonl", validate=False)
    assert primary.exists()
    assert "Exported from `q?.json`" in (out / "README.md").read_text(encoding="utf-8")


@pytest.mark.skipif(not HAS_DATASETS, reason="datasets not installed")
def test_export_parquet(tmp_path):
    src = tmp_path / "q.json"
    src.write_text(json.dumps({"schema_version": "questions.v1",
                               "questions": [{"id": "q1", "text": "x"}]}))
    primary = export_artifact("questions", src, tmp_path / "out", fmt="parquet")
    assert primary.exists()


def test_flatten_task_runs_lifts_metrics_to_columns():
    data = {
        "schema_version": "task_runs.v1",
        "model": "haiku",
        "results": [
            {"task": "t1", "harness": "docker-claude", "passed": True, "reward": 1.0,
             "metrics": {"operation_count": 1, "operations_by_type": {"harness": 1},
                         "cost_usd": 0.05, "n_calls": 7, "total_tokens": 600, "mode": "claude"}},
        ],
    }
    rows = flatten("task_runs", data)
    assert rows[0]["task"] == "t1"
    assert rows[0]["model"] == "haiku"
    # telemetry lifted to flat, typed columns
    assert rows[0]["metric_cost_usd"] == 0.05
    assert rows[0]["metric_n_calls"] == 7
    assert rows[0]["metric_mode"] == "claude"
    # full metrics block still present as a JSON-encoded column
    assert json.loads(rows[0]["metrics"])["operation_count"] == 1


def test_export_task_runs_jsonl(tmp_path):
    src = tmp_path / "tr.json"
    src.write_text(json.dumps({
        "schema_version": "task_runs.v1",
        "harnesses": ["docker-claude"],
        "tasks": [{"name": "t1", "path": "p", "metadata": {}, "verifier": {},
                   "agent": {}, "environment": {}}],
        "results": [{"task": "t1", "harness": "docker-claude", "passed": False,
                     "metrics": {"operation_count": 0, "operations_by_type": {}},
                     "operations": []}],
        "summary": {"per_harness": {}, "comparisons": {}},
    }))
    primary = export_artifact("task_runs", src, tmp_path / "out", fmt="jsonl")
    assert primary.exists()
    assert json.loads(primary.read_text().strip())["task"] == "t1"


def _run_export(src: Path, out: Path, kind: str = "answers", *extra: str) -> None:
    from dokimi.cli import build_parser

    args = build_parser().parse_args(["data", "export", "--kind", kind, "--input", str(src),
                                      "--out-dir", str(out), *extra])
    args.func(args)


def _export_error(src: Path, out: Path, *extra: str, capsys) -> str:
    with refusal() as exc:
        _run_export(src, out, "answers", *extra)
    assert exc.code == 2
    assert not (out / "README.md").exists()
    err = exc.err
    assert err.startswith(f"Error: cannot export {src} to {out}: ")
    return err


def test_cli_export_of_a_missing_input_exits_cleanly(tmp_path, capsys):
    err = _export_error(tmp_path / "nope.json", tmp_path / "out", capsys=capsys)
    assert "nope.json" in err and "Traceback" not in err


def test_cli_export_to_an_unwritable_out_dir_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"schema_version": "answers.v1", "answers": []}), encoding="utf-8")
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    err = _export_error(src, blocker / "out", capsys=capsys)
    assert "cannot export" in err and "a.json" in err


def test_cli_export_of_invalid_json_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text("{not json", encoding="utf-8")
    assert "a.json" in _export_error(src, tmp_path / "out", capsys=capsys)


def test_cli_export_of_a_schema_invalid_artifact_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"schema_version": "answers.v1", "answers": "x"}), encoding="utf-8")
    assert "validation failed" in _export_error(src, tmp_path / "out", capsys=capsys)


def test_cli_export_without_validation_of_a_wrong_shape_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"not_answers": []}), encoding="utf-8")
    err = _export_error(src, tmp_path / "out", "--no-validate", capsys=capsys)
    assert err.startswith(f"Error: cannot export {src} to {tmp_path / 'out'}: Artifact")


def test_cli_export_without_validation_of_a_non_list_record_array_exits_cleanly(tmp_path, capsys):
    src = tmp_path / "a.json"
    src.write_text(json.dumps({"answers": {}}), encoding="utf-8")
    err = _export_error(src, tmp_path / "out", "--no-validate", capsys=capsys)
    assert "must be a list" in err and "Traceback" not in err


@pytest.mark.parametrize("raw", ["[]", '"s"', "null", "3"])
@pytest.mark.parametrize("extra", [(), ("--no-validate",)])
@pytest.mark.parametrize("kind", ["answers", "eval", "arms", "task_runs"])
def test_cli_export_of_a_non_object_top_level_exits_cleanly(tmp_path, capsys, raw, extra, kind):
    src = tmp_path / "a.json"
    src.write_text(raw, encoding="utf-8")
    with refusal() as exc:
        _run_export(src, tmp_path / "out", kind, *extra)
    assert exc.code == 2
    assert not (tmp_path / "out" / "README.md").exists()
    assert exc.err.startswith(f"Error: cannot export {src} to {tmp_path / 'out'}: ")


def test_flatten_missing_record_key_raises():
    import pytest
    with pytest.raises(ValueError):
        flatten("answers", {"not_answers": []})


def test_flatten_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError):
        flatten("bogus", {})


def test_flatten_non_object_root_raises():
    import pytest
    with pytest.raises(ValueError):
        flatten("answers", [])


def test_flatten_non_list_records_raises():
    import pytest
    with pytest.raises(ValueError):
        flatten("answers", {"answers": {"q": 1}})
