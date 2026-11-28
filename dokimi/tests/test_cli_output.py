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
    assert doc["reason"] == "--provider wants a model"  # the document says why, as error_document promises
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
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["backends"])
    assert exc.value.code == 5  # a crash is exit 5 (§2.6); no document without --json
    assert capsys.readouterr().out == ""


def test_a_json_leaf_that_crashes_exits_5_and_prints_its_infra_document(monkeypatch, capsys):
    from dokimi.commands import repo

    def crash(args):
        raise RuntimeError("repo fell over")

    monkeypatch.setattr(repo, "cmd_repo_validate", crash)
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["eval", "check", "--schema-only", "x", "--json"])
    assert exc.value.code == 5
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "infra"


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
    (["eval", "check", "--schema-only", ".", "--json", "--no-such-flag"], "eval check", "unrecognized arguments: --no-such-flag"),
    (["eval", "check", "--schema-only", ".", "--json", "--cache-dir"], "eval check", "argument --cache-dir: expected one argument"),
    (["eval", "check", "--schema-only", ".", "--jso", "--no-such-flag"], "eval check", "unrecognized arguments: --no-such-flag"),
    (["eval", "check", "--schema-only", ".", "--json=true"], "eval check", "argument --json: ignored explicit argument 'true'"),
    (["--no-such-flag", "eval", "check", "--schema-only", ".", "--json"], "eval check", "unrecognized arguments: --no-such-flag"),
    (["eval", "--no-such-flag", "check", "--schema-only", ".", "--json"], "eval check", "unrecognized arguments: --no-such-flag"),
])
def test_a_json_leaf_usage_error_prints_its_failed_document_with_exit_2(monkeypatch, capsys, argv, kind, message):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["kind"], doc["status"], doc["error"]["code"]) == (kind, "failed", "input")
    assert doc["reason"] == message and schema_errors("cli_output.v1", doc) == []


@pytest.mark.parametrize("path", sorted(_json_leaves()), ids=" ".join)
def test_a_usage_error_document_names_a_help_command_that_works(monkeypatch, capsys, path):
    # #941: the failed document's `next` is the leaf's own --help, free, and running it shows that leaf's usage
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, [*path, "--json", "--no-such-flag"])
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert schema_errors("cli_output.v1", doc) == []
    [step] = doc["next"]
    assert step["argv"] == ["dokimi", *path, "--help"]
    assert (step["spends"], step["est_usd"], step["kind"]) == (False, 0, "sequence")
    with pytest.raises(SystemExit) as helped:
        _run_main(monkeypatch, step["argv"][1:])
    assert helped.value.code == 0
    assert f"usage: {' '.join(step['argv'][:-1])}" in capsys.readouterr().out


@pytest.mark.parametrize("argv, help_argv", [
    (["--no-such-flag", "backends", "--json"], ["dokimi", "backends", "--help"]),
    (["--no-such-flag", "repo", "budget", ".", "--json"], ["dokimi", "repo", "budget", "--help"]),
    (["repo", "--no-such-flag", "budget", ".", "--json"], ["dokimi", "repo", "budget", "--help"]),
])
def test_an_unknown_flag_before_the_leaf_carries_the_help_step(monkeypatch, capsys, argv, help_argv):
    # the root parser reports these extras, not the leaf: its document names the help too
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert schema_errors("cli_output.v1", doc) == []
    assert [s["argv"] for s in doc["next"]] == [help_argv]


def test_a_missing_argument_document_carries_the_same_help_step(monkeypatch, capsys):
    # the error() path (not the extras path): a required positional left out
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, ["repo", "budget", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert schema_errors("cli_output.v1", doc) == []
    assert [s["argv"] for s in doc["next"]] == [["dokimi", "repo", "budget", "--help"]]


def test_an_empty_help_argv_adds_no_next_step(capsys):
    cli_output.reset()
    # an empty argv is no runnable step, and cli_output.v1 requires a non-empty `argv` in `next`
    cli._usage_document("backends", "bad", help_argv=[])
    doc = json.loads(capsys.readouterr().out)
    assert doc["next"] == []
    assert schema_errors("cli_output.v1", doc) == []


def test_root_extras_with_a_cli_kind_do_not_read_cmd(monkeypatch, capsys):
    cli_output.reset()
    # the kind falls back to `cmd` only when `cli_kind` is absent: a namespace without `cmd` must not raise
    ns = argparse.Namespace(cli_kind="backends", emits_cli_output=True, json=True)
    monkeypatch.setattr(argparse.ArgumentParser, "parse_known_args", lambda self, args=None, namespace=None: (ns, ["--x"]))
    cli._RoutedParser(prog="dokimi").parse_known_args([])
    assert json.loads(capsys.readouterr().out)["kind"] == "backends"


@pytest.mark.parametrize("argv", [["repo", "budget", "--jso"], ["repo", "budget", "--j"]])
def test_an_abbreviated_json_flag_on_a_leaf_prints_its_document(monkeypatch, capsys, argv):
    # the abbreviation branch of _json_requested: only the leaf's own --json is an abbreviation of --json here
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["kind"], doc["status"]) == ("repo budget", "failed")


