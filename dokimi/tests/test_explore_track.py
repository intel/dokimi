# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ExploreBench track + exploration report + token-role rollup.

Covers:

* ``explore/tasks.py`` — task loading and reference-location construction.
* ``explore/explorer.py`` — static/oracle explorers and the subagent-op bridge.
* ``explore/runner.py`` — scoring tasks through arms into ``explore_runs.v1``.
* ``report/exploration_report.py`` — Markdown panels.
* ``metrics/usage.py`` — ``roll_up_by_role`` main-vs-subagent split.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.artifacts import validate_artifact
from dokimi.explore import (
    ExploreRunner,
    StaticExplorer,
    discover_explore_tasks,
    load_explore_task,
)
from dokimi.explore.explorer import (
    ExplorerResult,
    build_subagent_op,
    op_marker_line,
    subagent_op_from_answer,
)
from dokimi.explore.tasks import (
    REPO_ROOT,
    ExploreReference,
    ExploreTask,
    _task_from_data,
)
from dokimi.harnesses.operations import load_operations
from dokimi.metrics.exploration import parse_final_answer
from dokimi.metrics.usage import UsageRecord, roll_up_by_role, to_record
from dokimi.report.exploration_report import (
    render_explore_report,
    render_exploration_metrics,
)


# ── task loading ─────────────────────────────────────────────────────────────


