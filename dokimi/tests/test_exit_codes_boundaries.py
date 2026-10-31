# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The validation/runtime boundary of the exit-code table (docs/quickstart.md).

Only input and usage validation exits 2. A failure while the run is under way (an arm run,
a fetch, a probe workspace, a model call, a missing optional dependency) keeps the code it
had before the audit, 1.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dokimi import cli
from dokimi.commands import benchmark, evaluate
from dokimi.eval import arms_run
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
        raise arms_run.ProbeError("Probe call to 'openai/m' failed, so every call in the run would too")

    answered = []
    monkeypatch.setattr("dokimi.eval.arms_run.probe_sampling", refused)
    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run",
                        lambda self, *a, **k: answered.append(1) or [])
    _argv(monkeypatch, *_arms_argv(tmp_path, "--judge", "--judge-temperature", "0.5"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert not answered
    assert "Probe call" in capsys.readouterr().err


def test_arm_run_unknown_arm_spec_exits_2(tmp_path, monkeypatch, capsys):
    _argv(monkeypatch, "arms", "run", "--product", "onedal", "--questions", _questions(tmp_path),
          "--arms", "no-such-arm-kind", "--out-json", str(tmp_path / "a.json"),
          "--out-md", str(tmp_path / "a.md"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "Error building arms/plugins" in capsys.readouterr().err


@pytest.fixture
def fresh_probe_cache():
    # probe_sampling is cached per process; each test starts with no cached probe.
    arms_run.probe_sampling.cache_clear()
    yield
    arms_run.probe_sampling.cache_clear()


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
    from dokimi.exit_codes import EXIT_FAILURE

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == EXIT_FAILURE
    assert probed == ["gpt-4o-mini", "reject-temp-model"]  # each cell probed once, in order
    assert answered == []
    assert not (tmp_path / "matrix" / "cells").exists()
    assert "matrix cell 'cell-bad'" in capsys.readouterr().err


def test_matrix_cell_that_cannot_be_set_up_exits_2_before_any_answer(tmp_path, monkeypatch, capsys):
    # Cell 2 cannot be set up (a refused input, not a model failure): usage error, and cell 1,
    # which was set up, has not been answered or written either.
    real_prepare = arms_run.prepare_arms
    calls = []

    def prepare(config, specs, questions):
        calls.append(config.model)
        if config.model == "bad-cell-model":
            raise ValueError("this cell names a harness that cannot run its plugins")
        return real_prepare(config, specs, questions)

    answered = []
    monkeypatch.setattr("dokimi.eval.arms_run.prepare_arms", prepare)
    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run",
                        lambda self, *a, **k: answered.append(1) or [])
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [
        {"id": "cell-ok", "model": "gpt-4o-mini", "provider": "openai", "harness": "arms-runner"},
        {"id": "cell-bad", "model": "bad-cell-model", "provider": "openai", "harness": "arms-runner"},
    ]}), encoding="utf-8")
    _argv(monkeypatch, "arms", "matrix-run", "--product", "onedal", "--questions", _questions(tmp_path),
          "--arms", "baseline", "--matrix-cells", str(cells), "--out-dir", str(tmp_path / "matrix"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert calls == ["gpt-4o-mini", "bad-cell-model"]
    assert answered == []
    assert not (tmp_path / "matrix").exists()
    assert "Error building matrix cell 'cell-bad'" in capsys.readouterr().err


def _one_cell(tmp_path):
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"cells": [
        {"id": "c1", "model": "gpt-4o-mini", "provider": "openai", "harness": "arms-runner"}]}), encoding="utf-8")
    return ["arms", "matrix-run", "--product", "onedal", "--questions", _questions(tmp_path), "--arms", "baseline",
            "--matrix-cells", str(cells), "--out-dir", str(tmp_path / "matrix")]


def test_matrix_cell_that_fails_while_it_runs_exits_1(tmp_path, monkeypatch, capsys):
    # A provider failure mid-matrix is a run failure, not a bad invocation.
    def fail(self, *a, **k):
        raise ValueError("provider returned 500")

    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run", fail)
    _argv(monkeypatch, *_one_cell(tmp_path))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "Error running matrix cell 'c1': provider returned 500" in capsys.readouterr().err


