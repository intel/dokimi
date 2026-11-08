# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The --json contract (issue #94 §2.7, stage F2): cli_output.v1 on stdout, every exit code.

The envelope and its schema, the error path of a leaf that opted in (exit 2 for input, exit 5 for
a crash), the ``data schema show cli_output.v1`` kind, and the inventory: every leaf with ``--json``
either prints the envelope or is named in ``NOT_YET_ENVELOPE``, which only shrinks.
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from dokimi import cli, cli_output
from dokimi.artifacts import schema_errors
from dokimi.utils import InputError

# --json leaves still on their own payload (the envelope changes their shape): remove a row when
# the leaf sets `emits_cli_output`, never add one.
NOT_YET_ENVELOPE = {
    ("catalog", "formats"), ("eval", "inspect"), ("repo", "budget"), ("repo", "compare"),
    ("repo", "fetch"), ("repo", "hits", "judge"), ("repo", "inspect"), ("repo", "report"),
    ("repo", "scan"), ("repo", "validate"),
}


def _leaves(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()):
    """Every leaf parser under ``parser`` as (path, parser); an alias is the same parser twice."""
    sub = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if sub is None:
        yield prefix, parser
        return
    seen: set[int] = set()
    for name, child in sub.choices.items():
        if id(child) in seen:
            continue
        seen.add(id(child))
        yield from _leaves(child, (*prefix, name))


def _json_leaves() -> dict[tuple[str, ...], argparse.ArgumentParser]:
    return {path: p for path, p in _leaves(cli.build_parser())
            if any("--json" in a.option_strings for a in p._actions)}


def test_the_envelope_has_every_key_of_the_proposal_and_validates():
    doc = cli_output.envelope("backends", summary={"backends": 2})
    assert list(doc) == ["schema_version", "kind", "run_id", "status", "verdict", "reason", "error", "summary",
                         "subjects", "artifacts", "cost", "limits", "next", "data"]
    assert doc["verdict"] is None and doc["summary"]["dokimi_version"] and doc["run_id"] is None
    assert doc["cost"]["remaining_usd"] is None and doc["cost"]["calls"] == 0
    assert schema_errors("cli_output.v1", doc) == []


def test_an_error_document_carries_the_code_and_the_exit_code_of_the_table():
    doc = cli_output.error_document("backends", "input", "bad flag", field="--json")
    assert doc["status"] == "failed" and doc["error"] == {
        "code": "input", "message": "bad flag", "retryable": False, "field": "--json"}
    assert schema_errors("cli_output.v1", doc) == []
    assert cli_output.EXIT_OF_ERROR["input"] == 2 and cli_output.EXIT_OF_ERROR["infra"] == 5
    assert cli_output.error_document("backends", "infra", "boom")["error"]["retryable"] is True


def test_the_envelope_refuses_an_unknown_status_or_error_code():
    with pytest.raises(ValueError):
        cli_output.envelope("x", status="ok")
    with pytest.raises(ValueError):
        cli_output.error_document("x", "crash", "boom")


@pytest.mark.parametrize(
    "damage",
    [
        lambda doc: doc.pop("cost"),
        lambda doc: doc.pop("run_id"),
        lambda doc: doc.update(verdict="maybe"),
        lambda doc: doc.update(status="ok"),
        lambda doc: doc.update(extra=1),
        lambda doc: doc.update(schema_version="cli_output.v2"),
        lambda doc: doc["error"].update(code="crash"),
        lambda doc: doc["next"].append({"argv": [], "why": "x", "spends": False, "est_usd": 0, "kind": "sequence"}),
        lambda doc: doc["subjects"].append({"subject": "skill:a"}),
    ],
    ids=["no-cost", "no-run-id", "bad-verdict", "bad-status", "extra-key", "wrong-version", "bad-error-code",
         "empty-argv", "subject-no-verdict"],
)
def test_the_schema_rejects_a_document_whose_shape_drifted(damage):
    doc = cli_output.error_document("backends", "input", "bad")  # error is set, so damage can reach it
    damage(doc)
    assert schema_errors("cli_output.v1", doc)


def test_data_is_open_so_a_leaf_can_put_its_payload_there():
    doc = cli_output.envelope("backends", data={"rows": [{"backend": "claude-cli", "anything": 1}]})
    assert schema_errors("cli_output.v1", doc) == []


def test_data_schema_show_prints_the_schema_document(capsys):
    args = cli.parse_args(["data", "schema", "show", "cli_output.v1"])
    args.func(args)
    doc = json.loads(capsys.readouterr().out)
    assert doc == cli_output.schema_document()
    assert doc["$id"].endswith("cli_output.v1.json")


def _run_main(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["dokimi", *argv])
    cli.main()


def test_a_json_leaf_prints_one_envelope_on_an_input_error_and_exits_2(monkeypatch, capsys):
    from dokimi.commands import backends

    def refuse(args):
        raise InputError("--provider wants a model")

    monkeypatch.setattr(backends, "cmd_backends", refuse)
    monkeypatch.setenv("DOKIMI_RUN_ID", "review-run")
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 2
    out = capsys.readouterr()
    doc = json.loads(out.out)  # exactly one document on stdout
    assert doc["run_id"] == "review-run"
    assert doc["kind"] == "backends" and doc["error"]["code"] == "input"
    assert doc["error"]["message"] == "--provider wants a model" and doc["status"] == "failed"
    assert schema_errors("cli_output.v1", doc) == []