def _write_task(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "t.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_task_from_data_builds_reference_locations(tmp_path):
    path = _write_task(tmp_path, "")
    task = _task_from_data(
        {
            "id": "t1",
            "product": "oneTBB",
            "query": "where?",
            "references": [
                {"path": "./docs/a.md", "lines": [120, 180]},
                {"path": "src/b.cpp"},  # whole-file
            ],
        },
        path,
    )
    ref = task.reference_locations()
    assert ref.files["docs/a.md"] == ((120, 180),)
    assert ref.files["src/b.cpp"] == ()  # whole-file → no ranges
    assert task.product == "oneTBB"


def test_task_single_line_reference(tmp_path):
    task = _task_from_data(
        {"id": "t", "query": "q", "references": [{"path": "a.py", "lines": [42]}]},
        _write_task(tmp_path, ""),
    )
    assert task.reference_locations().files["a.py"] == ((42, 42),)


def test_task_missing_fields_raise(tmp_path):
    with pytest.raises(ValueError):
        _task_from_data({"id": "t"}, _write_task(tmp_path, ""))


@pytest.mark.parametrize("bad", [[1, 2, 3], [0], [-5, 10], ["x"]])
def test_parse_lines_rejects_malformed(tmp_path, bad):
    with pytest.raises(ValueError):
        _task_from_data(
            {"id": "t", "query": "q", "references": [{"path": "a.py", "lines": bad}]},
            _write_task(tmp_path, ""),
        )


def test_discover_raises_on_duplicate_ids(tmp_path):
    body = "id: dup\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n"
    (tmp_path / "one.yaml").write_text(body, encoding="utf-8")
    (tmp_path / "two.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        discover_explore_tasks(tmp_path)
    with pytest.raises(ValueError):
        load_explore_task("dup", root=tmp_path)


def test_load_task_by_id_when_filename_differs(tmp_path):
    # id != filename stem must still be loadable (as listed by `explore list`).
    (tmp_path / "renamed.yaml").write_text(
        "id: real-id\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n",
        encoding="utf-8",
    )
    task = load_explore_task("real-id", root=tmp_path)
    assert task.id == "real-id"
    with pytest.raises(FileNotFoundError):
        load_explore_task("nope", root=tmp_path)


def test_load_task_id_not_shadowed_by_filename_stem(tmp_path):
    # foo.yaml declares id "bar"; another file declares id "foo". Requesting
    # "foo" must run the task whose *id* is foo, not foo.yaml.
    (tmp_path / "foo.yaml").write_text(
        "id: bar\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n", encoding="utf-8"
    )
    (tmp_path / "other.yaml").write_text(
        "id: foo\nproduct: p\nquery: q\nreferences:\n  - path: b.py\n", encoding="utf-8"
    )
    assert load_explore_task("foo", root=tmp_path).references[0].path == "b.py"
    assert load_explore_task("bar", root=tmp_path).references[0].path == "a.py"


def test_default_repo_root_replaces_the_dokimi_fallback(tmp_path):
    """A task without `repo_root:` scores against the caller's tree."""
    (tmp_path / "t.yaml").write_text(
        "id: t\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n", encoding="utf-8"
    )
    external = tmp_path / "external-repo"
    external.mkdir()

    task = load_explore_task("t", root=tmp_path, default_repo_root=external)
    assert task.repo_root == external.resolve()
    assert discover_explore_tasks(tmp_path, default_repo_root=external)[
        0
    ].repo_root == external.resolve()

    # Without it, the historical fallback (this repository) still applies.
    assert load_explore_task("t", root=tmp_path).repo_root == REPO_ROOT


def test_declared_repo_root_wins_over_default(tmp_path):
    (tmp_path / "t.yaml").write_text(
        "id: t\nproduct: p\nquery: q\nrepo_root: declared\nreferences:\n  - path: a.py\n",
        encoding="utf-8",
    )
    task = load_explore_task(
        "t", root=tmp_path, default_repo_root=tmp_path / "external-repo"
    )
    assert task.repo_root == (tmp_path / "declared").resolve()


def test_explore_list_passes_repo_root_from_cli(tmp_path, capsys):
    from dokimi.cli import build_parser

    (tmp_path / "t.yaml").write_text(
        "id: t\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n", encoding="utf-8"
    )
    external = tmp_path / "external-repo"
    external.mkdir()
    seen: list[Path] = []

    parser = build_parser()
    args = parser.parse_args(
        ["explore", "list", "--tasks-root", str(tmp_path), "--repo-root", str(external)]
    )
    assert args.repo_root == str(external)

    import dokimi.explore as explore_pkg

    real_discover = explore_pkg.discover_explore_tasks

    def spy(root=None, default_repo_root=None):
        seen.append(default_repo_root)
        return real_discover(root, default_repo_root=default_repo_root)

    explore_pkg.discover_explore_tasks = spy
    try:
        args.func(args)
    finally:
        explore_pkg.discover_explore_tasks = real_discover
    assert seen == [external]
    assert "t\tp\t1 ref(s)" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["explore", "list"],
    ["explore", "run", "--all"],
    ["explore", "run", "--tasks", "t"],
])
def test_explore_without_bundled_tasks_says_so(tmp_path, monkeypatch, argv, capsys):
    # An installed wheel has no data/explore_tasks: say so, do not find nothing.
    import dokimi.explore.tasks as tasks_mod
    from dokimi.cli import build_parser

    monkeypatch.setattr(tasks_mod, "EXPLORE_TASKS_ROOT", tmp_path / "absent")
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "ship with a source checkout" in err
    assert "--tasks-root" in err
    assert "--repo-root" in err


def test_a_task_file_path_needs_no_bundled_tasks(tmp_path, monkeypatch):
    import dokimi.explore.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "EXPLORE_TASKS_ROOT", tmp_path / "absent")
    path = tmp_path / "t.yaml"
    path.write_text("id: t\nquery: q\nreferences:\n  - path: a.py\n", encoding="utf-8")
    assert load_explore_task(str(path)).id == "t"


def test_seed_tasks_load_and_reference_real_files():
    # The shipped seed set must parse and its references must exist in the repo.
    tasks = discover_explore_tasks()
    assert tasks, "no seed explore tasks discovered"
    for task in tasks:
        for ref in task.references:
            assert (task.repo_root / ref.path).exists(), f"{task.id}: missing {ref.path}"
    # And a known one is loadable by id.
    one = load_explore_task("citation-localization-scoring")
    assert one.references[0].path == "dokimi/metrics/exploration.py"