def test_matrix_cell_whose_judge_probe_fails_exits_1_before_any_answer(tmp_path, monkeypatch, capsys,
                                                                       fresh_probe_cache):
    # The judge is probed per cell in setup; a failed probe call is a run failure (1), not a refusal (2).
    def fake_usage(prompt, model, provider="openai", **kwargs):
        if model == "judge-x":
            raise RuntimeError("503: judge unavailable")
        return ("OK", {})

    answered = []
    monkeypatch.setattr("dokimi.llm.llm_call_with_usage", fake_usage)
    monkeypatch.setattr("dokimi.eval.arm_runner.ArmRunner.run", lambda self, *a, **k: answered.append(1) or [])
    _argv(monkeypatch, *_one_cell(tmp_path), "--judge", "--judge-model", "judge-x", "--judge-temperature", "0.5")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert answered == []
    assert "Error running matrix cell 'c1'" in capsys.readouterr().err


# ── repo fetch / reach: a fetch failure keeps 1; a bad source or profile is 2 ──────────────

def _failed_fetch(source, cache_dir=None):
    raise SourceError("git fetch failed: could not connect to 127.0.0.1:9")


def test_matrix_out_dir_with_no_existing_ancestor_exits_2(tmp_path, monkeypatch, capsys):
    # No ancestor of the out dir exists (an unmounted drive): a refused path, not a StopIteration.
    from dokimi.commands import arms

    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
    monkeypatch.setattr("pathlib.Path.is_symlink", lambda self: False)
    with pytest.raises(SystemExit) as exc:
        arms._check_cells_dir(tmp_path / "z" / "out")
    assert exc.value.code == 2
    assert "cannot write" in capsys.readouterr().err


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
    # open_source is stubbed, so the result does not depend on git, the network or the host.
    monkeypatch.setattr("dokimi.repos.open_source", _failed_fetch)
    _argv(monkeypatch, "repo", command, f"https://127.0.0.1:9/example.git@{PIN}",
          "--cache-dir", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert "✗ git fetch failed" in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    OSError("[Errno 17] File exists: '/tmp/cache'"),  # the cache dir cannot be made
    ValueError("'utf-8' codec can't decode byte 0xe9 in position 3"),  # git output is not UTF-8
])
@pytest.mark.parametrize("command", REPO_SOURCE_COMMANDS)
def test_repo_source_os_or_value_error_keeps_exit_1_with_a_message(error, command, tmp_path, monkeypatch, capsys):
    # open_source can raise these outside SourceError; the command prints them and keeps 1, as on main.
    def open_failed(source, cache_dir=None):
        raise error

    monkeypatch.setattr("dokimi.repos.open_source", open_failed)
    _argv(monkeypatch, "repo", command, f"https://127.0.0.1:9/example.git@{PIN}",
          "--cache-dir", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize("error", [
    OSError("[Errno 20] Not a directory: '/tmp/cache'"),  # the cache dir cannot be made
    ValueError("'utf-8' codec can't decode byte 0xe9 in position 3"),  # git output is not UTF-8
])
@pytest.mark.parametrize("command", ["fetch", "reach"])
def test_repo_fetch_and_reach_os_or_value_error_keeps_exit_1_with_a_message(command, error, tmp_path,
                                                                            monkeypatch, capsys):
    # fetch and reach print the failure and keep 1, as the other repo commands do; no traceback.
    def open_failed(source, cache_dir=None):
        raise error

    monkeypatch.setattr("dokimi.repos.open_source", open_failed)
    extra = ["--model", "m", "--out", str(tmp_path / "out")] if command == "reach" else []
    _argv(monkeypatch, "repo", command, f"https://127.0.0.1:9/example.git@{PIN}",
          "--cache-dir", str(tmp_path / "cache"), *extra)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    assert str(error) in capsys.readouterr().err


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


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("command", [*REPO_SOURCE_COMMANDS, "fetch", "reach"])
def test_repo_pinned_local_source_inside_a_repository_exits_2(command, tmp_path, monkeypatch, capsys):
    # A pinned local path that is a subdirectory, not the root, is a mistyped argument; no git operation
    # failed. open_source says so before it resolves the pin.
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "f.txt").write_text("x", encoding="utf-8")
    # gpgsign is off here: a host with a global commit.gpgsign=true would refuse the commit.
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
                  "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    extra = ["--model", "m"] if command == "reach" else []
    err = _expect_exit(monkeypatch, capsys, 2, "repo", command, f"{repo / 'sub'}@HEAD", *extra)
    assert "inside a repository; give its root" in err


