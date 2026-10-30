# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exit codes of the input and usage refusals, per command (the exit-code table in docs/quickstart.md).

A bad input file, an input the command cannot use, a flag combination it refuses and a
missing credential exit 2. Exit 1 is reserved for a verdict or a gate that ran and failed.
Each case is one command; argparse refusals are excluded by the ``usage:`` guard, so a
case cannot pass on a flag typo.
"""

from __future__ import annotations

import pytest

from dokimi import cli


def _missing(tmp_path):
    return str(tmp_path / "missing.json")


def _personas_approve(tmp_path):
    return ["personas", "approve", "--file", _missing(tmp_path)]


def _personas_discover_no_source(tmp_path):
    return ["personas", "discover", "--product", "lib"]


def _tools_preference_missing_groups(tmp_path):
    return ["tools", "preference", "--groups", _missing(tmp_path), "--model", "m"]


def _report_eval_missing(tmp_path):
    return ["report", "eval", "--product", "lib", "--eval", _missing(tmp_path)]


def _report_generate_missing_questions(tmp_path):
    eval_file = tmp_path / "eval.json"  # a readable eval, so the questions file is the refusal
    eval_file.write_text('{"evaluations": [{"question_id": "q1"}]}', encoding="utf-8")  # a run that judged something
    return ["report", "generate", "--product", "lib", "--eval", str(eval_file),
            "--questions", _missing(tmp_path)]


def _baseline_save_missing(tmp_path):
    return ["baseline", "save", "--from-eval", _missing(tmp_path)]


def _novelty_routing_flag_combination(tmp_path):
    return ["novelty", "routing", "--all-vs-all", "--artifact", _missing(tmp_path)]


def _library_list_unknown_domain(tmp_path):
    return ["library", "list", "--domain", "no-such-domain"]


@pytest.mark.parametrize(
    ("argv_for", "needle"),
    [
        pytest.param(_personas_approve, "missing.json", id="personas_approve"),
        pytest.param(_personas_discover_no_source, "Either --repo or --description must be provided.",
                     id="personas_discover_no_source"),
        pytest.param(_tools_preference_missing_groups, "missing.json", id="tools_preference_missing_groups"),
        pytest.param(_report_eval_missing, "missing.json", id="report_eval_missing"),
        pytest.param(_report_generate_missing_questions, "missing.json", id="report_generate_missing_questions"),
        pytest.param(_baseline_save_missing, "missing.json", id="baseline_save_missing"),
        pytest.param(_novelty_routing_flag_combination, "--all-vs-all takes no --artifact",
                     id="novelty_routing_flag_combination"),
        pytest.param(_library_list_unknown_domain, "Intent domain 'no-such-domain' not found",
                     id="library_list_unknown_domain"),
    ],
)
def test_input_or_usage_refusal_exits_2_with_a_message(argv_for, needle, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv_for(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert "usage:" not in err, "argparse refused the flags; the case does not reach the rule"
    assert "Traceback" not in err
    # The message must name the input at fault, not just any exit 2.
    assert needle in err
    assert exc.value.code == 2


def test_explore_list_without_a_bundled_task_root_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dokimi.explore.tasks.EXPLORE_TASKS_ROOT", tmp_path / "absent")
    monkeypatch.setattr("sys.argv", ["dokimi", "explore", "list"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert "Pass --tasks-root" in capsys.readouterr().err
    assert exc.value.code == 2


def test_benchmark_preflight_missing_credential_exits_2(tmp_path, monkeypatch, capsys):
    for var in ("OPENAI_API_KEY", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["dokimi", "benchmark", "preflight", "--library", "onetbb", "--provider", "openai",
         "--judge-provider", "openai", "--output-dir", str(tmp_path / "out")],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert "usage:" not in err
    assert "missing OPENAI_API_KEY for provider openai" in err
    assert exc.value.code == 2