# ── explorers + subagent op bridge ───────────────────────────────────────────


def test_oracle_explorer_scores_perfectly(tmp_path):
    task = ExploreTask(
        id="t",
        product="p",
        query="q",
        references=(ExploreReference("a.py", (10, 20)),),
        path=tmp_path / "t.yaml",
        repo_root=tmp_path,
    )
    explorer = StaticExplorer.from_references(task.references)
    result = explorer.explore(task.query, repo_root=task.repo_root)
    score_input = parse_final_answer(result.final_answer)
    assert score_input.citations[0].path == "a.py"


def test_subagent_op_from_answer_and_marker_roundtrip():
    answer = "<final_answer>\nsrc/a.py:1-5\nsrc/b.py:9\n</final_answer>"
    op = subagent_op_from_answer("fastcontext", answer, elapsed_sec=1.2)
    assert op.type == "subagent"
    assert op.metadata["citation_count"] == 2
    assert op.metadata["citation_paths"] == ["src/a.py", "src/b.py"]
    # The marker line parses back into an equivalent op.
    line = op_marker_line(op)
    parsed = load_operations(None, line, "")
    assert len(parsed) == 1
    assert parsed[0].type == "subagent"
    assert parsed[0].metadata["citation_count"] == 2


def test_build_subagent_op_flags_invalid_answer():
    cset = parse_final_answer("<final_answer>\nnot a citation\n</final_answer>")
    op = build_subagent_op("x", cset)
    assert op.metadata["final_answer_valid"] is False


def test_command_explorer_missing_binary_returns_error_result(tmp_path):
    from dokimi.explore import CommandExplorer

    result = CommandExplorer("this-binary-does-not-exist-xyz {query_quoted}").explore(
        "q", repo_root=tmp_path
    )
    assert result.returncode == 124
    assert result.final_answer == ""


def test_command_explorer_timeout_returns_error_result(tmp_path):
    from dokimi.explore import CommandExplorer

    result = CommandExplorer("sleep 5", timeout_sec=0.2).explore("q", repo_root=tmp_path)
    assert result.returncode == 124


def test_command_explorer_timeout_preserves_partial_telemetry(tmp_path):
    from dokimi.explore import CommandExplorer

    writer = tmp_path / "slow.py"
    writer.write_text(
        "import os, time\n"
        "open(os.environ['DOKIMI_OPERATIONS_FILE'], 'a')."
        "write('{\"type\": \"read\", \"name\": \"pre-timeout\"}\\n')\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    result = CommandExplorer(
        f"python {writer.as_posix()}", timeout_sec=0.5, output_root=tmp_path / "o"
    ).explore("q", repo_root=tmp_path)
    assert result.returncode == 124
    # Telemetry written before the hang is preserved, not discarded.
    assert any(op.name == "pre-timeout" for op in result.operations)


def test_command_explorer_nul_byte_in_query_fails_the_row(tmp_path):
    from dokimi.explore import CommandExplorer

    # A NUL byte cannot reach argv; it must fail this row, not the explore run.
    result = CommandExplorer("echo {query_quoted}", output_root=tmp_path / "o").explore(
        "where\x00is it?", repo_root=tmp_path
    )
    assert result.returncode == 124
    assert result.final_answer == ""


def test_command_explorer_empty_command_returns_error(tmp_path):
    from dokimi.explore import CommandExplorer

    # A template that renders to nothing must not raise.
    result = CommandExplorer("   ").explore("q", repo_root=tmp_path)
    assert result.returncode == 127
    assert result.final_answer == ""