@pytest.mark.parametrize("command", ["validate", "scan"])
def test_repo_manifest_path_leaving_the_tree_exits_2_with_a_message(command, tmp_path, monkeypatch, capsys):
    # The tree refuses a path that escapes it with SourceError; the manifest is the caller's input.
    repo = tmp_path / "repo"
    repo.mkdir()
    _argv(monkeypatch, "repo", command, str(repo), "--manifest", "../x.yaml")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "path escapes the repository" in capsys.readouterr().err


REACH_ARGS = ["--harness", "claude-code", "--model", "m", "--out", "reach-out"]


@pytest.mark.parametrize("command", ["validate", "scan", "reach"])
def test_repo_manifest_read_that_fails_in_git_keeps_exit_1(command, tmp_path, monkeypatch, capsys):
    # A `git show` that fails while the manifest is read (a blob fetched lazily from origin that is gone)
    # is a fetch failure, not a bad manifest path: exit 1, as on main.
    def failing_load(tree, args, **kwargs):
        raise SourceError("git show failed: fatal: could not fetch the blob from promisor remote")

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("dokimi.commands.repo._load_manifest", failing_load)
    extra = [*REACH_ARGS[:-1], str(tmp_path / "reach-out")] if command == "reach" else []
    err = _expect_exit(monkeypatch, capsys, 1, "repo", command, str(repo), *extra)
    assert "git show failed" in err


def _expect_exit(monkeypatch, capsys, code, *argv):
    _argv(monkeypatch, *argv)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == code
    return capsys.readouterr().err


def test_repo_validate_invalid_manifest_exits_2(tmp_path, monkeypatch, capsys):
    (tmp_path / ".agent-eval").mkdir()
    (tmp_path / ".agent-eval" / "repo-eval.yaml").write_text("schema: no-such-schema\n", encoding="utf-8")
    err = _expect_exit(monkeypatch, capsys, 2, "repo", "validate", str(tmp_path))
    assert err.startswith("✗ ")


@pytest.mark.parametrize("command", ["validate", "scan"])
def test_repo_manifest_yaml_timestamp_out_of_range_exits_2(command, tmp_path, monkeypatch, capsys):
    # PyYAML raises a bare ValueError for a date it cannot build: a manifest error, not a traceback.
    (tmp_path / ".agent-eval").mkdir()
    (tmp_path / ".agent-eval" / "repo-eval.yaml").write_text("id: 2026-99-99\n", encoding="utf-8")
    err = _expect_exit(monkeypatch, capsys, 2, "repo", command, str(tmp_path))
    assert err.startswith("✗ ") and "Traceback" not in err


def test_manifest_read_that_fails_in_git_stays_a_source_error():
    # SourceError is a ValueError: a failed `git show` must not be relabelled a YAML error (exit 2).
    from dokimi.repos.manifest import load_manifest

    class FailingTree:
        label = "repo"

        def is_file(self, path):
            return True

        def read_text(self, path):
            raise SourceError("git show failed for the manifest blob")

    with pytest.raises(SourceError, match="git show failed"):
        load_manifest(FailingTree(), ".agent-eval/repo-eval.yaml")


def test_repo_budget_unknown_harness_exits_2(tmp_path, monkeypatch, capsys):
    err = _expect_exit(monkeypatch, capsys, 2, "repo", "budget", str(tmp_path), "--harness", "no-such-harness")
    assert "no harness profile" in err


@pytest.mark.parametrize("command", ["report", "compare"])
def test_repo_report_and_compare_batch_that_is_not_a_directory_exit_2(command, tmp_path, monkeypatch, capsys):
    missing = tmp_path / "no-such-batch"
    extra = ["--baseline", "none"] if command == "compare" else []
    err = _expect_exit(monkeypatch, capsys, 2, "repo", command, str(missing), *extra)
    assert f"not a directory: {missing}" in err


def test_repo_scan_unwritable_out_exits_2(tmp_path, monkeypatch, capsys):
    (tmp_path / "AGENTS.md").write_text("# guidance\n", encoding="utf-8")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    out = blocker / "scan.json"
    err = _expect_exit(monkeypatch, capsys, 2, "repo", "scan", str(tmp_path), "--out", str(out))
    assert f"cannot write {out}" in err


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


