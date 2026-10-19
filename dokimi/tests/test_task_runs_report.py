# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the executable task-run Markdown report renderer."""

import pytest

from dokimi.harnesses.runner import summarize_task_results
from dokimi.report.task_runs_report import _md_cell, render_task_runs_report


def _artifact():
    return {
        "schema_version": "task_runs.v1",
        "model": "haiku",
        "repeats": 1,
        "baseline_harness": "docker-claude",
        "harnesses": ["docker-claude", "docker-claude-skill:s/SKILL.md"],
        "tasks": [
            {"name": "false-sharing", "path": "p", "metadata": {"difficulty": "medium"},
             "verifier": {}, "agent": {}, "environment": {}},
            {"name": "simd-sort", "path": "p", "metadata": {"difficulty": "hard"},
             "verifier": {}, "agent": {}, "environment": {}},
        ],
        "results": [
            {"task": "false-sharing", "harness": "docker-claude", "passed": False,
             "reward": 0.0, "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 1, "operations_by_type": {}, "cost_usd": 0.06}},
            {"task": "false-sharing", "harness": "docker-claude-skill:s/SKILL.md", "passed": True,
             "reward": 1.0, "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 1, "operations_by_type": {}, "cost_usd": 0.08}},
            {"task": "simd-sort", "harness": "docker-claude", "passed": True,
             "reward": 1.0, "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 1, "operations_by_type": {}, "cost_usd": 0.24}},
            {"task": "simd-sort", "harness": "docker-claude-skill:s/SKILL.md", "passed": False,
             "reward": 0.0, "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 1, "operations_by_type": {}, "cost_usd": 0.57}},
        ],
        "summary": {
            "per_harness": {
                "docker-claude": {"n": 2, "passed": 1, "pass_rate": 0.5, "avg_reward": 0.5,
                                  "elapsed_sec": 60.0, "operation_count": 2,
                                  "operations_by_type": {},
                                  "cost": {"total_cost_usd": 0.30, "cost_known_n": 2,
                                           "completion_tokens": 100, "cache_read_tokens": 9}},
                "docker-claude-skill:s/SKILL.md": {"n": 2, "passed": 1, "pass_rate": 0.5,
                                  "avg_reward": 0.5, "elapsed_sec": 90.0, "operation_count": 2,
                                  "operations_by_type": {},
                                  "cost": {"total_cost_usd": 0.65, "cost_known_n": 2,
                                           "completion_tokens": 150, "cache_read_tokens": 13}},
            },
            "comparisons": {
                "docker-claude-skill:s/SKILL.md": {
                    "baseline_harness": "docker-claude", "paired_pass_rate_delta": 0.0,
                    "passed_delta": 0, "operation_count_delta": 0, "cost_delta_usd": 0.35},
            },
        },
    }


def test_md_cell_escapes_pipes_and_newlines():
    assert _md_cell("a | b\nc") == r"a \| b c"
    assert _md_cell(None) == ""


def test_report_has_all_sections():
    md = render_task_runs_report(_artifact())
    for section in ("# Executable task-run report", "## Headline", "## Difficulty rollup",
                    "## Comparison vs baseline", "## Per-task pass + cost",
                    "## Per-cell answer detail"):
        assert section in md


def test_report_difficulty_rollup_splits_medium_and_hard():
    md = render_task_runs_report(_artifact())
    # medium has 1 task, hard has 1 task
    assert "| medium | 1 |" in md
    assert "| hard | 1 |" in md


def test_report_renders_without_cell_dirs():
    # output_dir is None for every row -> self-report column blank, no crash.
    md = render_task_runs_report(_artifact())
    assert "(no result)" in md


def test_rollups_keep_distinct_harnesses():
    # Two non-skill harnesses must NOT be merged into one bucket: each keeps its
    # own pass count. codex solves t1, claude-code fails it.
    data = {
        "schema_version": "task_runs.v1", "model": "m", "repeats": 1,
        "baseline_harness": "claude-code", "harnesses": ["claude-code", "codex"],
        "tasks": [{"name": "t1", "path": "p", "metadata": {"difficulty": "easy"},
                   "verifier": {}, "agent": {}, "environment": {}}],
        "results": [
            {"task": "t1", "harness": "claude-code", "passed": False, "reward": 0.0,
             "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 0, "operations_by_type": {}, "cost_usd": 0.01}},
            {"task": "t1", "harness": "codex", "passed": True, "reward": 1.0,
             "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 0, "operations_by_type": {}, "cost_usd": 0.02}},
        ],
        "summary": {"per_harness": {
            "claude-code": {"n": 1, "passed": 0, "pass_rate": 0.0, "avg_reward": 0.0,
                            "elapsed_sec": 1.0, "operation_count": 0, "operations_by_type": {},
                            "cost": {"total_cost_usd": 0.01, "cost_known_n": 1}},
            "codex": {"n": 1, "passed": 1, "pass_rate": 1.0, "avg_reward": 1.0,
                      "elapsed_sec": 1.0, "operation_count": 0, "operations_by_type": {},
                      "cost": {"total_cost_usd": 0.02, "cost_known_n": 1}},
        }, "comparisons": {}},
    }
    md = render_task_runs_report(data)
    # both harness ids appear as distinct columns; per-task row shows 0/1 and 1/1
    assert "`claude-code` pass" in md
    assert "`codex` pass" in md
    assert "| t1 | easy | 0/1 |" in md  # claude-code column = 0/1, not merged with codex


def test_per_task_cost_unknown_renders_dash():
    # A harness whose rows omit cost_usd (oracle/dry-run) must show — not $0.0000.
    data = {
        "schema_version": "task_runs.v1", "model": "m", "repeats": 1,
        "harnesses": ["docker-oracle"],
        "tasks": [{"name": "t1", "path": "p", "metadata": {"difficulty": "easy"},
                   "verifier": {}, "agent": {}, "environment": {}}],
        "results": [
            {"task": "t1", "harness": "docker-oracle", "passed": True, "reward": 1.0,
             "returncode": 0, "output_dir": None,
             "metrics": {"operation_count": 0, "operations_by_type": {}}},  # no cost_usd
        ],
        "summary": {"per_harness": {
            "docker-oracle": {"n": 1, "passed": 1, "pass_rate": 1.0, "avg_reward": 1.0,
                              "elapsed_sec": 1.0, "operation_count": 0, "operations_by_type": {},
                              "cost": {"total_cost_usd": None, "cost_known_n": 0}},
        }, "comparisons": {}},
    }
    md = render_task_runs_report(data)
    assert "| t1 | easy | 1/1 | — |" in md
    assert "$0.0000" not in md


def test_errored_cells_render_as_missing_data_not_failures():
    from dokimi.harnesses.runner import summarize_task_results

    def row(task, harness, passed, error=None):
        metrics = {"operation_count": 0, "operations_by_type": {}}
        if error:
            metrics["error"] = error
        return {"task": task, "harness": harness, "passed": passed,
                "reward": None if error else float(passed), "returncode": 1 if error else 0,
                "elapsed_sec": 1.0, "output_dir": None, "metrics": metrics}

    rows = [
        row("t1", "base", False), row("t1", "skill", True),
        row("t2", "base", True), row("t2", "skill", False, error="RuntimeError: docker build failed"),
    ]
    data = {
        "schema_version": "task_runs.v1", "model": "m", "repeats": 1,
        "baseline_harness": "base", "harnesses": ["base", "skill"],
        "tasks": [{"name": t, "path": "p", "metadata": {"difficulty": "easy"},
                   "verifier": {}, "agent": {}, "environment": {}} for t in ("t1", "t2")],
        "results": rows,
        "summary": summarize_task_results(rows, baseline_harness="base"),
    }
    md = render_task_runs_report(data)
    assert "| `skill` | 100.00% (1/1) | 1 |" in md          # headline: scored cells, errored column
    assert "missing data, not a 0" in md
    assert "| t2 | easy | 1/1 | — | — (+1 err) | — |" in md
    assert "— (arms lost different cells)" in md         # unpaired deltas withheld
    assert "| `skill` | 1 | null | ERR |" in md
    assert "(harness error)" in md

    # A foreign row marked passed with metrics.error is not a pass in the tables either.
    rows[3]["passed"] = True
    data["summary"] = summarize_task_results(rows, baseline_harness="base")
    md = render_task_runs_report(data)
    assert "| easy | 2 | 1/2 | 1/1 (+1 err) |" in md
    assert "| `skill` | 1 | null | ERR |" in md


def test_solver_error_cells_are_errored_in_the_report_like_in_the_summary():
    from dokimi.harnesses.runner import summarize_task_results

    def row(task, harness, **extra):
        return {"task": task, "harness": harness, "passed": True, "reward": 1.0,
                "returncode": 0, "elapsed_sec": 1.0, "output_dir": None,
                "metrics": {"operation_count": 0, "operations_by_type": {}, **extra}}

    rows = [row("t1", "base", solver_error="api_error: 401"), row("t1", "skill"),
            row("t2", "base"), row("t2", "skill")]
    data = {
        "schema_version": "task_runs.v1", "model": "m", "repeats": 1,
        "baseline_harness": "base", "harnesses": ["base", "skill"],
        "tasks": [{"name": t, "path": "p", "metadata": {"difficulty": "easy"},
                   "verifier": {}, "agent": {}, "environment": {}} for t in ("t1", "t2")],
        "results": rows,
        "summary": summarize_task_results(rows, baseline_harness="base"),
    }
    md = render_task_runs_report(data)
    assert "| `base` | 100.00% (1/1) | 1 |" in md          # headline agrees: 1 errored
    assert "| t1 | easy | — (+1 err) | — | 1/1 | — |" in md
    assert "| `base` | 1 | 1 | ERR |" in md


# ── McNemar section ───────────────────────────────────────────────────────────


def _with_mcnemar(**overrides):
    data = _artifact()
    mc = {
        "test": "mcnemar_exact", "n": 10, "n_gained": 6, "n_lost": 0,
        "n_both_passed": 3, "n_both_failed": 1, "n_discordant": 6, "n_excluded": 0,
        "paired_pass_rate_delta": 0.6, "ci_lo": 0.3, "ci_hi": 0.9, "ci_confidence": 0.95,
        "p_exact": 0.0312, "p_adjusted": 0.0312, "correction": "holm",
        "family_size": 1, "significant": True, "significant_adjusted": True,
        "n_units": 10, "pseudo_replicated": False,
    }
    mc.update(overrides)
    data["summary"]["comparisons"]["docker-claude-skill:s/SKILL.md"]["mcnemar"] = mc
    return data


def test_mcnemar_section_reports_the_discordant_cells():
    md = render_task_runs_report(_with_mcnemar())
    assert "## Paired significance vs baseline (McNemar)" in md
    section = md.split("## Paired significance vs baseline (McNemar)")[1]
    row = next(line for line in section.splitlines()
               if line.startswith("| `docker-claude-skill:s/SKILL.md` |"))
    assert row == ("| `docker-claude-skill:s/SKILL.md` | +60.00 pp | "
                   "[+30.0, +90.0] pp | 6 | 0 | 3 | 1 | 0.0312 | 0.0312 | yes | 10 |")


def test_mcnemar_section_absent_when_the_artifact_has_no_test():
    """Older artifacts predate the block; the rest of the report still renders."""
    md = render_task_runs_report(_artifact())
    assert "McNemar" not in md
    assert "## Comparison vs baseline" in md


def test_no_discordant_pair_renders_a_dash_not_a_verdict():
    md = render_task_runs_report(_with_mcnemar(
        n_gained=0, n_lost=0, n_discordant=0, paired_pass_rate_delta=0.0,
        p_exact=None, p_adjusted=None, significant=None, significant_adjusted=None,
    ))
    section = md.split("## Paired significance vs baseline (McNemar)")[1]
    row = next(line for line in section.splitlines()
               if line.startswith("| `docker-claude-skill:s/SKILL.md` |"))
    # No p-value and no yes/no: absent evidence, not evidence of no effect.
    assert row.endswith("| — | — | — | 10 |")
    assert "| None |" not in md


def test_excluded_cells_are_disclosed():
    md = render_task_runs_report(_with_mcnemar(n_excluded=3))
    assert "3 cell-exclusion(s)" in md
    assert "cell-exclusion(s)" not in render_task_runs_report(_with_mcnemar())


def test_pseudo_replication_is_disclosed_when_repeats_are_paired_as_cells():
    """10 pairs from 4 tasks is not a 10-unit sample, and the report has to say so.

    McNemar assumes independent pairs; repeats of one task share its difficulty,
    so p is anti-conservative. A clean-looking 0.0312 with no caveat would be
    read as a claim at α.
    """
    md = render_task_runs_report(_with_mcnemar(n_units=4, pseudo_replicated=True))
    assert "only 4 independent task(s)" in md
    assert "anti-conservative" in md
    # The n=n_units case must stay silent rather than warn on every single run.
    assert "anti-conservative" not in render_task_runs_report(_with_mcnemar())


def test_report_puts_the_discovery_arm_beside_no_skill_and_with_skill():
    arms = ["docker-claude", "docker-claude-skill:s", "docker-claude-skill-discover:s"]
    rows = []
    for harness, passed, invoked in zip(arms, (False, True, False), (None, 1, 0), strict=True):
        metrics = {"operation_count": 0, "operations_by_type": {}}
        if invoked is not None:
            metrics.update(n_skill_invocations=invoked, skill_notice=harness == arms[1],
                           skill_listing={"truncated": harness == arms[2]})
        rows.append({"task": "t1", "harness": harness, "passed": passed, "reward": float(passed),
                     "returncode": 0, "output_dir": None, "metrics": metrics,
                     "operations": []})
    data = {"schema_version": "task_runs.v1", "model": "m", "repeats": 1,
            "baseline_harness": arms[0], "harnesses": arms, "results": rows,
            "tasks": [{"name": "t1", "path": "p", "metadata": {"difficulty": "easy"},
                       "verifier": {}, "agent": {}, "environment": {}}],
            "summary": summarize_task_results(rows, baseline_harness=arms[0])}
    from dokimi.artifacts import validate_artifact
    validate_artifact("task_runs", data)
    md = render_task_runs_report(data)
    assert all(f"| `{h}` | " in md.split("## Difficulty rollup")[0] for h in arms)
    assert "## Skill discovery" in md
    assert "| `docker-claude-skill-discover:s` | 100% | 0% | +100.00 pp | 100% | 0% |" in md
    assert "1 discovery row(s) ran with a description longer" in md
    # An artifact from before the two match flags gets the combined reading.
    entry = data["summary"]["discovery_gaps"][arms[2]]
    entry["gap"] = None
    for key in ("attempted_cells_match", "errored_cells_match"):
        del entry[key]
    assert "— (arms ran or lost different cells)" in render_task_runs_report(data)
    del data["summary"]["discovery_gaps"]
    assert "## Skill discovery" not in render_task_runs_report(data)


def test_discovery_gap_withheld_when_arms_attempted_different_cells():
    """Gap is None when discovery arm ran fewer tasks than the with-skill arm."""
    arms = ["docker-claude", "docker-claude-skill:s", "docker-claude-skill-discover:s"]
    rows = []
    # Discovery arm: runs 1 task (killed run or different --tasks selection)
    # With-skill arm: runs 2 tasks
    for harness in arms:
        for task_idx in (0, 1):
            if harness == arms[2] and task_idx == 1:
                # Discovery arm only ran the first task (index 0)
                continue
            passed = (harness == arms[1])  # Only skill arm passes
            metrics = {"operation_count": 0, "operations_by_type": {}}
            if harness != arms[0]:
                metrics.update(n_skill_invocations=(1 if harness == arms[1] else 0),
                              skill_notice=harness == arms[1])
            task_name = f"t{task_idx}"
            rows.append({"task": task_name, "harness": harness, "passed": passed,
                        "reward": float(passed), "returncode": 0, "output_dir": None,
                        "metrics": metrics, "operations": []})

    tasks = [{"name": f"t{i}", "path": "p", "metadata": {"difficulty": "easy"},
             "verifier": {}, "agent": {}, "environment": {}} for i in range(2)]
    data = {"schema_version": "task_runs.v1", "model": "m", "repeats": 1,
            "baseline_harness": arms[0], "harnesses": arms, "results": rows, "tasks": tasks,
            "summary": summarize_task_results(rows, baseline_harness=arms[0])}

    # Gap should be None because attempted cells differ, and nothing errored
    entry = data["summary"]["discovery_gaps"][arms[2]]
    assert entry["gap"] is None
    assert entry["attempted_cells_match"] is False and entry["errored_cells_match"] is True
    assert "— (arms ran different cells)" in render_task_runs_report(data)
    assert "lost different cells" not in render_task_runs_report(data)


def _gap_data(disc_tasks, skill_errored, disc_errored):
    """Three arms over tasks t0 and t1; each arm runs the listed tasks and errors on the listed ones."""
    arms = ["docker-claude", "docker-claude-skill:s", "docker-claude-skill-discover:s"]
    errored = {arms[1]: skill_errored, arms[2]: disc_errored}
    rows = []
    for harness in arms:
        for task_idx in (0, 1):
            if harness == arms[2] and task_idx not in disc_tasks:
                continue
            metrics = {"operation_count": 0, "operations_by_type": {}}
            if harness != arms[0]:
                metrics.update(n_skill_invocations=(1 if harness == arms[1] else 0),
                               skill_notice=harness == arms[1])
            if task_idx in errored.get(harness, ()):
                metrics["error"] = "boom"
            passed = harness == arms[1] and task_idx not in errored.get(harness, ())
            rows.append({"task": f"t{task_idx}", "harness": harness, "passed": passed,
                         "reward": float(passed), "returncode": 0, "output_dir": None,
                         "metrics": metrics, "operations": []})
    tasks = [{"name": f"t{i}", "path": "p", "metadata": {"difficulty": "easy"},
              "verifier": {}, "agent": {}, "environment": {}} for i in range(2)]
    return {"schema_version": "task_runs.v1", "model": "m", "repeats": 1,
            "baseline_harness": arms[0], "harnesses": arms, "results": rows, "tasks": tasks,
            "summary": summarize_task_results(rows, baseline_harness=arms[0])}


@pytest.mark.parametrize("disc_tasks, skill_errored, disc_errored, attempted_match, errored_match, label", [
    # The discovery arm ran fewer cells, and lost none.
    ((0,), (), (), False, True, "— (arms ran different cells)"),
    # Both arms ran the same cells, and the discovery arm lost one.
    ((0, 1), (), (1,), True, False, "— (arms lost different cells)"),
    # The arms ran different cells and lost different ones.
    ((0,), (1,), (), False, False, "— (arms ran and lost different cells)"),
])
def test_discovery_gap_labels_run_and_loss_differences_apart(
        disc_tasks, skill_errored, disc_errored, attempted_match, errored_match, label):
    data = _gap_data(disc_tasks, skill_errored, disc_errored)
    entry = data["summary"]["discovery_gaps"]["docker-claude-skill-discover:s"]
    assert entry["gap"] is None
    assert entry["attempted_cells_match"] is attempted_match
    assert entry["errored_cells_match"] is errored_match
    assert label in render_task_runs_report(data)


def test_discovery_gap_both_arms_fully_errored():
    """Gap is None when both arms have no pass_rate (all errored), and no crash."""
    arms = ["docker-claude", "docker-claude-skill:s", "docker-claude-skill-discover:s"]
    rows = []
    for harness in arms:
        metrics = {"error": "rejected credentials" if harness != arms[0] else "no api key"}
        if harness != arms[0]:
            metrics["n_skill_invocations"] = 0
            metrics["skill_notice"] = harness == arms[1]
        rows.append({"task": "t1", "harness": harness, "passed": False, "reward": 0.0,
                    "returncode": 1, "output_dir": None, "metrics": metrics, "operations": []})

    data = {"schema_version": "task_runs.v1", "model": "m", "repeats": 1,
            "baseline_harness": arms[0], "harnesses": arms, "results": rows,
            "tasks": [{"name": "t1", "path": "p", "metadata": {"difficulty": "easy"},
                      "verifier": {}, "agent": {}, "environment": {}}],
            "summary": summarize_task_results(rows, baseline_harness=arms[0])}

    # Gap should be None because pass_rate is None for both arms
    assert data["summary"]["discovery_gaps"][arms[2]]["gap"] is None
    assert data["summary"]["discovery_gaps"][arms[2]]["discover_pass_rate"] is None
    assert data["summary"]["discovery_gaps"][arms[2]]["with_skill_pass_rate"] is None


def test_fmt_rate_rejects_bools():
    """bool is a subclass of int, so _fmt_rate(True) must not render as 100%."""
    from dokimi.report.task_runs_report import _fmt_rate

    assert _fmt_rate(0.5) == "50%"
    assert _fmt_rate(1.0) == "100%"
    assert _fmt_rate(0.0) == "0%"
    assert _fmt_rate(True) == "—"
    assert _fmt_rate(False) == "—"
    assert _fmt_rate(None) == "—"


def test_discovery_gap_no_scored_cells_differs_from_different_attempted():
    """When gap is None, distinguish between no scored cells vs arms attempted different cells."""
    arms = ["docker-claude", "docker-claude-skill:s", "docker-claude-skill-discover:s"]
    rows = []
    for harness in arms:
        for task_idx in (0, 1):
            if harness == arms[2] and task_idx == 1:
                continue
            passed = (harness == arms[1])
            metrics = {"operation_count": 0, "operations_by_type": {}}
            if harness != arms[0]:
                metrics.update(n_skill_invocations=(1 if harness == arms[1] else 0),
                              skill_notice=harness == arms[1])
            rows.append({"task": f"t{task_idx}", "harness": harness, "passed": passed,
                        "reward": float(passed), "returncode": 0, "output_dir": None,
                        "metrics": metrics, "operations": []})

    data = {"schema_version": "task_runs.v1", "model": "m", "repeats": 1,
            "baseline_harness": arms[0], "harnesses": arms, "results": rows,
            "tasks": [{"name": f"t{i}", "path": "p", "metadata": {"difficulty": "easy"},
                      "verifier": {}, "agent": {}, "environment": {}} for i in range(2)],
            "summary": summarize_task_results(rows, baseline_harness=arms[0])}

    # The discovery arm ran one task fewer, so the label says it ran different cells
    md = render_task_runs_report(data)
    assert "— (arms ran different cells)" in md
    assert "— (no scored cells)" not in md

    # When both arms fully errored, should say "no scored cells"
    rows_all_err = []
    for harness in arms:
        metrics = {"error": "rejected credentials" if harness != arms[0] else "no api key"}
        if harness != arms[0]:
            metrics["n_skill_invocations"] = 0
            metrics["skill_notice"] = harness == arms[1]
        rows_all_err.append({"task": "t1", "harness": harness, "passed": False, "reward": 0.0,
                            "returncode": 1, "output_dir": None, "metrics": metrics,
                            "operations": []})

    data_err = {"schema_version": "task_runs.v1", "model": "m", "repeats": 1,
                "baseline_harness": arms[0], "harnesses": arms, "results": rows_all_err,
                "tasks": [{"name": "t1", "path": "p", "metadata": {"difficulty": "easy"},
                          "verifier": {}, "agent": {}, "environment": {}}],
                "summary": summarize_task_results(rows_all_err, baseline_harness=arms[0])}

    md_err = render_task_runs_report(data_err)
    assert "— (no scored cells)" in md_err
    assert "— (arms ran or lost different cells)" not in md_err