def test_command_explorer_quoting_error_returns_error(tmp_path):
    from dokimi.explore import CommandExplorer

    # An apostrophe in the query inside a quoted {query} template renders an
    # unbalanced shell string; fail this row, not the whole run.
    explorer = CommandExplorer("sh -c 'echo {query}'")
    result = explorer.explore("the explorer's answer", repo_root=tmp_path)
    assert result.returncode == 127


def test_runner_does_not_score_failed_partial_answer(tmp_path):
    # A failed explorer that wrote a perfect-looking partial answer must score 0.
    class _Failing:
        name = "failing"

        def explore(self, query, *, repo_root):
            return ExplorerResult(
                final_answer="<final_answer>\na.py:10-20\n</final_answer>",
                returncode=1,
            )

    task = _task(tmp_path, (ExploreReference("a.py", (10, 20)),))
    output = ExploreRunner(
        tasks=[task], arms={"failing": lambda t: _Failing()}, output_root=tmp_path / "o"
    ).run()
    row = output["results"][0]
    assert row["metrics"]["failed"] is True
    assert row["score"]["file_f1"] == 0.0       # partial answer not credited
    assert output["summary"]["per_arm"]["failing"]["avg_file_f1"] == 0.0


def test_command_explorer_resolves_relative_output_dir(tmp_path, monkeypatch):
    # Relative output_root + repo_root != cwd: the child writes telemetry via
    # $DOKIMI_OPERATIONS_FILE, which must resolve to the same absolute
    # path the runner reads back.
    from dokimi.explore import CommandExplorer

    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import os\n"
        "open(os.environ['DOKIMI_OPERATIONS_FILE'], 'a')."
        "write('{\"type\": \"subagent\", \"name\": \"child-op\"}\\n')\n",
        encoding="utf-8",
    )
    explorer = CommandExplorer(f"python {writer.as_posix()}", output_root=Path("out"))
    result = explorer.explore("q", repo_root=repo)
    assert any(op.name == "child-op" for op in result.operations)


def test_command_explorer_clears_stale_operation_logs(tmp_path):
    from dokimi.explore import CommandExplorer

    out = tmp_path / "out"
    out.mkdir()
    (out / "operations.jsonl").write_text(
        '{"type": "subagent", "name": "stale"}\n', encoding="utf-8"
    )
    # `echo` writes stdout but no operations.jsonl; the stale file must be gone.
    result = CommandExplorer("echo hi", output_root=out).explore("q", repo_root=tmp_path)
    assert all(op.name != "stale" for op in result.operations)


# ── runner → explore_runs.v1 ─────────────────────────────────────────────────


def _task(tmp_path, refs):
    return ExploreTask(
        id="loc",
        product="p",
        query="find it",
        references=refs,
        path=tmp_path / "t.yaml",
        repo_root=tmp_path,
    )


def test_runner_oracle_vs_empty_artifact_valid(tmp_path):
    task = _task(tmp_path, (ExploreReference("a.py", (10, 20)),))
    arms = {
        "oracle": lambda t: StaticExplorer.from_references(t.references),
        "empty": lambda t: StaticExplorer("<final_answer>\n</final_answer>", name="empty"),
    }
    output = ExploreRunner(
        tasks=[task], arms=arms, baseline_arm="empty", output_root=tmp_path / "out"
    ).run()

    validate_artifact("explore_runs", {**output, "schema_version": "explore_runs.v1"})
    per_arm = output["summary"]["per_arm"]
    assert per_arm["oracle"]["avg_file_f1"] == 1.0
    assert per_arm["oracle"]["avg_line_f1"] == 1.0
    assert per_arm["empty"]["avg_file_f1"] == 0.0
    # oracle beats the empty baseline.
    assert output["summary"]["comparisons"]["oracle"]["file_f1_delta"] == 1.0
    # each result carries a subagent op
    oracle_row = next(r for r in output["results"] if r["arm"] == "oracle")
    assert any(op["type"] == "subagent" for op in oracle_row["operations"])