@pytest.mark.parametrize("argv", [["eval", "check", "--schema-only", ".", "--bogus"], ["eval", "check", "--schema-only", ".", "--", "--json"]])
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


def test_trace_consistency_is_an_input_error_when_no_trace_reads(tmp_path, capsys):
    # Test-narrowed: renamed from ..._json_is_failed_when_no_trace_reads; no-trace reads exit 2 (input), not 1 (A2b)
    args = cli.parse_args(["trace", "consistency", "--arm", f"base={tmp_path}/missing.jsonl", "--json"])
    with pytest.raises(InputError, match="no trace could be read"):
        args.func(args)
    assert capsys.readouterr().out == ""  # the input error document is cli.main's, not the leaf's


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
    (["eval", "check", "--schema-only", ".", "--json", "--out-json", "f"], "eval check"),  # a moved flag
    (["tasks", "inspect", "x", "--json"], "eval inspect"),  # a moved command (a tombstone)
])
def test_a_moved_spelling_under_json_prints_its_moved_document_with_exit_2(monkeypatch, capsys, argv, kind):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["kind"], doc["status"], doc["error"]["code"]) == (kind, "failed", "moved")
    assert doc["reason"].startswith("moved: dokimi ") and schema_errors("cli_output.v1", doc) == []


@pytest.mark.parametrize("argv", [["eval", "check", "--schema-only", ".", "--out-json", "f"], ["tasks", "inspect", "x"]])
def test_a_moved_spelling_without_json_prints_no_document(monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.startswith("moved: dokimi ")


def test_a_failed_budget_gate_under_json_says_why(monkeypatch, capsys, tmp_path):
    # a warning under --fail-on warn: the status is failed, so the document carries the reason too
    (tmp_path / "CLAUDE.md").write_text("c" * 20, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("a" * 10, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "budget", str(tmp_path), "--json", "--fail-on", "warn"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed"
    assert doc["reason"] == f"{len(doc['data']['warnings'])} warning(s) under --fail-on warn"


def test_a_failed_inspect_gate_under_json_says_why(monkeypatch, capsys, tmp_path):
    from tests.test_repo_environment import _repo

    _repo(tmp_path, {"src/a.py": "x = 1\n"})  # the checks.test.missing ERROR of test_repo_inspect_cli
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "inspect", str(tmp_path), "--json", "--fail-on", "fail"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["reason"] == f"{doc['data']['counts']['error']} error(s) under --fail-on fail"


def test_inspect_fail_on_warn_gates_on_a_warning_finding(monkeypatch, capsys, tmp_path):
    from tests.test_repo_environment import _repo

    # runner-unknown is a WARNING and the tree has no ERROR: --fail-on fail passes, --fail-on warn fails
    _repo(tmp_path, {"AGENTS.md": "x", "Gemfile": "gem 'rake'\n", "spec/a_spec.rb": "x"})
    _run_main(monkeypatch, ["repo", "inspect", str(tmp_path), "--json", "--fail-on", "fail"])
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, ["repo", "inspect", str(tmp_path), "--json", "--fail-on", "warn"])
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    counts = doc["data"]["counts"]
    assert counts["error"] == 0 and counts["warning"]
    assert doc["reason"] == f"0 error(s), {counts['warning']} warning(s) under --fail-on warn"


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

    assert _scan_reason({"incomplete": True, "incomplete_reasons": [], "counts": {"fail": 0}}, [], True) == "scan incomplete"
    assert _scan_reason({"incomplete": False, "incomplete_reasons": [], "counts": {"fail": 2}}, [], True) == "2 claim(s) failed"
    # every cause the gate tripped on is in the reason, not only the first
    assert _scan_reason({"incomplete": True, "incomplete_reasons": ["x"], "counts": {"fail": 2}}, [], True) == \
        "2 claim(s) failed; scan incomplete: x"
    assert _scan_reason({"incomplete": False, "incomplete_reasons": [], "counts": {"fail": 0}}, ["bad"], True) == \
        "1 --static problem(s) not merged"
    # without --fail-on fail, failed claims and an incomplete scan are not what failed the run
    assert _scan_reason({"incomplete": True, "incomplete_reasons": ["x"], "counts": {"fail": 2}}, ["bad"], False) == \
        "1 --static problem(s) not merged"


def _leaf_with_json_and_jsonl() -> cli._LeafParser:
    leaf = cli._LeafParser(prog="dokimi probe")
    leaf.add_argument("source")
    leaf.add_argument("--json", action="store_true")
    leaf.add_argument("--jsonl")
    leaf.set_defaults(emits_cli_output=True)
    return leaf


def test_a_prefix_that_is_ambiguous_at_a_leaf_is_not_a_json_request():
    # no shipped leaf has --json next to another --js* option (catalog check's --jsonl has no --json): a probe leaf does
    leaf = _leaf_with_json_and_jsonl()
    assert cli._json_requested(leaf, ["--jso"]) is False
    assert cli._json_requested(leaf, ["--json"]) is True
    check = cli._leaf_at(cli.build_parser(), ["catalog", "check"])
    assert check is not None and cli._json_requested(check, ["--jso"]) is False


def test_an_ambiguous_json_prefix_at_a_leaf_prints_no_document(capsys):
    cli_output.reset()
    with pytest.raises(SystemExit) as exc:
        _leaf_with_json_and_jsonl().parse_args(["--jso"])
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert out.out == "" and "ambiguous option: --jso" in out.err


@pytest.mark.parametrize("argv, message", [
    (["eval", "check", "--schema-only"], "the following arguments are required: source"),
    (["eval", "check", "--schema-only", ".", "--cache-dir"], "argument --cache-dir: expected one argument"),
])
def test_a_leaf_usage_error_without_json_prints_its_message_on_stderr_only(monkeypatch, capsys, argv, message):
    # leaf.error runs for these (unlike an unrecognized --bogus, which the root reports): the flag gates its document
    cli_output.reset()
    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, argv)
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert out.out == "" and message in out.err


#: #941: every --json leaf is in exactly one of these sets, so a new leaf has to choose.
LEAVES_WITH_NEXT = {("repo", "scan"), ("repo", "inspect"), ("runs", "list")}
LEAVES_WITHOUT_NEXT_BY_DESIGN = {
    ("backends",), ("catalog", "check"), ("catalog", "eval"), ("catalog", "formats"), ("eval", "check"),
    ("eval", "graph"),
    ("eval", "inspect"), ("memory", "lint"), ("repo", "budget"), ("repo", "compare"), ("repo", "fetch"),
    ("repo", "hits", "judge"), ("repo", "report"), ("runs", "gc"), ("runs", "rm"), ("runs", "show"),
    ("skill", "routing"), ("trace", "consistency"), ("trace", "inspect"), ("trace", "prefilter"),
}


def test_every_json_leaf_gives_a_next_step_or_none_by_design():
    # #941: a new --json leaf must be added to exactly one of the two sets
    assert not LEAVES_WITH_NEXT & LEAVES_WITHOUT_NEXT_BY_DESIGN
    assert set(_json_leaves()) == LEAVES_WITH_NEXT | LEAVES_WITHOUT_NEXT_BY_DESIGN


def test_every_leaf_in_the_next_set_prints_a_step_on_a_real_run(monkeypatch, capsys, tmp_path):
    # #941: the set "gives a next step" is true of the code: each of its leaves prints a non-empty `next`
    from pathlib import Path

    from dokimi.artifacts import save_artifact

    repo = _guidance_repo(tmp_path)
    root = tmp_path / "results"
    root.mkdir()
    fixture = json.loads((Path(__file__).parent / "fixtures" / "artifacts" / "eval.v1.json").read_text("utf-8"))
    monkeypatch.setenv("DOKIMI_RUN_ID", "r-eval")
    save_artifact("eval", fixture, root / "eval" / "e.json")
    monkeypatch.delenv("DOKIMI_RUN_ID")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    cache = ["--cache-dir", str(tmp_path / "cache")]
    real = {("repo", "scan"): [str(repo), *cache], ("repo", "inspect"): [str(repo), *cache], ("runs", "list"): []}
    assert set(real) == LEAVES_WITH_NEXT
    for leaf, extra in real.items():
        doc = _json_run(monkeypatch, capsys, [*leaf, *extra])
        assert doc["next"], leaf
        assert doc["next"][0]["argv"][0] == "dokimi", leaf
        assert _exit_code(monkeypatch, [*doc["next"][0]["argv"][1:], "--json"]) == 0
        capsys.readouterr()


def _guidance_repo(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# Guidance\n\nRun `make test` before a commit.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    identity = ["-c", "user.name=dokimi-test", "-c", "user.email=dokimi-test@example.invalid"]
    subprocess.run(["git", "-C", str(repo), *identity, "commit", "-q", "-m", "init"], check=True)
    return repo


def test_the_guidance_repo_commits_without_a_configured_git_identity(monkeypatch, tmp_path):
    # a global config that refuses to guess an identity: the helper must carry its own
    config = tmp_path / "gitconfig"
    config.write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(name, raising=False)
    import subprocess

    repo = _guidance_repo(tmp_path)
    subject = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"],
                             capture_output=True, text=True, check=True).stdout.strip()
    assert subject == "init"


def _exit_code(monkeypatch, argv):
    try:
        _run_main(monkeypatch, argv)
    except SystemExit as exc:
        return exc.code or 0
    return 0


def _json_run(monkeypatch, capsys, argv):
    assert _exit_code(monkeypatch, [*argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _check_free_step(monkeypatch, capsys, doc, argv):
    assert schema_errors("cli_output.v1", doc) == []
    [step] = doc["next"]
    assert step["argv"] == argv
    assert (step["spends"], step["est_usd"], step["kind"]) == (False, 0, "sequence")
    assert _exit_code(monkeypatch, [*step["argv"][1:], "--json"]) == 0


@pytest.mark.parametrize("leaf", ["scan", "inspect"])
@pytest.mark.parametrize("with_cache", [False, True])
def test_repo_scan_and_inspect_name_repo_budget_and_it_runs(monkeypatch, capsys, tmp_path, leaf, with_cache):
    # #941: a complete document's next is `repo budget` on the same source, with --cache-dir when it was given
    repo = _guidance_repo(tmp_path)
    cache = ["--cache-dir", str(tmp_path / "cache")] if with_cache else []
    monkeypatch.setenv("DOKIMI_REPO_CACHE", str(tmp_path / "default-cache"))
    doc = _json_run(monkeypatch, capsys, ["repo", leaf, str(repo), *cache])
    assert doc["status"] == "complete"
    _check_free_step(monkeypatch, capsys, doc, ["dokimi", "repo", "budget", str(repo), *cache])


@pytest.mark.parametrize("leaf", ["scan", "inspect"])
def test_a_cache_dir_starting_with_a_dash_gives_a_step_that_runs(monkeypatch, capsys, tmp_path, leaf):
    # #941: `--cache-dir -cache` as two tokens is a usage error; the step keeps the value in one token
    repo = _guidance_repo(tmp_path)
    (tmp_path / "-cache").mkdir()
    monkeypatch.chdir(tmp_path)
    doc = _json_run(monkeypatch, capsys, ["repo", leaf, str(repo), "--cache-dir=-cache"])
    [step] = doc["next"]
    assert step["argv"] == ["dokimi", "repo", "budget", str(repo), "--cache-dir=-cache"]
    assert _exit_code(monkeypatch, [*step["argv"][1:], "--json"]) == 0


def test_a_failed_repo_inspect_has_no_next_step(monkeypatch, capsys, tmp_path):
    # #941: no step on a document that is not complete
    repo = _guidance_repo(tmp_path)
    (repo / "AGENTS.md").write_text("# Guidance\n\nRun `no-such-tool-941 --x` first.\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, ["repo", "inspect", str(repo), "--cache-dir", str(tmp_path / "c"), "--json",
                                "--fail-on", "warn", "--large-bytes", "1"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed" and doc["next"] == []


def test_a_failed_repo_scan_has_no_next_step(monkeypatch, capsys, tmp_path):
    # #941: a scan that fails on an unusable --static report names no step either
    repo = _guidance_repo(tmp_path)
    static = tmp_path / "static.json"
    static.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, ["repo", "scan", str(repo), "--cache-dir", str(tmp_path / "c"), "--static",
                                str(static), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed" and doc["next"] == []


def test_runs_list_skips_a_run_id_that_runs_show_would_read_as_a_file(monkeypatch, capsys, tmp_path):
    # #941: `runs show target.json` opens the file target.json before it looks for a run of that id
    from pathlib import Path

    from dokimi.artifacts import save_artifact

    root = _runs_root(monkeypatch, tmp_path)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "artifacts" / "eval.v1.json").read_text("utf-8"))
    for run_id, name, when in (("older", "target.json", "2026-11-25T00:00:00Z"),
                               ("target.json", "newest.json", "2026-11-26T00:00:00Z")):
        monkeypatch.setenv("DOKIMI_RUN_ID", run_id)
        save_artifact("eval", {**fixture, "evaluated_at": when}, root / name)
    monkeypatch.delenv("DOKIMI_RUN_ID")
    doc = _json_run(monkeypatch, capsys, ["runs", "list"])
    assert [r["run_id"] for r in doc["data"]["runs"]][:1] == ["target.json"]
    [step] = doc["next"]
    assert step["argv"] == ["dokimi", "runs", "show", "older"]


def _runs_root(monkeypatch, tmp_path):
    root = tmp_path / "results"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DOKIMI_RUNS_DIR", raising=False)
    return root


def test_runs_list_names_runs_show_of_the_newest_run_and_it_runs(monkeypatch, capsys, tmp_path):
    from pathlib import Path

    from dokimi.artifacts import save_artifact

    root = _runs_root(monkeypatch, tmp_path)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "artifacts" / "eval.v1.json").read_text("utf-8"))
    monkeypatch.setenv("DOKIMI_RUN_ID", "r-eval")
    save_artifact("eval", fixture, root / "eval" / "e.json")
    monkeypatch.delenv("DOKIMI_RUN_ID")
    doc = _json_run(monkeypatch, capsys, ["runs", "list"])
    assert doc["status"] == "complete"
    _check_free_step(monkeypatch, capsys, doc, ["dokimi", "runs", "show", doc["data"]["runs"][0]["run_id"]])


def test_runs_list_without_a_run_has_no_next_step(monkeypatch, capsys, tmp_path):
    # #941: an empty root, and artifacts that carry no run_id, give no `runs show` target
    root = _runs_root(monkeypatch, tmp_path)
    assert _json_run(monkeypatch, capsys, ["runs", "list"])["next"] == []
    (root / "pairwise.json").write_text(json.dumps({"schema_version": "pairwise.v0"}), encoding="utf-8")
    assert _json_run(monkeypatch, capsys, ["runs", "list"])["next"] == []


def test_a_partial_runs_list_has_no_next_step(monkeypatch, capsys, tmp_path):
    from pathlib import Path

    from dokimi.artifacts import save_artifact

    root = _runs_root(monkeypatch, tmp_path)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "artifacts" / "eval.v1.json").read_text("utf-8"))
    monkeypatch.setenv("DOKIMI_RUN_ID", "r-eval")
    save_artifact("eval", fixture, root / "eval" / "e.json")
    monkeypatch.delenv("DOKIMI_RUN_ID")
    (root / "broken.json").write_text('{"schema_version": "eval.v1", "evaluations": [', encoding="utf-8")
    doc = _json_run(monkeypatch, capsys, ["runs", "list"])
    assert doc["status"] == "partial" and doc["next"] == []


@pytest.mark.parametrize("leaf", ["scan", "inspect"])
def test_a_source_starting_with_a_dash_is_refused_so_no_step_names_it(monkeypatch, capsys, tmp_path, leaf):
    # `repo scan --json -- -repo`: the source parser refuses it, so no `repo budget -repo` step is ever printed
    _guidance_repo(tmp_path).rename(tmp_path / "-repo")
    monkeypatch.chdir(tmp_path)
    assert _exit_code(monkeypatch, ["repo", leaf, "--json", "--", "-repo"]) != 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed"
    assert doc["next"] == []
