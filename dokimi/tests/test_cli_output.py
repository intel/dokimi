# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The --json contract (issue #94 §2.7, stage F2): cli_output.v1 on stdout, every exit code.

The envelope and its schema, the error path of a leaf that opted in (exit 2 for input, exit 5 for
a crash), the ``data schema show cli_output.v1`` kind, and the inventory: every leaf with ``--json``
sets ``emits_cli_output``, and a --json leaf that exits without its document gets one.
"""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from dokimi import cli, cli_output
from dokimi.artifacts import schema_errors
from dokimi.utils import InputError

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


def test_a_malformed_run_id_still_gets_the_input_document_with_a_null_run_id(monkeypatch, capsys):
    monkeypatch.setenv("DOKIMI_RUN_ID", "bad id!")
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["run_id"] is None and doc["error"]["code"] == "input"
    assert "DOKIMI_RUN_ID" in doc["error"]["message"]
    assert schema_errors("cli_output.v1", doc) == []


def test_every_json_leaf_emits_cli_output():
    leaves = _json_leaves()
    enveloped = {path for path, p in leaves.items() if p.get_default("emits_cli_output")}
    assert set(leaves) == enveloped
    assert all(leaves[path].get_default("cli_kind") for path in enveloped - {("backends",)})


def test_a_json_leaf_that_exits_without_its_document_prints_one_with_the_same_code(monkeypatch, capsys):
    from dokimi.commands import backends

    def refuse(args):
        print("✗ no backend", file=sys.stderr)
        sys.exit(2)

    monkeypatch.setattr(backends, "cmd_backends", refuse)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed" and doc["error"]["code"] == "input"
    assert schema_errors("cli_output.v1", doc) == []


@pytest.mark.parametrize("argv, kind, message", [
    (["repo", "budget", "--json"], "repo budget", "the following arguments are required: source"),
    (["repo", "validate", ".", "--json", "--no-such-flag"], "repo validate", "unrecognized arguments: --no-such-flag"),
    (["repo", "validate", ".", "--json", "--cache-dir"], "repo validate", "argument --cache-dir: expected one argument"),
    (["repo", "validate", ".", "--jso", "--no-such-flag"], "repo validate", "unrecognized arguments: --no-such-flag"),
    (["repo", "validate", ".", "--json=true"], "repo validate", "argument --json: ignored explicit argument 'true'"),
    (["--no-such-flag", "repo", "validate", ".", "--json"], "repo validate", "unrecognized arguments: --no-such-flag"),
    (["repo", "--no-such-flag", "validate", ".", "--json"], "repo validate", "unrecognized arguments: --no-such-flag"),
])
def test_a_json_leaf_usage_error_prints_its_failed_document_with_exit_2(monkeypatch, capsys, argv, kind, message):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["kind"], doc["status"], doc["error"]["code"]) == (kind, "failed", "input")
    assert doc["reason"] == message and schema_errors("cli_output.v1", doc) == []


@pytest.mark.parametrize("argv", [["repo", "budget", "--jso"], ["repo", "budget", "--j"]])
def test_an_abbreviated_json_flag_on_a_leaf_prints_its_document(monkeypatch, capsys, argv):
    # the abbreviation branch of _json_requested: only the leaf's own --json is an abbreviation of --json here
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["kind"], doc["status"]) == ("repo budget", "failed")


@pytest.mark.parametrize("argv", [["repo", "validate", ".", "--bogus"], ["repo", "validate", ".", "--", "--json"]])
def test_a_usage_error_without_a_json_request_prints_no_document(monkeypatch, capsys, argv):
    # no --json token before the bare "--": the leaf's error stays on stderr
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


def test_a_usage_error_of_a_group_prints_no_document(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "no-such-leaf", "--json"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""  # a group is not a --json leaf: its error stays on stderr


def test_a_json_leaf_that_exits_1_after_its_document_prints_one_document(monkeypatch, capsys):
    from dokimi.commands import backends

    def fail(args):
        cli_output.emit_leaf("backends", {"rows": []}, status="failed", reason="gate")
        sys.exit(1)

    monkeypatch.setattr(backends, "cmd_backends", fail)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)  # one document: the leaf's own, not a second one
    assert doc["status"] == "failed" and doc["reason"] == "gate"


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


def test_a_trace_leaf_refused_before_any_read_prints_an_input_error_document(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["dokimi", "trace", "prefilter", str(tmp_path / "none.md"),
                                     str(tmp_path / "x.jsonl"), "--json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    doc = json.loads(capsys.readouterr().out)  # one document, on stdout
    assert exc.value.code == 2
    assert doc["kind"] == "trace prefilter" and doc["status"] == "failed" and doc["data"] is None
    assert doc["error"]["code"] == "input" and doc["error"]["message"].startswith("guidance ")
    assert schema_errors("cli_output.v1", doc) == []


def test_catalog_formats_json_is_the_envelope_with_its_packages_under_data(tmp_path, monkeypatch, capsys):
    _run_main(monkeypatch, ["catalog", "formats", "--root", str(tmp_path), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "catalog formats" and doc["status"] == "complete"
    assert doc["data"]["root"] == str(tmp_path) and doc["data"]["packages"] == []
    assert schema_errors("cli_output.v1", doc) == []


def test_a_json_after_a_bare_double_dash_is_not_a_json_request(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "budget", "--", "--json"])
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""  # the --json after -- is a positional: no document


@pytest.mark.parametrize("code, error_code, retryable", [(1, None, None), (2, "input", False), (5, "infra", True)])
def test_a_json_leaf_exit_without_its_document_pins_status_error_and_retryable(monkeypatch, capsys, code,
                                                                               error_code, retryable):
    from dokimi.commands import backends

    def leave(args):
        print("✗ nope", file=sys.stderr)
        sys.exit(code)

    monkeypatch.setattr(backends, "cmd_backends", leave)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == code
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "backends" and doc["status"] == "failed"
    assert doc["reason"] == f"exit {code}: see stderr"
    if error_code is None:
        assert doc["error"] is None
    else:
        assert doc["error"]["code"] == error_code and doc["error"]["retryable"] is retryable
    assert schema_errors("cli_output.v1", doc) == []


def test_a_json_leaf_that_exits_0_without_a_document_prints_nothing(monkeypatch, capsys):
    from dokimi.commands import backends

    def done(args):
        sys.exit(0)

    monkeypatch.setattr(backends, "cmd_backends", done)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends", "--json"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_a_group_leaf_that_exits_1_names_itself_in_its_document(monkeypatch, capsys):
    from dokimi.commands import runs

    def fail(args):
        sys.exit(1)

    monkeypatch.setattr(runs, "cmd_runs_list", fail)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["runs", "list", "--json"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["kind"] == "runs list" and doc["error"] is None


def test_a_judge_cost_says_when_its_calls_are_unpriced():
    from types import SimpleNamespace

    from dokimi.commands.repo_hits import _cost

    assert _cost(SimpleNamespace(spent_usd=0.0, calls=3, unpriced=3), None) == {
        "spent_usd": 0.0, "calls": 3, "cap_usd": None, "remaining_usd": None, "priced": False}
    assert _cost(SimpleNamespace(spent_usd=0.0, calls=0, unpriced=0), None)["priced"] is None


@pytest.mark.parametrize("spent, cap, remaining", [(0.2, 0.5, 0.3), (0.7, 0.5, 0.0), (0.1234567, 1.0, 0.876543)])
def test_a_priced_judge_cost_has_the_remaining_budget_floored_at_zero(spent, cap, remaining):
    from types import SimpleNamespace

    from dokimi.commands.repo_hits import _cost

    cost = _cost(SimpleNamespace(spent_usd=spent, calls=2, unpriced=0), cap)
    assert cost["remaining_usd"] == remaining and cost["cap_usd"] == cap and cost["priced"] is True


def test_an_error_object_is_retryable_only_for_infra_and_an_error_document_takes_a_reason():
    # one error object for every document: the retry rule lives in error_object alone
    assert cli_output.error_object("infra", "x")["retryable"] is True
    assert cli_output.error_object("input", "x", field="--n")["retryable"] is False
    doc = cli_output.error_document("repo budget", "moved", "moved: dokimi x", reason="the new spelling")
    assert doc["reason"] == "the new spelling" and doc["error"]["retryable"] is False
    assert schema_errors("cli_output.v1", doc) == []


@pytest.mark.parametrize("argv, kind", [
    (["repo", "validate", ".", "--json", "--out-json", "f"], "repo validate"),  # a moved flag
    (["tasks", "inspect", "x", "--json"], "eval inspect"),  # a moved command (a tombstone)
])
def test_a_moved_spelling_under_json_prints_its_moved_document_with_exit_2(monkeypatch, capsys, argv, kind):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["kind"], doc["status"], doc["error"]["code"]) == (kind, "failed", "moved")
    assert doc["reason"].startswith("moved: dokimi ") and schema_errors("cli_output.v1", doc) == []


@pytest.mark.parametrize("argv", [["repo", "validate", ".", "--out-json", "f"], ["tasks", "inspect", "x"]])
def test_a_moved_spelling_without_json_prints_no_document(monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("moved: dokimi ")


def test_a_failed_budget_gate_under_json_says_why(monkeypatch, capsys, tmp_path):
    # a warning under --fail-on-warning: the status is failed, so the document carries the reason too
    (tmp_path / "CLAUDE.md").write_text("c" * 20, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("a" * 10, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "budget", str(tmp_path), "--json", "--fail-on-warning"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed"
    assert doc["reason"] == f"{len(doc['data']['warnings'])} warning(s) under --fail-on-warning"


def test_a_failed_inspect_gate_under_json_says_why(monkeypatch, capsys, tmp_path):
    from tests.test_repo_environment import _repo

    _repo(tmp_path, {"src/a.py": "x = 1\n"})  # the checks.test.missing ERROR of test_repo_inspect_cli
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "inspect", str(tmp_path), "--json", "--fail-on-error"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["reason"] == f"{doc['data']['counts']['error']} error(s) under --fail-on-error"


def test_emit_refuses_a_payload_value_that_is_not_json(capsys):
    # --json and --out share one document: a value JSON cannot hold is an error, not a str() in the output
    with pytest.raises(TypeError):
        cli_output.emit(cli_output.envelope("repo budget", data={"items": {1, 2}}))
    assert capsys.readouterr().out == ""


def test_eval_inspect_json_still_prints_a_non_json_value_as_its_str(monkeypatch, capsys, tmp_path):
    # the one leaf that stringified its payload before: it keeps doing so, now in the leaf and not in emit
    from dokimi import task_formats

    class _Report:
        def as_dict(self):
            return {"path": "x", "fields": {1, 2}}

    monkeypatch.setattr(task_formats, "inspect_path", lambda path: [_Report()])
    _run_main(monkeypatch, ["eval", "inspect", str(tmp_path), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["data"]["items"] == [{"path": "x", "fields": "{1, 2}"}]


def test_a_payload_that_will_not_serialize_leaves_the_fallback_document_to_print(monkeypatch, capsys):
    # the document counts as printed only once it is serialized: a TypeError must not hide main's infra document
    cli_output.reset()  # a document printed by an earlier test in this process does not count here
    with pytest.raises(TypeError):
        cli_output.emit(cli_output.envelope("repo budget", data={"items": {1, 2}}))
    assert cli_output.printed() is False
    assert capsys.readouterr().out == ""


def test_a_failed_scan_with_no_incomplete_reason_still_says_what_failed():
    from dokimi.commands.repo import _scan_reason

    assert _scan_reason({"incomplete": True, "incomplete_reasons": [], "counts": {"fail": 0}}, []) == "scan incomplete"
    assert _scan_reason({"incomplete": False, "incomplete_reasons": [], "counts": {"fail": 2}}, []) == "2 claim(s) failed"
