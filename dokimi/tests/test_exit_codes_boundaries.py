# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The validation/runtime boundary of the exit-code table (CLI API v2 §2.6).

Only input and usage validation exits 2. A failure while the run is under way (an arm run,
a fetch, a probe workspace, a model call, a missing optional dependency) keeps the code it
had before the audit, 1.
"""

from __future__ import annotations

import importlib.util
import json

import pytest

from dokimi import cli
from dokimi.commands import arms_sampling, benchmark, evaluate
from dokimi.records import RecordError
from dokimi.repos import SourceError
from dokimi.repos.reach import ReachError

PIN = "a" * 40


def _argv(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["dokimi", *argv])


def _questions(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps([{"id": "q1", "question": "How do I parallelize a loop?"}]), encoding="utf-8")
    return str(path)


def _arms_argv(tmp_path, *extra):
    return ["arms", "run", "--product", "onedal", "--questions", _questions(tmp_path),
            "--arms", "baseline", "--out-json", str(tmp_path / "a.json"),
            "--out-md", str(tmp_path / "a.md"), *extra]


# ── arms run: a runtime failure keeps 1; an unusable arm spec is 2 ─────────────────────────

@pytest.mark.parametrize(
    "error",
    [ValueError("simulated runtime failure in the arm run"),
     RecordError("answers[0]: missing question_id")],
    ids=["value-error", "record-error"],
)
def test_arm_run_failure_keeps_exit_1(error, tmp_path, monkeypatch, capsys):
    def boom(self, *args, **kwargs):
        raise error

    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run", boom)
    _argv(monkeypatch, *_arms_argv(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert str(error) in capsys.readouterr().err


def test_arm_run_sampling_probe_failure_keeps_exit_1_before_any_answer(tmp_path, monkeypatch, capsys):
    # The probe is one real model call: its failure is a model failure, not a usage error.
    def refused(*args, **kwargs):
        raise ValueError("Probe call to 'openai/m' failed, so every call in the run would too")

    answered = []
    monkeypatch.setattr("dokimi.commands.arms._probe_sampling", refused)
    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run",
                        lambda self, *a, **k: answered.append(1) or [])
    _argv(monkeypatch, *_arms_argv(tmp_path, "--judge", "--judge-temperature", "0.5"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert not answered
    assert "Probe call" in capsys.readouterr().err


def test_arm_run_unknown_arm_spec_exits_2(tmp_path, monkeypatch, capsys):
    _argv(monkeypatch, *_arms_argv(tmp_path)[:-4], "--arms", "no-such-arm-kind",
          "--out-json", str(tmp_path / "a.json"), "--out-md", str(tmp_path / "a.md"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "Error building arms/plugins" in capsys.readouterr().err


@pytest.fixture
def fresh_probe_cache():
    # _probe_sampling is cached per process; each test starts with no cached probe.
    arms_sampling._probe_sampling.cache_clear()
    yield
    arms_sampling._probe_sampling.cache_clear()


def test_matrix_cell_refused_at_probe_stops_before_any_answer(tmp_path, monkeypatch, capsys, fresh_probe_cache):
    # Cell 1 is accepted, cell 2 refuses the temperature. Every cell is probed before the first
    # answer, so the refusal costs no answer call and writes no cell artifact.
    probed = []

    def fake_usage(prompt, model, provider="openai", **kwargs):
        probed.append(model)
        if model == "reject-temp-model":
            raise RuntimeError("400: temperature not supported for this model")
        return ("OK", {})

    answered = []
    monkeypatch.setattr("dokimi.llm.llm_call_with_usage", fake_usage)
    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run",
                        lambda self, *a, **k: answered.append(1) or [])
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [
        {"id": "cell-ok", "model": "gpt-4o-mini", "provider": "openai", "harness": "arms-runner"},
        {"id": "cell-bad", "model": "reject-temp-model", "provider": "openai", "harness": "arms-runner"},
    ]}), encoding="utf-8")
    _argv(monkeypatch, "arms", "matrix-run", "--product", "onedal", "--questions", _questions(tmp_path),
          "--arms", "baseline", "--matrix-cells", str(cells), "--temperature", "0.5",
          "--out-dir", str(tmp_path / "matrix"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert probed == ["gpt-4o-mini", "reject-temp-model"]  # each cell probed once, in order
    assert answered == []
    assert not (tmp_path / "matrix" / "cells").exists()
    assert "matrix cell 'cell-bad'" in capsys.readouterr().err


# ── repo fetch / reach: a fetch failure keeps 1; a bad source or profile is 2 ──────────────

def _failed_fetch(source, cache_dir=None):
    raise SourceError("git fetch failed: could not connect to 127.0.0.1:9")


def test_repo_fetch_git_failure_keeps_exit_1(monkeypatch, capsys):
    monkeypatch.setattr("dokimi.repos.open_source", _failed_fetch)
    _argv(monkeypatch, "repo", "fetch", f"https://127.0.0.1:9/example.git@{PIN}")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "git fetch failed" in capsys.readouterr().err


def test_repo_fetch_unpinned_remote_exits_2(monkeypatch, capsys):
    _argv(monkeypatch, "repo", "fetch", "https://example.com/example.git")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "must be pinned" in capsys.readouterr().err


REPO_SOURCE_COMMANDS = ["validate", "scan", "inspect", "budget"]


@pytest.mark.parametrize("command", REPO_SOURCE_COMMANDS)
def test_repo_unreachable_source_keeps_exit_1(command, tmp_path, monkeypatch, capsys):
    # A pinned source that cannot be fetched is a fetch failure (network), not a bad invocation.
    _argv(monkeypatch, "repo", command, f"https://127.0.0.1:9/example.git@{PIN}",
          "--cache-dir", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "✗" in capsys.readouterr().err


@pytest.mark.parametrize("command", REPO_SOURCE_COMMANDS)
def test_repo_unpinned_remote_exits_2_for_every_source_command(command, tmp_path, monkeypatch, capsys):
    _argv(monkeypatch, "repo", command, "https://example.com/example.git",
          "--cache-dir", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "✗" in capsys.readouterr().err


def test_repo_reach_fetch_failure_keeps_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dokimi.repos.open_source", _failed_fetch)
    _argv(monkeypatch, "repo", "reach", f"https://127.0.0.1:9/example.git@{PIN}",
          "--model", "m", "--out", str(tmp_path / "out"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "git fetch failed" in capsys.readouterr().err


def test_repo_reach_unknown_harness_profile_exits_2(tmp_path, monkeypatch, capsys):
    _argv(monkeypatch, "repo", "reach", str(tmp_path), "--harness", "no-such-harness",
          "--model", "m", "--out", str(tmp_path / "out"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "no harness profile" in capsys.readouterr().err


def test_repo_reach_probe_workspace_failure_keeps_exit_1(tmp_path, monkeypatch, capsys):
    (tmp_path / "CLAUDE.md").write_text("# guidance\n", encoding="utf-8")

    def workspace_failed(*args, **kwargs):
        raise ReachError("could not create the probe workspace")

    monkeypatch.setattr("dokimi.repos.reach.run_reach", workspace_failed)
    _argv(monkeypatch, "repo", "reach", str(tmp_path), "--model", "m", "--out", str(tmp_path / "out"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "probe workspace" in capsys.readouterr().err


def test_repo_reach_unrunnable_harness_binary_keeps_exit_1_with_a_message(tmp_path, monkeypatch, capsys):
    # run_reach asks the harness for its version first: a --bin that cannot run is a runtime failure
    # and must print its message, not escape as a ProfileError traceback.
    (tmp_path / "CLAUDE.md").write_text("# guidance\n", encoding="utf-8")
    _argv(monkeypatch, "repo", "reach", str(tmp_path), "--model", "m", "--bin", str(tmp_path / "no-such-claude"),
          "--out", str(tmp_path / "out"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "cannot run" in capsys.readouterr().err


def test_repo_reach_workspace_source_error_keeps_exit_1_with_a_message(tmp_path, monkeypatch, capsys):
    (tmp_path / "CLAUDE.md").write_text("# guidance\n", encoding="utf-8")

    def links_escape(*args, **kwargs):
        raise SourceError("path escapes the repository: ../x")

    monkeypatch.setattr("dokimi.repos.reach.run_reach", links_escape)
    _argv(monkeypatch, "repo", "reach", str(tmp_path), "--model", "m", "--out", str(tmp_path / "out"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "path escapes the repository" in capsys.readouterr().err


# ── benchmark batch: nothing selected is a usage refusal, as with an unknown library ───────

class _NoLibraries:
    def get(self, key):
        raise KeyError(key)

    def list(self):
        return []


def test_batch_with_no_known_library_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _NoLibraries())
    _argv(monkeypatch, "benchmark", "batch", "--libraries", "no-such-library")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "No libraries to run." in capsys.readouterr().err


# ── benchmark preflight: an invalid --doc-source is 2; a source that fails its check is 1 ──

class _OneLibrary:
    def get(self, key):
        from dokimi.registry import ProductEntry

        return ProductEntry(key="onetbb", name="oneTBB", description="TBB", doc_sources=["local:docs"])


def test_preflight_unknown_doc_source_format_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _OneLibrary())
    _argv(monkeypatch, "benchmark", "preflight", "--library", "onetbb",
          "--doc-source", "bogus:docs", "--output-dir", str(tmp_path / "out"),
          "--provider", "openai", "--judge-provider", "openai")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "Unknown --doc-source format" in capsys.readouterr().err


# ── catalog trigger-set: input refusals are 2; a rewrite that returns nothing is 1 ─────────

def _skill_root(tmp_path):
    root = tmp_path / "skills"
    skill = root / "linux-perf"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: linux-perf\ndescription: "Profile Linux programs with perf."\n---\n# linux-perf\nbody\n',
        encoding="utf-8",
    )
    return root


def _trigger_questions(tmp_path, questions):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    return f"{path}=@metadata.skill_under_test"


def test_trigger_set_rewrite_returning_nothing_keeps_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("dokimi.llm.llm_call", lambda *args, **kwargs: "   ")
    spec = _trigger_questions(tmp_path, [
        {"id": "1", "text": "perf shows low IPC", "metadata": {"skill_under_test": "linux-perf"}},
    ])
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--questions", spec, "--rewrite", "--model", "m", "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "rewrite returned empty text" in capsys.readouterr().err


def test_trigger_set_question_without_text_exits_2(tmp_path, monkeypatch, capsys):
    spec = _trigger_questions(tmp_path, [
        {"id": "1", "metadata": {"skill_under_test": "linux-perf"}},
    ])
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--questions", spec, "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "has no text" in capsys.readouterr().err


def test_trigger_set_no_text_refused_before_any_rewrite_call(tmp_path, monkeypatch, capsys):
    # The set is refused on its second question; the first must not have been sent to the rewrite model.
    rewrite_calls = []
    monkeypatch.setattr("dokimi.llm.llm_call", lambda *args, **kwargs: rewrite_calls.append(1) or "rewritten")
    spec = _trigger_questions(tmp_path, [
        {"id": "1", "text": "perf shows low IPC", "metadata": {"skill_under_test": "linux-perf"}},
        {"id": "2", "metadata": {"skill_under_test": "linux-perf"}},
    ])
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--questions", spec, "--rewrite", "--model", "m", "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "has no text" in capsys.readouterr().err
    assert rewrite_calls == []
    assert not (tmp_path / "o.json").exists()


def test_trigger_set_strict_refusal_exits_1(tmp_path, monkeypatch, capsys):
    # One question is labelled and one is not, so the set has an unresolved label; --strict refuses it.
    # --strict is a lint gate over a valid set: its verdict is exit 1, as on main.
    spec = _trigger_questions(tmp_path, [
        {"id": "1", "text": "perf shows low IPC", "metadata": {"skill_under_test": "linux-perf"}},
        {"id": "2", "text": "what is the weather"},
    ])
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--questions", spec, "--strict", "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert not (tmp_path / "o.json").exists()


# ── evaluate: a missing optional dependency keeps its base exit (a message, exit 1) ────────

def test_missing_ragas_extra_keeps_its_base_exit(monkeypatch):
    real_find_spec = importlib.util.find_spec

    def without_ragas(name, *args, **kwargs):
        return None if name == "ragas" else real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", without_ragas)
    message = "RAGAS meta-evaluation needs the ragas extra: pip install 'dokimi[ragas]'"
    with pytest.raises(SystemExit) as exc:
        evaluate._require_ragas()
    assert exc.value.code == message