def test_runner_records_explorer_operations(tmp_path):
    task = _task(tmp_path, (ExploreReference("a.py", (1, 2)),))
    op_answer = "<final_answer>\na.py:1-2\n</final_answer>"
    explorer = StaticExplorer(
        op_answer,
        name="withops",
        operations=tuple(load_operations(None, op_marker_line(
            build_subagent_op("inner-search", parse_final_answer(op_answer))
        ), "")),
    )
    output = ExploreRunner(
        tasks=[task], arms={"withops": lambda t: explorer}, output_root=tmp_path / "o"
    ).run()
    row = output["results"][0]
    names = {op["name"] for op in row["operations"]}
    assert "inner-search" in names and "withops" in names


# ── report rendering ─────────────────────────────────────────────────────────


def test_render_explore_report_orders_by_file_f1():
    artifact = {
        "summary": {
            "per_arm": {
                "weak": {"n": 1, "avg_file_f1": 0.2, "avg_line_f1": 0.1,
                         "avg_citation_validity_rate": 1.0, "avg_citation_compactness": 3.0},
                "strong": {"n": 1, "avg_file_f1": 0.9, "avg_line_f1": 0.8,
                           "avg_citation_validity_rate": 1.0, "avg_citation_compactness": 1.1},
            },
            "comparisons": {
                "strong": {"baseline_arm": "weak", "file_f1_delta": 0.7, "line_f1_delta": 0.7}
            },
        }
    }
    md = render_explore_report(artifact)
    assert md.index("strong") < md.index("weak")  # strongest first
    assert "+0.700" in md


def test_render_exploration_metrics_skips_rows_without_block():
    artifact = {
        "results": [
            {"task": "t1", "harness": "h", "metrics": {"exploration_metrics": {
                "pre_edit_tool_calls": 3, "repeated_read_ratio": 0.25,
                "broad_search_count": 2, "broad_search_after_subagent_count": 1,
                "main_agent_tokens": 1000, "full_system_tokens": 1200}}},
            {"task": "t2", "harness": "h", "metrics": {"operation_count": 0}},
        ]
    }
    md = render_exploration_metrics(artifact)
    assert "t1" in md and "t2" not in md
    assert "2/1" in md  # broad / after-subagent


def test_render_escapes_pipes_in_arm_names():
    artifact = {
        "summary": {
            "per_arm": {
                "command:rg x | head": {"n": 1, "avg_file_f1": 0.5, "avg_line_f1": None,
                                        "avg_citation_validity_rate": 1.0,
                                        "avg_citation_compactness": None},
            },
            "comparisons": {},
        }
    }
    md = render_explore_report(artifact)
    assert r"command:rg x \| head" in md  # pipe escaped, table intact


def test_render_empty_artifacts():
    assert "No exploration results" in render_explore_report({"summary": {"per_arm": {}}})
    assert "No exploration telemetry" in render_exploration_metrics({"results": []})


# ── token-role rollup (slice 3) ──────────────────────────────────────────────


def test_roll_up_by_role_splits_tokens_and_withholds_partial_cost():
    records = [
        UsageRecord(total_tokens=1000, cost_usd=0.01, role="main"),
        UsageRecord(total_tokens=200, cost_usd=0.002, role="subagent"),
        UsageRecord(total_tokens=50, role="subagent"),  # unpriced
    ]
    roll = roll_up_by_role(records)
    assert roll["main_agent_tokens"] == 1000
    assert roll["subagent_tokens"] == 250
    assert roll["full_system_tokens"] == 1250
    assert roll["main_agent_cost"] == 0.01
    assert roll["subagent_cost"] == 0.002
    # One subagent call is unpriced → full-system cost is withheld, not 0.012.
    assert roll["full_system_cost"] is None
    assert roll["full_system_cost_complete"] is False