def test_external_content_walkthrough_states_the_exit_of_a_preflight_without_keys(tmp_path, monkeypatch, capsys):
    # The walkthrough shows a preflight run with no API keys and says what it exited with; that number
    # must be the real refusal, so the page cannot drift from the code.
    doc = Path(__file__).resolve().parents[1] / "docs" / "external-content.md"
    stated = int(re.search(r"It exited (\d+) here because no API keys were set", doc.read_text(encoding="utf-8")).group(1))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(benchmark, "_load_registry", lambda args: _OneLibrary())
    _argv(monkeypatch, "benchmark", "preflight", "--library", "onetbb", "--output-dir", str(tmp_path / "out"),
          "--provider", "openai", "--judge-provider", "openai")
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == stated
    assert "missing OPENAI_API_KEY for provider openai" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    "text_fields",
    [{}, {"text": ""}, {"text": "   "}, {"text": None}, {"text": 5}],
    ids=["missing", "empty", "whitespace", "null", "not-a-string"],
)
def test_trigger_set_question_without_text_exits_2(text_fields, tmp_path, monkeypatch, capsys):
    spec = _trigger_questions(tmp_path, [
        {"id": "1", **text_fields, "metadata": {"skill_under_test": "linux-perf"}},
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


def test_trigger_set_file_without_labels_is_valid_and_its_questions_stay_unlabelled(tmp_path, monkeypatch, capsys):
    # A --trigger-set file whose questions carry no expected_skill is valid: they are read unlabelled.
    path = tmp_path / "unlabelled.json"
    path.write_text(json.dumps({"questions": [{"id": "u1", "question": "How do I cook rice?"}]}), encoding="utf-8")
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--format", "questions", "--trigger-set", str(path), "--out", str(tmp_path / "o.json"))
    cli.main()
    assert (tmp_path / "o.json").exists()
    assert "Traceback" not in capsys.readouterr().err


def test_questions_file_with_a_field_label_and_no_labels_is_refused_and_says_so(tmp_path, monkeypatch, capsys):
    # A --questions source whose rule is a field no question has is a mistyped path, not a set of
    # negatives: exit 2, and the message says the file has no labels.
    path = tmp_path / "unlabelled.json"
    path.write_text(json.dumps({"questions": [{"id": "u1", "question": "How do I cook rice?"}]}), encoding="utf-8")
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--format", "questions", "--questions", f"{path}=@expected_skill", "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "@expected_skill resolves for none of 1 questions (none has that field, so the file has no labels)" \
        in capsys.readouterr().err


def test_trigger_set_unresolved_field_rule_is_refused_before_any_rewrite_call(tmp_path, monkeypatch, capsys):
    # A mistyped @field path labels no question: exit 2, and none of the questions was sent to --rewrite.
    rewrite_calls = []
    monkeypatch.setattr("dokimi.llm.llm_call", lambda *args, **kwargs: rewrite_calls.append(1) or "rewritten")
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": [
        {"id": str(i), "text": f"question {i}", "metadata": {"skill_under_test": "linux-perf"}} for i in range(3)
    ]}), encoding="utf-8")
    _argv(monkeypatch, "catalog", "trigger-set", "--catalog", str(_skill_root(tmp_path)),
          "--questions", f"{path}=@metadata.skil_under_test", "--rewrite", "--model", "m",
          "--out", str(tmp_path / "o.json"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "resolves for none of 3 questions" in capsys.readouterr().err
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
    # The gate that fired is the unresolved label: the summary is printed before the exit.
    assert json.loads(capsys.readouterr().out)["label_unresolved"] == 1


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


# ── questions refine: only the artifact refusal is a usage error; a run-time ValueError is not ──

def test_refine_runtime_value_error_is_not_a_usage_refusal(tmp_path, monkeypatch):
    # A ValueError from the refiner's own steps (here the similarity pass) is a run failure. It must
    # reach the top level (exit 1, a traceback), not be turned into the input refusal (exit 2).
    from dokimi.questions.refiner import QuestionRefiner

    def similarity_failed(self, questions):
        raise ValueError("similarity backend returned NaN")

    monkeypatch.setattr(QuestionRefiner, "_deduplicate", similarity_failed)
    _argv(monkeypatch, "questions", "refine", "--questions", _questions(tmp_path),
          "--product", "numba", "--dry-run")
    with pytest.raises(ValueError, match="similarity backend returned NaN"):
        cli.main()
