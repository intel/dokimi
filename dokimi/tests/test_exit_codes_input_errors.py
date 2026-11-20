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
    return ["eval", "personas", "validate", "--file", _missing(tmp_path)]


def _personas_discover_no_source(tmp_path):
    return ["eval", "personas", "discover", "--product", "lib"]


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


def _headroom_unreadable_arms(tmp_path):
    return ["eval", "headroom", str(tmp_path / "missing.json")]


def _library_list_unknown_domain(tmp_path):
    return ["catalog", "list", "--kind", "product", "--domain", "no-such-domain"]


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
        pytest.param(_library_list_unknown_domain, "Intent domain 'no-such-domain' not found",
                     id="library_list_unknown_domain"),
        pytest.param(_headroom_unreadable_arms, "cannot read",
                     id="headroom_unreadable_arms"),
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
         "--judge-provider", "openai", "--judge-model", "judge-x", "--out-dir", str(tmp_path / "out")],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert "usage:" not in err
    assert "missing OPENAI_API_KEY for provider openai" in err
    assert exc.value.code == 2


@pytest.mark.parametrize("command, extra", [("run", []), ("batch", ["--libraries", "onetbb"])])
def test_benchmark_run_and_batch_missing_credential_exit_2_before_the_pipeline(
    command, extra, tmp_path, monkeypatch, capsys
):
    """#967: `benchmark run|batch` refuse a missing key as `benchmark preflight` does, with no output dir."""
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    class _Pipeline:
        def __init__(self, *a, **kw):
            raise AssertionError("the pipeline was built without provider credentials")

    monkeypatch.setattr("dokimi.orchestrator.EvaluationPipeline", _Pipeline)
    out = tmp_path / "out"
    flag = "--out-dir"
    lib = ["--library", "onetbb"] if command == "run" else []
    monkeypatch.setattr("sys.argv", ["dokimi", "benchmark", command, *lib, *extra, flag, str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "missing OPENAI_API_KEY for provider openai" in err
    assert "missing ANTHROPIC_API_KEY for provider anthropic" in err
    assert exc.value.code == 2
    assert not out.exists()


def test_benchmark_run_with_both_keys_set_reaches_the_pipeline(tmp_path, monkeypatch):
    """#967: the credential check does not block a run whose keys are set."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    built = []

    class _Pipeline:
        def __init__(self, **kw):
            built.append(kw)

        def run(self, concurrency):
            return {"steps": {}}

    monkeypatch.setattr("dokimi.orchestrator.EvaluationPipeline", _Pipeline)
    monkeypatch.setattr(
        "sys.argv", ["dokimi", "benchmark", "run", "--library", "onetbb", "--out-dir", str(tmp_path / "out")]
    )
    cli.main()
    assert len(built) == 1


@pytest.mark.parametrize(
    "flag, vertex", [("--provider", "google-vertex"), ("--judge-provider", "google-vertex"),
                     ("--judge-provider", "google")],
)
def test_benchmark_run_vertex_adc_needs_no_gemini_key(flag, vertex, tmp_path, monkeypatch):
    """#967: Vertex reads application-default credentials (docs/reviewing.md), so no `GEMINI_API_KEY`."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "adc.json"))
    built = []

    class _Pipeline:
        def __init__(self, **kw):
            built.append(kw)

        def run(self, concurrency):
            return {"steps": {}}

    monkeypatch.setattr("dokimi.orchestrator.EvaluationPipeline", _Pipeline)
    argv = ["dokimi", "benchmark", "run", "--library", "onetbb", "--out-dir", str(tmp_path / "out"),
            flag, vertex]
    if flag == "--provider":
        argv += ["--model", "gemini-2.5-pro"]
    else:
        argv += ["--judge-model", "gemini-2.5-pro"]
    monkeypatch.setattr("sys.argv", argv)
    cli.main()
    assert len(built) == 1


def _run_with_fake_pipeline(monkeypatch, tmp_path, argv_tail):
    built = []

    class _Pipeline:
        def __init__(self, **kw):
            built.append(kw)

        def run(self, concurrency):
            return {"steps": {}}

    monkeypatch.setattr("dokimi.orchestrator.EvaluationPipeline", _Pipeline)
    out = tmp_path / "out"
    monkeypatch.setattr(
        "sys.argv", ["dokimi", "benchmark", "run", "--library", "onetbb", "--out-dir", str(out), *argv_tail]
    )
    return built, out


def test_benchmark_run_bedrock_iam_credentials_need_no_bearer_token(tmp_path, monkeypatch):
    """#967: Bedrock also runs on AWS access keys, so a missing `AWS_BEARER_TOKEN_BEDROCK` is no refusal."""
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    built, _ = _run_with_fake_pipeline(
        monkeypatch, tmp_path,
        ["--provider", "amazon-bedrock", "--model", "us.anthropic.claude-sonnet-4-6",
         "--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.claude-haiku-4-5"],
    )
    cli.main()
    assert len(built) == 1


def test_benchmark_run_gemini_route_without_a_gemini_key_exits_2(tmp_path, monkeypatch, capsys):
    """#967: a `gemini/` model takes the Gemini API key, not Vertex ADC, so a missing key is refused."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    built, out = _run_with_fake_pipeline(
        monkeypatch, tmp_path,
        ["--provider", "google-vertex", "--model", "gemini/gemini-2.5-pro",
         "--judge-provider", "openai", "--judge-model", "gpt-4o"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert "missing GEMINI_API_KEY for provider google-vertex" in capsys.readouterr().err
    assert exc.value.code == 2
    assert not built and not out.exists()


def test_benchmark_run_gemini_route_takes_google_api_key(tmp_path, monkeypatch):
    """#967: litellm reads `GOOGLE_API_KEY` for a `gemini/` model when `GEMINI_API_KEY` is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    built, _ = _run_with_fake_pipeline(
        monkeypatch, tmp_path,
        ["--provider", "google-vertex", "--model", "gemini/gemini-2.5-pro",
         "--judge-provider", "openai", "--judge-model", "gpt-4o"],
    )
    cli.main()
    assert len(built) == 1


def test_benchmark_run_gemini_route_takes_gemini_api_key(tmp_path, monkeypatch):
    """#967: `GEMINI_API_KEY` alone lets a `gemini/` model on `google-vertex` through."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    built, _ = _run_with_fake_pipeline(
        monkeypatch, tmp_path,
        ["--provider", "openai", "--model", "gpt-4o",
         "--judge-provider", "google", "--judge-model", "gemini/gemini-2.5-pro"],
    )
    cli.main()
    assert len(built) == 1


def test_benchmark_run_explicit_vertex_route_needs_no_provider_key(tmp_path, monkeypatch):
    """#967: `--provider anthropic --model vertex_ai/...` runs on ADC, so an unset `ANTHROPIC_API_KEY` is no refusal."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    built, _ = _run_with_fake_pipeline(
        monkeypatch, tmp_path,
        ["--provider", "anthropic", "--model", "vertex_ai/gemini-2.5-pro",
         "--judge-provider", "google-vertex", "--judge-model", "gemini-2.5-pro"],
    )
    cli.main()
    assert len(built) == 1
