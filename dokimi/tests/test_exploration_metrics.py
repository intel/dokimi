# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the exploration-quality layer.

Covers the two slices that land independently of any LLM/Docker integration:

* ``metrics/exploration.py`` — ``<final_answer>`` citation parsing and
  file/line precision/recall/F1 + compactness scoring.
* ``harnesses/exploration.py`` — deriving the ``exploration_metrics`` block
  from an ``OperationRecord`` stream, including its wiring into
  ``HarnessResult.as_dict``.
"""

from __future__ import annotations

import pytest

from dokimi.harnesses.exploration import summarize_exploration
from dokimi.harnesses.models import HarnessResult, OperationRecord
from dokimi.metrics.exploration import (
    Citation,
    ReferenceLocations,
    citation_path,
    normalize_path,
    parse_final_answer,
    score_localization,
)
from dokimi.skills import load_skill


# ── citation parsing ─────────────────────────────────────────────────────────


def test_parse_basic_block():
    text = (
        "Here is what I found.\n"
        "<final_answer>\n"
        "src/pkg/mod.py:42-58\n"
        "tests/test_mod.py:101-119\n"
        "</final_answer>\n"
    )
    cs = parse_final_answer(text)
    assert cs.citations == (
        Citation("src/pkg/mod.py", 42, 58),
        Citation("tests/test_mod.py", 101, 119),
    )
    assert cs.malformed == ()
    assert cs.validity_rate == 1.0


def test_parse_single_line_and_whole_file_and_bullets():
    text = (
        "<final_answer>\n"
        "- `src/a.py:5`\n"          # single line + bullet + backticks
        "* ./src/b.py\n"            # whole-file with leading ./
        "1. docs/c.md:10-12\n"      # numbered
        "</final_answer>"
    )
    cs = parse_final_answer(text)
    assert Citation("src/a.py", 5, 5) in cs.citations
    assert Citation("src/b.py", None, None) in cs.citations
    assert Citation("docs/c.md", 10, 12) in cs.citations
    assert any(c.is_whole_file for c in cs.citations)


def test_bullet_only_lines_are_not_phantom_citations():
    # Stray bullet/empty lines must not parse into Citation(path="-").
    cs = parse_final_answer(
        "<final_answer>\n- \n* \n1. \nsrc/a.py:1-2\n</final_answer>"
    )
    assert cs.citations == (Citation("src/a.py", 1, 2),)
    assert cs.malformed == ()


def test_parse_malformed_counts_against_validity():
    text = (
        "<final_answer>\n"
        "src/ok.py:1-2\n"
        "this is just a sentence, not a citation\n"
        "</final_answer>"
    )
    cs = parse_final_answer(text)
    assert len(cs.citations) == 1
    assert len(cs.malformed) == 1
    assert cs.validity_rate == 0.5


def test_reversed_range_is_normalized():
    cs = parse_final_answer("<final_answer>\nsrc/x.py:58-42\n</final_answer>")
    assert cs.citations[0] == Citation("src/x.py", 42, 58)


def test_overlap_detection():
    cs = parse_final_answer(
        "<final_answer>\nsrc/x.py:10-20\nsrc/x.py:15-25\nsrc/y.py:1-2\n</final_answer>"
    )
    assert cs.overlapping == 1


def test_fallback_scan_without_block():
    # No <final_answer> wrapper: loose scan picks up path:line tokens, but does
    # not flag prose as malformed.
    cs = parse_final_answer("I think the bug is in src/pkg/mod.py:42 near the top.")
    assert Citation("src/pkg/mod.py", 42, 42) in cs.citations
    assert cs.malformed == ()


def test_backticked_path_followed_by_range_is_a_citation():
    # Models often quote only the path: `src/a.py`:10-20, not `src/a.py:10-20`.
    cs = parse_final_answer(
        "<final_answer>\n- `src/a.py`:10-20\n`src/b.py`:7\n</final_answer>"
    )
    assert cs.citations == (Citation("src/a.py", 10, 20), Citation("src/b.py", 7, 7))
    assert cs.malformed == ()
    assert citation_path("`src/a.py`:10-20") == "src/a.py"


def test_loose_scan_reads_backticked_path_followed_by_range():
    cs = parse_final_answer("The bug is in `src/pkg/mod.py`:42-58 near the top.")
    assert cs.citations == (Citation("src/pkg/mod.py", 42, 58),)


def test_fallback_scan_ignores_wrapping_punctuation():
    # A citation in parentheses, brackets or quotes names the file, not "(src/a.py".
    cs = parse_final_answer(
        "The bug (src/a.py:10-20) and [src/b.py:5], also \"src/c.py:7\" and <src/d.py:1>."
    )
    assert [c.path for c in cs.citations] == ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
    assert cs.citations[0] == Citation("src/a.py", 10, 20)


def test_normalize_path_variants():
    assert normalize_path("./a/b.py") == "a/b.py"
    assert normalize_path("/a/b.py") == "a/b.py"
    assert normalize_path("`a\\b.py`") == "a/b.py"


def test_citation_path_strips_range():
    assert citation_path("src/a.py:10-20") == "src/a.py"
    assert citation_path("src/a.py:42") == "src/a.py"
    assert citation_path("- `./src/a.py`") == "src/a.py"
    assert citation_path("src/a.py") == "src/a.py"


# ── localization scoring ─────────────────────────────────────────────────────


def _ref(files):
    return ReferenceLocations(files=files)


def test_perfect_file_and_line_match():
    pred = parse_final_answer("<final_answer>\nsrc/a.py:10-20\n</final_answer>")
    score = score_localization(pred, _ref({"src/a.py": ((10, 20),)}))
    assert score.file_f1 == 1.0
    assert score.line_f1 == 1.0
    assert score.citation_compactness == 1.0


def test_partial_file_precision_full_recall():
    # Predicted the right file plus an extra one; reference has only one file.
    pred = parse_final_answer(
        "<final_answer>\nsrc/a.py:10-20\nsrc/extra.py:1-5\n</final_answer>"
    )
    score = score_localization(pred, _ref({"src/a.py": ((10, 20),)}))
    assert score.file_precision == 0.5
    assert score.file_recall == 1.0
    assert round(score.file_f1, 4) == 0.6667


def test_line_partial_overlap():
    # Cited 10-20 (11 lines); reference 15-25 (11 lines); overlap 15-20 (6 lines).
    pred = parse_final_answer("<final_answer>\nsrc/a.py:10-20\n</final_answer>")
    score = score_localization(pred, _ref({"src/a.py": ((15, 25),)}))
    assert round(score.line_precision, 4) == round(6 / 11, 4)
    assert round(score.line_recall, 4) == round(6 / 11, 4)
    assert score.citation_compactness == 1.0


def test_whole_file_citation_scores_file_only_no_lines():
    # A whole-file citation gets file credit but contributes no predicted lines,
    # and must not score as maximally compact (0.0) — compactness is undefined.
    pred = parse_final_answer("<final_answer>\nsrc/a.py\n</final_answer>")
    score = score_localization(pred, _ref({"src/a.py": ((10, 20),)}))
    assert score.file_f1 == 1.0
    assert score.line_precision == 0.0
    assert score.line_recall == 0.0
    assert score.citation_compactness is None


def test_overbroad_citation_compactness_above_one():
    # Cited 1-100 (100 lines) against a 10-line reference.
    pred = parse_final_answer("<final_answer>\nsrc/a.py:1-100\n</final_answer>")
    score = score_localization(pred, _ref({"src/a.py": ((1, 10),)}))
    assert score.citation_compactness == 10.0
    assert round(score.line_precision, 4) == 0.1
    assert score.line_recall == 1.0


def test_reference_keys_that_normalize_alike_pool_their_ranges():
    # ``a.py`` and ``./a.py`` are one file: citing both reference ranges recalls
    # all of them, not just the range of whichever key came last.
    pred = parse_final_answer(
        "<final_answer>\na.py:1-5\na.py:10-12\n</final_answer>"
    )
    score = score_localization(pred, _ref({"a.py": ((1, 5),), "./a.py": ((10, 12),)}))
    assert score.line_recall == 1.0
    assert score.line_precision == 1.0
    assert score.citation_compactness == 1.0


def test_mixed_wholefile_and_ranged_refs_score_lines_only_for_ranged():
    # a.py is a whole-file ref (no line truth); b.py is ranged. A perfect answer
    # that cites a range for a.py + the exact b.py range must score line F1 = 1.0
    # (a.py's lines are not line-level false positives).
    pred = parse_final_answer(
        "<final_answer>\na.py:1-100\nb.py:10-20\n</final_answer>"
    )
    score = score_localization(pred, _ref({"a.py": (), "b.py": ((10, 20),)}))
    assert score.file_f1 == 1.0
    assert score.line_precision == 1.0
    assert score.line_recall == 1.0
    assert score.line_f1 == 1.0
    assert score.citation_compactness == 1.0


def test_whole_file_reference_has_no_line_metrics():
    pred = parse_final_answer("<final_answer>\nsrc/a.py:1-5\n</final_answer>")
    score = score_localization(pred, _ref({"src/a.py": ()}))
    assert score.file_f1 == 1.0
    # No line-level ground truth → line metrics and compactness are undefined,
    # NOT a vacuous perfect 1.0.
    assert score.line_f1 is None
    assert score.line_precision is None
    assert score.line_recall is None
    assert score.citation_compactness is None


def test_file_only_task_empty_answer_not_perfect_line_match():
    # The reported bug: an empty answer on a file-only task scored line_f1 1.0.
    empty = parse_final_answer("<final_answer>\n</final_answer>")
    score = score_localization(empty, _ref({"src/a.py": ()}))
    assert score.file_f1 == 0.0      # missed the file
    assert score.line_f1 is None     # not a perfect line match


def test_merged_overlapping_citations_not_double_counted():
    # 10-20 and 15-25 merge to 10-25 (16 lines), not 22.
    pred = parse_final_answer(
        "<final_answer>\nsrc/a.py:10-20\nsrc/a.py:15-25\n</final_answer>"
    )
    score = score_localization(pred, _ref({"src/a.py": ((10, 25),)}))
    assert score.line_precision == 1.0
    assert score.line_recall == 1.0
    assert score.citation_compactness == 1.0


# ── operation-stream summary ─────────────────────────────────────────────────


def _op(type_, name="x", **metadata):
    return OperationRecord(type=type_, name=name, metadata=metadata)


def test_summary_none_without_exploration_signal():
    ops = [_op("harness", "toy"), _op("log", "note")]
    assert summarize_exploration(ops) is None


def test_pre_edit_counts_and_ordering():
    ops = [
        _op("search", "grep", broad=False),
        _op("read", "read", path="a.py"),
        _op("read", "read", path="b.py"),
        _op("edit", "write", path="a.py"),
        _op("read", "read", path="c.py"),  # after edit: not counted pre-edit
    ]
    m = summarize_exploration(ops)
    assert m["pre_edit_tool_calls"] == 3
    assert m["pre_edit_reads"] == 2
    assert m["pre_edit_searches"] == 1
    assert m["read_count"] == 3


def test_no_edit_run_marks_pre_edit_counts_unavailable():
    # Reads/searches but no edit (Q&A / pure exploration): pre-edit counts are
    # unavailable (None), not a misleading 0.
    ops = [_op("read", "read", path="a.py"), _op("search", "grep", command="rg x src/")]
    m = summarize_exploration(ops)
    assert m["pre_edit_tool_calls"] is None
    assert m["pre_edit_reads"] is None
    assert m["pre_edit_searches"] is None
    assert m["read_count"] == 1  # raw counts still reported


def test_repeated_read_ratio():
    ops = [
        _op("read", "read", path="a.py"),
        _op("read", "read", path="a.py"),  # repeat
        _op("read", "read", path="b.py"),
        _op("read", "read", path="a.py"),  # repeat again
    ]
    m = summarize_exploration(ops)
    assert m["repeated_read_ratio"] == 0.5  # 2 of 4 reads were re-reads


def test_broad_search_after_subagent_and_inference():
    ops = [
        _op("subagent", "fastcontext", citation_paths=["a.py", "b.py"]),
        _op("search", "grep", command="grep -R foo ."),  # inferred broad, after subagent
        _op("search", "grep", broad=True),               # flagged broad
    ]
    m = summarize_exploration(ops)
    assert m["subagent_invocation_count"] == 1
    assert m["broad_search_count"] == 2
    assert m["broad_search_inferred_count"] == 1
    assert m["broad_search_after_subagent_count"] == 2


def test_scoped_search_not_inferred_broad():
    # A scoped ripgrep (path argument present) is targeted, not broad; only the
    # recursive grep should count.
    ops = [
        _op("search", "rg", command="rg symbol src/module.py"),
        _op("search", "grep", command="grep -R foo ."),
    ]
    m = summarize_exploration(ops)
    assert m["search_count"] == 2
    assert m["broad_search_count"] == 1
    assert m["broad_search_inferred_count"] == 1


def test_snake_case_tool_names_classified():
    # Underscores must act as separators so read_file/str_replace_editor match.
    ops = [
        _op("tool", "read_file", path="a.py"),
        _op("tool", "str_replace_editor", path="a.py"),
    ]
    m = summarize_exploration(ops)
    assert m["read_count"] == 1
    # str_replace_editor is an edit op, so the read precedes the first edit.
    assert m["pre_edit_tool_calls"] == 1
    assert m["pre_edit_reads"] == 1


def test_prose_query_not_inferred_broad():
    # A human-readable query mentioning "find" is intent, not a shell `find`.
    ops = [_op("search", "grep", query="find Foo in src/bar.py")]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 0


def test_unscoped_ripgrep_is_broad_but_scoped_is_not():
    ops = [
        _op("search", "rg", command="rg symbol"),                 # no path → broad
        _op("search", "rg", command="rg symbol src/module.py"),   # scoped → not broad
        _op("search", "rg", command="/usr/bin/rg pattern"),       # binary path → broad
    ]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 2
    assert m["broad_search_inferred_count"] == 2


def test_ripgrep_option_values_not_treated_as_path():
    # -g/--type take a value; without a PATH these still recurse the cwd → broad.
    ops = [
        _op("search", "rg", command="rg -g '*.py' symbol"),       # broad
        _op("search", "rg", command="rg --type py symbol"),       # broad
        _op("search", "rg", command="rg --type=py symbol"),       # broad
        _op("search", "rg", command="rg -g '*.py' symbol src/"),  # scoped → not broad
    ]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 3


def test_rg_pattern_option_makes_positionals_paths():
    # With -e/--regexp/--files the pattern is not a positional, so a single
    # positional is already a PATH (scoped), and none means broad.
    ops = [
        _op("search", "rg", command="rg -e foo src/"),     # scoped → not broad
        _op("search", "rg", command="rg -e foo"),          # no path → broad
        _op("search", "rg", command="rg --files src/"),    # scoped → not broad
        _op("search", "rg", command="rg --files"),         # cwd listing → broad
    ]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 2


def test_argv_list_preserves_boundaries():
    # metadata.args as an argv list must not be re-joined: ["rg", "error
    # message"] has no PATH and is broad, but a naive join would mis-split it.
    ops = [
        _op("search", "rg", args=["rg", "error message"]),       # no path → broad
        _op("search", "rg", args=["rg", "error message", "src/"]),  # scoped → not broad
    ]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 1


def test_rg_double_dash_ends_flag_parsing():
    # `--` makes following `-foo` a positional (pattern), so a trailing path is
    # scoped; without a path it is broad.
    ops = [
        _op("search", "rg", command="rg -- -foo src/"),   # scoped → not broad
        _op("search", "rg", command="rg -- -foo"),         # pattern only → broad
    ]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 1


def test_quoted_multiword_pattern_is_broad():
    # shlex parsing keeps "error message" as one token, so no PATH was supplied.
    ops = [
        _op("search", "rg", command='rg "error message"'),         # broad
        _op("search", "rg", command='rg "error message" src/'),    # scoped → not broad
    ]
    m = summarize_exploration(ops)
    assert m["broad_search_count"] == 1


def test_nested_metadata_tokens_and_citations_are_read():
    # The documented DOKIMI_OP shape nests fields under `metadata`;
    # load_operations preserves that as op.metadata["metadata"].
    ops = [
        OperationRecord(
            type="subagent",
            name="fastcontext",
            metadata={"metadata": {"total_tokens": 2000,
                                   "citation_paths": ["a.py"]}},
        ),
        OperationRecord(type="read", name="read", metadata={"metadata": {"path": "a.py"}}),
    ]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["subagent_tokens"] == 2000
    assert m["full_system_tokens"] == 12_000
    assert m["main_reads_overlap_with_citations"] == 1.0  # read a.py matches citation


def test_harness_seed_op_not_classified_as_exploration():
    # A harness id containing "review" (substring "view") must not be read-
    # classified; a non-exploring run emits no exploration block.
    seed = OperationRecord(type="harness", name="code-review")
    assert summarize_exploration([seed]) is None
    result = HarnessResult(
        harness="code-review",
        task="t",
        command=["run"],
        returncode=0,
        elapsed_sec=1.0,
        reward=None,
        passed=False,
        operations=[seed],
        metrics={},
    )
    assert "exploration_metrics" not in result.as_dict()["metrics"]


def test_broad_after_failed_subagent_not_counted():
    # A broad search after a FAILED/empty subagent is a fallback, not redundant
    # search after focused evidence, so it must not inflate the after count.
    failed = [
        OperationRecord(type="subagent", name="fc", status="error",
                        metadata={"citation_count": 0}),
        _op("search", "grep", command="grep -R x ."),
    ]
    m = summarize_exploration(failed)
    assert m["broad_search_count"] == 1
    assert m["broad_search_after_subagent_count"] == 0

    # A broad search after a SUCCESSFUL subagent still counts.
    ok = [
        OperationRecord(type="subagent", name="fc", status="ok",
                        metadata={"citation_count": 2, "citation_paths": ["a.py"]}),
        _op("search", "grep", command="grep -R x ."),
    ]
    m2 = summarize_exploration(ok)
    assert m2["broad_search_after_subagent_count"] == 1


def test_main_reads_overlap_with_citations():
    ops = [
        _op("subagent", "fastcontext", citation_paths=["a.py", "b.py"]),
        _op("read", "read", path="a.py"),   # overlaps a citation
        _op("read", "read", path="z.py"),   # does not
    ]
    m = summarize_exploration(ops)
    assert m["main_reads_overlap_with_citations"] == 0.5


def test_partial_read_paths_withhold_path_metrics():
    # One read has a path, one does not: path-dependent metrics are not
    # derivable over the full set, so they are withheld (None).
    ops = [
        _op("subagent", "fastcontext", citation_paths=["a.py"]),
        _op("read", "read", path="a.py"),
        _op("read", "read"),  # no path metadata
    ]
    m = summarize_exploration(ops)
    assert m["read_count"] == 2
    assert m["repeated_read_ratio"] is None
    assert m["main_reads_overlap_with_citations"] is None


def test_overlap_handles_ranged_citation_entries():
    # Subagent stored full <final_answer> entries (path:start-end); the bare
    # main read of the same file must still count as overlapping.
    ops = [
        _op("subagent", "fastcontext", citations=["a.py:10-20", "b.py:5"]),
        _op("read", "read", path="a.py"),
    ]
    m = summarize_exploration(ops)
    assert m["main_reads_overlap_with_citations"] == 1.0


def test_token_accounting_split():
    ops = [
        _op("subagent", "fastcontext", total_tokens=2000),
        _op("read", "read", path="a.py", total_tokens=300),
        _op("search", "grep", total_tokens=200),
    ]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["main_agent_tokens"] == 10_000
    assert m["subagent_tokens"] == 2000
    assert m["subagent_tokens_complete"] is True
    assert m["full_system_tokens"] == 12_000
    assert m["read_search_token_share"] == 0.05  # (300+200)/10000


def test_unmeasured_subagent_tokens_not_reported_as_free():
    # A subagent ran but reported no token metadata: subagent cost is unknown,
    # so it must not read as 0, and full_system must not equal the main total.
    ops = [_op("subagent", "fastcontext", citation_paths=["a.py"])]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["subagent_tokens"] is None
    assert m["subagent_tokens_complete"] is False
    assert m["full_system_tokens"] is None


def test_partial_subagent_tokens_marked_incomplete():
    # One subagent reports tokens, another does not: report the known lower bound
    # but flag it incomplete and withhold a (would-be understated) full_system.
    ops = [
        _op("subagent", "fastcontext", total_tokens=2000),
        _op("subagent", "fastcontext"),
    ]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["subagent_tokens"] == 2000
    assert m["subagent_tokens_complete"] is False
    assert m["full_system_tokens"] is None


def test_no_subagents_full_system_equals_main():
    ops = [_op("read", "read", path="a.py", total_tokens=300)]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["subagent_tokens"] == 0
    assert m["subagent_tokens_complete"] is True
    assert m["full_system_tokens"] == 10_000


def test_token_share_none_without_per_op_tokens():
    ops = [_op("read", "read", path="a.py"), _op("search", "grep")]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["read_search_token_share"] is None


def test_token_share_none_with_partial_per_op_tokens():
    # Mixed telemetry: one op has tokens, one does not. A partial sum would be a
    # misleading lower bound, so the share is withheld entirely.
    ops = [
        _op("read", "read", path="a.py", total_tokens=300),
        _op("search", "grep"),  # no token metadata
    ]
    m = summarize_exploration(ops, main_tokens=10_000)
    assert m["read_search_token_share"] is None


# ── HarnessResult wiring ─────────────────────────────────────────────────────


def test_harness_result_emits_exploration_block_when_exploring():
    result = HarnessResult(
        harness="h",
        task="t",
        command=["run"],
        returncode=0,
        elapsed_sec=1.0,
        reward=1.0,
        passed=True,
        operations=[
            OperationRecord(type="read", name="read", metadata={"path": "a.py"}),
            OperationRecord(type="edit", name="write", metadata={"path": "a.py"}),
        ],
        metrics={"total_tokens": 500},
    )
    row = result.as_dict()
    assert "exploration_metrics" in row["metrics"]
    assert row["metrics"]["exploration_metrics"]["read_count"] == 1
    assert row["metrics"]["exploration_metrics"]["main_agent_tokens"] == 500


def test_harness_result_omits_block_without_exploration():
    result = HarnessResult(
        harness="h",
        task="t",
        command=["run"],
        returncode=0,
        elapsed_sec=1.0,
        reward=None,
        passed=False,
        operations=[OperationRecord(type="harness", name="h")],
        metrics={},
    )
    assert "exploration_metrics" not in result.as_dict()["metrics"]


# ── skill fixture loads ──────────────────────────────────────────────────────


def test_fastcontext_skill_fixture_loads():
    skill = load_skill("data/skills/fastcontext")
    assert skill.name == "fastcontext"
    assert skill.description
    assert "<final_answer>" in skill.body


@pytest.mark.parametrize("path", ["(app)/page.tsx", "[id].tsx", "[...slug]/page.tsx"])
def test_fallback_scan_keeps_leading_brackets_of_a_path(path):
    cs = parse_final_answer(f"See {path}:10-20 for it.")
    assert cs.citations == (Citation(path, 10, 20),)


def test_fallback_scan_strips_a_wrapping_pair_but_keeps_a_bracketed_path():
    cs = parse_final_answer("Seen in ([id].tsx:5) and (app)/x.tsx:3.")
    assert [c.path for c in cs.citations] == ["[id].tsx", "(app)/x.tsx"]


@pytest.mark.parametrize(
    ("text", "paths"),
    [
        ("(see (app)/page.tsx:10)", ["(app)/page.tsx"]),
        ("see [id].tsx:3", ["[id].tsx"]),
        ("see [...slug]/page.tsx:3", ["[...slug]/page.tsx"]),
        ("(src/a.py:10, src/b.py:20)", ["src/a.py", "src/b.py"]),
        ("(src/a.py:10 and more)", ["src/a.py"]),
    ],
)
def test_loose_scan_keeps_a_bracket_only_when_the_path_closes_it(text, paths):
    from dokimi.metrics.exploration import _parse_loose

    assert [c.path for c in _parse_loose(text).citations] == paths