def test_roll_up_full_cost_when_all_priced():
    records = [
        UsageRecord(total_tokens=1000, cost_usd=0.01, role="main"),
        UsageRecord(total_tokens=200, cost_usd=0.002, role="subagent"),
    ]
    roll = roll_up_by_role(records)
    assert roll["full_system_cost"] == 0.012
    assert roll["full_system_cost_complete"] is True


def test_role_survives_metrics_serialization_roundtrip():
    # A subagent record serialized to a metrics dict and re-read must keep its
    # role, or the rollup would bucket its tokens under main.
    sub = UsageRecord(total_tokens=200, cost_usd=0.002, role="subagent")
    restored = to_record(sub.as_metrics_dict())
    assert restored.role == "subagent"
    roll = roll_up_by_role([restored])
    assert roll["subagent_tokens"] == 200
    assert roll["main_agent_tokens"] == 0


def test_roll_up_cost_none_when_all_unpriced():
    roll = roll_up_by_role([UsageRecord(total_tokens=10, role="subagent")])
    assert roll["main_agent_cost"] is None
    assert roll["subagent_cost"] is None
    assert roll["full_system_cost"] is None
    assert roll["full_system_cost_complete"] is False
    assert roll["full_system_tokens"] == 10


def test_command_explorer_passes_real_paths_to_the_command(tmp_path):
    from dokimi.explore import CommandExplorer

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os, sys\nprint(all(os.path.isdir(p) for p in sys.argv[1:]))\n", encoding="utf-8"
    )
    explorer = CommandExplorer(
        f"python {probe.as_posix()} {{repo_root}} {{output_dir}}", output_root=tmp_path / "out"
    )
    assert explorer.explore("q", repo_root=tmp_path).final_answer.strip() == "True"


def test_command_explorer_spawns_through_its_runner(tmp_path, monkeypatch):
    """A hung explorer is killed with its tree; what it printed before is the answer."""
    from dokimi.explore import CommandExplorer
    from dokimi.process import FakeRunner, reply

    monkeypatch.setenv("DOKIMI_TEST_ONLY_OPERATOR", "kept")
    fake = FakeRunner(lambda call: reply("<final_answer>\na.py\n", timed_out=True))
    explorer = CommandExplorer("fc --q {query_quoted}", timeout_sec=7, env={"X": "1"},
                               output_root=tmp_path / "o", runner=fake)
    result = explorer.explore("where?", repo_root=tmp_path)
    assert result.returncode == 124 and result.final_answer.startswith("<final_answer>")
    (call,) = fake.calls
    assert call.argv == ("fc", "--q", "where?") and call.cwd == tmp_path and call.timeout == 7
    assert call.capture_limit is None  # the final answer and the markers are parsed from all of it
    assert call.env["X"] == "1" and call.env["DOKIMI_TEST_ONLY_OPERATOR"] == "kept"
    assert call.env["DOKIMI_OUTPUT_DIR"] == str((tmp_path / "o").resolve())


def test_command_explorer_reads_markers_from_stderr(tmp_path):
    from dokimi.explore import CommandExplorer
    from dokimi.process import FakeRunner, reply

    fake = FakeRunner(lambda call: reply("answer", 'DOKIMI_OP {"type": "read", "name": "on-stderr"}\n'))
    result = CommandExplorer("explore {query_quoted}", runner=fake).explore("q", repo_root=tmp_path)
    assert "on-stderr" in [op.name for op in result.operations]


def test_command_explorer_timeout_none_means_no_limit(tmp_path):
    """`timeout_sec=None` meant no timeout; it must not raise TypeError in the runner."""
    from dokimi.explore import CommandExplorer
    from dokimi.process import FakeRunner, reply

    fake = FakeRunner(lambda call: reply("answer"))
    result = CommandExplorer("explore {query_quoted}", timeout_sec=None, runner=fake).explore(
        "q", repo_root=tmp_path)
    assert result.returncode == 0 and fake.calls[0].timeout >= 24 * 3600