def test_a_json_leaf_that_crashes_prints_the_infra_envelope_and_exits_5(monkeypatch, capsys):
    from dokimi.commands import backends

    def crash(args):
        raise RuntimeError("probe fell over")

    monkeypatch.setattr(backends, "cmd_backends", crash)
    monkeypatch.setenv("DOKIMI_RUN_ID", "review-run")
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 5
    out = capsys.readouterr()
    doc = json.loads(out.out)
    assert doc["run_id"] == "review-run"
    assert doc["error"]["code"] == "infra" and "RuntimeError: probe fell over" in doc["error"]["message"]
    assert schema_errors("cli_output.v1", doc) == []
    assert "RuntimeError" in out.err  # the traceback goes to stderr, not stdout


def test_a_crash_without_json_is_not_wrapped(monkeypatch, capsys):
    from dokimi.commands import backends

    def crash(args):
        raise RuntimeError("probe fell over")

    monkeypatch.setattr(backends, "cmd_backends", crash)
    with pytest.raises(RuntimeError):
        _run_main(monkeypatch, ["backends"])
    assert capsys.readouterr().out == ""


def test_a_json_leaf_outside_the_opt_in_that_crashes_re_raises_and_prints_no_envelope(monkeypatch, capsys):
    from dokimi.commands import repo

    def crash(args):
        raise RuntimeError("repo fell over")

    monkeypatch.setattr(repo, "cmd_repo_validate", crash)
    with pytest.raises(RuntimeError):
        _run_main(monkeypatch, ["repo", "validate", "x", "--json"])
    assert capsys.readouterr().out == ""


def test_a_malformed_run_id_still_gets_the_input_document_with_a_null_run_id(monkeypatch, capsys):
    monkeypatch.setenv("DOKIMI_RUN_ID", "bad id!")
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["run_id"] is None and doc["error"]["code"] == "input"
    assert "DOKIMI_RUN_ID" in doc["error"]["message"]
    assert schema_errors("cli_output.v1", doc) == []


def test_the_inventory_every_json_leaf_is_enveloped_or_named_as_not_yet():
    leaves = _json_leaves()
    enveloped = {path for path, p in leaves.items() if p.get_default("emits_cli_output")}
    assert enveloped == {("backends",), ("runs", "list"), ("runs", "show"), ("trace", "inspect"),
                         ("trace", "consistency"), ("trace", "prefilter")}
    assert set(leaves) == enveloped | NOT_YET_ENVELOPE
    assert not enveloped & NOT_YET_ENVELOPE
    assert all(leaves[path].get_default("cli_kind") for path in enveloped - {("backends",)})


def test_a_group_leaf_names_itself_on_a_crash_document(monkeypatch, capsys):
    from dokimi.commands import runs

    def crash(args):
        raise RuntimeError("runs fell over")

    monkeypatch.setattr(runs, "cmd_runs_list", crash)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["runs", "list", "--json"])
    assert exc.value.code == 5
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "runs list" and doc["error"]["code"] == "infra"
    assert schema_errors("cli_output.v1", doc) == []


def test_trace_inspect_json_is_partial_when_a_trace_does_not_read(tmp_path, capsys):
    good = tmp_path / "good.jsonl"
    usage = {"input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}
    good.write_text(json.dumps({"type": "system", "subtype": "init", "model": "m", "tools": []}) + "\n"
                    + json.dumps({"type": "assistant", "parent_tool_use_id": None,
                                  "message": {"id": "m1", "usage": usage, "content": []}}) + "\n", encoding="utf-8")
    args = cli.parse_args(["trace", "inspect", str(tmp_path / "missing.jsonl"), str(good), "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "trace inspect" and doc["status"] == "partial" and doc["reason"]
    assert doc["verdict"] is None and doc["error"] is None
    assert schema_errors("cli_output.v1", doc) == []


def test_trace_consistency_json_is_failed_when_no_trace_reads(tmp_path, capsys):
    args = cli.parse_args(["trace", "consistency", "--arm", f"base={tmp_path}/missing.jsonl", "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "trace consistency" and doc["status"] == "failed" and doc["reason"]
    assert schema_errors("cli_output.v1", doc) == []


def test_trace_consistency_json_is_partial_when_some_trace_reads(tmp_path, capsys):
    good = tmp_path / "good.jsonl"
    usage = {"input_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}
    good.write_text(json.dumps({"type": "system", "subtype": "init", "model": "m", "tools": []}) + "\n"
                    + json.dumps({"type": "assistant", "parent_tool_use_id": None,
                                  "message": {"id": "m1", "usage": usage, "content": []}}) + "\n"
                    + json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                                  "num_turns": 1, "usage": usage}) + "\n", encoding="utf-8")
    args = cli.parse_args(["trace", "consistency", "--arm", f"base={good}",
                           "--arm", f"base={tmp_path}/missing.jsonl", "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "trace consistency" and doc["status"] == "partial" and doc["reason"]
    assert schema_errors("cli_output.v1", doc) == []


def test_a_trace_leaf_refused_before_any_read_prints_a_failed_document(tmp_path, capsys):
    args = cli.parse_args(["trace", "prefilter", str(tmp_path / "none.md"), str(tmp_path / "x.jsonl"), "--json"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert isinstance(exc.value.code, str)  # sys.exit(message): the interpreter exits 1
    doc = json.loads(capsys.readouterr().out)  # one document; the exit message is the same text
    assert doc["kind"] == "trace prefilter" and doc["status"] == "failed" and doc["data"] is None
    assert exc.value.code == doc["reason"]
    assert schema_errors("cli_output.v1", doc) == []
